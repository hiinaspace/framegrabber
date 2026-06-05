# framegrabber

A small watcher that pushes an [ntfy](https://ntfy.sh) alert when Valve's **Steam Frame** moves
toward release. Posture is deliberately low-key — "a fancier RSS feed" — on the assumption that
Valve announces availability (firm date / reservations / orders) with a week or so of lead time,
not out of nowhere. So it polls every ~15 minutes and sends **one push per new event, no
repeats**; the alert is your cue to go read the details and decide what to do.

It's a single **stdlib-only** Python file (`framegrabber.py`) — no third-party deps, no build —
so it ships as a Kubernetes **ConfigMap** and runs on a stock `python:3.12-slim` image via a
**CronJob**. State lives in a JSON file on a persistent volume so cron runs don't re-alert.

## Signals

1. **Steam store appdetails API** (rule-based, no fuzz): fires when the Steam Frame (appid
   `4165890`) stops being `coming_soon`, gets a price, gets purchasable packages, or gets a
   concrete release date. This structurally captures the most likely "final notification":
   *Valve announced a firm date/price.*
2. **Google News RSS** (keyword-filtered): new "Steam Frame" headlines that also mention an
   availability keyword (release, order, pre-order, reservation, price, date, …). Plain
   substring matching — no LLM.

## Deployment

Runs as a k8s CronJob, defined in the `fleet-infra` repo at `infra/my-cluster/framegrabber/`
(FluxCD-managed). The CronJob mounts `framegrabber.py` from a ConfigMap, reads config from a
ConfigMap + a sops-encrypted Secret (`NTFY_TOKEN`), and persists state to a longhorn PVC. To
change the script, update it there (the ConfigMap is generated from the file) and Flux redeploys.

## Local run / dev

```sh
uv run pytest -q
uv run ruff format . && uv run ruff check . && uv run ty check

# one manual cycle against the live APIs (won't push without a topic):
NTFY_TOPIC=test FRAMEGRABBER_STATE=/tmp/fg.json uv run python framegrabber.py --dry-run -v

# send a test push:
NTFY_SERVER=https://ntfy.vrg.party NTFY_TOPIC=steamframe NTFY_TOKEN=tk_... \
  uv run python framegrabber.py --force-alert
```

## Config

All via environment variables (see `env.example`): `NTFY_SERVER`, `NTFY_TOPIC`, `NTFY_TOKEN`,
`FRAMEGRABBER_STATE`, `FRAMEGRABBER_CC`, `FRAMEGRABBER_APPIDS` (add `4165910` to also watch the
Steam Machine), `FRAMEGRABBER_NEWS_KEYWORDS`, `FRAMEGRABBER_PRIMARY_ONLY`.

Switching ntfy → Pushover/Telegram is a small change in `notify()`.
