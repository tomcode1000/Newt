"""Span inputs stay small. neatlogs serializes every span's arguments (including `self`) by walking
attributes with no cycle check; a Worker holding a real OpenRouter client never finished serializing,
which froze all 4 workers of the first `newt run` before any sandbox started."""

import threading
from types import SimpleNamespace

from neatlogs.decorators._base import _safe_json_dumps
from openrouter import OpenRouter

from newt.coordinator import Coordinator, RunReport
from newt.ledger import Ledger
from newt.tools import ToolBox
from newt.worker import Worker


def serialize_within(value, seconds=5.0):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("json", _safe_json_dumps(value)), daemon=True)
    t.start()
    t.join(seconds)
    assert "json" in out, "span input serialization did not finish"
    return out["json"]


def test_worker_with_real_client_serializes_quickly():
    worker = Worker("# page", "p.md", client=OpenRouter(api_key="x"), model="m")
    text = serialize_within({"self": worker})
    assert '"page": "p.md"' in text and len(text) < 500


def test_toolbox_coordinator_and_report_serialize_quickly(tmp_path):
    tools = ToolBox(SimpleNamespace(name="newt-abc"))
    coord = Coordinator(tmp_path, ledger=Ledger(), client=OpenRouter(api_key="x"), model="m")
    text = serialize_within({"self": coord, "tools": tools, "report": RunReport(run_id=1, gate=True)})
    assert "newt-abc" in text and len(text) < 1000
