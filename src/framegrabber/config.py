"""Static + env-driven configuration.

Everything that might reasonably change (secrets, region, which app ids to watch) is
overridable via environment variables so the systemd unit can inject them from a 600-perm
EnvironmentFile without touching the repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- Steam identifiers (verified live against store.steampowered.com) -------------------
# 4165890 = Steam Frame, 4165910 = Steam Machine. Both type="hardware", "coming soon".
STEAM_FRAME_APPID = 4165890
STEAM_MACHINE_APPID = 4165910

STORE_URL = "https://store.steampowered.com/hardware/steamframe"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

# A real-looking desktop UA; Steam serves the age-gated hardware page only with one set.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/124.0"

# Default Google News RSS query. %22..%22 = exact phrase "Steam Frame".
NEWS_RSS_URL = (
    "https://news.google.com/rss/search?"
    "q=%22Steam+Frame%22+(release+OR+reservation+OR+pre-order+OR+available+OR+order)"
    "&hl=en-US&gl=US&ceid=US:en"
)

CLAUDE_MODEL = "claude-haiku-4-5-20251001"


def _state_dir() -> Path:
    # systemd sets STATE_DIRECTORY for StateDirectory= units; fall back to XDG.
    env = os.environ.get("STATE_DIRECTORY")
    if env:
        return Path(env.split(":")[0])
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "framegrabber"


def _appids() -> list[int]:
    raw = os.environ.get("FRAMEGRABBER_APPIDS")
    if raw:
        return [int(x) for x in raw.replace(",", " ").split()]
    return [STEAM_FRAME_APPID]


@dataclass(frozen=True)
class Config:
    appids: list[int] = field(default_factory=_appids)
    cc: str = os.environ.get("FRAMEGRABBER_CC", "us")
    state_dir: Path = field(default_factory=_state_dir)

    # ntfy push. Topic is the only real secret (anyone who knows it can read/post).
    ntfy_server: str = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    ntfy_topic: str = os.environ.get("NTFY_TOPIC", "")

    news_rss_url: str = os.environ.get("FRAMEGRABBER_NEWS_RSS", NEWS_RSS_URL)
    claude_model: str = os.environ.get("FRAMEGRABBER_CLAUDE_MODEL", CLAUDE_MODEL)

    # When true, the news + landing-page LLM triage steps are skipped (primary signal only).
    primary_only: bool = os.environ.get("FRAMEGRABBER_PRIMARY_ONLY", "") == "1"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def alerted_flag(self) -> Path:
        return self.state_dir / "ALERTED"


def load() -> Config:
    return Config()
