/* CTA Lab —— TradingView 风格期货图表前端
 *
 * 依赖：lightweight-charts v5.2.1（standalone 版，已打包在 static/）
 *
 * v5 关键 API（与 v4 不同）：
 *   chart.addSeries(LightweightCharts.CandlestickSeries, opts, paneIndex)
 *   LightweightCharts.createSeriesMarkers(series, markers)
 *   chart.panes()[i].setHeight(px)
 */

(() => {
  'use strict';

  const LWC = window.LightweightCharts;
  const COLORS = {
    up: '#26a69a', down: '#ef5350',
    ma1: '#f0b90b', ma2: '#2196f3', ma3: '#e040fb',
    boll: '#787b86',
    macd: '#2962ff', signal: '#ff6d00',
    rsi: '#ba68c8', atr: '#4db6ac',
    text: '#d1d4dc', dim: '#787b86',
  };

  const state = {
    symbols: [],
    current: null,
    interval: 'd',
    indicators: new Set(['ma']),
    bars: [],
    markers: [],
    strategies: [],
    compareMode: false,
    wyckoffOn: false,
    wyckoffPure: false,
    wyckoffMode: 'macro',
    wyckoffBracketsOn: true,
    wyckoffData: null,
    wyckoffMarkers: [],
    wyckoffSeries: [],
    wyckoffBracketSeries: [],
    wyckoffTrades: [],
    loading: 0,
  };

  let chart = null;
  let series = {};
  let markerApi = null;
  let subPaneIndex = null;
  let subPriceLines = [];

  const $ = (sel) => document.querySelector(sel);
  const fmt = (v, d = 2) =>
    (v === null || v === undefined || Number.isNaN(v))
      ? '—'
      : Number(v).toLocaleString('zh-CN', {
          minimumFractionDigits: d, maximumFractionDigits: d,
        });

  function showLoading(text = '加载中…') {
    state.loading++;
    $('#spinner-text').textContent = text;
    $('#spinner').classList.add('on');
  }
  function hideLoading() {
    state.loading = Math.max(0, state.loading - 1);
    if (state.loading === 0) $('#spinner').classList.remove('on');
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    return res.json();
  }

  /* ------------------------------------------------------------ 指标 */
  const IND = {
    sma(arr, n) {
      const out = new Array(arr.length).fill(null);
      let sum = 0;
      for (let i = 0; i < arr.length; i++) {
        sum += arr[i];
        if (i >= n) sum -= arr[i - n];
        if (i >= n - 1) out[i] = sum / n;
      }
      return out;
    },
    ema(arr, n) {
      const out = new Array(arr.length).fill(null);
      const k = 2 / (n + 1);
      let prev = null;
      for (let i = 0; i < arr.length; i++) {
        if (prev === null) {
          if (i >= n - 1) {
            let s = 0;
            for (let j = i - n + 1; j <= i; j++) s += arr[j];
            prev = s / n;
            out[i] = prev;
          }
        } else {
          prev = arr[i] * k + prev * (1 - k);
          out[i] = prev;
        }
      }
      return out;
    },
    boll(arr, n, mult) {
      const mid = IND.sma(arr, n);
      const up = new Array(arr.length).fill(null);
      const dn = new Array(arr.length).fill(null);
      for (let i = n - 1; i < arr.length; i++) {
        let s = 0;
        for (let j = i - n + 1; j <= i; j++) s += (arr[j] - mid[i]) ** 2;
        const sd = Math.sqrt(s / n);
        up[i] = mid[i] + mult * sd;
        dn[i] = mid[i] - mult * sd;
      }
      return { mid, up, dn };
    },
    macd(arr, fast = 12, slow = 26, sig = 9) {
      const ef = IND.ema(arr, fast);
      const es = IND.ema(arr, slow);
      const dif = arr.map((_, i) =>
        (ef[i] === null || es[i] === null) ? null : ef[i] - es[i]);
      const valid = dif.filter(v => v !== null);
      const deaValid = IND.ema(valid, sig);
      const dea = new Array(arr.length).fill(null);
      let k = 0;
      for (let i = 0; i < arr.length; i++) if (dif[i] !== null) dea[i] = deaValid[k++];
      const hist = arr.map((_, i) =>
        (dif[i] === null || dea[i] === null) ? null : (dif[i] - dea[i]) * 2);
      return { dif, dea, hist };
    },
    rsi(arr, n = 14) {
      const out = new Array(arr.length).fill(null);
      let gain = 0, loss = 0;
      for (let i = 1; i < arr.length; i++) {
        const ch = arr[i] - arr[i - 1];
        const g = Math.max(ch, 0), l = Math.max(-ch, 0);
        if (i <= n) {
          gain += g; loss += l;
          if (i === n) {
            gain /= n; loss /= n;
            out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
          }
        } else {
          gain = (gain * (n - 1) + g) / n;
          loss = (loss * (n - 1) + l) / n;
          out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
        }
      }
      return out;
    },
    atr(bars, n = 14) {
      const out = new Array(bars.length).fill(null);
      const tr = bars.map((b, i) => {
        if (i === 0) return b.high - b.low;
        const pc = bars[i - 1].close;
        return Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc));
      });
      let prev = null;
      for (let i = 0; i < tr.length; i++) {
        if (i === n - 1) {
          let s = 0;
          for (let j = 0; j <= i; j++) s += tr[j];
          prev = s / n;
          out[i] = prev;
        } else if (i >= n) {
          prev = (prev * (n - 1) + tr[i]) / n;
          out[i] = prev;
        }
      }
      return out;
    },
  };

  /* ------------------------------------------------------------ 图表 */
  function buildChart() {
    if (chart) { chart.remove(); chart = null; }
    series = {};
    markerApi = null;
    subPaneIndex = null;
    subPriceLines = [];

    chart = LWC.createChart($('#chart'), {
      layout: {
        background: { type: 'solid', color: '#131722' },
        textColor: COLORS.text,
        fontSize: 11,
        panes: { separatorColor: '#2a2e39', separatorHoverColor: '#2962ff' },
      },
      grid: {
        vertLines: { color: 'rgba(42,46,57,.55)' },
        horzLines: { color: 'rgba(42,46,57,.55)' },
      },
      crosshair: {
        mode: LWC.CrosshairMode.Normal,
        vertLine: { color: '#758696', width: 1, style: 3, labelBackgroundColor: '#2a2e39' },
        horzLine: { color: '#758696', width: 1, style: 3, labelBackgroundColor: '#2a2e39' },
      },
      rightPriceScale: {
        borderColor: '#2a2e39',
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#2a2e39',
        timeVisible: state.interval !== 'd',
        secondsVisible: false,
        rightOffset: 6,
        barSpacing: 7,
      },
      localization: { locale: 'zh-CN' },
      autoSize: true,
    });

    series.candle = chart.addSeries(LWC.CandlestickSeries, {
      upColor: COLORS.up, downColor: COLORS.down,
      borderUpColor: COLORS.up, borderDownColor: COLORS.down,
      wickUpColor: COLORS.up, wickDownColor: COLORS.down,
      priceLineVisible: false,
    }, 0);

    series.volume = chart.addSeries(LWC.HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
      priceLineVisible: false,
      lastValueVisible: false,
    }, 1);
    series.volume.priceScale().applyOptions({
      scaleMargins: { top: 0.15, bottom: 0 },
    });

    const maColors = [COLORS.ma1, COLORS.ma2, COLORS.ma3];
    series.ma = maColors.map((c) =>
      chart.addSeries(LWC.LineSeries, {
        color: c, lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      }, 0));

    series.ema = maColors.map((c) =>
      chart.addSeries(LWC.LineSeries, {
        color: c, lineWidth: 1, lineStyle: 2,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      }, 0));

    series.boll = [0, 1, 2].map((i) =>
      chart.addSeries(LWC.LineSeries, {
        color: COLORS.boll, lineWidth: 1,
        lineStyle: i === 1 ? 2 : 0,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      }, 0));

    bindCrosshair();
    syncPaneHeights();
  }

  function ensureSubPane() {
    if (subPaneIndex !== null) return subPaneIndex;
    chart.addPane();
    subPaneIndex = chart.panes().length - 1;
    series.subHist = chart.addSeries(LWC.HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
    }, subPaneIndex);
    series.subLine = [0, 1].map(() =>
      chart.addSeries(LWC.LineSeries, {
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      }, subPaneIndex));
    syncPaneHeights();
    return subPaneIndex;
  }

  function clearSubPane() {
    if (subPaneIndex === null) return;
    subPriceLines.forEach(pl => {
      try { series.subLine[0].removePriceLine(pl); } catch (e) { /* ignore */ }
    });
    subPriceLines = [];
    if (series.subHist) {
      try { chart.removeSeries(series.subHist); } catch (e) { /* ignore */ }
      delete series.subHist;
    }
    if (series.subLine) {
      series.subLine.forEach(s => {
        try { chart.removeSeries(s); } catch (e) { /* ignore */ }
      });
      delete series.subLine;
    }
    subPaneIndex = null;
    syncPaneHeights();
  }

  function syncPaneHeights() {
    if (!chart) return;
    const panes = chart.panes();
    if (!panes || !panes.length) return;
    const h = $('#chart-wrap').clientHeight || 600;
    const subs = Math.max(0, panes.length - 2);
    const mainH = Math.max(200, h - 118 - subs * 118);
    try {
      panes[0].setHeight(mainH);
      if (panes[1]) panes[1].setHeight(110);
      for (let i = 2; i < panes.length; i++) panes[i].setHeight(118);
    } catch (e) { /* 不同版本 pane API 差异 */ }
  }

  /* ------------------------------------------------------------ 渲染 */
  function toChartTime(bar) {
    if (state.interval === 'd') return bar.time;
    // 后端给 UTC 秒；+8h 让图表按北京时间显示
    return bar.time + 8 * 3600;
  }

  function render(bars) {
    state.bars = bars;
    if (!bars.length) return;

    series.candle.setData(bars.map(b => ({
      time: toChartTime(b),
      open: b.open, high: b.high, low: b.low, close: b.close,
    })));

    series.volume.setData(bars.map(b => ({
      time: toChartTime(b),
      value: b.volume,
      color: b.close >= b.open ? 'rgba(38,166,154,.45)' : 'rgba(239,83,80,.45)',
    })));

    const closes = bars.map(b => b.close);

    const showMA = state.indicators.has('ma');
    series.ma.forEach((s, i) => {
      s.applyOptions({ visible: showMA });
      if (!showMA) return;
      const vals = IND.sma(closes, [5, 10, 20][i]);
      s.setData(toLineData(bars, vals));
    });

    const showEMA = state.indicators.has('ema');
    series.ema.forEach((s, i) => {
      s.applyOptions({ visible: showEMA });
      if (!showEMA) return;
      const vals = IND.ema(closes, [12, 26, 60][i]);
      s.setData(toLineData(bars, vals));
    });

    const showBOLL = state.indicators.has('boll');
    if (showBOLL) {
      const { up, mid, dn } = IND.boll(closes, 20, 2);
      [up, mid, dn].forEach((vals, i) => {
        series.boll[i].applyOptions({ visible: true });
        series.boll[i].setData(toLineData(bars, vals));
      });
    } else {
      series.boll.forEach(s => s.applyOptions({ visible: false }));
    }

    const sub = ['macd', 'rsi', 'atr'].find(k => state.indicators.has(k));
    clearSubPane();
    if (sub) renderSubIndicator(sub, bars, closes);

    applyMarkers();
    updateLegend(bars[bars.length - 1]);
  }

  function toLineData(bars, vals) {
    const out = [];
    for (let i = 0; i < bars.length; i++) {
      if (vals[i] === null || vals[i] === undefined) continue;
      out.push({ time: toChartTime(bars[i]), value: vals[i] });
    }
    return out;
  }

  function renderSubIndicator(kind, bars, closes) {
    ensureSubPane();

    if (kind === 'macd') {
      const { dif, dea, hist } = IND.macd(closes);
      const hd = [];
      for (let i = 0; i < bars.length; i++) {
        if (hist[i] === null) continue;
        hd.push({
          time: toChartTime(bars[i]),
          value: hist[i],
          color: hist[i] >= 0 ? 'rgba(38,166,154,.6)' : 'rgba(239,83,80,.6)',
        });
      }
      series.subHist.setData(hd);
      series.subLine[0].applyOptions({ color: COLORS.macd, visible: true });
      series.subLine[1].applyOptions({ color: COLORS.signal, visible: true });
      series.subLine[0].setData(toLineData(bars, dif));
      series.subLine[1].setData(toLineData(bars, dea));

    } else if (kind === 'rsi') {
      const vals = IND.rsi(closes, 14);
      series.subHist.setData([]);
      series.subLine[0].applyOptions({ color: COLORS.rsi, visible: true });
      series.subLine[1].applyOptions({ visible: false });
      series.subLine[0].setData(toLineData(bars, vals));
      [30, 70].forEach(level => {
        subPriceLines.push(series.subLine[0].createPriceLine({
          price: level,
          color: 'rgba(120,123,134,.55)',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: String(level),
        }));
      });

    } else if (kind === 'atr') {
      const vals = IND.atr(bars, 14);
      series.subHist.setData([]);
      series.subLine[0].applyOptions({ color: COLORS.atr, visible: true });
      series.subLine[1].applyOptions({ visible: false });
      series.subLine[0].setData(toLineData(bars, vals));
    }
  }

  function applyMarkers() {
    if (!chart || !series.candle) return;
    if (markerApi) {
      try { markerApi.setMarkers([]); } catch (e) { /* ignore */ }
      markerApi = null;
    }

    const seen = new Set();
    const list = [];

    // 回测买卖点优先
    state.markers.forEach(m => {
      const t = state.interval === 'd' ? m.time : m.time + 8 * 3600;
      const key = `${t}|${m.text}`;
      if (seen.has(key)) return;
      seen.add(key);
      list.push({
        time: t, position: m.position, shape: m.shape,
        color: m.color, text: m.text,
      });
    });

    // 维科夫结构事件
    if (state.wyckoffOn) {
      state.wyckoffMarkers.forEach(m => {
        const key = `${m.time}|${m.text}`;
        if (seen.has(key)) return;
        seen.add(key);
        list.push(m);
      });

      // 维科夫回测的进出场点（最醒目）
      state.wyckoffTrades.forEach(m => {
        const key = `${m.time}|${m.text}`;
        if (seen.has(key)) return;
        seen.add(key);
        list.push(m);
      });
    }

    if (!list.length) return;
    list.sort((a, b) => (a.time > b.time ? 1 : a.time < b.time ? -1 : 0));
    markerApi = LWC.createSeriesMarkers(series.candle, list);
  }

  function updateLegend(bar) {
    if (!bar) { $('#legend').innerHTML = ''; return; }
    const chg = bar.close - bar.open;
    const pct = bar.open ? (chg / bar.open) * 100 : 0;
    const cls = chg >= 0 ? 'pos' : 'neg';

    const items = [
      `<span class="lg">O <span class="val">${fmt(bar.open)}</span></span>`,
      `<span class="lg">H <span class="val">${fmt(bar.high)}</span></span>`,
      `<span class="lg">L <span class="val">${fmt(bar.low)}</span></span>`,
      `<span class="lg">C <span class="val ${cls}">${fmt(bar.close)}</span></span>`,
      `<span class="lg"><span class="val ${cls}">${chg >= 0 ? '+' : ''}${fmt(chg)} (${pct.toFixed(2)}%)</span></span>`,
      `<span class="lg">量 <span class="val">${fmt(bar.volume, 0)}</span></span>`,
      `<span class="lg">持仓 <span class="val">${fmt(bar.oi, 0)}</span></span>`,
    ];
    if (state.indicators.has('ma')) {
      items.push(`<span class="lg"><span class="sw" style="background:${COLORS.ma1}"></span>MA5</span>`);
      items.push(`<span class="lg"><span class="sw" style="background:${COLORS.ma2}"></span>MA10</span>`);
      items.push(`<span class="lg"><span class="sw" style="background:${COLORS.ma3}"></span>MA20</span>`);
    }
    if (state.indicators.has('ema')) {
      items.push(`<span class="lg"><span class="sw" style="background:${COLORS.ma2};opacity:.6"></span>EMA</span>`);
    }
    if (state.indicators.has('boll')) {
      items.push(`<span class="lg"><span class="sw" style="background:${COLORS.boll}"></span>BOLL(20,2)</span>`);
    }
    $('#legend').innerHTML = items.join('');
  }

  /* ------------------------------------------------------------ 数据 */
  async function loadSymbols() {
    const data = await api('/api/symbols');
    state.symbols = data.symbols;
    renderSymbolList();
    if (!state.current && state.symbols.length) {
      // 默认优先选螺纹钢（流动性好、历史长），否则取第一个
      const preferred = state.symbols.find(s => s.vt_symbol === 'rb.SHFE')
        || state.symbols.find(s => s.symbol.toLowerCase() === 'rb')
        || state.symbols[0];
      selectSymbol(preferred);
    }
  }

  function renderSymbolList() {
    const q = ($('#symbol-search').value || '').trim().toLowerCase();
    const groups = {};

    state.symbols.forEach(s => {
      const hay = `${s.symbol} ${s.name} ${s.exchange}`.toLowerCase();
      if (q && !hay.includes(q)) return;
      (groups[s.exchange_cn] ||= []).push(s);
    });

    let html = '';
    Object.keys(groups).sort().forEach(g => {
      html += `<div class="sym-group">${g}</div>`;
      groups[g].forEach(s => {
        const active = state.current && state.current.vt_symbol === s.vt_symbol;
        const isIdx = s.symbol.endsWith('_index');
        const badge = isIdx
          ? `<span style="font-size:10px;color:#2962ff;background:rgba(41,98,255,0.15);padding:1px 4px;border-radius:3px;margin-left:auto;margin-right:4px;">指数</span>`
          : `<span style="font-size:10px;color:#f0b90b;background:rgba(240,185,11,0.15);padding:1px 4px;border-radius:3px;margin-left:auto;margin-right:4px;">主连</span>`;
        html += `<div class="sym-item${active ? ' active' : ''}" data-vt="${s.vt_symbol}">`
             +  `<span class="code">${s.symbol}</span>`
             +  badge
             +  `<span class="nm">${s.name}</span></div>`;
      });
    });
    $('#symbol-list').innerHTML = html || '<div class="sym-group">无匹配</div>';

    $('#symbol-list').querySelectorAll('.sym-item').forEach(el => {
      el.addEventListener('click', () => {
        const sym = state.symbols.find(x => x.vt_symbol === el.dataset.vt);
        if (sym) selectSymbol(sym);
      });
    });
  }

  async function selectSymbol(sym) {
    state.current = sym;
    state.compareMode = false;
    state.markers = [];
    state.wyckoffOn = false;
    state.wyckoffData = null;
    $('#btn-compare').classList.remove('active');
    $('#btn-wyckoff').classList.remove('active');
    renderSymbolList();
    updateTitle();
    await loadBars();
  }

  function updateTitle() {
    const s = state.current;
    if (!s) return;
    $('#tb-title').innerHTML =
      `${s.symbol} <span class="ex">${s.name} · ${s.exchange_cn} · ${fmt(s.count, 0)} 根</span>`;
  }

  async function loadBars() {
    const s = state.current;
    if (!s) return;
    showLoading('加载 K 线…');
    try {
      const data = await api(
        `/api/bars?vt_symbol=${encodeURIComponent(s.vt_symbol)}&interval=${state.interval}`
      );
      buildChart();
      render(data.bars);
      chart.timeScale().fitContent();
      $('#readout').textContent = '';
      if (state.wyckoffOn && state.wyckoffData) {
        renderWyckoff(state.wyckoffData);
        applyMarkers();
      }
    } catch (err) {
      alert('加载失败：' + err.message);
    } finally {
      hideLoading();
    }
  }

  /* ------------------------------------------------------------ 对比 */
  async function toggleCompare() {
    if (!state.symbols.length) return;

    if (state.compareMode) {
      state.compareMode = false;
      $('#btn-compare').classList.remove('active');
      await loadBars();
      return;
    }

    const cur = state.current;
    const same = state.symbols
      .filter(s => s.exchange === cur.exchange && s.vt_symbol !== cur.vt_symbol)
      .slice(0, 2)
      .map(s => s.vt_symbol);
    const picks = [cur.vt_symbol, ...same];
    if (picks.length < 2) {
      alert('同交易所没有其他品种可对比');
      return;
    }

    showLoading('加载对比数据…');
    try {
      const data = await api(
        `/api/compare?symbols=${encodeURIComponent(picks.join(','))}&interval=${state.interval}`
      );
      state.compareMode = true;
      $('#btn-compare').classList.add('active');
      renderCompare(data);
    } catch (err) {
      alert('对比失败：' + err.message);
    } finally {
      hideLoading();
    }
  }

  function renderCompare(data) {
    buildChart();
    series.candle.applyOptions({ visible: false });
    series.volume.applyOptions({ visible: false });
    series.ma.forEach(s => s.applyOptions({ visible: false }));
    series.ema.forEach(s => s.applyOptions({ visible: false }));
    series.boll.forEach(s => s.applyOptions({ visible: false }));

    const palette = ['#2962ff', '#f0b90b', '#e040fb', '#26a69a', '#ff6d00', '#26c6da'];
    const legend = [];

    data.series.forEach((s, i) => {
      const line = chart.addSeries(LWC.LineSeries, {
        color: palette[i % palette.length],
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        title: s.vt_symbol,
      }, 0);
      line.setData(s.points.map(p => ({
        time: state.interval === 'd' ? p.time : p.time + 8 * 3600,
        value: p.value,
      })));
      legend.push(
        `<span class="lg"><span class="sw" style="background:${palette[i % palette.length]}"></span>`
        + `${s.vt_symbol} <span class="val">${s.count}</span></span>`
      );
    });

    $('#legend').innerHTML =
      `<span class="lg">归一化对比（起点 = 100）</span>` + legend.join('');
    $('#readout').textContent = '';
    chart.timeScale().fitContent();
  }

  /* ------------------------------------------------------------ 回测 */
  async function loadStrategies() {
    const data = await api('/api/strategies');
    state.strategies = data.strategies;
    $('#strategy-sel').innerHTML = data.strategies.map(s =>
      `<option value="${s.name}" title="${s.doc}">${s.name}</option>`).join('');
    if (data.strategies.length) $('#strategy-sel').value = 'Donchian';
  }

  async function runBacktest() {
    const s = state.current;
    if (!s) return;
    const strategy = $('#strategy-sel').value;
    const meta = state.strategies.find(x => x.name === strategy);

    showLoading('回测中…');
    try {
      const data = await api('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vt_symbol: s.vt_symbol,
          strategy,
          params: meta ? meta.default : {},
          start: s.start,
          end: s.end,
        }),
      });

      state.markers = data.markers;
      if (!state.compareMode) applyMarkers();
      renderStats(data);
      $('#stats').classList.add('open');
      $('#btn-stats').classList.add('active');
    } catch (err) {
      alert('回测失败：' + err.message);
    } finally {
      hideLoading();
    }
  }

  function renderStats(data) {
    const st = data.stats || {};
    const ts = data.trade_stats || {};
    const score = data.score || {};
    const sign = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');

    // 爆仓 / 高杠杆告警
    const warnEl = $('#warning');
    if (st.liquidated) {
      warnEl.style.display = 'block';
      warnEl.innerHTML =
        `<b>⚠ 回测期间已爆仓</b>：最低权益 ${fmt(st.min_balance, 0)}`
        + `（首次爆仓 ${st.liquidation_date || '未知'}）。<br>`
        + `下方收益 / 回撤 / 夏普全为 0，这是 vnpy 对非正权益的归零行为，`
        + `并非策略空仓 —— 这些指标不可用。<br>`
        + `估算名义杠杆 <b>${st.est_leverage}</b> 倍。`
        + `请调小 fixed_size 或调高 capital 后重跑（vnpy 回测不校验保证金）。`;
    } else if ((st.est_leverage || 0) > 3) {
      warnEl.style.display = 'block';
      warnEl.innerHTML =
        `<b>⚠ 名义杠杆约 ${st.est_leverage} 倍，偏高</b>。`
        + `vnpy 回测不校验保证金，实盘中容易因保证金不足被强平。`;
    } else {
      warnEl.style.display = 'none';
      warnEl.innerHTML = '';
    }

    const kpis = [
      ['总收益率', `${fmt(st.total_return)}%`, sign(st.total_return)],
      ['年化收益', `${fmt(st.annual_return)}%`, sign(st.annual_return)],
      ['最大回撤', `${fmt(st.max_ddpercent)}%`, 'neg'],
      ['夏普比率', fmt(st.sharpe_ratio, 3), sign(st.sharpe_ratio)],
      ['收益回撤比', fmt(st.return_drawdown_ratio, 3), sign(st.return_drawdown_ratio)],
      ['完成回合', fmt(ts.round_trips, 0), ''],
      ['胜率', `${fmt(ts.win_rate)}%`, ''],
      ['盈亏比', fmt(ts.payoff_ratio, 2), ''],
      ['盈利因子', fmt(ts.profit_factor, 3), ''],
      ['单笔期望', fmt(ts.expectancy, 0), sign(ts.expectancy)],
      ['总手续费', fmt(st.total_commission, 0), ''],
      ['总滑点', fmt(st.total_slippage, 0), ''],
      ['稳健性评分', `${fmt(score.total, 1)} ${score.grade || ''}`, ''],
    ];

    $('#kpi-row').innerHTML = kpis.map(([k, v, cls]) =>
      `<div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`
    ).join('');

    $('#notes').innerHTML = (data.notes && data.notes.length)
      ? data.notes.map(n => `<li>${n}</li>`).join('')
      : '<li>无</li>';
  }


  /* ------------------------------------------------------------ 维科夫 */
  const WYCKOFF_STYLE = {
    SC:  { pos: 'belowBar', shape: 'circle',   color: '#ef5350' },
    BC:  { pos: 'aboveBar', shape: 'circle',   color: '#ef5350' },
    AR:  { pos: 'belowBar', shape: 'square',   color: '#f0b90b' },
    ST:  { pos: 'belowBar', shape: 'square',   color: '#2196f3' },
    SPRING: { pos: 'belowBar', shape: 'circle', color: '#7e57c2' },
    SPRING_RECOVERED: { pos: 'belowBar', shape: 'arrowUp', color: '#7e57c2' },
    UTAD: { pos: 'aboveBar', shape: 'circle', color: '#7e57c2' },
    UTAD_RECOVERED: { pos: 'aboveBar', shape: 'arrowDown', color: '#7e57c2' },
    TEST: { pos: 'belowBar', shape: 'arrowUp', color: '#26a69a' },
    TEST_SELL: { pos: 'aboveBar', shape: 'arrowDown', color: '#ef5350' },
    SOS: { pos: 'belowBar', shape: 'arrowUp', color: '#26a69a' },
    SOW: { pos: 'aboveBar', shape: 'arrowDown', color: '#ef5350' },
    LPS: { pos: 'belowBar', shape: 'arrowUp', color: '#00897b' },
    LPSY: { pos: 'aboveBar', shape: 'arrowDown', color: '#c62828' },
    FTI: { pos: 'aboveBar', shape: 'arrowDown', color: '#ff6d00' },
    FTI_BREAKOUT: { pos: 'belowBar', shape: 'arrowUp', color: '#ff6d00' },
  };

  function clearWyckoff() {
    state.wyckoffMarkers = [];
    state.wyckoffTrades = [];
    (state.wyckoffSeries || []).forEach(sr => {
      try { chart.removeSeries(sr); } catch (e) { /* ignore */ }
    });
    state.wyckoffSeries = [];
    (state.wyckoffBracketSeries || []).forEach(sr => {
      try { chart.removeSeries(sr); } catch (e) { /* ignore */ }
    });
    state.wyckoffBracketSeries = [];
    $('#wyckoff-legend').classList.remove('on');
    $('#wyckoff-legend').innerHTML = '';
  }

  async function toggleWyckoff(isPure = null, mode = null) {
    if (!state.current) return;
    const btn = $('#btn-wyckoff');
    const btnMode = $('#btn-wyckoff-mode');
    const btnPure = $('#btn-wyckoff-pure');
    const btnBrackets = $('#btn-wyckoff-brackets');

    if (mode !== null) state.wyckoffMode = mode;
    if (isPure !== null) {
      state.wyckoffPure = isPure;
    } else if (state.wyckoffOn && mode === null) {
      state.wyckoffOn = false;
      btn.classList.remove('active');
      btnMode.style.display = 'none';
      btnPure.style.display = 'none';
      btnBrackets.style.display = 'none';
      clearWyckoff();
      applyMarkers();
      return;
    } else {
      state.wyckoffOn = true;
    }

    btn.classList.add('active');
    btnMode.style.display = 'inline-block';
    btnPure.style.display = 'inline-block';
    btnBrackets.style.display = 'inline-block';

    btnMode.textContent = state.wyckoffMode === 'macro' ? '宏观跨度' : '微观短线';
    btnMode.classList.toggle('active', state.wyckoffMode === 'macro');

    if (state.wyckoffPure) btnPure.classList.add('active');
    else btnPure.classList.remove('active');
    if (state.wyckoffBracketsOn) btnBrackets.classList.add('active');
    else btnBrackets.classList.remove('active');

    const desc = state.wyckoffMode === 'macro' ? '宏观大跨度(指数平滑桥接)' : '微观短线跨度';
    showLoading(`识别${desc}…`);
    try {
      const qs = new URLSearchParams({
        vt_symbol: state.current.vt_symbol,
        pure_wyckoff: state.wyckoffPure ? 'true' : 'false',
        mode: state.wyckoffMode,
        bridge_index: 'true',
      });
      const data = await api(`/api/wyckoff?${qs.toString()}`);
      state.wyckoffData = data;
      renderWyckoff(data);
      applyMarkers();
    } catch (err) {
      alert('维科夫分析失败：' + err.message);
    } finally {
      hideLoading();
    }
  }

  function toggleWyckoffMode() {
    const nextMode = state.wyckoffMode === 'macro' ? 'micro' : 'macro';
    toggleWyckoff(state.wyckoffPure, nextMode);
  }

  function toggleWyckoffPure() {
    toggleWyckoff(!state.wyckoffPure, state.wyckoffMode);
  }

  function toggleWyckoffBrackets() {
    state.wyckoffBracketsOn = !state.wyckoffBracketsOn;
    $('#btn-wyckoff-brackets').classList.toggle('active', state.wyckoffBracketsOn);
    if (state.wyckoffData) {
      renderWyckoff(state.wyckoffData);
      applyMarkers();
    }
  }

  function renderWyckoff(data) {
    clearWyckoff();

    const markers = [];
    const sides = [data.accumulation, data.distribution].filter(Boolean);

    // 1. 结构事件标签 (SC / AR / ST / Spring / Test / SOS / LPS)
    sides.forEach(side => {
      (side.events || []).forEach(e => {
        const st = WYCKOFF_STYLE[e.name] || {
          pos: 'belowBar', shape: 'circle', color: '#787b86',
        };
        markers.push({
          time: e.time,
          position: st.pos,
          shape: st.shape,
          color: st.color,
          text: e.name,
        });
      });
    });

    // 2. 维科夫箱体带 (Ice Line 支撑 / Creek Line 阻力)
    sides.forEach(side => {
      (side.ranges || []).forEach(r => {
        if (!r.end || r.start === r.end) return;
        const isLong = r.direction === 'long';
        const iceColor = isLong ? '#26a69a' : '#ef5350';
        const creekColor = isLong ? '#2962ff' : '#ff6d00';

        // 冰线 (实线, 加粗)
        if (r.ice) {
          const iceLine = chart.addSeries(LWC.LineSeries, {
            color: iceColor,
            lineWidth: 2,
            lineStyle: 0,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          }, 0);
          iceLine.setData([
            { time: r.start, value: r.ice },
            { time: r.end, value: r.ice },
          ]);
          state.wyckoffSeries.push(iceLine);
        }

        // 小溪线 (虚线)
        if (r.creek) {
          const creekLine = chart.addSeries(LWC.LineSeries, {
            color: creekColor,
            lineWidth: 1,
            lineStyle: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          }, 0);
          creekLine.setData([
            { time: r.start, value: r.creek },
            { time: r.end, value: r.creek },
          ]);
          state.wyckoffSeries.push(creekLine);
        }
      });
    });

    // 3. 回测交易持仓盒 (Trade Bracket) 与 进出场连线
    const tradeMarkers = [];
    const bt = data.backtest || {};
    const trades = bt.trades || [];

    trades.forEach(t => {
      const isLong = t.direction === 'long';
      const isWin = t.pnl > 0;
      const pnlColor = isWin ? '#26a69a' : '#ef5350';

      // 入场标记 (箭头)
      tradeMarkers.push({
        time: t.entry_time,
        position: isLong ? 'belowBar' : 'aboveBar',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        color: isLong ? '#26a69a' : '#ef5350',
        text: (isLong ? '买 ' : '空 ') + (t.signal || ''),
      });

      // 出场标记 (圆点)
      if (t.exit_time) {
        tradeMarkers.push({
          time: t.exit_time,
          position: isLong ? 'aboveBar' : 'belowBar',
          shape: 'circle',
          color: pnlColor,
          text: `${isWin ? '+' : ''}${t.r.toFixed(1)}R (${t.exit_reason})`,
        });

        // 如果开启交易盒：绘制进出场盈亏连线 + 止损线 + 目标线
        if (state.wyckoffBracketsOn) {
          // A. 进出场价格连线 (实线)
          const tradeLine = chart.addSeries(LWC.LineSeries, {
            color: pnlColor,
            lineWidth: 2,
            lineStyle: 0,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          }, 0);
          tradeLine.setData([
            { time: t.entry_time, value: t.entry_price },
            { time: t.exit_time, value: t.exit_price },
          ]);
          state.wyckoffBracketSeries.push(tradeLine);

          // B. 目标止盈线 (绿色短虚线)
          if (t.target) {
            const targetLine = chart.addSeries(LWC.LineSeries, {
              color: 'rgba(38, 166, 154, 0.75)',
              lineWidth: 1,
              lineStyle: 2,
              priceLineVisible: false,
              lastValueVisible: false,
              crosshairMarkerVisible: false,
            }, 0);
            targetLine.setData([
              { time: t.entry_time, value: t.target },
              { time: t.exit_time, value: t.target },
            ]);
            state.wyckoffBracketSeries.push(targetLine);
          }

          // C. 止损保护线 (红色短虚线)
          if (t.stop) {
            const stopLine = chart.addSeries(LWC.LineSeries, {
              color: 'rgba(239, 83, 80, 0.75)',
              lineWidth: 1,
              lineStyle: 2,
              priceLineVisible: false,
              lastValueVisible: false,
              crosshairMarkerVisible: false,
            }, 0);
            stopLine.setData([
              { time: t.entry_time, value: t.stop },
              { time: t.exit_time, value: t.stop },
            ]);
            state.wyckoffBracketSeries.push(stopLine);
          }
        }
      }
    });

    state.wyckoffMarkers = markers;
    state.wyckoffTrades = tradeMarkers;

    // 4. 底部维科夫图例
    const accN = (data.accumulation?.events || []).length;
    const distN = (data.distribution?.events || []).length;
    const st = bt.stats || {};
    const legend = $('#wyckoff-legend');
    legend.classList.add('on');
    const modeBadge = data.mode === 'macro' ? '宏观跨度' : '微观短线';
    const bridgeBadge = data.is_bridged ? ' (指数平滑桥接)' : '';
    legend.innerHTML =
      `<span style="color:#2962ff;font-weight:600">● ${modeBadge}${bridgeBadge}</span>`
      + `<span>吸筹 <b>${data.accumulation?.ranges?.length || 0}</b> 区间 (${accN}事件)</span>`
      + `<span>派发 <b>${data.distribution?.ranges?.length || 0}</b> 区间 (${distN}事件)</span>`
      + `<span style="color:#26a69a">━ 冰线</span>`
      + `<span style="color:#2962ff">╍ 小溪</span>`
      + `<span style="color:#fff">◆ 交易 <b>${st.trades || 0}</b> 笔</span>`
      + `<span style="color:${(st.total_return || 0) >= 0 ? '#26a69a' : '#ef5350'}">`
      + `净收益 <b>${(st.total_return || 0).toFixed(1)}%</b> (胜率 ${(st.win_rate || 0).toFixed(0)}%, avgR ${(st.avg_r || 0).toFixed(2)})</span>`;
  }

  /* ------------------------------------------------------------ 十字光标 */
  function bindCrosshair() {
    chart.subscribeCrosshairMove(param => {
      if (!state.bars.length) return;
      if (!param.time) {
        updateLegend(state.bars[state.bars.length - 1]);
        $('#readout').textContent = '';
        return;
      }
      const bar = state.bars.find(b => toChartTime(b) === param.time);
      if (!bar) return;
      updateLegend(bar);
      const d = bar.time;
      $('#readout').textContent = state.interval === 'd'
        ? `日期 ${d}`
        : `时间 ${new Date((d + 8 * 3600) * 1000).toISOString().slice(0, 16).replace('T', ' ')}`;
    });
  }

  /* ------------------------------------------------------------ 事件 */
  function bindEvents() {
    $('#symbol-search').addEventListener('input', renderSymbolList);

    document.querySelectorAll('.iv-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (btn.classList.contains('active')) return;
        document.querySelectorAll('.iv-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.interval = btn.dataset.interval;
        state.markers = [];
        state.compareMode = false;
        state.wyckoffOn = false;
        state.wyckoffData = null;
        $('#btn-compare').classList.remove('active');
        $('#btn-wyckoff').classList.remove('active');
        await loadBars();
      });
    });

    document.querySelectorAll('.ind-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.ind;
        if (['macd', 'rsi', 'atr'].includes(key)) {
          ['macd', 'rsi', 'atr'].forEach(k => {
            if (k !== key) {
              state.indicators.delete(k);
              document.querySelector(`.ind-btn[data-ind="${k}"]`)
                ?.classList.remove('active');
            }
          });
        }
        if (state.indicators.has(key)) {
          state.indicators.delete(key);
          btn.classList.remove('active');
        } else {
          state.indicators.add(key);
          btn.classList.add('active');
        }
        if (state.bars.length && !state.compareMode) {
          render(state.bars);
          applyMarkers();
        }
      });
    });

    $('#btn-compare').addEventListener('click', toggleCompare);
    $('#btn-wyckoff').addEventListener('click', () => toggleWyckoff());
    $('#btn-wyckoff-mode').addEventListener('click', toggleWyckoffMode);
    $('#btn-wyckoff-pure').addEventListener('click', toggleWyckoffPure);
    $('#btn-wyckoff-brackets').addEventListener('click', toggleWyckoffBrackets);
    $('#btn-backtest').addEventListener('click', runBacktest);

    $('#btn-stats').addEventListener('click', () => {
      $('#stats').classList.toggle('open');
      $('#btn-stats').classList.toggle('active');
      setTimeout(syncPaneHeights, 240);
    });

    window.addEventListener('resize', () => {
      if (chart) setTimeout(syncPaneHeights, 60);
    });
  }

  /* ------------------------------------------------------------ 启动 */
  async function init() {
    if (!LWC) {
      document.body.innerHTML =
        '<p style="padding:24px;color:#ef5350">图表库加载失败：static/lightweight-charts.js 缺失</p>';
      return;
    }
    bindEvents();
    try {
      await loadStrategies();
      await loadSymbols();

      // 支持分享链接：?wyckoff=1 自动开启维科夫标注，?symbol=rb.SHFE 指定品种
      const qs = new URLSearchParams(location.search);
      const wantSym = qs.get('symbol');
      if (wantSym) {
        const hit = state.symbols.find(s => s.vt_symbol.toLowerCase() === wantSym.toLowerCase());
        if (hit) await selectSymbol(hit);
      }
      if (qs.get('wyckoff') === '1') {
        await toggleWyckoff();
      }
    } catch (err) {
      alert('初始化失败：' + err.message);
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
