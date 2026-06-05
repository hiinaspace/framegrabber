"""State persistence + alert-flag behavior."""

from __future__ import annotations

from framegrabber import state


def test_load_missing_returns_empty(tmp_path):
    st = state.State.load(tmp_path / "nope.json")
    assert st.appdetails == {}
    assert st.seen_news == []


def test_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    st = state.State.load(p)
    st.appdetails["4165890"] = {"coming_soon": True, "package_count": 0}
    st.seen_news.append("guid-1")
    st.landing_hash = "abc"
    st.save()

    st2 = state.State.load(p)
    assert st2.appdetails["4165890"]["coming_soon"] is True
    assert st2.seen_news == ["guid-1"]
    assert st2.landing_hash == "abc"


def test_corrupt_state_starts_fresh(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    st = state.State.load(p)
    assert st.appdetails == {}


def test_seen_news_capped(tmp_path):
    p = tmp_path / "state.json"
    st = state.State.load(p)
    st.seen_news = [f"g{i}" for i in range(600)]
    st.save()
    st2 = state.State.load(p)
    assert len(st2.seen_news) == 500
    assert st2.seen_news[-1] == "g599"


def test_alert_flag_lifecycle(tmp_path):
    flag = tmp_path / "ALERTED"
    assert not state.is_alerted(flag)
    state.set_alerted(flag, "Steam Frame available: price appeared")
    assert state.is_alerted(flag)
    assert "price appeared" in flag.read_text()
    assert state.clear_alerted(flag) is True
    assert not state.is_alerted(flag)
    assert state.clear_alerted(flag) is False
