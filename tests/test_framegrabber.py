"""Tests for the self-contained framegrabber script (no network)."""

from __future__ import annotations

import json

import framegrabber as fg

COMING_SOON = {
    "name": "Steam Frame",
    "release_date": {"coming_soon": True, "date": "Coming soon"},
    "price_overview": None,
    "packages": None,
}
RELEASED = {
    "name": "Steam Frame",
    "release_date": {"coming_soon": False, "date": "1 Aug, 2026"},
    "price_overview": {"final": 49900},
    "packages": [123],
}


# --- appdetails trigger logic ------------------------------------------------------------


def test_no_event_on_first_run():
    assert fg.appdetails_reasons(None, fg.fingerprint(COMING_SOON)) == []


def test_no_event_on_identical():
    fp = fg.fingerprint(COMING_SOON)
    assert fg.appdetails_reasons(fp, fg.fingerprint(COMING_SOON)) == []


def test_event_on_release():
    reasons = fg.appdetails_reasons(fg.fingerprint(COMING_SOON), fg.fingerprint(RELEASED))
    joined = " ".join(reasons).lower()
    assert (
        "coming soon" in joined and "price" in joined and "package" in joined and "2026" in joined
    )


def test_event_on_firm_date_only():
    # Still "coming soon" but a concrete date appears — the likely terminal event.
    new = {**COMING_SOON, "release_date": {"coming_soon": True, "date": "Q1 2026"}}
    reasons = fg.appdetails_reasons(fg.fingerprint(COMING_SOON), fg.fingerprint(new))
    assert any("date set" in r for r in reasons)


def test_literal_coming_soon_date_does_not_fire():
    new = {**COMING_SOON, "release_date": {"coming_soon": True, "date": "Coming soon"}}
    assert fg.appdetails_reasons(fg.fingerprint(COMING_SOON), fg.fingerprint(new)) == []


# --- news matching + parsing -------------------------------------------------------------


def test_news_matches_availability():
    kw = ["release", "reservation", "order"]
    assert fg.news_matches("Steam Frame reservations open", "", kw)
    assert fg.news_matches(
        "Hands-on with the Steam Frame", "pre-order details and release date", ["release"]
    )


def test_news_ignores_unrelated_or_specsy():
    kw = ["release", "order"]
    assert not fg.news_matches("Steam Deck 2 rumor", "", kw)  # not the Frame
    assert not fg.news_matches("Steam Frame specs leak", "teardown photos", kw)  # no keyword


def test_parse_rss():
    raw = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Steam Frame reservations open</title><link>http://x</link><guid>g1</guid>
        <description>now available</description></item>
      <item><title>Other</title><link>http://y</link></item>
    </channel></rss>"""
    items = fg.parse_rss(raw)
    assert len(items) == 2
    assert items[0]["id"] == "g1" and items[0]["link"] == "http://x"
    assert items[1]["id"] == "http://y"  # falls back to link when guid missing


def test_parse_rss_malformed_returns_empty():
    assert fg.parse_rss(b"<not xml") == []


# --- state round-trip --------------------------------------------------------------------


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert fg.load_state(p) == {"appdetails": {}, "seen_news": []}
    st = {"appdetails": {"4165890": fg.fingerprint(COMING_SOON)}, "seen_news": ["a", "b"]}
    fg.save_state(p, st)
    assert fg.load_state(p)["seen_news"] == ["a", "b"]


def test_state_seen_news_capped(tmp_path):
    p = tmp_path / "state.json"
    fg.save_state(p, {"appdetails": {}, "seen_news": [f"g{i}" for i in range(600)]})
    assert len(fg.load_state(p)["seen_news"]) == 500


def test_corrupt_state_starts_fresh(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{bad")
    assert fg.load_state(p) == {"appdetails": {}, "seen_news": []}


# --- orchestration (mock network via monkeypatch) ----------------------------------------


def test_news_cold_start_seeds_no_push(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fg, "fetch_news", lambda url: [{"id": "g1", "title": "t", "link": "", "summary": ""}]
    )
    pushed = []
    monkeypatch.setattr(fg, "notify", lambda cfg, **kw: pushed.append(kw) or True)
    cfg = fg.Config(ntfy_topic="t", state_file=tmp_path / "s.json")
    state = {"appdetails": {}, "seen_news": []}
    fg.run_news(cfg, state, dry_run=False)
    assert state["seen_news"] == ["g1"] and pushed == []


def test_news_new_matching_item_fires(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fg,
        "fetch_news",
        lambda url: [
            {"id": "g2", "title": "Steam Frame pre-order live", "link": "http://z", "summary": ""}
        ],
    )
    pushed = []
    monkeypatch.setattr(fg, "notify", lambda cfg, **kw: pushed.append(kw) or True)
    cfg = fg.Config(ntfy_topic="t", state_file=tmp_path / "s.json")
    state = {"appdetails": {}, "seen_news": ["old"]}  # not a cold start
    fg.run_news(cfg, state, dry_run=False)
    assert "g2" in state["seen_news"] and len(pushed) == 1


def test_appdetails_event_fires_and_dedupes(tmp_path, monkeypatch):
    seq = iter([COMING_SOON, RELEASED, RELEASED])
    monkeypatch.setattr(fg, "fetch_appdetails", lambda appid, cc: next(seq))
    pushed = []
    monkeypatch.setattr(fg, "notify", lambda cfg, **kw: pushed.append(kw) or True)
    cfg = fg.Config(ntfy_topic="t", state_file=tmp_path / "s.json")
    state = {"appdetails": {}, "seen_news": ["x"]}
    fg.run_appdetails(cfg, state, dry_run=False)  # baseline (coming soon)
    fg.run_appdetails(cfg, state, dry_run=False)  # released -> 1 push
    fg.run_appdetails(cfg, state, dry_run=False)  # still released -> no repeat
    assert len(pushed) == 1
    assert pushed[0]["priority"] == 4


# --- notify auth header ------------------------------------------------------------------


def test_notify_sets_bearer_and_strips_emoji(monkeypatch):
    captured = {}

    class _Req:
        def __init__(self, url, data=None, headers=None, method=None):
            captured["headers"] = headers
            captured["data"] = data

    monkeypatch.setattr(fg.urllib.request, "Request", _Req)
    monkeypatch.setattr(
        fg.urllib.request,
        "urlopen",
        lambda req, timeout=0: __import__("contextlib").nullcontext(),
    )
    cfg = fg.Config(ntfy_topic="t", ntfy_token="tk_abc")
    assert fg.notify(cfg, "🚨 hi", "body") is True
    captured["headers"]["Title"].encode("ascii")  # no raise
    assert captured["headers"]["Authorization"] == "Bearer tk_abc"


def test_notify_no_topic_false(monkeypatch):
    called = False

    def boom(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(fg.urllib.request, "urlopen", boom)
    assert fg.notify(fg.Config(ntfy_topic=""), "t", "b") is False
    assert called is False


def test_dump_state_is_json(tmp_path):
    p = tmp_path / "s.json"
    fg.save_state(p, {"appdetails": {}, "seen_news": []})
    json.loads(p.read_text())  # valid JSON
