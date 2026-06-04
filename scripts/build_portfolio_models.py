#!/usr/bin/env python3
"""One-off: build portfolio_models.json from (a) the 7 DCF models in the user's
Google Sheet and (b) 10 newly-created conservative Buffett-style models.

Two model types, both conservative Buffett style:
  - fcf_dcf: 15-yr mid-year per-share owner-earnings DCF with 3-phase growth
    (per-share growth already includes the buyback yield). Reproduces the
    sheet's published intrinsic values within <0.5%.
  - earnings_multiple: normalized EPS x justified P/E (+ net cash per share).
    Used for banks/financials and high-leverage names where a 15-yr FCF DCF
    with full net-debt subtraction is the wrong tool (it over/understates).

All inputs are PER SHARE in the listing/price currency, so the daily agent
needs no FX feed and upside = (IV/share - live price) / live price is a clean
same-currency ratio. Edit any spec and the IV updates on the next run.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "portfolio_models.json"
AS_OF = "2026-06-05"


def midyear_iv_ps(fcf_ps0, g1, g2, g3, r, tg, net_debt_ps):
    fcf = fcf_ps0
    pv = 0.0
    for n in range(1, 16):
        g = g1 if n <= 5 else g2 if n <= 10 else g3
        fcf *= (1 + g)
        pv += fcf / (1 + r) ** (n - 0.5)
    tv = fcf * (1 + tg) / (r - tg)
    pv_tv = tv / (1 + r) ** (15 - 0.5)
    return pv + pv_tv - net_debt_ps


def gps(g, b):
    return (1 + g) * (1 + b) - 1


# ── 7 models from the Google Sheet (Portfolio DCF tab) ───────────────────────
# Stored with the sheet's per-share growth bands so the engine reproduces the
# published intrinsic value within <0.5%.
SHEET = {
    # ticker: (fcf_ps0, g_ps1, g_ps2, g_ps3, r, tg, net_debt_ps, published, ccy)
    "DPZ":     (17.86, .087, .077, .066, .09, .025, 142.38, 363.68, "USD"),
    "UNH":     (26.43, .082, .071, .061, .09, .025,  88.09, 621.36, "USD"),
    "1810.HK": (1.51,  .120, .080, .050, .11, .030,  -4.31,  40.46, "HKD"),
    "LEN":     (6.06,  .094, .083, .073, .10, .020,   8.48, 143.21, "USD"),
    "POOL":    (10.35, .082, .082, .072, .09, .025,  32.02, 266.54, "USD"),
    "TTE":     (6.10,  .067, .057, .047, .09, .000,   9.48, 113.06, "USD"),
    "NVDA":    (3.91,  .202, .100, .059, .10, .030,  -1.56, 166.42, "USD"),
}

# ── 10 created models (conservative Buffett style, this session) ─────────────
# fcf_dcf entries: norm_oe_total($B/localB), shares_M, net_debt_total,
#   fcf_growth(3), buyback, r, tg, ccy, confidence, note
FCF_DCF = {
    "GOOGL": (72.0, 12121.0, -31.0, (.08, .06, .04), .025, .09, .025, "USD", "med",
              "FCF ~$72B (capex-heavy AI build); 8/6/4% growth, 9% Buffett hurdle. Conservative hurdle makes a 62x-FCF entry look rich."),
    "OXY":   (5.0, 994.6, 12.8, (.02, .01, .00), .01, .10, .00, "USD", "med",
              "Cycle-avg FCF ~$5B (ex-2022 spike); energy = no-growth conservative, 0% terminal, like the sheet's TTE."),
    "CRCL":  (0.45, 229.9, -1.5, (.12, .07, .03), .00, .12, .03, "USD", "low",
              "Recent IPO; FCF tied to interest on USDC reserves (rate-sensitive). 12% discount for risk. Speculative."),
    "BABA":  (13.0, 2399.1, -35.0, (.05, .04, .03), .02, .11, .03, "USD", "med",
              "Normalized owner-earnings ~$13B USD (reported FCF depressed by AI capex surge; NI ~$14B). ~$35B net cash. 11% for China risk. Per ADS."),
    "BIDU":  (2.5, 274.8, -22.75, (.04, .03, .02), .01, .12, .02, "USD", "med",
              "Core search earnings ~$2.5B; ~$23B net cash dominates value (cash-rich, trades near cash + iQiyi). 12% China discount. Per ADS."),
    "WISE.L": (0.45, 1000.3, -1.28, (.12, .08, .05), .00, .11, .03, "GBP_pence", "med",
               "Owner-earnings ~£0.45B (use NI, not float-inflated OCF). High-growth fintech 12/8/5%. Price in pence; IV converted GBP->pence."),
    "DOYU":  (0.00, 30.2, -0.25, (.00, .00, .00), .00, .12, .00, "USD", "low",
              "Loss-making micro-cap, no earnings power → IV is essentially a net-cash floor (heavily haircut for China trapped-cash risk + unreliable reported cash). A classic net-net; low confidence."),
}

# earnings_multiple entries: norm_eps, target_pe, net_cash_ps, ccy, confidence, note
EARN_MULT = {
    "AAL": (1.00, 8.0, 0.0, "USD", "low",
            "Airline: a 15-yr FCF DCF with full $27B net-debt subtraction implies ~zero equity. Use normalized mid-cycle EPS x 8 (cyclical, leveraged). Equity is partly a deleveraging option."),
    "SYF": (10.00, 9.0, 0.0, "USD", "med",
            "Credit-card lender: banks must retain capital to grow, so an FCF-DCF-with-buyback overstates. Normalized EPS ~$10 x 9 (subprime-tilted, cyclical credit)."),
    "USB": (4.19, 11.0, 0.0, "USD", "med",
            "Quality regional bank, ~14% ROE. Normalized EPS $4.19 x 11 conservative multiple. (FCF-DCF inappropriate for banks.)"),
}

# Non-modelable
SPECIAL = {
    "VOO": {"ticker": "VOO", "model_type": "none", "currency": "USD",
            "source": "n/a", "as_of": AS_OF, "confidence": "n/a",
            "note": "S&P 500 ETF — price ~ NAV by construction; no single-name DCF. Treated as fairly valued (market = intrinsic)."},
}


def main():
    models = {}

    for tk, (f, g1, g2, g3, r, tg, nd, pub, ccy) in SHEET.items():
        iv = midyear_iv_ps(f, g1, g2, g3, r, tg, nd)
        models[tk] = {
            "ticker": tk, "model_type": "fcf_dcf", "currency": ccy,
            "source": "google-sheet:Portfolio DCF (Buffett framework)",
            "as_of": AS_OF, "confidence": "user",
            "fcf_ps0": round(f, 4),
            "growth_ps": [g1, g2, g3],
            "discount_rate": r, "terminal_growth": tg,
            "net_debt_ps": round(nd, 4),
            "iv_per_share": round(iv, 2),
            "published_iv_per_share": pub,
            "note": "User's own model from the Google Sheet.",
        }

    for tk, (oe, sh, nd, (g1, g2, g3), b, r, tg, ccy, conf, note) in FCF_DCF.items():
        scale = 100.0 if ccy == "GBP_pence" else 1.0  # GBP -> pence per share
        fcf_ps0 = oe * 1000.0 / sh * scale
        nd_ps = nd * 1000.0 / sh * scale
        gp = [gps(g1, b), gps(g2, b), gps(g3, b)]
        iv = midyear_iv_ps(fcf_ps0, gp[0], gp[1], gp[2], r, tg, nd_ps)
        models[tk] = {
            "ticker": tk, "model_type": "fcf_dcf", "currency": ccy,
            "source": "created (conservative Buffett style)",
            "as_of": AS_OF, "confidence": conf,
            "fcf_ps0": round(fcf_ps0, 4),
            "growth_ps": [round(x, 5) for x in gp],
            "discount_rate": r, "terminal_growth": tg,
            "net_debt_ps": round(nd_ps, 4),
            "inputs": {"normalized_owner_earnings_total": oe, "shares_m": sh,
                       "net_debt_total": nd, "fcf_growth": [g1, g2, g3],
                       "buyback_yield": b},
            "iv_per_share": round(iv, 2),
            "note": note,
        }

    for tk, (eps, pe, ncps, ccy, conf, note) in EARN_MULT.items():
        iv = eps * pe + ncps
        models[tk] = {
            "ticker": tk, "model_type": "earnings_multiple", "currency": ccy,
            "source": "created (conservative Buffett style)",
            "as_of": AS_OF, "confidence": conf,
            "normalized_eps": eps, "target_pe": pe, "net_cash_ps": ncps,
            "iv_per_share": round(iv, 2),
            "note": note,
        }

    for tk, spec in SPECIAL.items():
        models[tk] = spec

    payload = {
        "_comment": "Per-company intrinsic-value models for the portfolio. fcf_dcf = 15-yr mid-year per-share owner-earnings DCF (conservative Buffett). earnings_multiple = normalized EPS x justified P/E (for banks / high-leverage). All per-share inputs in the listing/price currency. The daily agent recomputes iv_per_share from these inputs and compares to the live price. Edit freely.",
        "as_of": AS_OF,
        "models": models,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT} with {len(models)} models")
    # Review table
    print(f"\n{'ticker':9} {'type':18} {'ccy':10} {'IV/sh':>10} {'conf':5} source")
    for tk, m in models.items():
        print(f"{tk:9} {m['model_type']:18} {m['currency']:10} "
              f"{str(m.get('iv_per_share','—')):>10} {m['confidence']:5} {m['source'][:32]}")


if __name__ == "__main__":
    main()
