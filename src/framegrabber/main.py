"""One-shot run entry point, driven by a systemd timer.

Exit code is always 0 on handled errors: the timer is the retry mechanism, and a crashing
oneshot just clutters the journal. Real availability detection (primary signal) is rule-based
and never depends on the LLM or the network being fully healthy.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import config, fetch, notify, state, triggers
from .classify import classify

log = logging.getLogger("framegrabber")


def _push(cfg: config.Config, dry_run: bool, **kw) -> None:
    if dry_run:
        log.info(
            "[dry-run] would push: title=%r priority=%s body=%r",
            kw.get("title"),
            kw.get("priority"),
            kw.get("body"),
        )
        return
    notify.notify(cfg, **kw)


def run_primary(cfg: config.Config, st: state.State, client, dry_run: bool) -> bool:
    """Poll appdetails for each watched app. Returns True if a fresh trigger fired this run."""
    fired = False
    for appid in cfg.appids:
        data = fetch.fetch_appdetails(client, appid, cfg.cc)
        if data is None:
            continue  # transient — keep prior fingerprint untouched
        name = data.get("name", str(appid))
        new_fp = triggers.fingerprint(data)
        old_fp = st.appdetails.get(str(appid))
        trig = triggers.evaluate(appid, name, old_fp, new_fp)
        st.appdetails[str(appid)] = new_fp  # only ever updated on a good response
        if trig:
            fired = True
            summary = f"{trig.name} is now available: " + "; ".join(trig.reasons)
            log.warning("PRIMARY TRIGGER: %s", summary)
            state.set_alerted(cfg.alerted_flag, summary)
            _push(
                cfg,
                dry_run,
                title=f"{trig.name} available!",
                body=summary + f"\n\nGo: {config.STORE_URL}",
                priority=5,
                tags="rotating_light",
            )
    return fired


def run_landing(cfg: config.Config, st: state.State, client, dry_run: bool) -> None:
    result = fetch.fetch_landing_signal(client)
    if result is None:
        return
    digest, content = result
    if digest == st.landing_hash:
        return
    # Cold start: record the baseline silently. Only changes *after* we start watching matter.
    if st.landing_hash is None:
        log.info("landing-page baseline recorded: %s", digest[:12])
        st.landing_hash = digest
        return
    log.info("landing-page signal changed: %s", digest[:12])
    if digest in st.classified_landing:
        st.landing_hash = digest
        return
    payload = (
        "The reservation/purchase-related text on the Steam Frame store page changed to:\n"
        + content[:4000]
    )
    verdict = classify(cfg.claude_model, payload, fail_open=True, claude_bin=cfg.claude_bin)
    st.landing_hash = digest
    st.classified_landing.append(digest)
    if verdict.get("availability_event"):
        kind = verdict.get("kind", "none")
        log.warning("LANDING TRIGGER (%s): %s", kind, verdict.get("summary"))
        _push(
            cfg,
            dry_run,
            title=f"Steam Frame: possible {kind}",
            body=verdict.get("summary", "Store page changed — check manually."),
            priority=5 if not verdict.get("_llm_failed") else 4,
            tags="eyes",
        )


def run_news(cfg: config.Config, st: state.State, dry_run: bool) -> None:
    items = fetch.fetch_news(cfg.news_rss_url)
    if items is None:
        return
    # Cold start: seed existing headlines as already-seen so we don't classify history (and
    # risk false-firing on an old reservation-rumor article). Only new articles get judged.
    if not st.seen_news:
        st.seen_news = [it["id"] for it in items if it["id"]]
        log.info("news baseline seeded with %d existing item(s)", len(st.seen_news))
        return
    seen = set(st.seen_news)
    fresh = [it for it in items if it["id"] and it["id"] not in seen]
    if not fresh:
        return
    log.info("%d new news item(s)", len(fresh))
    payload = "\n".join(f"- {it['title']} ({it['link']})" for it in fresh)
    verdict = classify(cfg.claude_model, payload, fail_open=False, claude_bin=cfg.claude_bin)
    for it in fresh:
        st.seen_news.append(it["id"])
    if verdict.get("availability_event"):
        log.warning("NEWS TRIGGER: %s", verdict.get("summary"))
        link = fresh[0]["link"]
        _push(
            cfg,
            dry_run,
            title="Steam Frame in the news",
            body=verdict.get("summary", "") + f"\n\n{link}",
            priority=4,
            url=link or config.STORE_URL,
            tags="newspaper",
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="framegrabber", description=__doc__)
    p.add_argument("--once", action="store_true", help="run a single poll cycle (default)")
    p.add_argument(
        "--dry-run", action="store_true", help="detect and log, but never push or write state"
    )
    p.add_argument("--force-alert", action="store_true", help="send a test push and exit")
    p.add_argument(
        "--ack", action="store_true", help="clear the ALERTED flag so urgent reminders stop"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config.load()

    if args.ack:
        cleared = state.clear_alerted(cfg.alerted_flag)
        log.info("ALERTED flag %s", "cleared" if cleared else "was not set")
        return 0

    if args.force_alert:
        ok = notify.notify(
            cfg,
            title="framegrabber test",
            body="If you see this on your phone, push alerting works.",
            priority=4,
            tags="white_check_mark",
        )
        return 0 if ok else 1

    st = state.State.load(cfg.state_file)
    try:
        with fetch.make_client() as client:
            fired = run_primary(cfg, st, client, args.dry_run)
            # Keep nagging until acknowledged, so one dropped push can't lose first-in-line.
            if not fired and state.is_alerted(cfg.alerted_flag):
                summary = (
                    cfg.alerted_flag.read_text()
                    if cfg.alerted_flag.exists()
                    else "Steam Frame is available."
                )
                log.info("re-sending urgent reminder (ALERTED flag set; run --ack to stop)")
                _push(
                    cfg,
                    args.dry_run,
                    title="Steam Frame still available!",
                    body=summary + "\n\n(reminder — run `framegrabber --ack` to stop)",
                    priority=5,
                    tags="rotating_light",
                )
            if not cfg.primary_only:
                run_landing(cfg, st, client, args.dry_run)
                run_news(cfg, st, args.dry_run)
    except Exception:  # never crash the timer
        log.exception("unhandled error during run")

    if not args.dry_run:
        st.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
