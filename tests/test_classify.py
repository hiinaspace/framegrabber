"""Classify: JSON extraction and failure policy (no real claude CLI call)."""

from __future__ import annotations

from framegrabber import classify


def test_resolve_claude_prefers_path(monkeypatch):
    monkeypatch.setattr(classify.shutil, "which", lambda name: "/usr/bin/claude")
    assert classify._resolve_claude("claude") == "/usr/bin/claude"


def test_resolve_claude_falls_back_to_local_bin(monkeypatch, tmp_path):
    monkeypatch.setattr(classify.shutil, "which", lambda name: None)
    fake_home = tmp_path
    (fake_home / ".local/bin").mkdir(parents=True)
    (fake_home / ".local/bin/claude").write_text("#!/bin/sh\n")
    monkeypatch.setattr(classify.Path, "home", staticmethod(lambda: fake_home))
    assert classify._resolve_claude("claude") == str(fake_home / ".local/bin/claude")


def test_resolve_claude_absolute_path(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("x")
    assert classify._resolve_claude(str(exe)) == str(exe)
    assert classify._resolve_claude(str(tmp_path / "missing")) is None


def test_resolve_claude_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(classify.shutil, "which", lambda name: None)
    monkeypatch.setattr(classify.Path, "home", staticmethod(lambda: tmp_path))
    assert classify._resolve_claude("claude") is None


def test_extract_plain_json():
    assert classify._extract_json('{"availability_event": true, "kind": "release"}') == {
        "availability_event": True,
        "kind": "release",
    }


def test_extract_json_from_chatty_text():
    text = (
        'Sure! Here is the verdict:\n{"availability_event": false, "kind": "none"}\nHope that helps'
    )
    assert classify._extract_json(text) == {"availability_event": False, "kind": "none"}


def test_extract_json_none_on_garbage():
    assert classify._extract_json("no json here") is None


def test_fail_open_synthesizes_alert(monkeypatch):
    monkeypatch.setattr(classify, "_run_claude", lambda *a, **k: None)
    v = classify.classify("model", "payload", fail_open=True)
    assert v["availability_event"] is True
    assert v["_llm_failed"] is True


def test_fail_closed_suppresses(monkeypatch):
    monkeypatch.setattr(classify, "_run_claude", lambda *a, **k: None)
    v = classify.classify("model", "payload", fail_open=False)
    assert v["availability_event"] is False
    assert v["_llm_failed"] is True


def test_passthrough_verdict(monkeypatch):
    monkeypatch.setattr(
        classify,
        "_run_claude",
        lambda *a, **k: {
            "availability_event": True,
            "kind": "reservation",
            "confidence": 0.9,
            "summary": "x",
        },
    )
    v = classify.classify("model", "payload", fail_open=False)
    assert v["kind"] == "reservation"
