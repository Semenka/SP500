// Client-side recompute of the Buffett-style intrinsic value used by the
// scheduled Python pipeline. The math here must stay byte-for-byte parallel
// to update_sp500_dashboard.buffett_intrinsic_value() — a parity check in
// scripts/run_update_agent.py asserts max-relative-error < 0.1% across all
// tickers before pushing.
//
// Globals injected by docs/index.html via the Jinja template:
//   window.SP500_LATEST       — { generated_at_et, session, tickers: [...] }
//   window.SP500_ASSUMPTIONS  — { version, narrative, base, sectors }

(function () {
  const A = window.SP500_ASSUMPTIONS;
  const tickers = window.SP500_LATEST.tickers.slice();

  const state = {
    sliders: {
      baseDiscountPremium: A.base.discount_premium,
      baseGrowthHaircut: A.base.growth_haircut,
      baseMOSAdd: A.base.mos_add,
      globalGrowthShift: 0,
    },
    sectorFilter: 'All',
    brkOnly: false,
    searchText: '',
    minDiscount: null,
  };

  // ── DCF port ───────────────────────────────────────────────────────────
  function sectorAdj(sectorKey) {
    if (!sectorKey || !A.sectors[sectorKey]) return [0, 0, 0];
    return A.sectors[sectorKey];
  }

  function buffettIV(t, sliders) {
    const fcf = t.fcf;
    if (fcf == null || isNaN(fcf) || fcf <= 0) return null;
    let growth = (t.rev_growth == null || isNaN(t.rev_growth)) ? 0.03 : t.rev_growth;
    growth += sliders.globalGrowthShift;
    const cash = Math.max(t.cash || 0, 0);

    const sec = sectorAdj(t.sector_key);
    const geoDr = sliders.baseDiscountPremium + sec[0];
    const geoG = sliders.baseGrowthHaircut + sec[1];
    const geoMos = sliders.baseMOSAdd + sec[2];

    const adjDiscount = A.base.discount_rate + geoDr;
    const adjMos = A.base.margin_of_safety + geoMos;
    growth += geoG;

    const gStart = Math.min(Math.max(growth, -0.05), 0.12);
    const gTerm = A.base.terminal_growth;
    const years = A.base.projection_years;

    let dcfSum = 0;
    let projFcf = fcf;
    for (let yr = 1; yr <= years; yr++) {
      let g;
      if (yr <= 5) g = gStart;
      else if (yr <= 10) {
        const fade = (yr - 5) / 5.0;
        g = gStart * (1 - fade) + gTerm * fade;
      } else g = gTerm;
      projFcf *= 1 + g;
      dcfSum += projFcf / Math.pow(1 + adjDiscount, yr);
    }
    const terminalFcf = projFcf * (1 + gTerm);
    const terminalVal = terminalFcf / (adjDiscount - gTerm);
    const terminalPv = terminalVal / Math.pow(1 + adjDiscount, years);
    return (dcfSum + terminalPv + cash) * (1 - adjMos);
  }

  // Justified-P/B excess-return model for financials & REITs. Mirrors
  // update_sp500_dashboard.financial_intrinsic_value(). Note: the global
  // growth-shift slider does NOT apply here (Python has no such param for
  // financials) — keep it that way to preserve parity.
  function financialIV(t, sliders) {
    const bvps = t.bvps;
    const shares = t.shares_out;
    const roe0 = t.roe;
    if (bvps == null || shares == null || roe0 == null || isNaN(bvps) || isNaN(roe0)) return null;
    const bookEquity = bvps * shares;
    if (bookEquity <= 0) return null;

    const sec = sectorAdj(t.sector_key);
    const geoDr = sliders.baseDiscountPremium + sec[0];
    const geoG = sliders.baseGrowthHaircut + sec[1];
    const geoMos = sliders.baseMOSAdd + sec[2];

    let r = A.base.discount_rate + geoDr;
    let g = Math.max(A.base.terminal_growth + geoG, 0);
    if (r - g < 0.02) r = g + 0.02;
    const roe = Math.min(Math.max(roe0, -0.05), 0.30);
    const adjMos = A.base.margin_of_safety + geoMos;

    let fairPb = (roe - g) / (r - g);
    fairPb = Math.min(Math.max(fairPb, 0.2), 4.0);
    return fairPb * bookEquity * (1 - adjMos);
  }

  function computeIV(t, sliders) {
    // Portfolio holdings valued by a per-company model carry a fixed intrinsic
    // value (from portfolio_models.json) — a fundamental DCF/multiple should
    // not move when you drag a macro slider, so return the stored IV as-is.
    if (t.valuation_method === 'model') return t.iv_b != null ? t.iv_b * 1e9 : null;
    if (t.valuation_method === 'etf') return null;
    return t.valuation_method === 'pb' ? financialIV(t, sliders) : buffettIV(t, sliders);
  }

  function recompute() {
    for (const t of tickers) {
      const iv = computeIV(t, state.sliders);
      t.iv_b_computed = iv != null ? iv / 1e9 : null;
      if (t.iv_b_computed != null && t.mcap_b && t.mcap_b > 0) {
        t.discount_computed = (t.iv_b_computed - t.mcap_b) / t.mcap_b;
      } else {
        t.discount_computed = null;
      }
    }
    render();
  }

  function getFiltered() {
    const q = state.searchText.toLowerCase();
    return tickers.filter((t) => {
      if (state.sectorFilter !== 'All' && (t.sector_key || '') !== state.sectorFilter) return false;
      if (state.brkOnly && t.brk_held !== 'YES') return false;
      if (q) {
        const hay = ((t.ticker || '') + ' ' + (t.company || '')).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (state.minDiscount != null && (t.discount_computed == null || t.discount_computed * 100 < state.minDiscount)) return false;
      return true;
    });
  }

  // ── Rendering ──────────────────────────────────────────────────────────
  function fmt(n, dp) {
    dp = dp == null ? 1 : dp;
    if (n == null || isNaN(n)) return '—';
    return Number(n).toFixed(dp);
  }

  function renderScatter(data) {
    const sectors = Array.from(new Set(data.map((d) => d.sector_key || 'Other'))).sort();
    const traces = sectors.map((sec) => {
      const subset = data.filter((d) => (d.sector_key || 'Other') === sec);
      return {
        x: subset.map((d) => d.mcap_b),
        y: subset.map((d) => (d.discount_computed != null ? d.discount_computed * 100 : null)),
        mode: 'markers',
        type: 'scatter',
        name: sec,
        text: subset.map(
          (d) =>
            d.ticker +
            ' — ' +
            (d.company || '') +
            '<br>IV $' + fmt(d.iv_b_computed) +
            'B · MCap $' + fmt(d.mcap_b) +
            'B · Discount ' + fmt(d.discount_computed != null ? d.discount_computed * 100 : null) + '%' +
            (d.brk_held === 'YES' ? '<br>BRK position: $' + fmt(d.brk_pos_b) + 'B' : '')
        ),
        hoverinfo: 'text',
        marker: {
          size: subset.map((d) => (d.brk_pos_b ? Math.min(40, 8 + Math.sqrt(d.brk_pos_b) * 3) : 8)),
          line: { width: 0.5, color: '#0f1419' },
        },
      };
    });
    Plotly.react(
      'scatter',
      traces,
      {
        paper_bgcolor: '#0f1419',
        plot_bgcolor: '#0f1419',
        font: { color: '#e6edf3' },
        xaxis: { title: 'Market Cap ($B, log)', type: 'log', gridcolor: '#30363d' },
        yaxis: { title: 'Discount %', gridcolor: '#30363d', zeroline: true, zerolinecolor: '#8b949e' },
        margin: { t: 20, l: 60, r: 20, b: 60 },
        showlegend: true,
        legend: { font: { size: 11 } },
      },
      { responsive: true, displaylogo: false }
    );
  }

  function renderTable(data) {
    const sorted = data.slice().sort((a, b) => (b.discount_computed || -Infinity) - (a.discount_computed || -Infinity));
    const html = sorted
      .map((d) => {
        const disc = d.discount_computed;
        const cls = disc == null ? '' : disc > 0 ? 'pos' : 'neg';
        const method = d.valuation_method === 'pb' ? 'P/B' : 'DCF';
        return (
          '<tr>' +
          '<td><strong>' + (d.ticker || '') + '</strong></td>' +
          '<td>' + (d.company || '') + '</td>' +
          '<td>' + (d.sector_key || '') + '</td>' +
          '<td class="method">' + method + '</td>' +
          '<td class="num">$' + fmt(d.price, 2) + '</td>' +
          '<td class="num">' + fmt(d.mcap_b) + '</td>' +
          '<td class="num">' + fmt(d.iv_b_computed) + '</td>' +
          '<td class="num ' + cls + '">' + (disc != null ? fmt(disc * 100) + '%' : '—') + '</td>' +
          '<td>' + (d.brk_held || '') + '</td>' +
          '<td class="num">' + (d.brk_pos_b != null ? fmt(d.brk_pos_b) : '') + '</td>' +
          '</tr>'
        );
      })
      .join('');
    document.getElementById('table-body').innerHTML = html;
    document.getElementById('row-count').textContent = sorted.length + ' companies';
  }

  function renderPortfolio() {
    const port = tickers.filter((t) => t.in_portfolio === true);
    const section = document.getElementById('portfolio-section');
    if (!port.length) {
      if (section) section.style.display = 'none';
      return;
    }
    if (section) section.style.display = '';
    const methodLabel = (d) => {
      if (d.valuation_method === 'model') {
        if (d.model_type === 'earnings_multiple') return 'Model · EPS×PE';
        if (d.model_type === 'netcash') return 'Model · net-cash';
        return 'Model · DCF';
      }
      if (d.valuation_method === 'etf') return 'ETF';
      if (d.valuation_method === 'pb') return 'P/B';
      if (d.iv_b_computed == null) return '—';
      return 'DCF';
    };
    const html = port
      .map((d) => {
        const disc = d.discount_computed;
        const cls = disc == null ? '' : disc > 0 ? 'pos' : 'neg';
        const verdict = disc == null ? '—' : disc > 0.25 ? 'BUY' : disc > -0.1 ? 'FAIR' : 'overvalued';
        const label = d.portfolio_display || d.company || '';
        const src = (d.model_source || '') + (d.model_note ? ' — ' + d.model_note : '');
        const conf = d.model_confidence && d.model_confidence !== 'user'
          ? ' <span class="conf">(' + d.model_confidence + ')</span>' : '';
        const ivps = d.iv_per_share != null ? fmt(d.iv_per_share, 2) : '—';
        const dr = d.model_discount_rate != null ? fmt(d.model_discount_rate * 100, 1) + '%' : '—';
        const sheet = d.sheet_iv_per_share != null ? fmt(d.sheet_iv_per_share, 0) : '';
        const guard = d.model_analyst_guard;
        const gTag = d.model_growth_y1_5 != null
          ? fmt(d.model_growth_y1_5 * 100, 1) + '%' + (guard && guard.applied ? '<span class="conf">▾</span>' : '')
          : '—';
        return (
          '<tr title="' + src.replace(/"/g, "'") + '">' +
          '<td><strong>' + (d.ticker || '') + '</strong></td>' +
          '<td>' + label + '</td>' +
          '<td class="method">' + methodLabel(d) + conf + '</td>' +
          '<td class="num">' + gTag + '</td>' +
          '<td class="num">' + dr + '</td>' +
          '<td class="num">' + (d.price != null ? fmt(d.price, 2) : '—') + '</td>' +
          '<td class="num">' + ivps + '</td>' +
          '<td class="num muted2">' + sheet + '</td>' +
          '<td class="num ' + cls + '">' + (disc != null ? fmt(disc * 100) + '%' : '—') + '</td>' +
          '<td class="' + cls + '">' + verdict + '</td>' +
          '</tr>'
        );
      })
      .join('');
    document.getElementById('portfolio-body').innerHTML = html;
  }

  function render() {
    const data = getFiltered();
    renderPortfolio();
    renderScatter(data);
    renderTable(data);
  }

  // ── History (time-series) ──────────────────────────────────────────────
  let historyRows = null;

  function parseCsv(text) {
    const lines = text.trim().split(/\r?\n/);
    if (lines.length < 2) return [];
    const header = lines[0].split(',');
    return lines.slice(1).map((line) => {
      const cols = line.split(',');
      const obj = {};
      for (let i = 0; i < header.length; i++) obj[header[i]] = cols[i];
      return obj;
    });
  }

  function loadHistory() {
    fetch('data/history.csv')
      .then((r) => {
        if (!r.ok) throw new Error('history.csv not found');
        return r.text();
      })
      .then((t) => {
        historyRows = parseCsv(t);
        document.getElementById('history-status').textContent =
          historyRows.length + ' snapshots loaded';
      })
      .catch(() => {
        document.getElementById('history-status').textContent =
          'history.csv unavailable (open via http:// or wait for first cron run).';
      });
  }

  function plotHistory(tickerList) {
    if (!historyRows) return;
    const traces = tickerList.map((tk) => {
      const rows = historyRows.filter((r) => r.ticker === tk);
      return {
        x: rows.map((r) => r.timestamp_utc),
        y: rows.map((r) => (r.discount_pct === '' || r.discount_pct == null ? null : parseFloat(r.discount_pct) * 100)),
        mode: 'lines+markers',
        name: tk,
      };
    });
    Plotly.react(
      'history',
      traces,
      {
        paper_bgcolor: '#0f1419',
        plot_bgcolor: '#0f1419',
        font: { color: '#e6edf3' },
        yaxis: { title: 'Discount %', gridcolor: '#30363d' },
        xaxis: { title: 'Time (UTC)', gridcolor: '#30363d' },
        margin: { t: 20, l: 60, r: 20, b: 60 },
      },
      { responsive: true, displaylogo: false }
    );
  }

  // ── Wire UI ────────────────────────────────────────────────────────────
  function debounce(fn, ms) {
    let h = null;
    return function () {
      const args = arguments;
      const ctx = this;
      clearTimeout(h);
      h = setTimeout(() => fn.apply(ctx, args), ms);
    };
  }

  function setSliderFromState() {
    document.getElementById('slider-dr-prem').value = (state.sliders.baseDiscountPremium * 100).toFixed(2);
    document.getElementById('slider-g-hair').value = (state.sliders.baseGrowthHaircut * 100).toFixed(2);
    document.getElementById('slider-mos').value = (state.sliders.baseMOSAdd * 100).toFixed(2);
    document.getElementById('slider-global-g').value = (state.sliders.globalGrowthShift * 100).toFixed(2);
    document.getElementById('val-dr-prem').textContent = (state.sliders.baseDiscountPremium * 100).toFixed(1) + '%';
    document.getElementById('val-g-hair').textContent = (state.sliders.baseGrowthHaircut * 100).toFixed(1) + '%';
    document.getElementById('val-mos').textContent = (state.sliders.baseMOSAdd * 100).toFixed(1) + '%';
    document.getElementById('val-global-g').textContent = (state.sliders.globalGrowthShift * 100).toFixed(1) + '%';
  }

  document.addEventListener('DOMContentLoaded', () => {
    // Populate sector dropdown
    const sel = document.getElementById('filter-sector');
    const sectors = Array.from(new Set(tickers.map((t) => t.sector_key).filter(Boolean))).sort();
    for (const s of sectors) {
      const opt = document.createElement('option');
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    }

    setSliderFromState();

    const dRecompute = debounce(recompute, 80);
    const dRender = debounce(render, 50);

    function bindSlider(id, key) {
      const el = document.getElementById(id);
      const valId = 'val-' + id.replace('slider-', '');
      el.addEventListener('input', () => {
        state.sliders[key] = parseFloat(el.value) / 100;
        document.getElementById(valId).textContent = (state.sliders[key] * 100).toFixed(1) + '%';
        dRecompute();
      });
    }
    bindSlider('slider-dr-prem', 'baseDiscountPremium');
    bindSlider('slider-g-hair', 'baseGrowthHaircut');
    bindSlider('slider-mos', 'baseMOSAdd');
    bindSlider('slider-global-g', 'globalGrowthShift');

    document.getElementById('filter-sector').addEventListener('change', (e) => {
      state.sectorFilter = e.target.value;
      dRender();
    });
    document.getElementById('filter-brk').addEventListener('change', (e) => {
      state.brkOnly = e.target.checked;
      dRender();
    });
    document.getElementById('filter-search').addEventListener('input', (e) => {
      state.searchText = e.target.value;
      dRender();
    });
    document.getElementById('filter-min-disc').addEventListener('input', (e) => {
      const v = parseFloat(e.target.value);
      state.minDiscount = isNaN(v) ? null : v;
      dRender();
    });
    document.getElementById('btn-reset').addEventListener('click', () => {
      state.sliders.baseDiscountPremium = A.base.discount_premium;
      state.sliders.baseGrowthHaircut = A.base.growth_haircut;
      state.sliders.baseMOSAdd = A.base.mos_add;
      state.sliders.globalGrowthShift = 0;
      setSliderFromState();
      recompute();
    });

    document.getElementById('btn-history').addEventListener('click', () => {
      const raw = document.getElementById('history-tickers').value;
      const list = raw.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
      if (list.length === 0) return;
      plotHistory(list);
    });

    recompute();
    loadHistory();
  });

  // Expose for the parity-check Node script.
  window.SP500_DCF = { buffettIV, financialIV, computeIV };
})();
