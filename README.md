# S&P 500 Portfolio Dashboard

A Buffett-style intrinsic value dashboard for all 494 S&P 500 companies, with a separate column block tracking Berkshire Hathaway's positions from each quarterly 13F filing. Live market data is pulled from Yahoo Finance; intrinsic value is computed via a 15-year owner-earnings DCF with a sector-aware geopolitical risk overlay.

The project lives as a single Excel workbook (`Portfolio.xlsx`) with a Python updater (`update_sp500_dashboard.py`) that refreshes prices, fundamentals, and IV columns on demand. The `.numbers` file is the macOS Numbers source; `.xlsx` is the format the scripts actually read and write.

## Live dashboard

An interactive web view of IV vs market-price discount is regenerated on every scheduled run and served from GitHub Pages out of the `docs/` folder. The page includes a scatter of discount vs market cap, a sortable/filterable table, a time-series chart, and sliders that let you re-tweak the base geopolitical assumptions (discount premium, growth haircut, MOS) in your browser and see every IV recompute live — no server required.

To enable Pages: repo Settings → Pages → Source = `main` / `docs`.

## Local update agent

`scripts/run_update_agent.py` is the entry point invoked twice per US trading day (09:00 ET and 15:30 ET) by **OpenClaw**, the local update agent. It runs the existing yfinance refresh, exports JSON + history for the web dashboard, re-renders `docs/index.html`, and pushes the result to GitHub. See [scripts/openclaw_agent.md](scripts/openclaw_agent.md) for the runbook and CLI flags. The agent is market-day-aware (hardcoded NYSE holiday table through 2030) so OpenClaw can fire weekdays naively.

## What's in the workbook

`Portfolio.xlsx` has 13 sheets. The two that matter for the dashboard:

- **Dashboard** — the main 494-row table. Columns A–N are market data refreshed by the script; columns O–S are static BRK 13F data updated by hand each quarter.
- **S&P 500** — the master ticker list.

The other sheets (`Comparison`, `Portfolio DCF`, `DPZ Summary`, `DPZ DCF`, `Per-Share Model`, `WACC`, `Peer Comparison`, `Все компании`, `Venture`, `COVID`) are independent analyses retained from earlier work.

### Dashboard column layout

| Col | Field | Source |
|---|---|---|
| A | # | Auto |
| B | Ticker | Manual |
| C | Company | Manual |
| D | Industry | Manual |
| E | Share Price ($) | yfinance |
| F | 1Y Price Change (%) | yfinance |
| G | Market Cap ($B) | yfinance |
| H | Revenue Growth (%) | yfinance |
| I | Gross Margin (%) | yfinance |
| J | P/E | yfinance |
| K | FCF ($M) | yfinance |
| L | Intrinsic Value ($B) | DCF (this script) |
| M | IV vs MCap Discount (%) | computed |
| N | P/FCF | computed |
| O | BRK? | manual — `YES`/`SOLD`/`—` |
| P | BRK Pos ($B) | manual — Q1 2026 13F |
| Q | BRK Net Action | manual — `Hold`/`Buy`/`Sell`/`Sold` |
| R | BRK Avg Cost ($) | manual — approximate cost basis |
| S | Activity Detail | manual — quarter-by-quarter narrative |

## Intrinsic value methodology

A conservative 15-year owner-earnings DCF with:

1. **Three-phase growth decay.** Years 1–5 use the starting growth rate (capped at –5%/+12%). Years 6–10 linearly fade from start to terminal. Years 11–15 use terminal growth (default 2.5%).
2. **Liquid assets added.** Cash and short-term investments from yfinance `totalCash` are added to the DCF sum so high-cash companies (BRK itself, mega-cap tech) aren't undervalued.
3. **Margin of safety applied last.** Enterprise value is multiplied by `(1 - MOS)`. Base MOS is 25%, modified by the geopolitical overlay.
4. **Geopolitical overlay (v5, May 22 2026).** Sector-specific adjustments to discount rate, growth rate, and margin of safety reflect the current macro picture (see below).

The math is in `buffett_intrinsic_value()` inside `update_sp500_dashboard.py`. Base inputs: discount rate 10%, terminal growth 2.5%, projection 15 years.

## Geopolitical risk overlay v5 (May 22 2026)

The overlay tightens or loosens the DCF inputs depending on industry exposure. Three knobs per sector: discount rate premium, growth haircut, margin-of-safety add. Base layers stack with sector-specific layers.

**Base layer:**
- Discount premium: +1.3%
- Growth haircut: –0.5%
- MOS add: +4.0%

**Macro context driving v5:**
- Iran war ongoing since Feb 28, 2026 (Op Epic Fury killed Supreme Leader Khamenei). Apr 8 ceasefire holding but fragile; Trump called off planned May 18 strike; Iran reviewing 14-point framework via Pakistan.
- Brent $107–112, WTI $102–108. Oil up ~45% since war began.
- Hormuz mostly closed by Iran; US blockading Iranian ports.
- Supreme Court (Feb 2026) ruled IEEPA tariff authority unconstitutional → ~10% effective tariffs (down from 11%) but other statutes preserve broad authority.
- ISM prices index 84.6 — highest since April 2022 on tariff + energy double pressure.
- Fed held at 3.50–3.75% (Apr 28-29, 8-4 vote with 4 dissents — dovish split forming). Next FOMC June 16–17. Markets price 1–2 cuts H2-26.
- Goldman baseline: no recession (2.3–2.6% GDP), but stagflation re-emerging. WSJ recession-probability survey ~28%.
- S&P 500 new ATH 7,501 May 15 close, +0.8%. Forward P/E ~21x. Q1 blended earnings +15.1% (beat).

**Sector effective adjustments after overlay (compared to v4):**

| Sector | Discount premium | Growth | MOS | Net IV impact |
|---|---|---|---|---|
| Energy / Oil & Gas | +0.1% | +2.5% | +2.0% | Strong tailwind (Brent $112) |
| Aerospace & Defense | +0.1% | +2.0% | +3.0% | Strong tailwind (war ongoing) |
| Insurance | +0.3% | +0.5% | +2.0% | Tailwind (CB lead Hormuz underwriter) |
| Technology | +2.8% | –2.0% | +8.0% | Mild penalty (rich at 21.2x fwd) |
| Banks | +3.3% | –2.0% | +8.0% | Penalty (BRK selling BAC) |
| Consumer Discretionary | +3.8% | –3.0% | +8.5% | Severe (oil + tariffs) |
| Transportation | +4.1% | –3.3% | +10.0% | Severe ($108 WTI fuel) |
| Automotive | +4.3% | –4.0% | +11.5% | Severe (oil + tariffs + EV) |
| Healthcare | +1.6% | 0% | +4.0% | Neutral defensive |

Earlier overlay versions (v1–v4) are visible in the git history.

## Berkshire Hathaway position tracking

Columns O–S capture Berkshire's S&P 500 holdings as disclosed in each 13F. The dashboard tracks the subset of BRK's portfolio that consists of S&P 500 components — currently 21 active positions plus 13 confirmed exits.

**Current state — Q1 2026 13F (filed May 15, 2026):**

Greg Abel's first quarter as CEO produced the most aggressive reshape in years: portfolio shrank to 29 positions ($263.1B), down from 42 ($274.2B). Net seller –$8.1B (14th straight quarter). Cash record $397B. First buyback ($234M) since May 2024.

| Rank | Ticker | $B | % | Action | Notes |
|---|---|---|---|---|---|
| 1 | AAPL | 57.93 | 22.0% | Hold | 227.9M sh — Abel paused the trim |
| 2 | AXP | 45.86 | 17.4% | Hold | 151.6M sh unchanged |
| 3 | KO | 30.41 | 11.6% | Hold | 400M sh since 1988 |
| 4 | BAC | 25.10 | 9.5% | Sell | 513M sh — trimmed <1% in Q1 (7th straight cut) |
| 5 | CVX | 17.50 | 6.6% | Sell | **Cut 35%** — Abel sold 46M sh at ~$182.59 (~$8.4B cashed) |
| 6 | OXY | 17.22 | 6.5% | Hold | 264.94M sh unchanged; price +58% YTD on oil shock |
| 7 | GOOGL | 15.60 | 5.9% | Buy | **Tripled** — 17.85M → 54.2M sh |
| 8 | MCO | 11.97 | 4.6% | Hold | "Core four" — untouchable |
| 9 | CB | 11.50 | 4.4% | Hold | Lead underwriter of US Hormuz shipping insurance |
| 10 | KHC | 9.50 | 3.6% | Hold | Jan sale filing reversed in March |

**Q1 2026 exits (16 total, 11 in the S&P 500 subset):** V, MA, AMZN, UNH, DPZ, HEI, LAMR, POOL, ALLE, AON, CHTR + DEO, FWONK, BATRK, LILA, LILAK (non-S&P 500).

**Q1 2026 new positions:** DAL ($2.6B, first airline since COVID), GOOG ($1B Class C), M ($55M).

**Q1 2026 reductions:** CVX –35%, STZ –95%, DVA –5%, BAC <1%, LLYVK, NUE.

**Q1 2026 increases:** GOOGL tripled, NYT tripled, LEN +43%, SIRI +5M sh to 124.8M, plus LEN.B.

## Files

| File | Purpose |
|---|---|
| `Portfolio.xlsx` | Main workbook — the dashboard lives here |
| `Portfolio.numbers` | macOS Numbers source file (not used by scripts) |
| `update_sp500_dashboard.py` | Python updater — refreshes columns E–N and recomputes IV |
| `Update Dashboard.command` | macOS shortcut that runs the Python updater |
| `Update Dashboard.bat` | Windows batch equivalent |
| `setup_schedule.sh` | Optional cron/launchd setup for scheduled refreshes |
| `README.md` | This file |
| `CLAUDE.md` | Guidance for AI assistants working on this repo |

`dashboard_update.log` and `last_update_report.json` are generated each run and `.gitignore`d.

## Running an update

**macOS:**
```bash
./Update\ Dashboard.command
```

**Windows:**
```cmd
Update Dashboard.bat
```

**Manual:**
```bash
python3 update_sp500_dashboard.py
```

The script will:
1. Install `yfinance`, `openpyxl`, `pandas` if missing.
2. Batch-fetch all 494 tickers (40 per batch, with retries + exponential backoff).
3. Compute IV with the v5 geopolitical overlay.
4. Write columns E–N. **Columns O–S (BRK data) are preserved** — never overwritten.
5. Drop tickers with no data, log errors to a separate `Errors` sheet, and save.

Runtime is roughly 3–4 minutes on a residential connection. Daily yfinance quota is comfortable for this size.

## Updating the BRK columns

BRK holdings change quarterly. After each 13F filing (~45 days after quarter end):

1. Read the new 13F on `13f.info`, `whalewisdom.com`, or directly from SEC EDGAR.
2. Update columns O–S manually for each affected row.
3. Update the geopolitical overlay (`SECTOR_GEO_ADJUSTMENTS` and base constants in `update_sp500_dashboard.py`) if macro conditions warrant.
4. Update the `A2` and `A3` header cells with the new version and context.
5. Click **Update Data** to re-run the IV math on the new overlay.

See `CLAUDE.md` for detailed conventions an AI assistant should follow when doing this.

## Caveats

- IV is a *conservative* anchor, not a target price. The 25%+ MOS and decaying growth assumptions deliberately under-estimate fair value.
- The 13F lags reality by ~45 days. Sirius XM, Heico, and Domino's add-ons noted in early Q1 may have been reversed by quarter-end.
- yfinance can return stale or empty data for individual tickers; the script logs errors but doesn't retry indefinitely.
- The geopolitical overlay is a personal model. Sector-keyword matching is fuzzy (`.lower().contains`) — multi-sector companies pick up the first matching adjustment.
- BRK column data is point-in-time as of Q1 2026 (filed May 15). Stock prices move; the `Pos $B` figures will go stale within weeks.

## License

Personal project, no license. The data is sourced from SEC filings and yfinance.
