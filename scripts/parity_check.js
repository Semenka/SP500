// Runtime JS↔Python parity gate.
//
// Loads docs/assets/dashboard.js, recomputes intrinsic value for every ticker
// in latest.json at BASE assumptions (no slider offsets), and compares against
// the iv_b that Python wrote. Exits non-zero if the max relative error exceeds
// the threshold — run_update_agent.py uses that to block a commit/push, so a
// drift between the two DCF/P-B implementations can never ship silently.
//
// Usage: node scripts/parity_check.js <latest.json> <assumptions.json>

const fs = require('fs');
const path = require('path');

const THRESHOLD = 0.001; // 0.1%

const latestPath = process.argv[2];
const assumptionsPath = process.argv[3];
if (!latestPath || !assumptionsPath) {
  console.error('usage: node parity_check.js <latest.json> <assumptions.json>');
  process.exit(2);
}

const latest = JSON.parse(fs.readFileSync(latestPath, 'utf8'));
const assumptions = JSON.parse(fs.readFileSync(assumptionsPath, 'utf8'));
const tickers = latest.tickers || [];

// Minimal browser-global shims so dashboard.js's IIFE can load under node
// without a DOM. We only need the exported math (window.SP500_DCF).
global.window = {};
global.document = { addEventListener: () => {} };
global.fetch = () => Promise.reject(new Error('no fetch in parity check'));
global.Plotly = { react: () => {} };
window.SP500_ASSUMPTIONS = assumptions;
window.SP500_LATEST = { tickers: [] };

const dashboardJs = path.join(__dirname, '..', 'docs', 'assets', 'dashboard.js');
eval(fs.readFileSync(dashboardJs, 'utf8'));

const { computeIV } = window.SP500_DCF;
const baseSliders = {
  baseDiscountPremium: assumptions.base.discount_premium,
  baseGrowthHaircut: assumptions.base.growth_haircut,
  baseMOSAdd: assumptions.base.mos_add,
  globalGrowthShift: 0,
};

let maxRel = 0;
let worst = null;
let nullMismatch = 0;
let compared = 0;

for (const t of tickers) {
  const ivJs = computeIV(t, baseSliders); // raw $
  const ivJsB = ivJs != null ? ivJs / 1e9 : null;
  const ivPyB = t.iv_b != null ? t.iv_b : null;

  if (ivPyB == null && ivJsB == null) continue;
  if ((ivPyB == null) !== (ivJsB == null)) {
    nullMismatch++;
    if (!worst) worst = { ticker: t.ticker, ivPyB, ivJsB, method: t.valuation_method };
    continue;
  }
  const rel = Math.abs(ivJsB - ivPyB) / Math.max(Math.abs(ivPyB), 1e-9);
  compared++;
  if (rel > maxRel) {
    maxRel = rel;
    worst = { ticker: t.ticker, ivPyB, ivJsB, rel, method: t.valuation_method };
  }
}

const summary = `compared=${compared} nullMismatch=${nullMismatch} maxRelErr=${(maxRel * 100).toExponential(2)}`;
if (nullMismatch > 0 || maxRel > THRESHOLD) {
  console.error(`FAIL ${summary} worst=${JSON.stringify(worst)}`);
  process.exit(1);
}
console.log(`OK ${summary}`);
