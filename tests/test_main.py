"""Integration tests for the landing + news orchestration, incl. cold-start guards."""

from __future__ import annotations

import pytest

from framegrabber import config, main, state


@pytest.fixture
def cfg(tmp_path):
    return config.Config(appids=[4165890], state_dir=tmp_path, ntfy_topic="t")


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(main.notify, "notify", lambda cfg, **kw: sent.append(kw) or True)
    return sent


# --- landing -----------------------------------------------------------------------------


def test_landing_cold_start_records_baseline_no_push(cfg, pushes, monkeypatch):
    monkeypatch.setattr(main.fetch, "fetch_landing_signal", lambda c: ("hash1", "wishlist"))
    called = []
    monkeypatch.setattr(main, "classify", lambda *a, **k: called.append(1) or {})
    st = state.State.load(cfg.state_file)

    main.run_landing(cfg, st, client=None, dry_run=False)

    assert st.landing_hash == "hash1"
    assert called == []  # never classified on cold start
    assert pushes == []


def test_landing_unchanged_no_push(cfg, pushes, monkeypatch):
    monkeypatch.setattr(main.fetch, "fetch_landing_signal", lambda c: ("hash1", "x"))
    monkeypatch.setattr(main, "classify", lambda *a, **k: pytest.fail("should not classify"))
    st = state.State.load(cfg.state_file)
    st.landing_hash = "hash1"

    main.run_landing(cfg, st, client=None, dry_run=False)
    assert pushes == []


def test_landing_change_to_reservation_fires(cfg, pushes, monkeypatch):
    monkeypatch.setattr(
        main.fetch, "fetch_landing_signal", lambda c: ("hash2", "Reserve your Steam Frame now")
    )
    monkeypatch.setattr(
        main,
        "classify",
        lambda *a, **k: {
            "availability_event": True,
            "kind": "reservation",
            "summary": "Reserve now",
        },
    )
    st = state.State.load(cfg.state_file)
    st.landing_hash = "hash1"  # already have a baseline

    main.run_landing(cfg, st, client=None, dry_run=False)

    assert st.landing_hash == "hash2"
    assert len(pushes) == 1
    assert "reservation" in pushes[0]["title"]


def test_landing_dry_run_suppresses_push(cfg, pushes, monkeypatch):
    monkeypatch.setattr(main.fetch, "fetch_landing_signal", lambda c: ("hash2", "Reserve"))
    monkeypatch.setattr(
        main, "classify", lambda *a, **k: {"availability_event": True, "kind": "reservation"}
    )
    st = state.State.load(cfg.state_file)
    st.landing_hash = "hash1"

    main.run_landing(cfg, st, client=None, dry_run=True)
    assert pushes == []


# --- news --------------------------------------------------------------------------------


def test_news_cold_start_seeds_no_classify(cfg, pushes, monkeypatch):
    items = [{"id": f"g{i}", "title": "t", "link": "l"} for i in range(5)]
    monkeypatch.setattr(main.fetch, "fetch_news", lambda url: items)
    monkeypatch.setattr(main, "classify", lambda *a, **k: pytest.fail("should not classify"))
    st = state.State.load(cfg.state_file)

    main.run_news(cfg, st, dry_run=False)

    assert set(st.seen_news) == {f"g{i}" for i in range(5)}
    assert pushes == []


def test_news_new_availability_item_fires(cfg, pushes, monkeypatch):
    monkeypatch.setattr(
        main.fetch,
        "fetch_news",
        lambda url: [{"id": "new1", "title": "Steam Frame reservations open", "link": "http://x"}],
    )
    monkeypatch.setattr(
        main,
        "classify",
        lambda *a, **k: {"availability_event": True, "summary": "Reservations open"},
    )
    st = state.State.load(cfg.state_file)
    st.seen_news = ["old1"]  # not a cold start

    main.run_news(cfg, st, dry_run=False)

    assert "new1" in st.seen_news
    assert len(pushes) == 1
    assert pushes[0]["url"] == "http://x"


def test_news_specs_rumor_does_not_fire(cfg, pushes, monkeypatch):
    monkeypatch.setattr(
        main.fetch,
        "fetch_news",
        lambda url: [{"id": "new2", "title": "Steam Frame specs leak", "link": "http://y"}],
    )
    monkeypatch.setattr(main, "classify", lambda *a, **k: {"availability_event": False})
    st = state.State.load(cfg.state_file)
    st.seen_news = ["old1"]

    main.run_news(cfg, st, dry_run=False)

    assert "new2" in st.seen_news  # marked seen so we don't re-judge
    assert pushes == []
