"""Persistent run-to-run state: appdetails fingerprints, landing hash, seen news, alert dedupe.

State is a single JSON file written atomically. The ``ALERTED`` sentinel file (separate, so it
survives state rewrites and is easy to inspect/delete by hand) marks that a real availability
event has fired; while it exists, every run keeps re-pushing the urgent alert so a single
dropped notification can't cost first-in-line.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class State:
    path: Path
    # appid (str) -> fingerprint dict from triggers.fingerprint()
    appdetails: dict[str, dict] = field(default_factory=dict)
    landing_hash: str | None = None
    # landing hashes already sent to the LLM, so identical diffs don't re-call it
    classified_landing: list[str] = field(default_factory=list)
    seen_news: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            log.warning("could not read state %s, starting fresh: %s", path, e)
            return cls(path=path)
        return cls(
            path=path,
            appdetails=raw.get("appdetails", {}),
            landing_hash=raw.get("landing_hash"),
            classified_landing=raw.get("classified_landing", []),
            seen_news=raw.get("seen_news", []),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Cap unbounded lists so the file can't grow forever.
        self.classified_landing = self.classified_landing[-50:]
        self.seen_news = self.seen_news[-500:]
        data = {
            "appdetails": self.appdetails,
            "landing_hash": self.landing_hash,
            "classified_landing": self.classified_landing,
            "seen_news": self.seen_news,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.path)


def is_alerted(flag: Path) -> bool:
    return flag.exists()


def set_alerted(flag: Path, summary: str) -> None:
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(summary)


def clear_alerted(flag: Path) -> bool:
    if flag.exists():
        flag.unlink()
        return True
    return False
