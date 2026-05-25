#!/usr/bin/env python3
"""
S&P 500 Dashboard Updater v5.0 — Iran War / Oil Shock Refresh (May 22, 2026)
Fetches live financial data from Yahoo Finance and updates the Dashboard sheet in Portfolio.xlsx.
Run manually via the "Update Data" button in the Dashboard sheet, or from terminal.

Features:
- Batch fetching with automatic retries and exponential backoff
- Per-ticker error isolation (one failure doesn't block others)
- Detailed error log written to 'Errors' sheet
- Data validation: sanity checks on prices, ratios, market caps
- Buffett-style intrinsic value: 15-yr DCF with decaying growth + liquid assets + margin of safety
- GEOPOLITICAL RISK OVERLAY v5 (May 22, 2026): IRAN WAR + OIL SHOCK INTENSIFIES
  * Iran war ONGOING since Feb 28 (Op Epic Fury killed Khamenei). Brent SPIKED
    to $107-112 (Apr 8 ceasefire holding but fragile, oil +45% since Feb).
    WTI $102-108. Hormuz mostly closed by Iran, US blockading Iranian ports.
    May 4 Project Freedom escort launched; May 6 paused; May 18 Trump called
    off planned Tuesday strike; Pakistan mediating Iran's 14-point framework.
  * Supreme Court ruled IEEPA tariff authority unconstitutional (Feb 2026).
    Tariffs now ~10% (down from 11%), but other statutes preserve broad
    presidential authority. ISM prices index 84.6 (highest since Apr 2022)
    on tariff + energy double pressure.
  * Fed 3.50-3.75% (held Apr 28-29, 8-4 vote with 4 dissents). PCE forecast
    2.7% — sticky. Job creation near zero past year (unusual outside recession).
    Markets price 1-2 cuts H2-26 but inflation fight constrains Fed.
  * Recession risk: Goldman baseline still no recession (2.3-2.6% GDP), but
    STAGFLATION re-emerging on oil shock + tariff persistence. ~28% WSJ.
  * S&P 500: New ATH 7,501 (May 15 close, +0.8%). Forward P/E 20.9-21.2x.
    Q1 blended earnings +15.1% (beat). 2026 EPS growth fcst 18.6%.
  * BRK Q1 2026 13F (filed May 15): $263B portfolio, 29 positions (-13).
    Cash $380-397B. 16 exits incl V/MA/UNH/DPZ/CHTR/HEI/LAMR/POOL/AON/ALLE/DEO.
    CVX -35% (-46M sh, sold ~$8.4B). STZ -95%. GOOGL TRIPLED to $15.6B.
    New positions: DAL ($2.6B), GOOG ($1B), Macy's ($55M). NYT tripled.
    Cash war chest $397B (highest ever). $234M buybacks (first since May-24).
  * Sector-specific: Energy/Defense/Insurance STRONG beneficiaries (Brent $112);
    Transportation/Auto/Industrial HEAVILY hurt (fuel + tariffs); Tech mild
    valuation premium; consumer pain RE-INTENSIFIED by oil + tariffs.
- JSON summary report saved alongside for diagnostics

Required: pip install yfinance openpyxl pandas
"""

import sys
import os
import json
import time
import datetime
import traceback
import warnings
warnings.filterwarnings('ignore')

# ── Dependency check ──────────────────────────────────────────────────────────
REQUIRED = ['yfinance', 'pandas', 'openpyxl']
missing = []
for pkg in REQUIRED:
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)
if missing:
    print(f"Missing packages: {', '.join(missing)}")
    print(f"Installing...")
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', *missing, '-q'])
    print("Installed. Re-importing...")

import yfinance as yf
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(SCRIPT_DIR, 'Portfolio.xlsx')
LOG_PATH = os.path.join(SCRIPT_DIR, 'dashboard_update.log')
REPORT_PATH = os.path.join(SCRIPT_DIR, 'last_update_report.json')
BATCH_SIZE = 40
MAX_RETRIES = 3
BASE_DELAY = 3  # seconds, doubles each retry

HEADER_ROW = 5
DATA_START = 6

# ── Validation bounds ─────────────────────────────────────────────────────────
VALID_RANGES = {
    'price':        (0.01, 100_000),
    'mcap_b':       (0.001, 20_000),
    'pe':           (-5000, 5000),
    'gross_margin': (-5, 5),
    'rev_growth':   (-10, 100),
    'fcf_m':        (-500_000, 500_000),
    'p_fcf':        (-5000, 5000),
}

def validate(key, val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    lo, hi = VALID_RANGES.get(key, (None, None))
    if lo is not None and not (lo <= val <= hi):
        return None
    return val


# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass



# ── Geopolitical risk overlay v5 (May 22, 2026) — IRAN WAR + OIL SHOCK ──────
# Current macro environment:
#   - Iran war ONGOING since Feb 28, 2026 (US/Israel Operation Epic Fury killed
#     Supreme Leader Khamenei). Apr 8 ceasefire HOLDING but FRAGILE.
#     Brent SPIKED to $107-112 (May 18-21); WTI $102-108. Oil up ~45% since
#     Iran war began. Hormuz MOSTLY CLOSED by Iran; US blockading Iranian ports.
#     May 4 Project Freedom escort op launched → paused May 6 → May 18 Trump
#     called off planned Tuesday strike. Pakistan mediating Iran's 14-point
#     framework. Iran reviewing US position May 21.
#     Chubb (CB) lead underwriter for US Hormuz shipping insurance program.
#   - Supreme Court ruled IEEPA tariff authority UNCONSTITUTIONAL (Feb 2026):
#     overall tariffs ~10% (down from 11%), but other statutes preserve broad
#     presidential authority — tariffs unlikely to disappear. ISM prices index
#     84.6 (highest since Apr 2022) on tariff + energy DOUBLE pressure.
#   - Fed: 3.50-3.75% held Apr 28-29 — 8-4 vote, 4 DISSENTS. PCE forecast 2.7%
#     — sticky. Job creation near zero past year (unusual outside recession).
#     Markets price 1-2 cuts H2-26 but inflation fight constrains Fed.
#   - Recession risk: Goldman baseline still no recession (2.3-2.6% GDP), but
#     STAGFLATION re-emerging on oil shock + tariff persistence. ~28% WSJ.
#   - S&P 500: NEW ATH 7,501 close May 15 (+0.8%, intraday 7,517). Forward
#     P/E ~20.9-21.2x. Q1 blended earnings +15.1% (beat). 2026 EPS growth
#     forecast 18.6%. Nasdaq new record 26,635. AI semis driving.
#   - BRK Q1 2026 13F (filed May 15): $263B portfolio, 29 positions (-13).
#     Cash $380-397B (record). 16 exits incl V/MA/UNH/DPZ/CHTR/HEI/LAMR/POOL/
#     AON/ALLE/DEO. CVX -35% sold ~$8.4B (84M sh / $17.5B). STZ -95% wiped.
#     GOOGL TRIPLED to $15.6B (54M sh). New: DAL $2.6B, GOOG $1B, Macy's $55M.
#     NYT tripled. LEN +43%. Op earnings $11.35B +18%. Insurance +28%.
#     OXY unchanged at 264.94M sh ($17.22B at $65/sh, +58% YTD).
#     SIRI +5M sh to 124.81M ($2.88B, 37% of SIRI outstanding).
#
# Sector impact matrix (discount rate premium / growth haircut):
#   BENEFICIARIES: Energy ($112 Brent), Defense (war ongoing), Insurance (Hormuz)
#   NEUTRAL:       Healthcare, Utilities, Consumer Staples
#   HURT:          Transportation/Auto ($108 WTI), Industrials (supply chains)
#                  Consumer Disc (oil + tariffs), Tech (valuation rich)

SECTOR_GEO_ADJUSTMENTS = {
    # sector_keyword: (discount_rate_premium, growth_haircut, margin_of_safety_add)
    #
    # v5 (May 22, 2026): Iran war ESCALATING again, Brent $112, WTI $108. Hormuz
    # mostly CLOSED. Stagflation re-emerging. Supreme Court tariff ruling but
    # tariffs persist. Energy benefit STRENGTHENED. Transportation/Auto pain
    # WORSE (fuel cost shock). Insurance premium boosted (Chubb Hormuz lead).
    # S&P still at ATH 7,501 but valuation premium warranted.
    #
    # STRONG Beneficiaries — bigger discount cut, growth boost
    'Energy':                  (-0.025,  0.035, -0.06),   # Brent $112, war ongoing — STRONGEST tailwind
    'Oil & Gas':               (-0.025,  0.035, -0.06),   # CVX/OXY: BRK still ~$35B combined exposure
    'Aerospace & Defense':     (-0.012,  0.030, -0.05),   # Defense budgets accelerating; war ongoing
    'Insurance':               (-0.010,  0.015, -0.02),   # CB Hormuz war premiums STRONG tailwind
    # Neutral / Defensive
    'Healthcare':              (0.003,   0.005, 0.00),    # UNH exited by BRK but defensive overall
    'Utilities':               (0.008,   0.005, 0.00),    # Rate-sensitive; energy cost pass-through
    'Consumer Staples':        (0.012,  -0.008, 0.025),   # Tariff + commodity input cost intensifying
    'Pharmaceuticals':         (0.003,   0.005, 0.00),
    # Mild Hurt — valuation-stretched / rate sensitive
    'Technology':              (0.015,  -0.015, 0.040),   # Fwd P/E 21.2x rich; mixed AI tailwind
    'Semiconductors':          (0.020,  -0.015, 0.040),   # AI demand strong but valuation peaks
    'Software':                (0.010,  -0.010, 0.020),   # AI tailwind partly offsets
    'Financial Services':      (0.015,  -0.012, 0.035),   # Stagflation concern, but credit OK
    'Banks':                   (0.020,  -0.015, 0.040),   # BAC trimmed by BRK; rate uncertainty
    'Real Estate':             (0.022,  -0.020, 0.050),   # Higher-for-longer + oil cost pressure
    # SEVERELY Hurt — oil shock + tariffs
    'Consumer Discretionary':  (0.025,  -0.025, 0.045),   # $112 Brent = $5.00+ gas, tariffs back
    'Retail':                  (0.025,  -0.025, 0.045),   # Macy's added by BRK (cheap entry)
    'Restaurants':             (0.020,  -0.020, 0.035),   # Input cost shock intensifying
    'Automotive':              (0.030,  -0.035, 0.075),   # $108 WTI + tariffs + EV uncertainty
    'Industrial':              (0.020,  -0.020, 0.040),   # Supply chain Hormuz disruption + tariffs
    'Industrials':             (0.020,  -0.020, 0.040),
    'Materials':               (0.012,  -0.012, 0.025),   # Mixed: commodity tailwind / input costs
    'Communication Services':  (0.005,  -0.005, 0.010),   # Insulated; GOOGL tripled by BRK
    'Transportation':          (0.028,  -0.028, 0.060),   # $108 WTI MASSIVE fuel headwind; DAL/airlines
}

# Base geopolitical risk premium applied to ALL companies
# v5 (May 22): TIGHTENED vs v4 — oil shock ($112 Brent), Iran war escalating,
# stagflation re-emerging, ISM prices at multi-year high. But S&P at fresh ATH.
# Net: discount premium and MOS BUMPED to reflect oil/war reality.
GEO_BASE_DISCOUNT_PREMIUM = 0.013   # +1.3% base discount (v4: 1.0%) — oil shock + stagflation re-emerging
GEO_BASE_GROWTH_HAIRCUT = -0.005    # -0.5% growth haircut (v4: -0.3%) — tariff+oil double pressure
GEO_BASE_MOS_ADD = 0.040            # +4.0% margin of safety (v4: 3.5%) — war ongoing + ATH valuations


def get_geo_adjustments(industry):
    """Look up sector-specific geopolitical adjustments from industry string."""
    if not industry:
        return (GEO_BASE_DISCOUNT_PREMIUM, GEO_BASE_GROWTH_HAIRCUT, GEO_BASE_MOS_ADD)
    industry_lower = industry.lower()
    for sector_key, adjustments in SECTOR_GEO_ADJUSTMENTS.items():
        if sector_key.lower() in industry_lower:
            dr_adj, g_adj, mos_adj = adjustments
            return (
                GEO_BASE_DISCOUNT_PREMIUM + dr_adj,
                GEO_BASE_GROWTH_HAIRCUT + g_adj,
                GEO_BASE_MOS_ADD + mos_adj,
            )
    return (GEO_BASE_DISCOUNT_PREMIUM, GEO_BASE_GROWTH_HAIRCUT, GEO_BASE_MOS_ADD)


# ── Buffett-style intrinsic value (v4 — Geopolitical Risk-Adjusted) ──────────
def buffett_intrinsic_value(fcf, growth_rate, liquid_assets=0,
                            discount_rate=0.10,
                            terminal_growth=0.025,
                            projection_years=15,
                            margin_of_safety=0.25,
                            industry=None):
    """
    Conservative 15-year Owner Earnings DCF with 2026 geopolitical overlay v5.

    Base model (Buffett philosophy):
    - 15-year FCF projection with 3-phase growth decay
    - Add liquid assets (cash + short-term investments)
    - 25% base margin of safety

    Geopolitical overlay v5 (May 22, 2026) — IRAN WAR + OIL SHOCK:
    - Iran war ongoing since Feb 28 (Khamenei killed). Apr 8 ceasefire fragile.
      Brent $107-112, WTI $102-108 (+45% since war began). Hormuz mostly closed.
      Trump called off planned May 18 strike; Iran reviewing 14-pt framework.
    - Supreme Court (Feb) ruled IEEPA tariffs unconstitutional → ~10% tariffs but
      other statutes preserve broad presidential authority. ISM prices 84.6.
    - Fed 3.50-3.75% (held with 4 dissents). PCE 2.7%. Stagflation re-emerging.
    - S&P 500 ATH 7,501 (May 15). Fwd P/E ~21x. Earnings +15.1% Q1 beat.
    - BRK Q1 2026 13F (5/15): $263B portfolio (-13 positions), cash $397B record.
      16 exits, CVX -35%, STZ -95%, GOOGL TRIPLED, DAL/GOOG/M new positions.
    - Sector-specific: Energy/Defense/Insurance STRONG benefit; Transportation/
      Auto/Industrial heavy hurt from oil shock + tariffs; Tech mild valuation
      haircut; Consumer pain RE-intensified.
    """
    if fcf is None or (isinstance(fcf, float) and pd.isna(fcf)) or fcf <= 0:
        return None
    if growth_rate is None or (isinstance(growth_rate, float) and pd.isna(growth_rate)):
        growth_rate = 0.03
    if liquid_assets is None or (isinstance(liquid_assets, float) and pd.isna(liquid_assets)):
        liquid_assets = 0
    liquid_assets = max(liquid_assets, 0)

    # Apply geopolitical adjustments
    geo_dr, geo_g, geo_mos = get_geo_adjustments(industry)
    adj_discount = discount_rate + geo_dr
    adj_mos = margin_of_safety + geo_mos
    growth_rate = growth_rate + geo_g

    # Cap starting growth conservatively (wider band for crisis)
    g_start = min(max(growth_rate, -0.05), 0.12)
    g_terminal = terminal_growth

    dcf_sum = 0.0
    projected_fcf = float(fcf)

    for yr in range(1, projection_years + 1):
        if yr <= 5:
            g = g_start
        elif yr <= 10:
            fade = (yr - 5) / 5.0
            g = g_start * (1 - fade) + g_terminal * fade
        else:
            g = g_terminal

        projected_fcf *= (1 + g)
        dcf_sum += projected_fcf / ((1 + adj_discount) ** yr)

    terminal_fcf = projected_fcf * (1 + g_terminal)
    terminal_value = terminal_fcf / (adj_discount - g_terminal)
    terminal_pv = terminal_value / ((1 + adj_discount) ** projection_years)

    enterprise_value = dcf_sum + terminal_pv + liquid_assets

    return enterprise_value * (1 - adj_mos)

# ── Fetch with retries ────────────────────────────────────────────────────────
def fetch_single_ticker(ticker):
    """Fetch data for one ticker with retries. Returns (data_dict, error_msg)."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}

            if not info or info.get('regularMarketPrice') is None and info.get('currentPrice') is None:
                # Sometimes yfinance returns empty info on first try
                if attempt < MAX_RETRIES:
                    time.sleep(BASE_DELAY * attempt)
                    continue
                return {k: None for k in ['price','yr_change','mcap_b','rev_growth','gross_margin','pe','fcf_m','iv_b','discount','p_fcf']}, f"Empty info after {MAX_RETRIES} attempts"

            price = info.get('currentPrice') or info.get('regularMarketPrice')
            price = validate('price', price)

            yr_change = info.get('52WeekChange')
            if yr_change is None:
                try:
                    hist = t.history(period='1y')
                    if not hist.empty and price:
                        first_close = hist['Close'].dropna().iloc[0]
                        if first_close > 0:
                            yr_change = (price - first_close) / first_close
                except Exception:
                    pass

            mcap = info.get('marketCap')
            mcap_b = validate('mcap_b', mcap / 1e9 if mcap else None)

            rev_growth = validate('rev_growth', info.get('revenueGrowth'))
            gross_margin = validate('gross_margin', info.get('grossMargins'))
            pe = validate('pe', info.get('trailingPE') or info.get('forwardPE'))

            fcf = info.get('freeCashflow')
            fcf_m = validate('fcf_m', fcf / 1e6 if fcf else None)

            # P/FCF ratio
            p_fcf = None
            if price and fcf and info.get('sharesOutstanding'):
                fcf_per_share = fcf / info['sharesOutstanding']
                if fcf_per_share != 0:
                    p_fcf = validate('p_fcf', price / fcf_per_share)

            # Liquid assets: cash + short-term investments on balance sheet
            cash = info.get('totalCash') or 0  # cash & cash equivalents
            # yfinance 'totalCash' includes cash + short-term investments

            # Industry for geopolitical risk adjustment
            industry = info.get('industry') or info.get('sector') or ''

            iv = buffett_intrinsic_value(
                fcf,
                rev_growth if rev_growth else 0.03,
                liquid_assets=cash,
                industry=industry,
            )
            iv_b = iv / 1e9 if iv else None

            discount = None
            if iv_b and mcap_b and mcap_b > 0:
                discount = (iv_b - mcap_b) / mcap_b

            return {
                'price': price,
                'yr_change': yr_change,
                'mcap_b': mcap_b,
                'rev_growth': rev_growth,
                'gross_margin': gross_margin,
                'pe': pe,
                'fcf_m': fcf_m,
                'iv_b': iv_b,
                'discount': discount,
                'p_fcf': p_fcf,
            }, None

        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < MAX_RETRIES:
                time.sleep(BASE_DELAY * (2 ** (attempt - 1)))

    return {k: None for k in ['price','yr_change','mcap_b','rev_growth','gross_margin','pe','fcf_m','iv_b','discount','p_fcf']}, last_err


def fetch_batch(tickers):
    """Fetch data for a list of tickers, isolating per-ticker errors."""
    results = {}
    errors = {}
    for ticker in tickers:
        data, err = fetch_single_ticker(ticker)
        results[ticker] = data
        if err:
            errors[ticker] = err
    return results, errors


# ── Style helpers ─────────────────────────────────────────────────────────────
GREEN_FONT = Font(name='Arial', size=9, bold=True, color='006100')
RED_FONT = Font(name='Arial', size=9, bold=True, color='9C0006')
GREEN_FILL = PatternFill('solid', fgColor='C6EFCE')
RED_FILL = PatternFill('solid', fgColor='FFC7CE')
NUM_FONT = Font(name='Arial', size=9)
GRAY_FONT = Font(name='Arial', size=9, color='CCCCCC')
CENTER = Alignment(horizontal='center')
ALT1 = PatternFill('solid', fgColor='F2F7FB')
ALT2 = PatternFill('solid', fgColor='FFFFFF')
BORDER = Border(
    left=Side(style='thin', color='D9E2F3'),
    right=Side(style='thin', color='D9E2F3'),
    top=Side(style='thin', color='D9E2F3'),
    bottom=Side(style='thin', color='D9E2F3'),
)

def write_cell(ws, row, col, val, fmt, conditional, base_fill):
    cell = ws.cell(row=row, column=col)
    if val is not None and not (isinstance(val, float) and pd.isna(val)):
        cell.value = val
        cell.number_format = fmt
        if conditional and isinstance(val, (int, float)):
            cell.font = GREEN_FONT if val > 0 else RED_FONT if val < 0 else NUM_FONT
            cell.fill = GREEN_FILL if val > 0 else RED_FILL if val < 0 else base_fill
        else:
            cell.font = NUM_FONT
            cell.fill = base_fill
        return True
    else:
        cell.value = '—'
        cell.font = GRAY_FONT
        cell.fill = base_fill
        return False
    cell.alignment = CENTER
    cell.border = BORDER


# ── Main update ───────────────────────────────────────────────────────────────
def update_dashboard():
    start_time = time.time()
    log("=" * 60)
    log("S&P 500 Dashboard update starting...")

    if not os.path.exists(XLSX_PATH):
        log(f"ERROR: {XLSX_PATH} not found")
        sys.exit(1)

    wb = load_workbook(XLSX_PATH)
    if 'Dashboard' not in wb.sheetnames:
        log("ERROR: 'Dashboard' sheet not found")
        sys.exit(1)

    ws = wb['Dashboard']

    # Read tickers
    tickers = []
    row = DATA_START
    while True:
        val = ws.cell(row=row, column=2).value
        if val is None or str(val).strip() == '':
            break
        tickers.append((row, str(val).strip()))
        row += 1

    log(f"Found {len(tickers)} tickers")

    # Fetch in batches
    all_data = {}
    all_errors = {}
    ticker_list = [t for _, t in tickers]

    for i in range(0, len(ticker_list), BATCH_SIZE):
        batch = ticker_list[i:i + BATCH_SIZE]
        batch_n = i // BATCH_SIZE + 1
        total_b = (len(ticker_list) + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"  Batch {batch_n}/{total_b} ({len(batch)} tickers)...")

        data, errs = fetch_batch(batch)
        all_data.update(data)
        all_errors.update(errs)

        if i + BATCH_SIZE < len(ticker_list):
            time.sleep(2)

    # Write MARKET data only — columns E(5) through N(14)
    # BRK columns O-S are PRESERVED (static 13F data, not overwritten)
    # E=price F=yr_change G=mcap H=rev_growth I=gross_margin J=pe K=fcf L=iv M=discount N=p_fcf
    success = 0
    partial = 0
    failed = 0
    no_data_tickers = []  # tickers with zero data (candidates for removal)

    for row_num, ticker in tickers:
        d = all_data.get(ticker, {})
        base_fill = ALT1 if (row_num - DATA_START) % 2 == 0 else ALT2

        col_map = [
            (5,  d.get('price'),        '$#,##0.00', False),
            (6,  d.get('yr_change'),     '0.0%',     True),
            (7,  d.get('mcap_b'),        '#,##0.0',  False),
            (8,  d.get('rev_growth'),    '0.0%',     True),
            (9,  d.get('gross_margin'),  '0.0%',     False),
            (10, d.get('pe'),            '0.0',      False),
            (11, d.get('fcf_m'),         '#,##0',    False),
            (12, d.get('iv_b'),          '#,##0.0',  False),
            (13, d.get('discount'),      '0.0%',     True),
            (14, d.get('p_fcf'),         '0.0',      False),
        ]

        filled = 0
        for col, val, fmt, cond in col_map:
            ok = write_cell(ws, row_num, col, val, fmt, cond, base_fill)
            cell = ws.cell(row=row_num, column=col)
            cell.alignment = CENTER
            cell.border = BORDER
            if ok:
                filled += 1

        if filled >= 7:
            success += 1
        elif filled > 0:
            partial += 1
        else:
            failed += 1
            no_data_tickers.append((row_num, ticker))

    # ── Remove rows with zero data (no 1-year data available) ─────────────────
    if no_data_tickers:
        log(f"Removing {len(no_data_tickers)} companies with no available data...")
        # Delete rows from bottom to top to preserve row indices
        for row_num, ticker in sorted(no_data_tickers, key=lambda x: x[0], reverse=True):
            log(f"  Removing {ticker} (row {row_num}) — no data available")
            ws.delete_rows(row_num, 1)
        # Re-number remaining rows
        r = DATA_START
        idx = 1
        while ws.cell(row=r, column=2).value:
            ws.cell(row=r, column=1).value = idx
            idx += 1
            r += 1
        log(f"  {len(no_data_tickers)} companies removed. {r - DATA_START} remaining.")

    # Update timestamp in A2
    now = datetime.datetime.now()
    elapsed = time.time() - start_time
    removed_count = len(no_data_tickers)
    remaining = success + partial
    ws['A2'] = (
        f'Last updated: {now:%Y-%m-%d %H:%M:%S} | '
        f'{success} full / {partial} partial / {removed_count} removed (no data) | '
        f'{elapsed:.0f}s elapsed'
    )
    ws['A2'].font = Font(name='Arial', size=10, color='666666')

    # ── Write Errors sheet ────────────────────────────────────────────────────
    if 'Errors' in wb.sheetnames:
        del wb['Errors']
    if all_errors:
        es = wb.create_sheet('Errors')
        es['A1'] = 'Ticker'
        es['B1'] = 'Error'
        es['C1'] = 'Timestamp'
        for c in range(1, 4):
            es.cell(row=1, column=c).font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
            es.cell(row=1, column=c).fill = PatternFill('solid', fgColor='C00000')
        for idx, (tk, err) in enumerate(all_errors.items(), 2):
            es.cell(row=idx, column=1, value=tk).font = Font(name='Arial', size=9)
            es.cell(row=idx, column=2, value=err).font = Font(name='Arial', size=9)
            es.cell(row=idx, column=3, value=now.strftime('%Y-%m-%d %H:%M:%S')).font = Font(name='Arial', size=9)
        es.column_dimensions['A'].width = 10
        es.column_dimensions['B'].width = 60
        es.column_dimensions['C'].width = 20

    # ── Update auto-filter after potential row removals ──────────────────────
    last_row = DATA_START
    while ws.cell(row=last_row, column=2).value:
        last_row += 1
    last_row -= 1
    if last_row >= DATA_START:
        ws.auto_filter.ref = f'A{HEADER_ROW}:S{last_row}'

    wb.save(XLSX_PATH)

    # ── JSON report ───────────────────────────────────────────────────────────
    report = {
        'timestamp': now.isoformat(),
        'elapsed_seconds': round(elapsed, 1),
        'total_tickers': len(tickers),
        'success': success,
        'partial': partial,
        'removed_no_data': removed_count,
        'errors_count': len(all_errors),
        'removed_tickers': [t for _, t in no_data_tickers],
        'sample_errors': dict(list(all_errors.items())[:10]),
    }
    try:
        with open(REPORT_PATH, 'w') as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass

    log(f"Update complete: {success} full / {partial} partial / {removed_count} removed | {len(all_errors)} errors")
    log(f"Elapsed: {elapsed:.1f}s | Saved to {XLSX_PATH}")
    log("=" * 60)

    return report


if __name__ == '__main__':
    report = update_dashboard()
    print(json.dumps(report, indent=2))
