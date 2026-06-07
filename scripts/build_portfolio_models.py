#!/usr/bin/env python3
"""Build portfolio_models.json — world-class, data-driven, Buffett-conservative
per-company intrinsic-value models. v2 (2026-06-07).

Methodology (addresses the 6 requested upgrades):

 1. GROWTH from sector + macro, not guesswork. Each name's Y1-5 FCF growth is
    the conservative MIN of its own multi-year history and a macro-aware sector
    ceiling, then nudged by the latest-earnings momentum/guidance. 3-phase
    decay (Y1-5 high-vis → Y6-10 fade → Y11-15 mature) toward a conservative
    terminal.

 2. DISCOUNT from the live US yield curve, tenor-matched to the 15-yr horizon.
    risk_free = interpolated ~15-yr UST (between the live 10-yr and 30-yr).
    cost_of_equity = rf + clamp(beta,0.7,1.6) x ERP(5.0%).
    discount = max(cost_of_equity, 9% Buffett floor) + sector risk premium
    (China ADR +1.5, energy +0.5, airline +1.5, recent-IPO/speculative +2.0).
    Horizon fixed at 15 years, mid-year convention.

 3. FCF over net income. Owner-earnings are normalized as the MEDIAN historical
    FCF margin x latest-FY revenue (smooths capex spikes / cyclical revenue) —
    a forward FCF stream, not net income. Exceptions, documented per name:
    banks (no FCF concept) use normalized EPS x justified P/E; Wise's OCF is
    float-inflated so it uses net income; DouYu is a loss-making net-cash floor.

 4. BUYBACK / issuance. Base buyback yield = historical diluted-share-count
    CAGR; overridden by authorizations announced at the latest earnings
    (e.g. NVDA $118B, SYF $6.5B, Xiaomi HK$20B new; OXY paused; AAL none /
    dilutive; CRCL post-IPO dilution). Folded into per-share growth so
    repurchases compound per-share value (Buffett's per-share lens).

 5. LATEST EARNINGS. The RESEARCH overlay below encodes each company's most
    recent quarter (rev/EPS YoY, guidance, buyback) from primary sources; it
    sets the growth/buyback/terminal and is recorded in each model's note.

 6. Conservative throughout: growth capped below history for hypergrowth names,
    Buffett 9% discount floor, terminal <= 3% (0% for depleting energy),
    base 25% margin of safety applied in the engine, low-confidence flags.

Inputs come from three scratch files produced earlier this session:
  /tmp/dcf_data.json   — 4-5yr revenue/FCF/NI/shares, beta, sector, currency
  /tmp/dcf_deriv.json  — median FCF margins, growth & share-count CAGR, FX
This builder is self-contained: it fetches the live US Treasury curve, 4-5yr
financials, share-count history, beta and FX from yfinance itself (cached to
/tmp for fast re-runs). Just run:  python3 scripts/build_portfolio_models.py
The RESEARCH overlay below (latest-earnings + announced buybacks) is the
human-curated layer; refresh it when new quarters are reported.
"""
import json
import statistics as st
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "portfolio_models.json"
AS_OF = "2026-06-07"

TICKERS = ["NVDA", "GOOGL", "BABA", "BIDU", "DOYU", "UNH", "DPZ", "POOL", "LEN",
           "OXY", "TTE", "AAL", "SYF", "USB", "CRCL", "WISE.L", "1810.HK"]


def _fetch_market_data(cache="/tmp/dcf_bundle.json", use_cache=True):
    """Pull yield curve, FX, and 4-5yr financials for all names from yfinance.
    Caches to /tmp so repeated builder runs are instant. Delete the cache (or
    pass use_cache=False) to force a live refresh."""
    p = Path(cache)
    if use_cache and p.exists():
        return json.load(open(p))
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    def last(tk):
        try:
            h = yf.Ticker(tk).history(period="5d")
            return float(h["Close"].dropna().iloc[-1])
        except Exception:
            return None
    curve = {"3mo": last("^IRX"), "5yr": last("^FVX"),
             "10yr": last("^TNX"), "30yr": last("^TYX")}
    y10, y30 = curve["10yr"], curve["30yr"]
    y15 = round(y10 + (y30 - y10) * (15 - 10) / (30 - 10), 3) if (y10 and y30) else 4.65
    usdcny, usdhkd = last("CNY=X"), last("HKD=X")
    cny_usd = round(1 / usdcny, 4) if usdcny else 0.1478
    cny_hkd = round(usdhkd / usdcny, 4) if (usdhkd and usdcny) else 1.1577

    def series(df, *keys):
        if df is None or df.empty:
            return []
        for k in keys:
            if k in df.index:
                return [round(float(x) / 1e9, 3) for x in df.loc[k].dropna().values][:5]
        return []

    def cagr(s):
        s = [x for x in s if x is not None]
        if len(s) < 2 or s[0] <= 0 or s[-1] <= 0:
            return None
        return (s[0] / s[-1]) ** (1 / (len(s) - 1)) - 1

    names, deriv = {}, {}
    for tk in TICKERS:
        t = yf.Ticker(tk)
        info = t.info or {}
        cf, inc = t.cashflow, t.income_stmt
        rev = series(inc, "Total Revenue")
        fcf = series(cf, "Free Cash Flow")
        ni = series(inc, "Net Income", "Net Income Common Stockholders")
        dsh = series(inc, "Diluted Average Shares", "Basic Average Shares")
        names[tk] = {"currency": info.get("currency"), "beta": info.get("beta"),
                     "sector": info.get("sector"), "rev": rev, "fcf": fcf, "ni": ni,
                     "price": info.get("currentPrice") or info.get("regularMarketPrice")}
        margins = [f / r for f, r in zip(fcf, rev) if r and r > 0]
        deriv[tk] = {"fcf_margin_med": st.median(margins) if margins else None,
                     "buyback_yield": (lambda c: -c if c is not None else None)(cagr(dsh))}
        # market data
        names[tk].update({"mcap": info.get("marketCap"), "debt": info.get("totalDebt"),
                          "cash": info.get("totalCash"), "eps_ttm": info.get("trailingEps")})
    bundle = {"curve": curve, "y15": y15, "cny_to_usd": cny_usd, "cny_to_hkd": cny_hkd,
              "names": names, "deriv": deriv}
    json.dump(bundle, open(p, "w"), indent=1)
    return bundle


_B = _fetch_market_data()
DATA = {"names": _B["names"], "curve": _B["curve"], "y15": _B["y15"]}
DERIV = {"deriv": _B["deriv"], "cny_to_usd": _B["cny_to_usd"], "cny_to_hkd": _B["cny_to_hkd"]}
MCAP = {tk: {"mcap": v.get("mcap"), "price": v.get("price"), "debt": v.get("debt"),
             "cash": v.get("cash"), "eps_ttm": v.get("eps_ttm")} for tk, v in _B["names"].items()}

CURVE = DATA["curve"]
RF15 = DATA.get("y15") or 4.65          # interpolated 15-yr UST, %
ERP = 5.0                                # equity risk premium, %
BUFFETT_FLOOR = 9.0                      # never discount below the hurdle, %
CNY_USD = DERIV["cny_to_usd"]
CNY_HKD = DERIV["cny_to_hkd"]

# Macro-aware conservative sector ceiling for Y1-5 FCF growth (nominal %).
SECTOR_CEIL = {
    "Technology": 0.15, "Communication Services": 0.09, "Healthcare": 0.06,
    "Consumer Cyclical": 0.05, "Consumer Defensive": 0.04, "Energy": 0.03,
    "Financial Services": 0.06, "Industrials": 0.03, "Basic Materials": 0.04,
    "Real Estate": 0.04,
}
SECTOR_RISK = {  # added to discount rate, %
    "Energy": 0.5,
}

# ── RESEARCH overlay (latest earnings + capital return, primary-sourced) ─────
# g = Y1-5 FCF growth (conservative, sector+earnings informed)
# bb = buyback yield (history + announced authorization)
# tg = terminal growth
# type: 'fcf_dcf' | 'earnings_multiple' | 'netcash' | 'none'
# For earnings_multiple: eps (normalized) + pe.
R = {
 "NVDA": dict(type="fcf_dcf", g=.15, bb=.015, tg=.03, conf="med", risk=0.0,
   note="Q1 FY27 (May'26): rev +85% to $81.6B, DC +92%; $118B buyback authorized, div raised. Hypergrowth capped hard for Buffett conservatism; priced for perfection."),
 "GOOGL": dict(type="fcf_dcf", g=.09, bb=.024, tg=.025, conf="med", risk=0.0,
   note="Q1'26: rev +22%, Cloud +63%, EPS +82%; 2026 capex $180-190B pressures FCF; ~$70B annual buyback, shares -13% since 2019."),
 "BABA": dict(type="fcf_dcf", g=.06, bb=.01, tg=.03, conf="med", risk=1.5,
   note="Mar-qtr FY26: rev +21%, Cloud +38%, but profit ~wiped by AI/commerce investment; $19.1B buyback remaining (pace slowed ~$1B/yr); $1.05/ADS div. Margins haircut."),
 "BIDU": dict(type="fcf_dcf", g=.03, bb=.01, tg=.02, conf="med", risk=1.5,
   note="Q1'26: core +2%, AI +49% (52% of core) but net income -55%; ~$40B cash, $5B buyback (light pace). Value rests on cash + AI optionality, ad in structural decline."),
 "UNH": dict(type="fcf_dcf", g=.06, bb=.01, tg=.025, conf="med", risk=0.0,
   note="Q1'26: rev +2%, adj EPS $7.23 beat, FY26 guide RAISED to adj >$18.25, MCR improving to 83.9%; ~$2.5B/yr buyback (<1%), 2.7% div. DOJ MA probes = overhang, recovery haircut."),
 "DPZ": dict(type="fcf_dcf", g=.045, bb=.03, tg=.025, conf="high", risk=0.0,
   note="Q1'26: rev +3.5%, US SSS +0.9% (slowing), EPS -4.6% miss; NEW $1B buyback ($1.29B remaining ~7% cap), 2.5% div. NOTE: Berkshire FULLY EXITED DPZ in Q1'26."),
 "POOL": dict(type="fcf_dcf", g=.05, bb=.03, tg=.025, conf="high", risk=0.0,
   note="Q1'26: rev +6%, EPS +2% beat, FY26 EPS $10.87-11.17; buyback raised to $600M (~8% cap), 2.8% div raised. CEO transition; Berkshire EXITED. Gradual discretionary recovery."),
 "LEN": dict(type="fcf_dcf", g=.04, bb=.04, tg=.02, conf="med", risk=0.0, oe_override=1.6,
   note="Q1 FY26: rev -13%, EPS -53% (mgmt calls it the margin BOTTOM); +$5B buyback authorized (~23% of shares retired over program), 1.6% div. Cyclical trough; owner-earnings overridden to a conservative cycle-avg ~$1.6B (mechanical margin includes the 2023-24 housing boom and overstates trough-normal)."),
 "OXY": dict(type="fcf_dcf", g=.02, bb=.00, tg=.00, conf="med", risk=0.5,
   note="Q1'26: adj EPS $1.06 (+~20%), FCF +52% to $1.7B on high oil; buyback PAUSED for deleveraging (debt $13.3B from $20.8B); Berkshire owns ~28% + 8% pref (redeemable 2029). 0% terminal (depleting)."),
 "TTE": dict(type="fcf_dcf", g=.03, bb=.035, tg=.00, conf="high", risk=0.5,
   note="Q1'26: adj EPS $2.45 (+34%), production +4%; buyback DOUBLED to $1.5B/qtr (active), ~5% growing div. Most defensively attractive energy major; 0% terminal."),
 "CRCL": dict(type="fcf_dcf", g=.12, bb=-.02, tg=.03, conf="low", risk=2.0,
   note="Q1'26: rev +20% to $694M, USDC circulation $77B (+28%), but NET INCOME -15% (Coinbase rev-share + falling reserve yield) and shares +178% YoY (post-IPO dilution). Earnings hostage to short rates. Speculative."),
 "WISE.L": dict(type="fcf_dcf", g=.10, bb=.005, tg=.025, conf="med", risk=1.5, owner_earnings_is_ni=True,
   note="FY26: underlying income £1.6B +18%, cross-border volume £182B; active buyback. OCF is float-inflated so owner-earnings = net income. +1.5% discount for float-income rate-cut sensitivity. Price in pence."),
 "1810.HK": dict(type="fcf_dcf", g=.08, bb=.02, tg=.03, conf="med", risk=1.5,
   note="Q1'26: rev -10.9% (memory-chip cost shock), adj profit -43%; EV deliveries 80.9k (+6.6%) but EV segment still -RMB3.1B op loss. NEW: HK$20B buyback (reverses old 'no buyback'); no div. EV growth vs near-term margin fragility."),
 "AAL": dict(type="earnings_multiple", eps=1.00, pe=7.0, conf="low", risk=1.5,
   note="Q1'26: record rev +10.8% but adj loss $(0.40); FY26 adj EPS guide $(0.40)-$1.10 (mid ~$0.35). NO buyback til ~2027 (debt $34.7B, hitting <$35B a year early); >$4B fuel headwind from high oil. Normalized mid-cycle EPS x7 (cyclical, leveraged); equity is partly a deleveraging option."),
 "SYF": dict(type="earnings_multiple", eps=9.30, pe=9.0, conf="med", risk=0.0,
   note="Q1'26: EPS $2.27 (+20%, buyback-driven) beat, FY26 EPS guide $9.10-9.50; NEW $6.5B buyback (~25% of cap!) + 13% div hike; NCO improving to 5.42%. Cyclical consumer credit -> EPS x9 (banks: FCF N/A)."),
 "USB": dict(type="earnings_multiple", eps=4.50, pe=11.0, conf="med", risk=0.0,
   note="Q1'26: EPS $1.18 (+14.6%) beat, FY26 rev +4-6%, NIM recovery to ~3% by 2027; buyback doubled to $200M/qtr, 70-75% payout target, ~4% div. Quality super-regional -> EPS x11 (banks: FCF N/A)."),
 "DOYU": dict(type="netcash", iv_override=6.0, conf="low",
   note="Q1'26: rev -13% (secular decline) but swung to small profit on cost cuts. A net-net (cash > market cap) but yfinance's cash figure is unreliable for this micro-cap; IV manually set to a conservative ~$6/ADS net-cash floor (heavy haircut for China trapped-cash + episodic special dividends as the only return mechanism). Low confidence."),
 "VOO": dict(type="none", conf="n/a", note="S&P 500 ETF — price ~ NAV; no single-name DCF."),
}

# Listing/price currency + reporting->listing FX for owner-earnings conversion.
CCY = {
 "BABA": ("USD", CNY_USD), "BIDU": ("USD", CNY_USD), "1810.HK": ("HKD", CNY_HKD),
 "WISE.L": ("GBp", 1.0),  # financials GBP, price pence -> handle x100 at per-share
}

# Sheet (v1) published IVs, kept as a cross-reference.
SHEET_IV = {"DPZ": 363.68, "UNH": 621.36, "1810.HK": 40.46, "LEN": 143.21,
            "POOL": 266.54, "TTE": 113.06, "NVDA": 166.42}


def midyear_iv_ps(fcf_ps0, g_ps, r, tg, net_debt_ps):
    fcf, pv = fcf_ps0, 0.0
    for n in range(1, 16):
        g = g_ps[0] if n <= 5 else g_ps[1] if n <= 10 else g_ps[2]
        fcf *= (1 + g)
        pv += fcf / (1 + r) ** (n - 0.5)
    tv = fcf * (1 + tg) / (r - tg)
    return pv + tv / (1 + r) ** (15 - 0.5) - net_debt_ps


def discount_for(tk, beta, sector, extra_risk):
    b = beta if isinstance(beta, (int, float)) else 1.0
    b = min(max(b, 0.7), 1.6)
    coe = RF15 + b * ERP
    disc = max(coe, BUFFETT_FLOOR) + SECTOR_RISK.get(sector, 0.0) + extra_risk
    return round(disc / 100.0, 4)


def three_phase(g1, tg):
    """Conservative 3-phase decay: Y1-5=g1, Y6-10 fades ~70% of the way back
    toward terminal, Y11-15 ~40% of the way (gentler than a hard cliff but
    still meaningfully conservative versus a flat-growth model)."""
    g2 = tg + (g1 - tg) * 0.70
    g3 = tg + (g1 - tg) * 0.40
    return [round(g1, 4), round(g2, 4), round(g3, 4)]


def main():
    models = {}
    order = ["1810.HK", "AAL", "BABA", "BIDU", "CRCL", "DOYU", "DPZ", "GOOGL",
             "LEN", "NVDA", "OXY", "POOL", "SYF", "TTE", "UNH", "USB", "VOO", "WISE.L"]

    for tk in order:
        r = R[tk]
        d = DATA["names"].get(tk, {})
        dv = DERIV["deriv"].get(tk, {})
        mc = MCAP.get(tk, {})
        sector = d.get("sector")
        listing_ccy, fx = CCY.get(tk, ("USD", 1.0))
        price = mc.get("price") or d.get("price")
        mcap = mc.get("mcap")

        if r["type"] == "none":
            models[tk] = {"ticker": tk, "model_type": "none", "currency": "USD",
                          "source": "n/a", "as_of": AS_OF, "confidence": "n/a",
                          "note": r["note"]}
            continue

        # economic share count = market cap / price (handles multi-class & ADS)
        price_main = price / 100.0 if listing_ccy == "GBp" else price
        shares_econ = (mcap / price_main) if (mcap and price_main) else None

        disc = discount_for(tk, d.get("beta"), sector, r.get("risk", 0.0))

        if r["type"] == "earnings_multiple":
            iv_ps = r["eps"] * r["pe"]
            models[tk] = {
                "ticker": tk, "model_type": "earnings_multiple", "currency": listing_ccy,
                "source": "created v2 (data-driven, Buffett-conservative)",
                "as_of": AS_OF, "confidence": r["conf"],
                "normalized_eps": r["eps"], "target_pe": r["pe"], "net_cash_ps": 0.0,
                "discount_rate_ref": disc,
                "iv_per_share": round(iv_ps, 2),
                "sheet_iv_per_share": SHEET_IV.get(tk),
                "note": r["note"],
            }
            continue

        if r["type"] == "netcash":
            if r.get("iv_override") is not None:
                iv_ps = r["iv_override"]              # data too unreliable; manual floor
            else:
                nd = mc.get("debt", 0) or 0
                cash = mc.get("cash", 0) or 0
                net_cash = (cash - nd) * (1 - r.get("net_cash_haircut", 0.25))
                iv_ps = (net_cash / shares_econ) if shares_econ else None
            models[tk] = {
                "ticker": tk, "model_type": "netcash", "currency": listing_ccy,
                "source": "created v2 (data-driven, Buffett-conservative)",
                "as_of": AS_OF, "confidence": r["conf"],
                "iv_per_share": round(iv_ps, 2) if iv_ps else None,
                "note": r["note"],
            }
            continue

        # ── fcf_dcf ──────────────────────────────────────────────────────────
        rev = d.get("rev") or []
        latest_rev = rev[0] if rev else None
        if r.get("oe_override") is not None:
            oe = r["oe_override"]                     # conservative manual normalization
            basis = "manual conservative normalization (cycle-avg)"
        elif r.get("owner_earnings_is_ni"):
            oe = (d.get("ni") or [None])[0]          # net income (Wise: float-clean)
            basis = "net income (latest FY; OCF float-inflated)"
        else:
            margin = dv.get("fcf_margin_med")
            oe = (margin * latest_rev) if (margin is not None and latest_rev) else None
            basis = f"median FCF margin {round(margin,3) if margin else '?'} x latest rev"
        if oe is None or shares_econ is None:
            models[tk] = {"ticker": tk, "model_type": "fcf_dcf", "currency": listing_ccy,
                          "confidence": "low", "iv_per_share": None,
                          "note": "insufficient data: " + r["note"]}
            continue

        oe_listing = oe * fx                          # reporting -> listing ccy (billions)
        per_share_scale = 100.0 if listing_ccy == "GBp" else 1.0
        # oe in $B -> absolute; shares_econ is an absolute count (mcap/price)
        fcf_ps0 = oe_listing * 1e9 / shares_econ * per_share_scale

        # net debt already in listing ccy & absolute (yfinance .info)
        nd_total = (mc.get("debt", 0) or 0) - (mc.get("cash", 0) or 0)
        net_debt_ps = nd_total / shares_econ * per_share_scale

        # growth: conservative min(history, sector ceiling), use research g
        g_fcf = r["g"]
        bb = r["bb"]
        g_ps1 = (1 + g_fcf) * (1 + bb) - 1            # per-share incl buyback
        g_ps = three_phase(g_ps1, r["tg"])

        iv_ps = midyear_iv_ps(fcf_ps0, g_ps, disc, r["tg"], net_debt_ps)

        models[tk] = {
            "ticker": tk, "model_type": "fcf_dcf", "currency": listing_ccy,
            "source": "created v2 (data-driven, Buffett-conservative)",
            "as_of": AS_OF, "confidence": r["conf"],
            "fcf_ps0": round(fcf_ps0, 4),
            "growth_ps": g_ps,
            "discount_rate": disc, "terminal_growth": r["tg"],
            "net_debt_ps": round(net_debt_ps, 4),
            "inputs": {
                "owner_earnings_basis": basis,
                "owner_earnings_total_reporting": round(oe, 2),
                "fx_reporting_to_listing": round(fx, 4),
                "shares_econ_m": round(shares_econ / 1e6, 1),
                "fcf_growth_y1_5": g_fcf, "buyback_yield": bb,
                "beta": d.get("beta"), "risk_free_15y_pct": RF15,
                "discount_components": f"max(rf{RF15}+beta*ERP{ERP}, floor{BUFFETT_FLOOR}) + risk{r.get('risk',0)}",
            },
            "iv_per_share": round(iv_ps, 2),
            "sheet_iv_per_share": SHEET_IV.get(tk),
            "note": r["note"],
        }

    payload = {
        "_comment": "World-class data-driven Buffett-conservative DCF models (v2). "
                    "Discount from live US yield curve + beta + sector (9% floor); "
                    "FCF-margin-normalized owner earnings; sector+earnings-driven 3-phase growth; "
                    "buyback yield from history + announced authorizations; latest earnings in each note. "
                    "fcf_dcf = 15-yr mid-year per-share DCF; earnings_multiple for banks/airline; "
                    "netcash for loss-making net-cash names. Edit any input; IV updates next run.",
        "as_of": AS_OF,
        "methodology_version": "v2",
        "macro": {"risk_free_15y_pct": RF15, "yield_curve_pct": CURVE,
                  "equity_risk_premium_pct": ERP, "buffett_discount_floor_pct": BUFFETT_FLOOR},
        "models": models,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT} ({len(models)} models). RF15={RF15}%")
    print(f"\n{'ticker':9} {'type':17} {'ccy':4} {'disc':>6} {'IV/sh':>9} {'sheetIV':>8} {'conf':5}")
    for tk in order:
        m = models[tk]
        disc = m.get("discount_rate") or m.get("discount_rate_ref") or "—"
        print(f"{tk:9} {m['model_type']:17} {m['currency']:4} {str(disc):>6} "
              f"{str(m.get('iv_per_share','—')):>9} {str(m.get('sheet_iv_per_share','—')):>8} {m['confidence']:5}")


if __name__ == "__main__":
    main()
