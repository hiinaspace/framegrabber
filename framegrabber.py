#!/usr/bin/env python3
"""framegrabber — watch Valve's Steam Frame store + news and push an ntfy alert on change.

Self-contained and stdlib-only, so it runs on a stock `python:3.12-slim` image with no
dependencies and no custom build — the whole program ships as a k8s ConfigMap and runs on a
CronJob. Posture is "a fancier RSS feed": poll every ~15 min, one push per new event, no
repeats/nagging. The likely terminal event is Valve announcing a firm release date/price (which
the appdetails API reflects structurally) with enough lead time to read the details yourself.

Signals:
  1. Steam store appdetails API (rule-based) — fires when the Steam Frame stops being
     "coming soon", gets a price, gets purchasable packages, or gets a concrete release date.
  2. Google News RSS (keyword-filtered) — new "Steam Frame" headlines matching availability
     keywords. No LLM; just substring matching.

State (seen news ids + last appdetails fingerprint) is a JSON file, intended to live on a
persistent volume so cron runs don't re-alert.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("framegrabber")

# --- constants (Steam Frame = 4165890, verified live; type=hardware) ---------------------
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STORE_URL = "https://store.steampowered.com/hardware/steamframe"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/124.0"
DEFAULT_RSS = (
    "https://news.google.com/rss/search?"
    "q=%22Steam+Frame%22+(release+OR+reservation+OR+pre-order+OR+available+OR+order)"
    "&hl=en-US&gl=US&ceid=US:en"
)
DEFAULT_KEYWORDS = (
    "release,launch,order,pre-order,preorder,reserve,reservation,"
    "available,availability,date,price,ship,buy,in stock"
)


@dataclass
class Config:
    ntfy_server: str = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    ntfy_topic: str = os.environ.get("NTFY_TOPIC", "")
    ntfy_token: str = os.environ.get("NTFY_TOKEN", "")
    cc: str = os.environ.get("FRAMEGRABBER_CC", "us")
    rss_url: str = os.environ.get("FRAMEGRABBER_NEWS_RSS", DEFAULT_RSS)
    primary_only: bool = os.environ.get("FRAMEGRABBER_PRIMARY_ONLY", "") == "1"
    state_file: Path = field(
        default_factory=lambda: Path(os.environ.get("FRAMEGRABBER_STATE", "/data/state.json"))
    )
    appids: list[int] = field(
        default_factory=lambda: [
            int(x)
            for x in os.environ.get("FRAMEGRABBER_APPIDS", "4165890").replace(",", " ").split()
        ]
    )
    keywords: list[str] = field(
        default_factory=lambda: [
            k.strip().lower()
            for k in os.environ.get("FRAMEGRABBER_NEWS_KEYWORDS", DEFAULT_KEYWORDS).split(",")
            if k.strip()
        ]
    )


# --- http (stdlib urllib; returns None on any failure so a run degrades gracefully) ------


def http_get(
    url: str, headers: dict[str, str] | None = None, timeout: float = 15.0
) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("GET %s failed: %s", url, e)
        return None


# --- signal 1: Steam appdetails (rule-based) ---------------------------------------------


def fetch_appdetails(appid: int, cc: str) -> dict | None:
    """Return the appdetails ``data`` dict, or None on failure / transient success:false."""
    raw = http_get(f"{APPDETAILS_URL}?appids={appid}&cc={cc}&l=english")
    if raw is None:
        return None
    try:
        entry = json.loads(raw).get(str(appid))
    except ValueError as e:
        log.warning("appdetails %s: bad JSON: %s", appid, e)
        return None
    if not entry or not entry.get("success") or "data" not in entry:
        log.info("appdetails %s: no usable data (rate-limited / not provisioned)", appid)
        return None
    return entry["data"]


def fingerprint(data: dict) -> dict:
    """Reduce appdetails to the fields that indicate orderability / a firm announcement."""
    release = data.get("release_date") or {}
    return {
        "coming_soon": bool(release.get("coming_soon", True)),
        "date": release.get("date", ""),
        "has_price": data.get("price_overview") is not None,
        "package_count": len(data.get("packages") or []),
    }


def appdetails_reasons(old: dict | None, new: dict) -> list[str]:
    """Human reasons the product looks newly-available vs ``old``. Empty = no event.

    With no prior fingerprint (first run) we never fire — just record the baseline.
    """
    if old is None:
        return []
    reasons: list[str] = []
    if old.get("coming_soon", True) and not new["coming_soon"]:
        reasons.append("no longer 'coming soon'")
    if not old.get("has_price", False) and new["has_price"]:
        reasons.append("a price appeared")
    old_pkgs = old.get("package_count", 0)
    if new["package_count"] > old_pkgs:
        reasons.append(f"purchasable packages appeared ({old_pkgs} -> {new['package_count']})")
    old_date = (old.get("date") or "").strip().lower()
    new_date = (new.get("date") or "").strip()
    if new_date and new_date.lower() != old_date and new_date.lower() != "coming soon":
        reasons.append(f"release date set to '{new_date}'")
    return reasons


# --- signal 2: news RSS (keyword-filtered) -----------------------------------------------


def parse_rss(raw: bytes) -> list[dict]:
    """Parse RSS 2.0 into [{id, title, link, summary}]. Tolerant of malformed feeds."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        log.warning("RSS parse error: %s", e)
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip() or link
        summary = (item.findtext("description") or "").strip()
        if guid:
            items.append({"id": guid, "title": title, "link": link, "summary": summary})
    return items


def news_matches(title: str, summary: str, keywords: list[str]) -> bool:
    """A relevant availability item: about the Steam Frame AND mentions a keyword."""
    if "steam frame" not in title.lower():
        return False
    hay = f"{title} {summary}".lower()
    return any(k in hay for k in keywords)


def fetch_news(url: str) -> list[dict] | None:
    raw = http_get(url)
    return None if raw is None else parse_rss(raw)


# --- state -------------------------------------------------------------------------------


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"appdetails": {}, "seen_news": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        log.warning("state %s unreadable, starting fresh: %s", path, e)
        return {"appdetails": {}, "seen_news": []}
    data.setdefault("appdetails", {})
    data.setdefault("seen_news", [])
    return data


def save_state(path: Path, state: dict) -> None:
    state["seen_news"] = state["seen_news"][-500:]  # cap growth
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


# --- ntfy push ---------------------------------------------------------------------------


def notify(
    cfg: Config,
    title: str,
    body: str,
    *,
    priority: int = 3,
    tags: str = "steam",
    click: str = STORE_URL,
) -> bool:
    if not cfg.ntfy_topic:
        log.error("NTFY_TOPIC not set; cannot push: %s", title)
        return False
    headers = {
        "Title": title.encode("ascii", "ignore").decode().strip() or "framegrabber",
        "Priority": str(priority),
        "Tags": tags,
        "Click": click,
    }
    if cfg.ntfy_token:
        headers["Authorization"] = f"Bearer {cfg.ntfy_token}"
    req = urllib.request.Request(
        f"{cfg.ntfy_server.rstrip('/')}/{cfg.ntfy_topic}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0):  # noqa: S310
            return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.error("ntfy push failed: %s", e)
        return False


def _push(cfg: Config, dry_run: bool, **kw) -> None:
    if dry_run:
        log.info("[dry-run] would push: %s", {k: kw.get(k) for k in ("title", "priority")})
        return
    notify(cfg, **kw)


# --- orchestration -----------------------------------------------------------------------


def run_appdetails(cfg: Config, state: dict, dry_run: bool) -> None:
    for appid in cfg.appids:
        data = fetch_appdetails(appid, cfg.cc)
        if data is None:
            continue  # transient — keep prior fingerprint
        name = data.get("name", str(appid))
        new_fp = fingerprint(data)
        reasons = appdetails_reasons(state["appdetails"].get(str(appid)), new_fp)
        state["appdetails"][str(appid)] = new_fp  # only updated on a good response
        if reasons:
            summary = f"{name}: " + "; ".join(reasons)
            log.warning("APPDETAILS EVENT: %s", summary)
            _push(
                cfg,
                dry_run,
                title=f"{name} — store changed",
                body=summary + f"\n\n{STORE_URL}",
                priority=4,
                tags="rotating_light",
            )


def run_news(cfg: Config, state: dict, dry_run: bool) -> None:
    items = fetch_news(cfg.rss_url)
    if items is None:
        return
    # Cold start: record existing ids as seen so we only alert on future articles.
    if not state["seen_news"]:
        state["seen_news"] = [it["id"] for it in items]
        log.info("news baseline seeded with %d item(s)", len(state["seen_news"]))
        return
    seen = set(state["seen_news"])
    for it in items:
        if it["id"] in seen:
            continue
        state["seen_news"].append(it["id"])
        if news_matches(it["title"], it["summary"], cfg.keywords):
            log.warning("NEWS EVENT: %s", it["title"])
            _push(
                cfg,
                dry_run,
                title="Steam Frame in the news",
                body=f"{it['title']}\n\n{it['link']}",
                priority=3,
                tags="newspaper",
                click=it["link"] or STORE_URL,
            )


def run(cfg: Config, dry_run: bool = False) -> None:
    state = load_state(cfg.state_file)
    try:
        run_appdetails(cfg, state, dry_run)
        if not cfg.primary_only:
            run_news(cfg, state, dry_run)
    finally:
        if not dry_run:
            save_state(cfg.state_file, state)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="framegrabber", description=__doc__)
    p.add_argument("--once", action="store_true", help="run one poll cycle (default)")
    p.add_argument("--dry-run", action="store_true", help="detect + log, never push or save state")
    p.add_argument("--force-alert", action="store_true", help="send a test push and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config()
    if args.force_alert:
        ok = notify(
            cfg,
            "framegrabber test",
            "If you see this, push alerting works.",
            priority=3,
            tags="white_check_mark",
        )
        return 0 if ok else 1
    run(cfg, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
