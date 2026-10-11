"""One chat call to OpenRouter, with rate-limit backoff.

Everything that talks to the model (worker, coordinator, fixer) goes through
`chat()`. It sends OpenAI-style messages and function tools, retries 429s and
transient upstream errors with backoff, and stops with `DailyLimitReached`
when the free-model daily quota is used up: that cannot be waited out within
a run, so Newt says so instead of pretending.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_ATTEMPTS = 8
MAX_WAIT_S = 90.0
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 529}


class DailyLimitReached(RuntimeError):
    """OpenRouter's per-day quota for free models is used up."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as the model sent it


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    message: dict[str, Any] = field(default_factory=dict)  # assistant message to append to history


def function_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert {name, description, input_schema} tool specs to OpenAI function tools."""
    return [
        {"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["input_schema"]}}
        for s in schemas
    ]


def chat(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 8000,
    sleep: Callable[[float], None] = time.sleep,
    on_event: Callable[[str], None] | None = None,
) -> Reply:
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if tools:
        kwargs["tools"] = tools
    on_event = on_event or (lambda msg: None)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _to_reply(client.chat.send(**kwargs))
        except Exception as e:  # SDK errors carry status_code; anything else is not ours to retry
            status = getattr(e, "status_code", None)
            if status == 200 and '"error"' in str(getattr(e, "body", "")):
                status = 502  # an upstream error delivered in a 200 body (e.g. provider_unavailable)
            if status not in RETRY_STATUSES:
                raise
            text = f"{e} {getattr(e, 'body', '')}"
            if status == 429 and "per-day" in text:
                raise DailyLimitReached(f"OpenRouter daily free-model limit reached: {str(e)[:300]}") from e
            if attempt == MAX_ATTEMPTS:
                raise
            wait = _wait_seconds(e, attempt)
            on_event(f"model call got HTTP {status}; retry {attempt}/{MAX_ATTEMPTS - 1} in {wait:.0f}s")
            sleep(wait)
    raise AssertionError("unreachable")


def _wait_seconds(error: Any, attempt: int) -> float:
    headers = getattr(error, "headers", None) or {}
    retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
    if retry_after:
        try:
            return min(MAX_WAIT_S, max(1.0, float(retry_after)))
        except ValueError:
            pass
    reset = headers.get("x-ratelimit-reset") if hasattr(headers, "get") else None
    if reset:
        try:
            seconds = float(reset) / 1000 - time.time()  # epoch milliseconds
            if 0 < seconds:
                return min(MAX_WAIT_S, max(1.0, seconds + 1))
        except ValueError:
            pass
    return min(MAX_WAIT_S, 5.0 * 2 ** (attempt - 1))


def _to_reply(result: Any) -> Reply:
    choice = result.choices[0]
    msg = choice.message
    content = _get(msg, "content")
    text = content if isinstance(content, str) else ("".join(_get(p, "text") or "" for p in content) if content else "")
    calls = [
        ToolCall(id=_get(tc, "id"), name=_get(_get(tc, "function"), "name"), arguments=_get(_get(tc, "function"), "arguments") or "{}")
        for tc in (_get(msg, "tool_calls") or [])
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
        ]
    # Reasoning models want their reasoning passed back unchanged on the next turn.
    details = _get(msg, "reasoning_details")
    if details:
        message["reasoning_details"] = [_plain(d) for d in details]

    usage = _get(result, "usage")
    prompt_details = _get(usage, "prompt_tokens_details") if usage else None
    return Reply(
        text=text,
        tool_calls=calls,
        finish_reason=_get(choice, "finish_reason"),
        input_tokens=(_get(usage, "prompt_tokens") or 0) if usage else 0,
        output_tokens=(_get(usage, "completion_tokens") or 0) if usage else 0,
        cached_tokens=(_get(prompt_details, "cached_tokens") or 0) if prompt_details else 0,
        message=message,
    )


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    value = getattr(obj, key, None)
    return None if type(value).__name__ == "Unset" else value


def _plain(obj: Any) -> Any:
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True, by_alias=True)
    if isinstance(obj, (dict, list, str, int, float, bool)) or obj is None:
        return obj
    return json.loads(json.dumps(obj, default=str))
