#!/usr/bin/env python3
"""
SP500 dashboard update agent.

Single entry point invoked by OpenClaw twice every US trading day:
  * 09:00 America/New_York  (30 min before market open)
  * 15:30 America/New_York  (30 min before market close)

What this does, in order:
  1. Check that today is an NYSE trading day (skip + exit 0 otherwise).
  2. Run the existing update_sp500_dashboard.update_dashboard() pipeline
     (refreshes Portfolio.xlsx, preserves cols O-S per CLAUDE.md).
  3. Export per-ticker data to docs/data/latest.json so the static
     dashboard can re-run the DCF client-side when sliders move.
  4. Export current base-case assumptions + sector overlay to
     docs/data/assumptions.json.
  5. Append one row per ticker to docs/data/history.csv for the
     time-series chart.
  6. Render docs/index.html from the Jinja2 template.
  7. git add docs/ Portfolio.xlsx ; git commit ; git push.

CLI:
  python3 scripts/run_update_agent.py --session=pre-open
  python3 scripts/run_update_agent.py --session=pre-close
  python3 scripts/run_update_agent.py --force      # bypass market-day check
  python3 scripts/run_update_agent.py --no-push    # skip git commit/push
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
ASSETS_DIR = DOCS_DIR / "assets"
TEMPLATE_PATH = ASSETS_DIR / "index.html.j2"
LATEST_JSON = DATA_DIR / "latest.json"
ASSUMPTIONS_JSON = DATA_DIR / "assumptions.json"
HISTORY_CSV = DATA_DIR / "history.csv"
RUNS_CSV = DATA_DIR / "runs.csv"
INDEX_HTML = DOCS_DIR / "index.html"
PARITY_JS = REPO_ROOT / "scripts" / "parity_check.js"
PORTFOLIO_CONFIG = REPO_ROOT / "portfolio.json"
PORTFOLIO_JSON = DATA_DIR / "portfolio.json"
PORTFOLIO_MODELS_FILE = REPO_ROOT / "portfolio_models.json"

# Telegram (reuses the OpenClaw "default" bot). Override via env if desired.
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
TELEGRAM_CHAT_ID = os.environ.get("SP500_TELEGRAM_CHAT_ID", "148594943")
# A name is a "deep value" signal when IV exceeds market cap by this much.
DEEP_DISCOUNT_THRESHOLD = float(os.environ.get("SP500_DISCOUNT_THRESHOLD", "0.30"))

# Make update_sp500_dashboard importable.
sys.path.insert(0, str(REPO_ROOT))


# ── Dependency check ─────────────────────────────────────────────────────────
def ensure_deps():
    required = ["jinja2"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Installing missing deps: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", *missing]
        )


# ── NYSE trading-day calendar (self-contained) ───────────────────────────────
# Full-day market closures, 2026–2032. Extend as years roll forward. Source:
# NYSE annual calendar releases (https://www.nyse.com/markets/hours-calendars).
# Early-close days (1pm ET, e.g. day after Thanksgiving) are NOT here — those
# are still trading days for our purposes.
NYSE_HOLIDAYS = {
    # 2026
    dt.date(2026, 1, 1),    # New Year's Day
    dt.date(2026, 1, 19),   # MLK Day
    dt.date(2026, 2, 16),   # Presidents Day
    dt.date(2026, 4, 3),    # Good Friday
    dt.date(2026, 5, 25),   # Memorial Day
    dt.date(2026, 6, 19),   # Juneteenth
    dt.date(2026, 7, 3),    # Independence Day observed (Jul 4 is Saturday)
    dt.date(2026, 9, 7),    # Labor Day
    dt.date(2026, 11, 26),  # Thanksgiving
    dt.date(2026, 12, 25),  # Christmas
    # 2027
    dt.date(2027, 1, 1),
    dt.date(2027, 1, 18),
    dt.date(2027, 2, 15),
    dt.date(2027, 3, 26),
    dt.date(2027, 5, 31),
    dt.date(2027, 6, 18),   # Juneteenth observed (Jun 19 is Saturday)
    dt.date(2027, 7, 5),    # Independence Day observed (Jul 4 is Sunday)
    dt.date(2027, 9, 6),
    dt.date(2027, 11, 25),
    dt.date(2027, 12, 24),  # Christmas observed (Dec 25 is Saturday)
    # 2028
    dt.date(2028, 1, 17),   # MLK (Jan 1 is Saturday; no make-up day for NYE)
    dt.date(2028, 2, 21),
    dt.date(2028, 4, 14),
    dt.date(2028, 5, 29),
    dt.date(2028, 6, 19),
    dt.date(2028, 7, 4),
    dt.date(2028, 9, 4),
    dt.date(2028, 11, 23),
    dt.date(2028, 12, 25),
    # 2029
    dt.date(2029, 1, 1),
    dt.date(2029, 1, 15),
    dt.date(2029, 2, 19),
    dt.date(2029, 3, 30),
    dt.date(2029, 5, 28),
    dt.date(2029, 6, 19),
    dt.date(2029, 7, 4),
    dt.date(2029, 9, 3),
    dt.date(2029, 11, 22),
    dt.date(2029, 12, 25),
    # 2030
    dt.date(2030, 1, 1),
    dt.date(2030, 1, 21),
    dt.date(2030, 2, 18),
    dt.date(2030, 4, 19),
    dt.date(2030, 5, 27),
    dt.date(2030, 6, 19),
    dt.date(2030, 7, 4),
    dt.date(2030, 9, 2),
    dt.date(2030, 11, 28),
    dt.date(2030, 12, 25),
}


def is_nyse_trading_day(today_et: dt.date) -> bool:
    if today_et.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    if today_et in NYSE_HOLIDAYS:
        return False
    if today_et.year > 2030:
        # Refuse to silently assume; explicit error so we extend the table.
        raise RuntimeError(
            f"NYSE_HOLIDAYS not extended past 2030; got {today_et}. "
            "Add the next years' holidays in scripts/run_update_agent.py."
        )
    return True


def load_portfolio():
    """Read the personal watchlist from portfolio.json. Returns a list of
    {symbol, display}. Missing/invalid config → empty list (no portfolio)."""
    if not PORTFOLIO_CONFIG.exists():
        return []
    try:
        cfg = json.loads(PORTFOLIO_CONFIG.read_text())
        holdings = cfg.get("holdings", [])
        return [h for h in holdings if h.get("symbol")]
    except Exception as e:
        print(f"portfolio: could not read {PORTFOLIO_CONFIG.name}: {e}")
        return []


def attach_portfolio(rows):
    """Flag S&P rows that are in the portfolio, then fetch + append any
    holdings not in the S&P universe (foreign/ADR/ETF). Mutates and returns
    `rows`. Also returns the portfolio subset for the dedicated data file."""
    import update_sp500_dashboard as ud

    holdings = load_portfolio()
    if not holdings:
        return rows, []
    by_symbol = {h["symbol"]: h for h in holdings}
    models = ud.load_portfolio_models()

    present = set()
    for r in rows:
        h = by_symbol.get(r.get("ticker"))
        if h:
            r["in_portfolio"] = True
            r["portfolio_display"] = h.get("display") or r.get("ticker")
            # Override the generic-engine IV with the per-company model.
            ud.apply_model_to_row(r, models.get(r["ticker"]))
            present.add(r["ticker"])

    missing = [h for h in holdings if h["symbol"] not in present]
    if missing:
        print(f"portfolio: fetching {len(missing)} non-S&P holdings: "
              f"{', '.join(h['symbol'] for h in missing)}")
        extra, errs = ud.fetch_portfolio_rows(missing, models=models)
        for r in extra:
            r["portfolio_display"] = r.get("company")
        if errs:
            print(f"portfolio: fetch errors: {errs}")
        rows.extend(extra)
    n_model = sum(1 for r in rows if r.get("in_portfolio") and r.get("valuation_method") == "model")
    print(f"portfolio: {n_model} holdings valued via per-company model")

    portfolio_rows = [r for r in rows if r.get("in_portfolio")]
    # Preserve the user's configured order.
    order = {h["symbol"]: i for i, h in enumerate(holdings)}
    portfolio_rows.sort(key=lambda r: order.get(r["ticker"], 999))
    return rows, portfolio_rows


def write_portfolio_json(portfolio_rows, run_meta):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_JSON.write_text(json.dumps({
        "generated_at_et": run_meta["timestamp_et"],
        "session": run_meta["session"],
        "holdings": portfolio_rows,
    }, indent=2, default=str))


def run_refresh():
    import update_sp500_dashboard as ud

    report = ud.update_dashboard()
    rows = ud.get_last_run_rows()
    rows, portfolio_rows = attach_portfolio(rows)
    report["portfolio_count"] = len(portfolio_rows)
    report["portfolio_valued"] = sum(1 for r in portfolio_rows if r.get("iv_b") is not None)
    assumptions = {
        "version": ud.GEO_VERSION,
        "narrative": ud.GEO_NARRATIVE,
        "base": {
            "discount_premium": ud.GEO_BASE_DISCOUNT_PREMIUM,
            "growth_haircut": ud.GEO_BASE_GROWTH_HAIRCUT,
            "mos_add": ud.GEO_BASE_MOS_ADD,
            "discount_rate": ud.DCF_DISCOUNT_RATE,
            "terminal_growth": ud.DCF_TERMINAL_GROWTH,
            "projection_years": ud.DCF_PROJECTION_YEARS,
            "margin_of_safety": ud.DCF_MARGIN_OF_SAFETY,
        },
        # JSON dicts preserve insertion order in Python 3.7+; the JS does
        # first-substring-match using the same iteration order.
        "sectors": {k: list(v) for k, v in ud.SECTOR_GEO_ADJUSTMENTS.items()},
        "macro": ud.MACRO_SNAPSHOT,
        "slider_guidance": ud.SLIDER_GUIDANCE,
        "dcf_input_notes": ud.DCF_INPUT_NOTES,
    }
    # Surface the portfolio-model macro (live yield curve etc.) for the header.
    try:
        pm = json.loads(PORTFOLIO_MODELS_FILE.read_text())
        assumptions["macro_curve"] = pm.get("macro")
        assumptions["models_methodology"] = pm.get("methodology_version")
    except Exception:
        pass
    return report, rows, assumptions, portfolio_rows


def write_latest_json(rows, run_meta):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": run_meta["timestamp_utc"],
        "generated_at_et": run_meta["timestamp_et"],
        "session": run_meta["session"],
        "tickers": rows,
    }
    LATEST_JSON.write_text(json.dumps(payload, indent=2, default=str))


def write_assumptions_json(assumptions):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSUMPTIONS_JSON.write_text(json.dumps(assumptions, indent=2))


def append_history(rows, run_meta):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not HISTORY_CSV.exists()
    with HISTORY_CSV.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow([
                "timestamp_utc", "timestamp_et", "session",
                "ticker", "price", "iv_b", "mcap_b", "discount_pct",
            ])
        for r in rows:
            if r.get("price") is None and r.get("iv_b") is None:
                continue
            w.writerow([
                run_meta["timestamp_utc"],
                run_meta["timestamp_et"],
                run_meta["session"],
                r.get("ticker"),
                r.get("price"),
                r.get("iv_b"),
                r.get("mcap_b"),
                r.get("discount"),
            ])


def render_index(rows, assumptions, run_meta, report):
    from jinja2 import Template

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"Missing dashboard template: {TEMPLATE_PATH}. "
            "It is committed to the repo at docs/assets/index.html.j2."
        )
    template = Template(TEMPLATE_PATH.read_text())
    session_labels = {
        "pre-open": "Pre-open snapshot",
        "pre-close": "Pre-close snapshot",
        "manual": "Manual run",
    }
    rendered = template.render(
        generated_at_et=run_meta["timestamp_et"],
        session_label=session_labels.get(run_meta["session"], run_meta["session"]),
        assumptions=assumptions,
        # JSON-embedded so the page works whether served from GitHub Pages
        # or opened as a local file (file:// blocks fetch()).
        latest_json=json.dumps(
            {"generated_at_et": run_meta["timestamp_et"], "session": run_meta["session"], "tickers": rows},
            default=str,
        ),
        assumptions_json=json.dumps(assumptions),
        report=report,
    )
    INDEX_HTML.write_text(rendered)


def git_commit_and_push(session_label: str, et_ts: str, push: bool):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )

    git("add", "docs", "Portfolio.xlsx")
    status = git("status", "--porcelain")
    if not status.stdout.strip():
        print("No changes to commit.")
        return
    msg = f"auto: update {session_label} {et_ts}"
    commit = git("commit", "-m", msg)
    if commit.returncode != 0:
        print(f"git commit failed: {commit.stderr.strip()}")
        return
    print(f"Committed: {msg}")
    if not push:
        print("--no-push: skipping git push")
        return
    push_result = git("push")
    if push_result.returncode != 0:
        print(f"git push failed: {push_result.stderr.strip()}")
    else:
        print("Pushed to origin.")


# ── P2c: run-health log ──────────────────────────────────────────────────────
def append_runs_csv(report, run_meta):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not RUNS_CSV.exists()
    with RUNS_CSV.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow([
                "timestamp_utc", "timestamp_et", "session", "elapsed_s",
                "full", "partial", "no_data_kept", "errors",
                "iv_coverage", "sector_coverage",
            ])
        w.writerow([
            run_meta["timestamp_utc"], run_meta["timestamp_et"], run_meta["session"],
            report.get("elapsed_seconds"), report.get("success"), report.get("partial"),
            report.get("no_data_kept"), report.get("errors_count"),
            report.get("iv_coverage"), report.get("sector_coverage"),
        ])


# ── P2b: runtime JS↔Python parity gate ───────────────────────────────────────
def run_parity_check():
    """Recompute IV in dashboard.js for every ticker and compare to Python's
    iv_b in latest.json. Returns (ok, message). Skips (ok=True) if node is
    missing — the gate should never block a refresh just because node isn't
    installed, but it WILL block on a real numeric drift."""
    node = shutil.which("node")
    if not node:
        return True, "node not found — parity gate skipped (install node to enable)"
    if not PARITY_JS.exists():
        return True, f"{PARITY_JS.name} missing — parity gate skipped"
    proc = subprocess.run(
        [node, str(PARITY_JS), str(LATEST_JSON), str(ASSUMPTIONS_JSON)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return False, f"PARITY FAIL: {out or proc.stderr.strip()}"
    return True, out or "parity ok"


# ── P3: Telegram notifier (reuses OpenClaw default bot) ──────────────────────
def _telegram_token():
    tok = os.environ.get("SP500_TELEGRAM_TOKEN")
    if tok:
        return tok
    if OPENCLAW_CONFIG.exists():
        try:
            d = json.loads(OPENCLAW_CONFIG.read_text())
            return d["channels"]["telegram"]["accounts"]["default"]["botToken"]
        except Exception:
            return None
    return None


def send_telegram(text, silent=False):
    import urllib.request
    import urllib.parse

    token = _telegram_token()
    if not token or not TELEGRAM_CHAT_ID:
        print("telegram: no token/chat_id — skipping")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
        "disable_notification": "true" if silent else "false",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = json.loads(resp.read().decode()).get("ok", False)
            print(f"telegram: sent (ok={ok})")
            return ok
    except Exception as e:
        print(f"telegram: failed: {e}")
        return False


def _prior_discounts():
    """Map ticker -> discount from the most recent PRIOR snapshot in history.csv
    (the run before the one we just appended). Used for crossing detection."""
    if not HISTORY_CSV.exists():
        return {}
    import collections
    by_ts = collections.OrderedDict()
    with HISTORY_CSV.open() as f:
        for row in csv.DictReader(f):
            by_ts.setdefault(row["timestamp_utc"], []).append(row)
    timestamps = list(by_ts.keys())
    if len(timestamps) < 2:
        return {}
    prior_rows = by_ts[timestamps[-2]]  # -1 is the run we just wrote
    out = {}
    for r in prior_rows:
        try:
            out[r["ticker"]] = float(r["discount_pct"]) if r["discount_pct"] else None
        except ValueError:
            out[r["ticker"]] = None
    return out


def _prior_snapshot():
    """Map ticker -> {price, iv_b, discount} from the most recent PRIOR run in
    history.csv (the run before the one we just appended). Used to compute the
    'revised valuations' section — what changed since last update."""
    if not HISTORY_CSV.exists():
        return {}
    import collections
    by_ts = collections.OrderedDict()
    with HISTORY_CSV.open() as f:
        for row in csv.DictReader(f):
            by_ts.setdefault(row["timestamp_utc"], []).append(row)
    timestamps = list(by_ts.keys())
    if len(timestamps) < 2:
        return {}
    out = {}
    for r in by_ts[timestamps[-2]]:
        def f(x):
            try:
                return float(x) if x not in (None, "") else None
            except ValueError:
                return None
        out[r["ticker"]] = {"price": f(r.get("price")), "iv_b": f(r.get("iv_b")),
                            "discount": f(r.get("discount_pct"))}
    return out


def fetch_news(tickers, max_items=1):
    """Best-effort: latest headline(s) per ticker via yfinance. Never raises —
    a news outage must not block the digest. Returns {ticker: [titles]}."""
    out = {}
    try:
        import yfinance as yf
    except Exception:
        return out
    for tk in tickers:
        try:
            items = yf.Ticker(tk).news or []
            titles = []
            for it in items[:max_items * 3]:
                # yfinance shapes vary: flat {'title':..} or {'content':{'title':..}}
                title = it.get("title") or (it.get("content") or {}).get("title")
                if title:
                    titles.append(title.strip())
                if len(titles) >= max_items:
                    break
            if titles:
                out[tk] = titles
        except Exception:
            continue
    return out


def build_alert_text(report, rows, run_meta, with_news=True, portfolio_rows=None):
    """Short digest: leads with the personal portfolio (IV vs price per holding,
    + change since last run), then market-wide undervalued names, newly-crossed
    signals, biggest revised valuations, and a headline for the top movers."""
    th = DEEP_DISCOUNT_THRESHOLD
    # Market-wide sections scan S&P constituents only; non-S&P portfolio extras
    # (foreign/ADR/ETF, tagged sp500=False) appear in the portfolio block above.
    sp_rows = [r for r in rows if r.get("sp500", True)]
    valued = [r for r in sp_rows if r.get("discount") is not None]
    undervalued = sorted(
        [r for r in valued if r["discount"] >= th],
        key=lambda r: r["discount"], reverse=True,
    )
    prior = _prior_discounts()
    crossed = [
        r for r in undervalued
        if prior.get(r["ticker"]) is not None and prior[r["ticker"]] < th
    ]
    brk_under = [r for r in undervalued if r.get("brk_held") == "YES"]

    # ── Revised valuations: biggest discount swing vs the prior run ──────────
    prior_snap = _prior_snapshot()
    revisions = []
    for r in valued:
        p = prior_snap.get(r["ticker"])
        if not p or p.get("discount") is None:
            continue
        delta = r["discount"] - p["discount"]          # change in discount (fraction)
        if abs(delta) >= 0.05:                          # ≥ 5 percentage points
            revisions.append((abs(delta), delta, r, p))
    revisions.sort(reverse=True, key=lambda x: x[0])
    top_revisions = revisions[:6]

    def line(r):
        star = " ⭐BRK" if r.get("brk_held") == "YES" else ""
        method = "P/B" if r.get("valuation_method") == "pb" else "DCF"
        return f"• {r['ticker']} {r['discount']*100:+.0f}% ({method}){star}"

    def rev_line(delta, r, p):
        arrow = "▲" if delta > 0 else "▼"
        return (f"• {r['ticker']} {arrow} disc {p['discount']*100:+.0f}% → "
                f"{r['discount']*100:+.0f}% ({delta*100:+.0f}pp)")

    lines = [
        f"📊 <b>S&amp;P 500 IV update — {run_meta['session']}</b>",
        f"{run_meta['timestamp_et']}",
    ]

    # ── Lead with the personal portfolio ─────────────────────────────────────
    if portfolio_rows is None:
        portfolio_rows = [r for r in rows if r.get("in_portfolio")]
    if portfolio_rows:
        lines += ["", "<b>📁 Your portfolio — IV vs price</b>"]
        def method_tag(r):
            vm = r.get("valuation_method")
            if vm == "model":
                return "model·EPS×PE" if r.get("model_type") == "earnings_multiple" else "model·DCF"
            if vm == "etf":
                return "ETF"
            if vm == "pb":
                return "P/B"
            return "DCF"
        for r in portfolio_rows:
            tk = r.get("portfolio_display") or r.get("ticker")
            d = r.get("discount")
            if d is None:
                lines.append(f"• {tk}: price-only (no model)")
                continue
            tag = ""
            ps = prior_snap.get(r["ticker"])
            if ps and ps.get("discount") is not None:
                dd = d - ps["discount"]
                if abs(dd) >= 0.01:
                    tag = f" ({'▲' if dd>0 else '▼'}{abs(dd)*100:.0f}pp)"
            verdict = "BUY" if d > 0.25 else "FAIR" if d > -0.10 else "overvalued"
            conf = r.get("model_confidence")
            cflag = f" ⚠{conf}" if conf in ("low",) else ""
            lines.append(f"• {tk}: <b>{d*100:+.0f}%</b>{tag} {verdict} [{method_tag(r)}{cflag}]")

    lines += [
        "",
        f"{report.get('success','?')} full / {report.get('partial','?')} partial / "
        f"{report.get('no_data_kept','?')} no-data · {report.get('elapsed_seconds','?')}s",
        f"IV coverage {report.get('iv_coverage',0)*100:.0f}% · "
        f"overlay {report.get('sector_coverage',0)*100:.0f}%",
        "",
        f"<b>{len(undervalued)} S&amp;P names ≥ +{th*100:.0f}% undervalued</b>",
    ]
    if undervalued:
        lines += [line(r) for r in undervalued[:6]]
    if crossed:
        lines += ["", f"<b>Newly crossed +{th*100:.0f}% since last run:</b>"]
        lines += [line(r) for r in crossed[:6]]
    if brk_under:
        lines += ["", "<b>BRK-held &amp; undervalued:</b> " + ", ".join(r["ticker"] for r in brk_under[:10])]

    if top_revisions:
        lines += ["", "<b>Biggest revised valuations vs last run:</b>"]
        lines += [rev_line(d, r, p) for _, d, r, p in top_revisions]

    # ── Major impacting news for the top movers (best-effort) ────────────────
    if with_news and top_revisions:
        movers = [r["ticker"] for _, _, r, _ in top_revisions[:4]]
        news = fetch_news(movers, max_items=1)
        if news:
            lines += ["", "<b>News on movers:</b>"]
            for tk in movers:
                if tk in news:
                    headline = news[tk][0]
                    if len(headline) > 90:
                        headline = headline[:87] + "…"
                    lines.append(f"• <b>{tk}</b>: {headline}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", choices=["pre-open", "pre-close", "manual"], default="manual")
    parser.add_argument("--force", action="store_true", help="bypass NYSE trading-day check")
    parser.add_argument("--no-push", action="store_true", help="skip git commit/push")
    parser.add_argument("--no-telegram", action="store_true", help="skip Telegram alert")
    parser.add_argument("--no-refresh", action="store_true",
                        help="re-render dashboard only, do not call yfinance (debug)")
    args = parser.parse_args()

    # Crash safety net: any unhandled failure still pings Telegram so a silent
    # dead agent is impossible. Re-raise so OpenClaw records the error too.
    try:
        _run(args)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        if not args.no_telegram:
            send_telegram(f"🛑 <b>SP500 agent crashed</b> ({args.session})\n<code>{type(e).__name__}: {e}</code>")
        raise


def _run(args):
    ensure_deps()

    et = ZoneInfo("America/New_York")
    now_et = dt.datetime.now(et)
    today_et = now_et.date()

    if not args.force:
        if not is_nyse_trading_day(today_et):
            print(f"skipped: {today_et} is not an NYSE trading day")
            sys.exit(0)

    run_meta = {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "session": args.session,
    }

    if args.no_refresh:
        # Allow dashboard re-rendering during local development without
        # waiting for the 3-4 minute yfinance pull.
        if not LATEST_JSON.exists():
            print("--no-refresh requires a prior latest.json; run without it first.")
            sys.exit(1)
        cached = json.loads(LATEST_JSON.read_text())
        rows = cached["tickers"]
        assumptions = json.loads(ASSUMPTIONS_JSON.read_text()) if ASSUMPTIONS_JSON.exists() else {}
        report = {}
        portfolio_rows = [r for r in rows if r.get("in_portfolio")]
    else:
        report, rows, assumptions, portfolio_rows = run_refresh()
        write_latest_json(rows, run_meta)
        write_assumptions_json(assumptions)
        write_portfolio_json(portfolio_rows, run_meta)
        append_history(rows, run_meta)
        append_runs_csv(report, run_meta)

    render_index(rows, assumptions, run_meta, report)
    print(f"Wrote dashboard: {INDEX_HTML}")

    # P2b: parity gate. A real JS↔Python drift blocks the commit/push so a
    # broken dashboard never ships; node-missing only warns.
    ok, parity_msg = run_parity_check()
    print(f"parity: {parity_msg}")
    if not ok:
        print("ABORTING commit/push due to parity failure.")
        if not args.no_telegram:
            send_telegram(
                f"⚠️ SP500 agent: parity gate FAILED, dashboard NOT pushed.\n{parity_msg}",
                silent=False,
            )
        sys.exit(1)

    git_commit_and_push(args.session, run_meta["timestamp_et"], push=not args.no_push)

    # P3: Telegram alert — leads with the portfolio, then market-wide signals.
    if not args.no_telegram and not args.no_refresh:
        send_telegram(build_alert_text(report, rows, run_meta, portfolio_rows=portfolio_rows))


if __name__ == "__main__":
    main()
