"""Network fetchers: Steam appdetails, the React landing page, and news RSS.

All functions return ``None`` on any failure (timeout, non-200, malformed body) rather than
raising, so a single run can degrade gracefully. The caller treats ``None`` as "no new
information this run" and never overwrites good stored state with it.
"""

from __future__ import annotations

import hashlib
import logging
import re

import feedparser
import httpx

from .config import APPDETAILS_URL, STORE_URL, USER_AGENT

log = logging.getLogger(__name__)

# Age-gate bypass: Steam gates hardware pages behind a birthdate check; this cookie satisfies it.
_COOKIES = {"birthtime": "0", "mature_content": "1", "wants_mature_content": "1"}

# Keys/substrings whose presence in the landing-page payload signals purchase/reservation state.
# We hash only the lines containing these so cosmetic churn (carousels) doesn't trip the diff.
_SIGNAL_TOKENS = (
    "reserve",
    "reservation",
    "waitlist",
    "wait list",
    "notify",
    "purchase",
    "add to cart",
    "addtocart",
    "buy now",
    "order now",
    "pre-order",
    "preorder",
    "release_date",
    "coming soon",
    "in stock",
    "out of stock",
    "available",
)


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        cookies=_COOKIES,
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
    )


def fetch_appdetails(client: httpx.Client, appid: int, cc: str) -> dict | None:
    """Return the ``data`` dict for one app, or None on any failure / ``success:false``.

    A ``success:false`` body is a transient/rate-limit condition for these ids, NOT evidence
    that the product changed — so we return None and let the caller keep prior state.
    """
    try:
        resp = client.get(
            APPDETAILS_URL,
            params={"appids": appid, "cc": cc, "l": "english"},
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("appdetails fetch failed for %s: %s", appid, e)
        return None

    entry = payload.get(str(appid))
    if not entry or not entry.get("success") or "data" not in entry:
        log.info(
            "appdetails returned no usable data for %s (rate-limited / not provisioned)", appid
        )
        return None
    return entry["data"]


def fetch_landing_signal(client: httpx.Client) -> tuple[str, str] | None:
    """Fetch the hardware page; return (digest, content) over purchase/reservation lines.

    ``content`` is the normalized text we hashed — the actual matched lines — so the caller can
    hand real text to the LLM rather than an opaque hash. Returns None on failure / no matches.
    The digest changes when reservation/CTA content appears or changes, while ignoring most of
    the volatile store chrome.
    """
    try:
        resp = client.get(STORE_URL)
        resp.raise_for_status()
        html = resp.text
    except httpx.HTTPError as e:
        log.warning("landing page fetch failed: %s", e)
        return None

    # Split into coarse lines and keep only those mentioning a signal token (case-insensitive).
    # Strip long digit/hash runs (timestamps, ids) so unrelated churn doesn't move the digest.
    lowered_lines = [
        ln for ln in re.split(r"[\n,]", html) if any(t in ln.lower() for t in _SIGNAL_TOKENS)
    ]
    if not lowered_lines:
        return None
    normalized = "\n".join(sorted(re.sub(r"\d{4,}", "#", ln.strip()) for ln in lowered_lines))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest, normalized


def fetch_news(rss_url: str) -> list[dict] | None:
    """Return a list of {id, title, link} news items, or None on failure."""
    try:
        feed = feedparser.parse(rss_url)
    except Exception as e:  # feedparser is lenient but be defensive
        log.warning("news RSS parse failed: %s", e)
        return None
    if getattr(feed, "bozo", False) and not feed.entries:
        log.warning("news RSS unparsable: %s", getattr(feed, "bozo_exception", "?"))
        return None
    items = []
    for e in feed.entries:
        items.append(
            {
                "id": e.get("id") or e.get("link", ""),
                "title": e.get("title", ""),
                "link": e.get("link", ""),
            }
        )
    return items
