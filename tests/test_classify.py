"""Classify: JSON extraction and failure policy (no real claude CLI call)."""

from __future__ import annotations

from framegrabber import classify


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
