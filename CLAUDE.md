# CLAUDE.md

Guidance for Claude (and other AI assistants) working on this repository.

## What this project is

A single-workbook S&P 500 intrinsic-value dashboard with a Buffett-style 15-year DCF and a sector-aware geopolitical risk overlay. A separate column block tracks Berkshire Hathaway's 13F positions. The owner refreshes BRK columns by hand each quarter; the IV columns are recomputed by `update_sp500_dashboard.py`.

The full architectural overview lives in `README.md`. **Read it first.**

## Files you will actually touch

- `Portfolio.xlsx` — the workbook. The `Dashboard` sheet is the one that matters.
- `update_sp500_dashboard.py` — the IV/refresh script. The overlay constants live near the top.
- `README.md`, `CLAUDE.md` — documentation.

Do not modify `Portfolio.numbers` (macOS-only binary), the `.command`/`.bat` launchers, or the analysis sheets (`Comparison`, `Portfolio DCF`, etc.) without an explicit request.

## Dashboard column conventions

The `Dashboard` sheet has 494 ticker rows starting at row 6 (header at row 5). Always preserve this layout.

| Cols | Meaning | Who writes |
|---|---|---|
| A–D | # / Ticker / Company / Industry | Manual; static |
| E–N | Market data + IV | `update_sp500_dashboard.py` overwrites every run |
| O–S | BRK 13F columns | **Never overwritten by the script.** Updated by hand or by a manual one-off Python pass. |
| A2 | Last-updated timestamp | Manual / script |
| A3 | Geopolitical context summary | Manual |

**Hard rule:** when you write code that touches the dashboard, it must read and preserve columns O–S. The main script does this by only writing columns 5–14. If you write a one-off updater, follow the same convention.

## BRK column semantics

- `O` (BRK?) — `YES` if currently held, `SOLD` if fully exited, `—` if never held. Never blank.
- `P` (BRK Pos $B) — current 13F position value in $B. `—` if SOLD.
- `Q` (BRK Net Action) — one of `Hold`, `Buy`, `Sell`, `Sold` describing the *most recent quarter's* net action.
- `R` (BRK Avg Cost $) — approximate cost basis per share. Stable across quarters.
- `S` (Activity Detail) — short narrative of buys/sells across recent quarters. Update by appending the latest quarter, not by rewriting from scratch.

When updating BRK columns:

1. Pull the 13F facts from `13f.info`, `whalewisdom.com`, or the SEC filing directly. The CIK is `0001067983`.
2. Cross-check the top-10 positions against at least two sources (CNBC, Kiplinger, Benzinga, SeekingAlpha) before publishing — initial coverage articles often disagree by ±10% on share counts.
3. For position values, use the share count × end-of-quarter price. If the 13F-reported value doesn't reconcile, trust the share count.
4. For "Action": if the position was unchanged, use `Hold`. If trimmed but still held, use `Sell`. If added, use `Buy`. If fully exited this quarter, use `Sold`.
5. Append the new quarter's action to the existing narrative in column S; don't wipe history.

## Geopolitical overlay (currently v5, May 22 2026)

The IV math is driven by three knobs set at the top of `update_sp500_dashboard.py`:

```python
GEO_BASE_DISCOUNT_PREMIUM = 0.013   # +1.3% — added to base 10% discount rate
GEO_BASE_GROWTH_HAIRCUT = -0.005    # -0.5% — added to revenue growth input
GEO_BASE_MOS_ADD = 0.040            # +4.0% — added to base 25% margin of safety
```

…plus a `SECTOR_GEO_ADJUSTMENTS` dict that adds sector-specific deltas on top of the base.

**When you bump the version (v4 → v5 → v6 …):**

1. Re-read the macro picture: Brent/WTI, S&P level, fwd P/E, Fed rate, recession-probability surveys, tariff regime, and any active geopolitical conflict.
2. Update the module-level docstring (the version banner at the top), the `# ── Geopolitical risk overlay ──` comment block, the `SECTOR_GEO_ADJUSTMENTS` dict, the three base constants, **and** the docstring inside `buffett_intrinsic_value()`. All five must reference the same version.
3. Update the workbook's `A3` cell to summarize the new context.
4. Tell the user to click "Update Data" — the script has to re-run for new IV numbers to appear in column L.

**Sector-keyword matching is fuzzy.** The lookup is `if sector_key.lower() in industry_lower`. yfinance industries like `Oil & Gas Integrated` will match `Oil & Gas`. Multi-word sectors like `Aerospace & Defense` will only match if yfinance returns that exact phrase. When in doubt, add an alternate key (e.g., `'Industrial'` and `'Industrials'` both exist for this reason).

## Running the script

The script lives in this directory and writes back to `Portfolio.xlsx` in place. Each run takes 3–4 minutes for 494 tickers in batches of 40.

```bash
python3 update_sp500_dashboard.py
```

It logs to `dashboard_update.log` and writes a JSON summary to `last_update_report.json`. Both are `.gitignore`d.

**Important: yfinance requires network access.** In a sandboxed environment without internet (e.g., a CI container, the Cowork Linux sandbox), the script will error out on the first batch. In that case, do the BRK/overlay edits and instruct the user to run the refresh on their own machine.

## Common task patterns

**Updating BRK columns after a new 13F is filed:**

1. Research the filing (13f.info + 2+ news sources).
2. Write a one-off Python script that opens `Portfolio.xlsx`, walks the rows, and updates O–S for affected tickers only. Don't run the full refresh script — that's a separate step.
3. Update `A2` / `A3` headers.
4. Save. Tell the user to run "Update Data" if they also want IV refreshed.

**Recalibrating the overlay:**

1. Search for the latest oil price, Fed rate, S&P level, ISM, recession-probability survey.
2. Update the five locations listed above, all to the same version number.
3. Bump the version (e.g. v4 → v5). Keep prior version comments in git history, not in the active code.

**Adding a new ticker (rare — happens at S&P 500 rebalances):**

1. Insert a row in the Dashboard sheet at the appropriate alphabetical position.
2. Fill columns A–D manually.
3. Set columns O–S to `—` / blank.
4. Run the script — it'll fill E–N.

## Anti-patterns to avoid

- **Don't rewrite the BRK Activity narrative from scratch.** It's a multi-quarter history; just append. The narrative tells the story; flat data alone doesn't.
- **Don't run the full Python refresh just to update BRK columns.** It takes 3–4 minutes and only adds value if market data changed. Use a small one-off script.
- **Don't trust a single news source on share counts.** Confirm with at least one secondary source; the first 24 hours after a 13F drop are messy.
- **Don't promise to "refresh prices" if you can't run yfinance.** In a sandboxed environment, do the structural updates and tell the user to click "Update Data" on their own machine.
- **Don't mix overlay versions.** If you bump the docstring to v6 but leave a sector adjustment dict labeled v5, future-you will be confused. Update all references in one commit.
- **Don't reformat columns A–N if you're only touching BRK.** Preserve fills, borders, fonts. The script applies its own styling on full runs.

## Where to find verified facts

| Topic | Primary source |
|---|---|
| BRK 13F filings | `13f.info/manager/0001067983-berkshire-hathaway-inc` |
| BRK 10-Q / 10-K | `berkshirehathaway.com/reports.html` |
| Oil prices | CNBC, OilPrice.com |
| S&P 500 level / fwd P/E | FactSet via Yahoo, MacroMicro |
| Fed decisions | `federalreserve.gov/newsevents/pressreleases/` |
| Recession surveys | WSJ economic forecasting survey, Goldman, JPM |

## Communication style for this repo

The user prefers terse, fact-dense updates. When summarizing changes:
- Lead with what changed, not what was checked.
- Tables for position lists, prose for context.
- Cite sources at the end.
- Don't overclaim — if a fact came from a single article and contradicts another, flag the disagreement.
