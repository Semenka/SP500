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

## Personal portfolio

`portfolio.json` (repo root) is your tracked watchlist — edit `holdings` to change it; the agent picks it up next run. Each holding is valued every run via the same DCF/P-B engine:

- S&P constituents already in the workbook are flagged in place.
- Non-S&P names (foreign/ADR/ETF) are fetched separately. Foreign symbols need a yfinance suffix (`.HK` Hong Kong, `.L` London). Discount % is currency-invariant, so cross-currency holdings compare correctly.
- ETFs and negative/again-FCF names show price only (no IV).

Outputs: `docs/data/portfolio.json` (the holdings subset), a "My Portfolio" table at the top of the dashboard (slider-recomputed), and the lead block of every Telegram digest (per-holding discount + change vs last run). The portfolio names are also appended to `history.csv` for the time-series chart.

### Per-company intrinsic-value models

`portfolio_models.json` holds a conservative-Buffett DCF model per holding. Portfolio IV comes from these models (not the generic S&P overlay engine), and upside = (IV/share − live price) / price is recomputed every run against the live price. Two model types:

- **`fcf_dcf`** — 15-yr mid-year per-share owner-earnings DCF, 3-phase growth (per-share growth already folds in the buyback yield). Reproduces the user's Google-Sheet models within <0.5%.
- **`earnings_multiple`** — normalized EPS × justified P/E (+ net cash/share). Used for banks (USB, SYF) and high-leverage names (AAL) where a 15-yr FCF DCF with full net-debt subtraction is the wrong tool.

All inputs are per-share in the listing currency, so no runtime FX is needed. Model IVs are fixed (a fundamental value doesn't move with the macro sliders) and recomputed only when you edit the spec. Regenerate with `python3 scripts/build_portfolio_models.py`. Each model carries a `confidence` flag and a `note` with the latest-earnings detail.

#### Methodology v2 (world-class, data-driven)

`build_portfolio_models.py` derives every input from data rather than hand-set guesses:

1. **Growth** — conservative MIN of each name's multi-year history and a macro-aware sector ceiling, nudged by the latest quarter's momentum/guidance; 3-phase decay to a conservative terminal.
2. **Discount rate** — from the live US Treasury curve, tenor-matched to the 15-yr horizon: `risk_free(~15y, interpolated 10y↔30y) + clamp(beta,0.7,1.6)×ERP(5%)`, floored at a 9% Buffett hurdle, plus a sector risk premium (China ADR +1.5, energy +0.5, airline +1.5, recent-IPO +2.0). Recomputed when the curve is refreshed.
3. **FCF over net income** — owner earnings = median historical FCF margin × latest revenue (smooths capex spikes / cyclical revenue). Exceptions: banks (no FCF) → normalized EPS × justified P/E; Wise (float-inflated OCF) → net income; DouYu → net-cash floor.
4. **Buyback / issuance** — base = historical diluted-share-count CAGR, overridden by authorizations announced at the latest earnings (NVDA $118B, SYF $6.5B, Xiaomi HK$20B new, OXY paused, AAL none/dilutive, CRCL post-IPO dilution); folded into per-share growth.
5. **Latest earnings** — every model's `note` carries the most recent quarter (rev/EPS YoY, guidance, buyback) from primary sources, and those facts set the growth/buyback/terminal.
6. **Conservative** — hypergrowth capped, 9% discount floor, terminal ≤3% (0% for depleting energy), low-confidence flags, and the original Google-Sheet IV kept as a `sheet_iv_per_share` cross-reference.

The `portfolio_models.json` header records the live `macro` block (yield curve, RF15, ERP, floor) used. The dashboard shows each holding's discount rate, IV/share, sheet-IV reference, and upside; hover a row for the earnings note.

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
