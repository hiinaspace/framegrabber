"""Tests for the primary, rule-based appdetails trigger logic."""

from __future__ import annotations

from framegrabber import triggers

# The real, current baseline (verified live): coming soon, no price, no packages.
COMING_SOON = {
    "name": "Steam Frame",
    "type": "hardware",
    "is_free": False,
    "release_date": {"coming_soon": True, "date": "Coming soon"},
    "price_overview": None,
    "packages": None,
}

RELEASED_WITH_PRICE = {
    "name": "Steam Frame",
    "type": "hardware",
    "is_free": False,
    "release_date": {"coming_soon": False, "date": "1 Aug, 2026"},
    "price_overview": {"final": 49900, "currency": "USD"},
    "packages": [123456],
}


def fp(data):
    return triggers.fingerprint(data)


def test_no_trigger_on_first_ever_run():
    # No prior fingerprint -> record baseline, never fire.
    assert triggers.evaluate(4165890, "Steam Frame", None, fp(COMING_SOON)) is None


def test_no_trigger_on_identical_snapshot():
    old = fp(COMING_SOON)
    assert triggers.evaluate(4165890, "Steam Frame", old, fp(COMING_SOON)) is None


def test_trigger_on_release():
    old = fp(COMING_SOON)
    trig = triggers.evaluate(4165890, "Steam Frame", old, fp(RELEASED_WITH_PRICE))
    assert trig is not None
    joined = " ".join(trig.reasons).lower()
    assert "coming soon" in joined
    assert "price" in joined
    assert "package" in joined
    assert "2026" in joined


def test_trigger_on_price_only():
    old = fp(COMING_SOON)
    new = fp({**COMING_SOON, "price_overview": {"final": 49900}})
    trig = triggers.evaluate(4165890, "Steam Frame", old, new)
    assert trig is not None
    assert any("price" in r for r in trig.reasons)


def test_trigger_on_packages_appearing():
    old = fp(COMING_SOON)
    new = fp({**COMING_SOON, "packages": [1, 2]})
    trig = triggers.evaluate(4165890, "Steam Frame", old, new)
    assert trig is not None
    assert any("package" in r for r in trig.reasons)


def test_coming_soon_date_string_does_not_falsely_trigger():
    # "date" still the literal "Coming soon" must not count as a real date.
    old = fp(COMING_SOON)
    new = fp({**COMING_SOON, "release_date": {"coming_soon": True, "date": "Coming soon"}})
    assert triggers.evaluate(4165890, "Steam Frame", old, new) is None
