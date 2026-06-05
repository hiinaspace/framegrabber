"""notify: header sanitization (HTTP headers must be ASCII)."""

from __future__ import annotations

import httpx

from framegrabber import config, notify


def test_emoji_title_is_stripped_to_ascii(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def fake_post(url, *, content, headers, timeout):
        captured["headers"] = headers
        captured["content"] = content
        return _Resp()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    cfg = config.Config(ntfy_topic="t")

    ok = notify.notify(
        cfg, "🚨 Steam Frame available!", "body 🚨 ok", priority=5, tags="rotating_light"
    )

    assert ok is True
    # Title header is pure ASCII; emoji removed.
    captured["headers"]["Title"].encode("ascii")  # must not raise
    assert "Steam Frame available!" in captured["headers"]["Title"]
    # Body keeps UTF-8 (sent as bytes).
    assert "🚨".encode() in captured["content"]


def test_all_emoji_title_falls_back(monkeypatch):
    monkeypatch.setattr(
        notify.httpx, "post", lambda *a, **k: type("R", (), {"raise_for_status": lambda s: None})()
    )
    cfg = config.Config(ntfy_topic="t")
    # Should not raise even if the title is entirely non-ASCII.
    assert notify.notify(cfg, "🚨🚨", "b") is True


def test_token_sets_authorization_header(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        notify.httpx,
        "post",
        lambda url, *, content, headers, timeout: (
            captured.update(headers=headers)
            or type("R", (), {"raise_for_status": lambda s: None})()
        ),
    )
    cfg = config.Config(ntfy_topic="t", ntfy_token="tk_secret")
    assert notify.notify(cfg, "t", "b") is True
    assert captured["headers"]["Authorization"] == "Bearer tk_secret"


def test_no_token_omits_authorization_header(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        notify.httpx,
        "post",
        lambda url, *, content, headers, timeout: (
            captured.update(headers=headers)
            or type("R", (), {"raise_for_status": lambda s: None})()
        ),
    )
    cfg = config.Config(ntfy_topic="t")
    assert notify.notify(cfg, "t", "b") is True
    assert "Authorization" not in captured["headers"]


def test_no_topic_returns_false(monkeypatch):
    called = False

    def fake_post(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    cfg = config.Config(ntfy_topic="")
    assert notify.notify(cfg, "t", "b") is False
    assert called is False


def test_http_error_returns_false(monkeypatch):
    def fake_post(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    cfg = config.Config(ntfy_topic="t")
    assert notify.notify(cfg, "t", "b") is False
