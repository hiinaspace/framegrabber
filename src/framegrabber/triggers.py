"""Rule-based detection on the Steam appdetails payload — the primary, LLM-free signal.

We reduce the full appdetails ``data`` to a small fingerprint and compare it to the last good
fingerprint. A change in any availability-bearing field is treated as a hard release/order
trigger. This is deliberately conservative: the current "coming soon" baseline has no price and
no packages, so any of these flipping is real news.
"""

from __future__ import annotations

from dataclasses import dataclass


def fingerprint(data: dict) -> dict:
    """Reduce appdetails ``data`` to the fields that indicate orderability."""
    release = data.get("release_date") or {}
    packages = data.get("packages") or []
    return {
        "coming_soon": bool(release.get("coming_soon", True)),
        "date": release.get("date", ""),
        "has_price": data.get("price_overview") is not None,
        "is_free": bool(data.get("is_free", False)),
        "package_count": len(packages),
    }


@dataclass
class Trigger:
    appid: int
    name: str
    reasons: list[str]


def evaluate(appid: int, name: str, old: dict | None, new: dict) -> Trigger | None:
    """Return a Trigger if ``new`` shows newly-available state vs ``old``, else None.

    With no prior fingerprint (first ever run) we never trigger — we just record the baseline.
    """
    if old is None:
        return None

    reasons: list[str] = []
    if old.get("coming_soon", True) and not new["coming_soon"]:
        reasons.append("no longer marked 'coming soon'")
    if not old.get("has_price", False) and new["has_price"]:
        reasons.append("a price now appears")
    old_pkgs = old.get("package_count", 0)
    if new["package_count"] > old_pkgs:
        reasons.append(f"purchasable packages appeared ({old_pkgs} -> {new['package_count']})")
    old_date = (old.get("date") or "").strip().lower()
    new_date = (new.get("date") or "").strip()
    if new_date and new_date.lower() != old_date and new_date.lower() != "coming soon":
        reasons.append(f"release date set to '{new_date}'")

    if not reasons:
        return None
    return Trigger(appid=appid, name=name, reasons=reasons)
