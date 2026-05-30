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


def run_refresh():
    import update_sp500_dashboard as ud

    report = ud.update_dashboard()
    rows = ud.get_last_run_rows()
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
    return report, rows, assumptions


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


def build_alert_text(report, rows, run_meta):
    th = DEEP_DISCOUNT_THRESHOLD
    valued = [r for r in rows if r.get("discount") is not None]
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

    def line(r):
        star = " ⭐BRK" if r.get("brk_held") == "YES" else ""
        method = "P/B" if r.get("valuation_method") == "pb" else "DCF"
        return f"• {r['ticker']} {r['discount']*100:+.0f}% ({method}){star}"

    lines = [
        f"📊 <b>S&amp;P 500 IV update — {run_meta['session']}</b>",
        f"{run_meta['timestamp_et']}",
        "",
        f"{report.get('success','?')} full / {report.get('partial','?')} partial / "
        f"{report.get('no_data_kept','?')} no-data · {report.get('elapsed_seconds','?')}s",
        f"IV coverage {report.get('iv_coverage',0)*100:.0f}% · "
        f"overlay {report.get('sector_coverage',0)*100:.0f}%",
        "",
        f"<b>{len(undervalued)} names ≥ +{th*100:.0f}% undervalued</b>",
    ]
    if undervalued:
        lines += [line(r) for r in undervalued[:8]]
    if crossed:
        lines += ["", f"<b>Newly crossed +{th*100:.0f}% since last run:</b>"]
        lines += [line(r) for r in crossed[:8]]
    if brk_under:
        lines += ["", f"<b>BRK-held & undervalued:</b> " + ", ".join(r["ticker"] for r in brk_under[:10])]
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
    else:
        report, rows, assumptions = run_refresh()
        write_latest_json(rows, run_meta)
        write_assumptions_json(assumptions)
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

    # P3: Telegram alert with run summary + deep-value signals.
    if not args.no_telegram and not args.no_refresh:
        send_telegram(build_alert_text(report, rows, run_meta))


if __name__ == "__main__":
    main()
