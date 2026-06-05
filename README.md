# framegrabber

A small watcher that pings your phone the moment Valve's **Steam Frame** becomes orderable —
whether that's a normal store "Buy" page or a reservation/waitlist setup. It polls Valve's
official store API + the hardware landing page + news, on a 2-minute systemd timer, and pushes
an alert via [ntfy](https://ntfy.sh). It only **alerts** — no auto-cart, no botting.

## How it decides

Three signals, in order of reliability:

1. **Steam store API (primary, rule-based — no LLM).** Polls
   `store.steampowered.com/api/appdetails` for the Steam Frame (appid **4165890**). It fires an
   urgent alert the instant the app stops being "coming soon", a price appears, purchasable
   packages appear, or a real release date is set. Verified live: today it's `coming_soon: true`,
   no price, no packages. This signal is conservative and near-zero-false-positive, and never
   depends on Claude or a healthy network.
2. **Landing-page reservation/CTA (LLM-triaged).** Hashes only the reservation/purchase-relevant
   lines of the React store page; on change, asks Haiku "is this an availability/reservation
   event?" and pushes if so. Catch-net for a reservation/waitlist flow the API might not reflect.
3. **News (LLM-triaged).** Google News RSS for "Steam Frame"; new headlines are judged by Haiku
   to filter specs/rumor noise from real "now orderable/reservable" news. Early heads-up.

The LLM is `claude -p --model haiku` shelled out locally (inherits your `~/.claude` auth). If it
fails, the landing-page signal **fails toward alerting** (a low-priority "check manually" ping);
the primary signal never touches it.

A real availability trigger writes a `~/.local/state/framegrabber/ALERTED` flag and **keeps
re-pinging every run until you acknowledge** (`framegrabber --ack`), so one dropped notification
can't cost you first-in-line.

## Setup

```sh
git clone <repo> ~/code/framegrabber && cd ~/code/framegrabber
uv sync                      # create .venv and install

# 1. Pick a long random ntfy topic and subscribe to it in the ntfy phone app.
mkdir -p ~/.config/framegrabber
cp env.example ~/.config/framegrabber/env
chmod 600 ~/.config/framegrabber/env
$EDITOR ~/.config/framegrabber/env     # set NTFY_TOPIC

# 2. Confirm push works end-to-end (should buzz your phone):
set -a; source ~/.config/framegrabber/env; set +a   # bash; fish: see note below
uv run framegrabber --force-alert

# 3. Install the user timer (edit the WorkingDirectory/ExecStart paths in the unit if you
#    cloned somewhere other than ~/code/framegrabber):
mkdir -p ~/.config/systemd/user
cp systemd/framegrabber.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now framegrabber.timer

# 4. Run even when you're not logged in:
loginctl enable-linger "$USER"
```

> fish shell: to load the env for a manual run, use
> `for l in (cat ~/.config/framegrabber/env | grep -v '^#'); set -gx (string split -m1 = $l); end`

## Usage

```sh
uv run framegrabber --once --verbose   # one manual poll (currently: "no change")
uv run framegrabber --force-alert       # send a test push
uv run framegrabber --dry-run           # detect + log, never push or write state
uv run framegrabber --ack               # stop the "still available!" reminders after you've acted
```

Check the timer and logs:

```sh
systemctl --user list-timers | grep framegrabber
journalctl --user -u framegrabber.service -n 50 --no-pager
```

## Config

All via `~/.config/framegrabber/env` (see `env.example`): `NTFY_TOPIC` (required), `NTFY_SERVER`,
`FRAMEGRABBER_CC` (store region, default `us`), `FRAMEGRABBER_APPIDS` (add `4165910` to also watch
the Steam Machine), `FRAMEGRABBER_CLAUDE_MODEL`, `FRAMEGRABBER_PRIMARY_ONLY=1` (API signal only).

## Develop

```sh
uv run pytest -q
uv run ruff format . && uv run ruff check . && uv run ty check
pre-commit install      # ruff format + lint + ty on commit
```

State: `~/.local/state/framegrabber/state.json`. Switching to Pushover/Telegram is a one-function
change in `src/framegrabber/notify.py`.

## But could I use a different AI than claude?

Yeah it's not a complex parsing job, and only runs when things change. Ask your AI of choice to change the code to call some other API.
