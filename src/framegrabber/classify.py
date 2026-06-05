"""LLM triage of the *noisy* secondary signals via the local `claude` CLI (Haiku).

The primary appdetails trigger never comes through here — this only judges landing-page diffs
and news headlines, where a human-ish "is this actually an availability/reservation event?"
call cuts false alerts. Output is forced to a small JSON verdict.

Failure policy is set by the caller via ``fail_open``: for the landing-page signal we fail
toward alerting (better a rare "check manually" ping than a miss); for news we fail closed
(headlines are low-stakes and high-volume).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _resolve_claude(bin_name: str) -> str | None:
    """Find the claude binary without relying on PATH (systemd units have a minimal one).

    Honors an absolute/explicit path, then PATH, then the usual install locations.
    """
    if "/" in bin_name:
        return bin_name if Path(bin_name).exists() else None
    found = shutil.which(bin_name)
    if found:
        return found
    for cand in (Path.home() / ".local/bin" / bin_name, Path("/usr/local/bin") / bin_name):
        if cand.exists():
            return str(cand)
    return None


_PROMPT = """\
You are a release-watcher for Valve's "Steam Frame" VR headset. Decide whether the input below \
is evidence that the Steam Frame just became orderable, reservable, or wait-listable by \
consumers — as opposed to specs, rumors, opinion, pricing speculation, or unrelated content.

Respond with ONLY a JSON object, no prose, in this exact shape:
{"availability_event": true|false, "kind": "release"|"reservation"|"waitlist"|"regional"|"none", \
"confidence": 0.0-1.0, "summary": "one short sentence for a phone alert"}

A reservation/waitlist setup counts as an availability_event (kind "reservation" or "waitlist"). \
Mark availability_event false for anything that is merely an announcement of future plans, a \
specs leak, or commentary.

INPUT:
"""


def _run_claude(
    model: str, payload: str, claude_bin: str = "claude", timeout: int = 60
) -> dict | None:
    exe = _resolve_claude(claude_bin)
    if exe is None:
        log.warning("claude binary %r not found (PATH or ~/.local/bin)", claude_bin)
        return None
    try:
        proc = subprocess.run(
            [exe, "-p", "--model", model, "--output-format", "json"],
            input=_PROMPT + payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("claude CLI invocation failed: %s", e)
        return None
    if proc.returncode != 0:
        log.warning("claude CLI exited %s: %s", proc.returncode, proc.stderr[:500])
        return None

    # `--output-format json` wraps the result; the model's text is in the "result" field.
    text = proc.stdout.strip()
    try:
        wrapper = json.loads(text)
        inner = wrapper.get("result", text) if isinstance(wrapper, dict) else text
    except ValueError:
        inner = text
    return _extract_json(inner)


def _extract_json(text: str) -> dict | None:
    """Pull the first {...} object out of a possibly-chatty response."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        log.warning("no JSON object in claude output: %s", text[:200])
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError as e:
        log.warning("could not parse claude JSON: %s (%s)", e, text[:200])
        return None


def classify(model: str, payload: str, *, fail_open: bool, claude_bin: str = "claude") -> dict:
    """Return a verdict dict. On LLM failure, synthesize one per ``fail_open``."""
    verdict = _run_claude(model, payload, claude_bin)
    if verdict is None:
        if fail_open:
            return {
                "availability_event": True,
                "kind": "none",
                "confidence": 0.0,
                "summary": "Steam Frame page changed but classification failed — check manually.",
                "_llm_failed": True,
            }
        return {
            "availability_event": False,
            "kind": "none",
            "confidence": 0.0,
            "summary": "",
            "_llm_failed": True,
        }
    return verdict
