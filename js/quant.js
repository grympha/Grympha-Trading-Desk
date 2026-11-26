// Grympha Trading Desk dashboard · js/quant.js
// Quant Agent tab: saved historical backtests beside each agent's Live Test record. Nothing is traded.
// Shares the global scope (core.js helpers: $, esc, date, chip, paginate, setHTML).
'use strict';
let quantData = null;
async function refreshQuant() {
 try {
  if (demo) { quantData = { status: 'WAITING', rows: [], note: 'Sample view: no backtests.' }; renderQuant(); return; }
  const res = await fetch('api/quant-agent', { cache: 'no-store' });
  if (!res.ok) throw Error('HTTP ' + res.status);
  quantData = await res.json(); renderQuant();
 } catch (e) { quantData = { error: e.message, rows: [] }; renderQuant(); }
}
const QA_VERDICT = { 'EDGE (keep testing live)': ['Edge', 'good'], 'NO CLEAR EDGE': ['No clear edge', 'warn'],
 'LOSING (switch off)': ['Losing', 'bad'], 'TOO FEW TRADES': ['Too few trades', ''] };
function qaR(v) { return v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + 'R'; }
function qaCls(v) { return v == null ? '' : v > 0 ? 'buy' : v < 0 ? 'sell' : ''; }
function qaPct(v) { return v == null ? '—' : Math.round(v * 100) + '%'; }
function qaDay(e) { return e ? new Date(e * 1000).toISOString().slice(0, 10) : '—'; }
function renderQuant() {
 const r = quantData; if (!r) return;
 $('qa-note').textContent = r.error ? 'Quant Agent unavailable: ' + r.error
  : r.status === 'WAITING' ? r.note
  : `Last run ${date(r.last_run_epoch, true)} · history ${qaDay(r.window_start)} to ${qaDay(r.window_end)} · ${r.rows.length} agents. `
    + 'Fills come from M1 candles, not ticks, so quick wicks can trigger stops the live 15 s poll misses. '
    + 'To refresh: python quant_agent.py --live --days 30 (MT5 open).';
 const order = Object.keys(QA_VERDICT);
 setHTML('qa-verdicts', r.verdicts ? order.filter(v => r.verdicts[v]).map(v =>
  `<div class="health-card"><div>${chip(QA_VERDICT[v][0], QA_VERDICT[v][1])}</div><p class="small" style="margin:6px 0 0"><b>${r.verdicts[v]}</b> agents</p></div>`).join('') : '');
 const sSel = $('qa-filter-strategy'), keep = sSel.value;
 const strategies = [...new Set((r.rows || []).map(x => x.strategy_id))].sort();
 sSel.innerHTML = '<option value="ALL">All</option>' + strategies.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
 sSel.value = strategies.includes(keep) ? keep : 'ALL';
 const wantV = $('qa-filter-verdict').value, wantS = sSel.value, current = $('qa-filter-status').value === 'CURRENT';
 const rows = (r.rows || []).filter(x => (wantV === 'ALL' || x.backtest.verdict === wantV)
  && (wantS === 'ALL' || x.strategy_id === wantS) && (!current || x.agent_status !== 'REMOVED'));
 paginate('qa-rows', 'qa-rows-pager', rows, x => {
  const b = x.backtest, f = x.forward, v = QA_VERDICT[b.verdict] || [b.verdict, ''];
  const oos = b.oos_trades ? `<span class="${qaCls(b.oos_net_r)}">${qaR(b.oos_net_r)}</span><small class="muted"> · ${b.oos_trades}</small>` : '—';
  const live = f ? `<span class="${qaCls(f.net_r)}">${qaR(f.net_r)}</span><small class="muted"> · ${f.trades} · ${qaPct(f.win_rate)}</small>` : '<span class="muted">no trades</span>';
  const st = x.agent_status === 'RUNNING' ? chip('Running', 'good') : x.agent_status === 'REMOVED' ? chip('Removed', 'bad') : chip(x.agent_status[0] + x.agent_status.slice(1).toLowerCase(), '');
  return `<tr><td>${esc(x.label)}</td><td><b>${esc(x.symbol)}</b></td><td>${chip(v[0], v[1])}</td><td>${b.trades ?? '—'}</td>`
   + `<td>${qaPct(b.win_rate)}</td><td class="${qaCls(b.net_r)}"><b>${qaR(b.net_r)}</b></td><td class="${qaCls(b.avg_r)}">${qaR(b.avg_r)}</td>`
   + `<td>${b.profit_factor == null ? '—' : Number(b.profit_factor).toFixed(2)}</td><td class="sell">${qaR(b.max_dd_r)}</td>`
   + `<td class="${qaCls(b.net_r_without_best)}">${qaR(b.net_r_without_best)}</td><td>${oos}</td><td>${live}</td><td>${st}</td></tr>`;
 }, { pageSize: 20, colspan: 13, empty: 'No backtests match this filter.' });
}
for (const id of ['qa-filter-verdict', 'qa-filter-strategy', 'qa-filter-status']) $(id).addEventListener('change', renderQuant);
