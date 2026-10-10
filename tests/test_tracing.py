import os

import pytest

from newt import tracing


def test_parse_env_handles_quotes_comments_and_export():
    text = """
# comment
export A=1
B = "two words"
C='x=y'
D=plain # trailing comment
EMPTY=
"""
    assert tracing._parse_env(text) == {"A": "1", "B": "two words", "C": "x=y", "D": "plain"}


def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("NEWT_TEST_KEY=from_file\nNEWT_TEST_OTHER=other\n")
    monkeypatch.setenv("NEWT_TEST_KEY", "from_env")
    monkeypatch.delenv("NEWT_TEST_OTHER", raising=False)
    tracing.load_env(env)
    assert os.environ["NEWT_TEST_KEY"] == "from_env"
    assert os.environ["NEWT_TEST_OTHER"] == "other"
    monkeypatch.delenv("NEWT_TEST_OTHER")


def test_anthropic_client_requires_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(tracing, "load_env", lambda *a, **k: {})
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        tracing.anthropic_client()
