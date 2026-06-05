"""Push notifications. Default backend is ntfy; the single notify() seam makes Pushover etc. a
drop-in replacement.
"""

from __future__ import annotations

import logging

import httpx

from .config import STORE_URL, Config

log = logging.getLogger(__name__)


def notify(
    cfg: Config,
    title: str,
    body: str,
    *,
    priority: int = 3,
    url: str = STORE_URL,
    tags: str = "steam",
) -> bool:
    """Send one push. Returns True on success. Never raises (alerting must not crash a run)."""
    if not cfg.ntfy_topic:
        log.error("NTFY_TOPIC is not set; cannot push: %s", title)
        return False
    # HTTP headers must be ASCII; the body is UTF-8 so emoji belong there, and the icon comes
    # from Tags. Drop any non-ASCII from the title header so a stray emoji can't crash a run.
    safe_title = title.encode("ascii", "ignore").decode("ascii").strip() or "framegrabber"
    try:
        resp = httpx.post(
            f"{cfg.ntfy_server.rstrip('/')}/{cfg.ntfy_topic}",
            content=body.encode("utf-8"),
            headers={
                "Title": safe_title,
                "Priority": str(priority),
                "Tags": tags,
                "Click": url,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return True
    except (httpx.HTTPError, UnicodeError, ValueError) as e:
        log.error("ntfy push failed: %s", e)
        return False
