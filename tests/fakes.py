"""A scripted stand-in for the OpenRouter client (chat.send), shared by the tests."""

import json
from types import SimpleNamespace


class FakeClient:
    """Plays back scripted model turns (OpenRouter chat shape). Each turn is a list of (tool name, input)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        self.chat = SimpleNamespace(send=self._send)

    def _send(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot: the list keeps growing
        tool_calls = []
        if self.turns:
            for i, (name, tool_input) in enumerate(self.turns.pop(0)):
                args = tool_input if isinstance(tool_input, str) else json.dumps(tool_input)
                tool_calls.append(SimpleNamespace(id=f"t{len(self.calls)}_{i}", type="function", function=SimpleNamespace(name=name, arguments=args)))
        message = SimpleNamespace(role="assistant", content=None if tool_calls else "done", tool_calls=tool_calls or None, reasoning_details=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(index=0, message=message, finish_reason="tool_calls" if tool_calls else "stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=SimpleNamespace(cached_tokens=0)),
        )


def tool_results(call):
    """The tool messages sent back to the model in one recorded call."""
    return [m for m in call["messages"] if m.get("role") == "tool"]


def report(status, **extra):
    base = dict(status=status, summary="s", steps=[], failing_step=None, evidence=None, cause="",
                cause_source="", cause_quote="", suggested_fix="", packages=[], confidence=0.9)
    base.update(extra)
    return ("report", base)
