# OpenClaw update-agent runbook

This agent refreshes the SP500 dashboard twice on every US trading day.

## Schedule

Two invocations, both in `America/New_York`:

| When        | Time     | Command |
|-------------|----------|---------|
| 30 min pre-open  | 09:00 ET | `python3 scripts/run_update_agent.py --session=pre-open` |
| 30 min pre-close | 15:30 ET | `python3 scripts/run_update_agent.py --session=pre-close` |

OpenClaw can naively fire weekdays at those times — the script itself does
the NYSE trading-day check (hardcoded holiday table in `run_update_agent.py`,
covers 2026–2030) and exits 0 on holidays. No need to track holiday calendars
in OpenClaw. Extend the table when 2030 nears.

### Live OpenClaw cron jobs (registered)

Both jobs run under a dedicated isolated agent `sp500`, full default toolset,
delivery `none` (the script sends its own Telegram digest), and a failure
alert to Telegram `148594943` after 1 consecutive error.

| Job name | id | cron (tz America/New_York) |
|---|---|---|
| `sp500-iv-preopen`  | `2b62e4a6-ed22-4b5c-a037-5785162c9534` | `0 9 * * 1-5` |
| `sp500-iv-preclose` | `70d1fe9e-8c1d-46f5-afcf-2f2c4eb084e2` | `30 15 * * 1-5` |

Manage them:

```
openclaw cron list
openclaw cron run <id>          # fire now (debug) — verified exit 0 end-to-end
openclaw cron runs --id <id>    # run history
openclaw cron disable <id>      # pause
```

The `sp500` agent's workspace is this repo; OpenClaw drops identity/scaffold
files (IDENTITY.md, SOUL.md, etc.) here — they're `.gitignore`d.

## What the agent does on each fire

1. NYSE trading-day check (skip + exit 0 if not a session).
2. Calls `update_sp500_dashboard.update_dashboard()` to refresh `Portfolio.xlsx`
   (preserves BRK columns O–S per `CLAUDE.md`).
3. Writes:
   - `docs/data/latest.json` — per-ticker inputs + IV + discount.
   - `docs/data/assumptions.json` — current base constants + sector overlay.
   - `docs/data/history.csv` — appends one row per ticker (ticker, price,
     iv_b, mcap_b, discount_pct) tagged with the session.
4. Renders `docs/index.html` from `docs/assets/index.html.j2`.
5. `git add docs Portfolio.xlsx ; git commit ; git push`.

## CLI

```
python3 scripts/run_update_agent.py --session=pre-open       # normal cron fire
python3 scripts/run_update_agent.py --session=pre-close      # normal cron fire
python3 scripts/run_update_agent.py --session=manual         # ad-hoc
python3 scripts/run_update_agent.py --force                  # bypass NYSE-day skip
python3 scripts/run_update_agent.py --no-push                # don't commit/push
python3 scripts/run_update_agent.py --no-telegram            # don't send Telegram alert
python3 scripts/run_update_agent.py --no-refresh             # re-render docs only
```

## Valuation models (v6)

- **DCF** (`buffett_intrinsic_value`) for operating companies — 15-yr owner-earnings DCF.
- **Justified-P/B** (`financial_intrinsic_value`) for banks/insurers/REITs/asset managers — FCF-DCF is meaningless for them (banks have no FCF; insurer float inflates it). Routed by `is_financial()`.
- Sector geo-overlay matches the granular yfinance `industry` first, then falls back to the broad GICS `sector` → ~100% coverage.

## Safety gates

- **Parity gate**: before each commit, `scripts/parity_check.js` recomputes every ticker's IV in `dashboard.js` and compares to Python. A drift > 0.1% aborts the push (and pings Telegram). Skips gracefully if `node` is absent.
- **Non-destructive**: tickers that return no data keep their row + manual BRK columns O–S; only market columns E–N show `—`.

## Telegram

Reuses the OpenClaw `default` bot token (read from `~/.openclaw/openclaw.json`); chat id `148594943`. Override with env `SP500_TELEGRAM_TOKEN` / `SP500_TELEGRAM_CHAT_ID`. Each run sends a summary + deep-value signals (names ≥ +`SP500_DISCOUNT_THRESHOLD`, default 30%, and BRK-held undervalued).

## Run-health log

`docs/data/runs.csv` appends one row per run (elapsed, full/partial/no-data, IV & sector coverage). The dashboard header shows IV-coverage and overlay-coverage badges.

## First-time setup

1. `pip3 install -r requirements.txt` from the repo root.
2. Enable GitHub Pages: repo → Settings → Pages → Source: `main` / `docs`.
3. Verify git is configured to push without prompts (SSH key or stored token).
4. Run once locally to confirm: `python3 scripts/run_update_agent.py --session=manual --force --no-push`.
5. Wire the two scheduled invocations into OpenClaw.

## Failure modes

- `pip` install pulled in unexpectedly — agent stalls on first run if deps
  missing. Pre-install once after `git clone`.
- yfinance rate-limited — partial refresh is OK; rows with no data are
  dropped from `Portfolio.xlsx`. Re-fires on the next session.
- git push rejected — investigate manually; the agent prints the error
  but exits non-zero. Local repo state is consistent (commit succeeded).
- GitHub Pages stale — Pages can take 30–60 s to rebuild after push;
  not an agent problem.
