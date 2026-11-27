// Grympha Trading Desk dashboard · js/livetest.js
// Live Test tab: forward-test agents on live MT5 data. Everything shown is SIMULATED.
// Shares the global scope (core.js helpers: $, esc, fmt, date, chip, paginate, setHTML).
'use strict';
let liveTestData = null, liveTestReq = 0, liveTestBusy = false;

// force: after a start/pause click. The 5 s page poll never starts a second request while one is
// still running (a newer request used to discard every slower answer, leaving "No agents yet").
async function refreshLiveTest(force = false) {
 if (liveTestBusy && !force) return;
 const req = ++liveTestReq; liveTestBusy = true;
 try {
  if (demo) { liveTestData = { agents: [], recent_trades: [], simulation: true }; renderLiveTest(); return; }
  const res = await fetch('api/live-test', { cache: 'no-store' });
  if (!res.ok) throw Error('HTTP ' + res.status);
  const r = await res.json(); if (req !== liveTestReq) return;
  liveTestData = r; renderLiveTest();
  refreshSignalAgent();
 } catch (e) { if (req !== liveTestReq) return; liveTestData = { error: e.message, agents: [], recent_trades: [] }; renderLiveTest(); }
 finally { if (req === liveTestReq) liveTestBusy = false; }
}

function ltControl(agentId, action) {
 fetch('api/live-test-control', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls' }, body: JSON.stringify({ action, agent_id: agentId }) })
  .then(res => { if (!res.ok) throw Error('HTTP ' + res.status); return refreshLiveTest(true); })
  .catch(err => { const n = $('lt-note'); if (n) n.textContent = 'Control failed: ' + err.message; });
}

function ltBulk(action) {
 fetch('api/live-test-control', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls' }, body: JSON.stringify({ action }) })
  .then(res => { if (!res.ok) throw Error('HTTP ' + res.status); return refreshLiveTest(true); })
  .catch(err => { const n = $('lt-note'); if (n) n.textContent = 'Control failed: ' + err.message; });
}

function ltRows() { return (liveTestData && liveTestData.recent_trades) || []; }
function ltFiltered() {
 const st = $('lt-filter-strategy').value, sy = $('lt-filter-symbol').value, rs = $('lt-filter-reason').value, dq = $('lt-filter-status').value;
 return ltRows().filter(t => (st === 'ALL' || t.strategy_id === st) && (sy === 'ALL' || t.symbol === sy)
  && (rs === 'ALL' || t.exit_reason === rs) && (dq === 'ALL' || t.data_quality_status === dq));
}
function ltFillSelect(id, values) {
 const el = $(id); if (!el) return; const keep = el.value;
 el.innerHTML = '<option value="ALL">All</option>' + values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
 el.value = [...el.options].some(o => o.value === keep) ? keep : 'ALL';
}

// Tick-mode agents (TrailFlip): what the stop-and-reverse is doing right now.
const LT_TICK_MODE = { BUY: 'Long', SELL: 'Short', FLAT: 'Flat · entry stops armed', WARMING_UP: 'Warming up (first minute)',
 DAILY_LIMIT: 'Stopped for today (daily loss limit)', FLIP_COOLDOWN: 'Exits only · too many flips (15 min)',
 WIDE_SPREAD: 'Exits only · spread too wide', NO_TREND: 'Waiting for a trend or burst',
 OUT_OF_SESSION: 'Outside trading hours (London open to New York close)' };
function ltTickLines(s) {
 if (!s) return '';
 const px = v => v == null ? '—' : Number(v).toFixed(2), level = s.level != null ? px(s.level) : (s.buy_stop != null ? `buy ${px(s.buy_stop)} · sell ${px(s.sell_stop)}` : '—');
 return `<li class="step-label"><span>Now</span><b>${esc(LT_TICK_MODE[s.mode] || s.mode)}</b></li>`
  + `<li class="step-label"><span>Trail D · level</span><b>${px(s.trail)} · ${level}</b></li>`
  + `<li class="step-label"><span>Flips last hour · today</span><b>${s.flips_last_hour}/${s.max_flips ?? '—'} · ${s.today_r >= 0 ? '+' : ''}${s.today_r}R</b></li>`;
}
function renderLiveTest() {
 const r = liveTestData; if (!r) return;
 if ($('lt-note')) $('lt-note').textContent = r.error
  ? ('Live Test unavailable: ' + r.error)
  : 'All entries, positions, exits and P/L here are simulated on live MT5 market data. Read-only feed; no orders are sent to MT5.';
 // Sort agents by win rate (highest first); agents with no closed trades sink to the bottom,
 // tie-broken by Net R then valid-trade count.
 const nr = a => (a.stats || {}).net_r || 0, vt = a => (a.stats || {}).valid_trades || 0;
 const agents = (r.agents || []).slice().sort((a, b) => {
  const wa = (a.stats || {}).win_rate, wb = (b.stats || {}).win_rate;
  if (wa == null && wb == null) return nr(b) - nr(a) || vt(b) - vt(a);
  if (wa == null) return 1;
  if (wb == null) return -1;
  return (wb - wa) || (nr(b) - nr(a)) || (vt(b) - vt(a));
 });
 // Best candidate = highest-win-rate agent that has at least one closed trade.
 if ($('lt-best')) {
  const best = agents.find(a => vt(a) > 0 && (a.stats || {}).win_rate != null);
  if (best) {
   const s = best.stats, net = nr(best);
   $('lt-best').textContent = `Best: ${best.label || best.strategy_id} · ${best.symbol} — `
    + `${(100 * s.win_rate).toFixed(1)}% win · ${net >= 0 ? '+' : ''}${net}R · ${vt(best)} trade${vt(best) === 1 ? '' : 's'}`;
  } else {
   $('lt-best').textContent = 'No candidate yet — no closed trades.';
  }
 }
 setHTML('lt-agents', agents.length ? agents.map(a => {
  const running = a.status === 'RUNNING', s = a.stats || {};
  const wr = s.win_rate == null ? '—' : (100 * s.win_rate).toFixed(1) + '%';
  return `<div class="pair-card"><div class="pair-top"><div class="pair-name">${esc(a.label || a.strategy_id)}<small>${esc(a.symbol)} · cfg ${esc(a.config_id)} · ${esc(a.strategy_version)}</small></div>${chip(running ? 'RUNNING' : 'PAUSED', running ? 'good' : 'warn')}</div>`
   + `<ul class="steps"><li class="step-label"><span>Feed</span><b>${esc(a.feed_status || '—')}</b></li>`
   + `<li class="step-label"><span>Last tick (MYT)</span><b>${esc(a.last_tick_myt || '—')}</b></li>`
   + `<li class="step-label"><span>Last signal</span><b>${a.last_signal ? esc(a.last_signal.side + ' · ' + a.last_signal.reason) : '—'}</b></li>`
   + ltTickLines(a.tick_state) + '</ul>'
   + `<div class="stats" style="margin-top:12px"><div><span class="stat-label">Valid trades</span><strong>${s.valid_trades ?? 0}</strong></div>`
   + `<div><span class="stat-label">Win rate</span><strong>${wr}</strong></div>`
   + `<div><span class="stat-label">Net R</span><strong>${s.net_r ?? 0}</strong></div>`
   + `<div><span class="stat-label">Data-affected</span><strong>${s.affected_by_data ?? 0}</strong></div></div>`
   + `<div class="card-time"><button type="button" class="button" data-lt-agent="${esc(a.id)}" data-lt-action="${running ? 'pause' : 'start'}">${running ? 'Pause agent' : 'Start agent'}</button>`
   + (a.trade_live_allowed === false ? ' <span class="muted small">Trades live as its strategy</span>'
     : ` <label class="lt-live${a.trade_live ? ' on' : ''}" title="Place this agent's new entries on MT5 too (real orders)"><input type="checkbox" data-lt-live="${esc(a.id)}" ${a.trade_live ? 'checked' : ''} ${demo ? 'disabled' : ''}> Trade live${a.trade_live ? ' · ON' : ''}</label>`)
   + '</div></div>';
 }).join('') : '<p class="muted">No Live Test agents registered yet. Start the bot to initialise the default agents.</p>');

 const positions = [];
 for (const a of agents) for (const p of (a.open_positions || [])) positions.push(Object.assign({ label: a.label }, p));
 paginate('lt-positions', 'lt-positions-pager', positions, p => {
  const buy = p.side === 'BUY', pnl = p.unrealized_pnl;
  return `<tr><td>${esc(p.strategy_id)}</td><td><b>${esc(p.symbol)}</b></td><td class="${buy ? 'buy' : 'sell'}">${esc(p.side)}</td>`
   + `<td>${fmt(p.entry_price, 2)}</td><td>${fmt(p.current_price, 2)}</td><td>${fmt(p.initial_sl, 2)}</td><td>${fmt(p.initial_tp, 2)}</td>`
   + `<td class="${(pnl || 0) >= 0 ? 'buy' : 'sell'}">${fmt(pnl, 2)}<small>${p.unrealized_r == null ? '' : fmt(p.unrealized_r, 2) + 'R'}</small></td>`
   + `<td>${Math.round((p.age_seconds || 0) / 60)}m</td><td>${esc(p.data_quality_status || 'OK')}</td></tr>`;
 }, { pageSize: 10, colspan: 10, empty: 'No open simulated positions.' });

 // per-symbol summary (R-based roll-up across agents)
 const bySym = r.by_symbol || [];
 setHTML('lt-symbols', bySym.length ? bySym.map(d => {
  const wr = d.win_rate == null ? '—' : (100 * d.win_rate).toFixed(1) + '%';
  return `<tr><td><b>${esc(d.symbol)}</b></td><td>${d.agents ?? 0}</td><td>${d.running ?? 0}</td><td>${esc(d.feed || '—')}</td>`
   + `<td>${d.signals ?? 0}</td><td>${d.no_fill ?? 0}</td><td>${d.closed ?? 0}</td><td>${wr}</td>`
   + `<td class="${(d.net_r || 0) >= 0 ? 'buy' : 'sell'}">${fmt(d.net_r, 2)}</td><td>${d.data_gap ?? 0}</td></tr>`;
 }).join('') : '<tr><td colspan="10" class="empty">No agents yet.</td></tr>');

 // signal log: setups that never became a position (skipped / NO_FILL / cancelled)
 paginate('lt-signals', 'lt-signals-pager', r.recent_signals || [], s => {
  const sell = s.side === 'SELL';
  return `<tr><td>${esc(s.epoch_myt || '—')}</td><td>${esc(s.strategy_id || '—')}</td><td><b>${esc(s.symbol || '—')}</b></td>`
   + `<td class="${s.side ? (sell ? 'sell' : 'buy') : ''}">${esc(s.side || '—')}</td><td>${esc(s.event || '—')}</td>`
   + `<td>${esc(s.reason || s.detail || '—')}</td><td>${s.h4_target_price == null ? '—' : fmt(s.h4_target_price, 2)}</td></tr>`;
 }, { pageSize: 10, colspan: 7, empty: 'No signal-log entries yet.' });

 ltFillSelect('lt-filter-strategy', [...new Set(ltRows().map(t => t.strategy_id))]);
 ltFillSelect('lt-filter-symbol', [...new Set(ltRows().map(t => t.symbol))]);
 ltFillSelect('lt-filter-reason', [...new Set(ltRows().map(t => t.exit_reason).filter(Boolean))]);
 paginate('lt-trades', 'lt-trades-pager', ltFiltered(), t => {
  const buy = t.side === 'BUY', r2 = t.pnl_r;
  return `<tr data-lt-trade="${esc(t.trade_id)}"><td>${esc(t.closed_myt || '—')}</td><td>${esc(t.strategy_id)}</td><td><b>${esc(t.symbol)}</b></td>`
   + `<td class="${buy ? 'buy' : 'sell'}">${esc(t.side)}</td><td>${fmt(t.entry_price, 2)}</td><td>${fmt(t.exit_price, 2)}</td>`
   + `<td>${esc(t.exit_reason || '—')}</td><td class="${(t.price_pnl || 0) >= 0 ? 'buy' : 'sell'}">${fmt(t.price_pnl, 2)}</td>`
   + `<td>${r2 == null ? '—' : fmt(r2, 2) + 'R'}</td><td>${esc(t.data_quality_status || 'OK')}</td></tr>`;
 }, { pageSize: 10, colspan: 10, empty: 'No closed simulated trades match these filters.' });
}

// ---- CSV (filtered export is client-side from loaded rows; "all" streams from the server) ----
const LT_CSV = ['trade_id', 'strategy_id', 'strategy_version', 'config_id', 'symbol', 'timeframe', 'session', 'side',
 'signal_reason', 'signal_time', 'trigger_price', 'entry_time', 'entry_price', 'initial_sl', 'initial_tp',
 'entry_spread', 'exit_time', 'exit_price', 'exit_spread', 'exit_reason', 'duration_seconds', 'price_pnl',
 'pnl_r', 'data_quality_status',
 // structure columns shared across the Live Test agents (empty where N/A):
 'signal_id', 'config_variant', 'setup', 'h4_liquidity_type', 'h4_target_price', 'ltf_swing_time',
 'ltf_swing_level', 'sweep_time', 'sweep_extreme', 'fvg_high', 'fvg_low', 'fvg_confirm_time', 'fvg_midpoint',
 'fill_price', 'ref_high', 'ref_low', 'choch_time', 'choch_level', 'target_level', 'mfe_r', 'mae_r'];
const LT_CSV_TIME = ['signal_time', 'entry_time', 'exit_time', 'ltf_swing_time', 'sweep_time', 'fvg_confirm_time', 'choch_time'];
function ltIso(e) { return e == null || e === '' ? '' : new Date(e * 1000).toISOString().replace(/\.\d+Z$/, 'Z'); }
function ltCsvDownload(rows, name) {
 const q = v => '"' + String(v ?? '').replaceAll('"', '""') + '"';
 const line = t => LT_CSV.map(k => {
  let v = t[k]; if (LT_CSV_TIME.includes(k)) v = ltIso(v);
  v = String(v ?? '').replace(/^([=+@-])/, "'$1"); return q(v);
 }).join(',');
 const csv = [LT_CSV.join(','), ...rows.map(line)].join('\r\n');
 const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
 const a = document.createElement('a'); a.href = url; a.download = name; a.click();
 setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- wire static controls (panel elements exist at load) ----
$('lt-agents').addEventListener('click', e => { const b = e.target.closest('[data-lt-agent]'); if (b) ltControl(b.dataset.ltAgent, b.dataset.ltAction); });
// "Trade live" switch: the agent's NEW simulated entries are also placed on MT5 (listed in Settings).
$('lt-agents').addEventListener('change', async e => {
 const box = e.target.closest('[data-lt-live]'); if (!box) return;
 const a = ((liveTestData && liveTestData.agents) || []).find(x => x.id === box.dataset.ltLive) || {}, on = box.checked;
 const s = a.stats || {}, real = document.body.classList.contains('real-account');
 const msg = on
  ? `Trade ${a.label || a.strategy_id} · ${a.symbol} LIVE?\n\nIts NEW simulated entries will also be placed on MT5 at market, sized by Settings → Trade management, with the usual position cap, news filter, weekend rule and duplicate guard. Exits, partial closes and stop moves copy the simulation.\n\nLive Test record: ${s.valid_trades ?? 0} trades · net ${Number(s.net_r || 0) >= 0 ? '+' : ''}${Number(s.net_r || 0).toFixed(2)}R.`
    + (a.status !== 'RUNNING' ? '\n\nThis agent is PAUSED in Live Test: start it, or it opens no trades.' : '')
    + (real ? '\n\n⚠ This is a REAL account. Real money.' : '')
  : `Stop live trading for ${a.label || a.strategy_id} · ${a.symbol}?\n\nNo new live entries. An open live trade keeps copying the simulation until it closes.`;
 if (!confirm(msg)) { box.checked = !on; return; }
 box.disabled = true;
 try {
  const res = await fetch('api/agent-execution', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls' }, body: JSON.stringify({ agent_id: box.dataset.ltLive, on }) });
  if (!res.ok) throw Error(res.status === 404 ? 'restart the dashboard (v1.43 backend)' : res.status === 400 ? 'agent not allowed or unknown' : 'HTTP ' + res.status);
  await refreshLiveTest(true);
  if (typeof refresh === 'function') refresh();
 } catch (err) { box.checked = !on; box.disabled = false; const n = $('lt-note'); if (n) n.textContent = 'Trade live not changed: ' + err.message; }
});
for (const id of ['lt-filter-strategy', 'lt-filter-symbol', 'lt-filter-reason', 'lt-filter-status']) $(id).addEventListener('change', renderLiveTest);
$('lt-csv-filtered').addEventListener('click', () => ltCsvDownload(ltFiltered(), 'live_test_trades_filtered.csv'));
$('lt-csv-all').addEventListener('click', () => { if (demo) { ltCsvDownload(ltRows(), 'live_test_trades.csv'); return; } location.href = 'api/live-test.csv'; });
$('lt-start-all').addEventListener('click', () => ltBulk('start_all'));
$('lt-pause-all').addEventListener('click', () => ltBulk('pause_all'));
$('lt-signals-csv').addEventListener('click', () => { if (demo) return; location.href = 'api/live-test-signals.csv'; });

// ---- Signal Agent: graded feed of new Live Test setups (simulated; nothing is traded) ----
let signalData = null;
async function refreshSignalAgent() {
 try {
  if (demo) { signalData = { feed: [], by_grade: [] }; renderSignalAgent(); return; }
  const res = await fetch('api/signal-agent', { cache: 'no-store' });
  if (!res.ok) throw Error('HTTP ' + res.status);
  signalData = await res.json(); renderSignalAgent();
 } catch (e) { signalData = { error: e.message, feed: [], by_grade: [] }; renderSignalAgent(); }
}
const SA_CLS = { A: 'good', B: 'warn', C: '', X: 'bad' };
// enough decimals for any instrument: FX 5, JPY / silver 3, gold / indices / crypto 2
const saPx = v => v == null ? '—' : fmt(v, Math.abs(v) < 20 ? 5 : Math.abs(v) < 1000 ? 3 : 2);
function renderSignalAgent() {
 const r = signalData; if (!r) return;
 if ($('sa-note')) $('sa-note').textContent = r.error ? ('Signal Agent unavailable: ' + r.error)
  : (r.backtested_agents ? `${r.backtested_agents} agents have a Quant Agent backtest`
     + (r.last_backtest_epoch ? ` (latest ${date(r.last_backtest_epoch, true)})` : '') + '. '
   : 'No Quant Agent backtest yet: grades use Live Test results only (run quant_agent.py --live). ')
   + 'Simulated setups only; nothing is traded.';
 setHTML('sa-grades', (r.by_grade || []).map(g => `<div><span class="stat-label">${chip(g.grade + ' · ' + g.name, SA_CLS[g.grade])}</span>`
  + `<strong>${g.signals}</strong><small class="muted"> setups · ${g.closed} closed`
  + (g.avg_r == null ? '' : ` · ${g.avg_r >= 0 ? '+' : ''}${g.avg_r}R avg · ${(100 * g.win_rate).toFixed(0)}% win`) + `</small></div>`).join(''));
 const want = $('sa-filter-grade').value;
 const rows = (r.feed || []).filter(f => want === 'ALL' || f.grade === want);
 paginate('sa-feed', 'sa-feed-pager', rows, f => {
  const buy = f.side === 'BUY', o = f.outcome || {}, rr = o.r;
  const out = o.status === 'CLOSED' ? `${esc(o.exit_reason || 'Closed')} · ${rr == null ? '—' : fmt(rr, 2) + 'R'}`
   : o.status === 'OPEN' ? `Open · ${rr == null ? '—' : fmt(rr, 2) + 'R'}` : '—';
  const [day, time] = String(f.entry_myt || '— ').split(' ');
  return `<tr><td style="white-space:nowrap">${esc(time || '—')}<small class="muted" style="display:block">${esc(day)}</small></td><td>${chip(f.grade, SA_CLS[f.grade])}</td><td>${esc(f.label || f.strategy_id)}</td>`
   + `<td><b>${esc(f.symbol)}</b></td><td class="${buy ? 'buy' : 'sell'}">${esc(f.side)}</td>`
   + `<td>${saPx(f.entry_price)}</td><td>${saPx(f.initial_sl)}</td><td>${saPx(f.initial_tp)}</td>`
   + `<td class="small" style="min-width:300px">${esc(f.reason || '')}</td><td class="${rr == null ? '' : rr >= 0 ? 'buy' : 'sell'}" style="white-space:nowrap">${out}</td></tr>`;
 }, { pageSize: 10, colspan: 10, empty: 'No graded setups match this filter yet.' });
}
$('sa-filter-grade').addEventListener('change', renderSignalAgent);
