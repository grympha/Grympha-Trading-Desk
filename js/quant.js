// Grympha Trading Desk dashboard · js/quant.js
// Quant Agent tab: saved historical backtests beside each agent's Live Test record. Nothing is traded.
// Shares the global scope (core.js helpers: $, esc, date, chip, paginate, setHTML).
'use strict';
let quantData = null;
async function refreshQuant() {
 refreshQuantRun();
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
    + 'Use Run a backtest above to test a strategy again (MT5 must be open).';
 const order = Object.keys(QA_VERDICT);
 setHTML('qa-verdicts', r.verdicts ? order.filter(v => r.verdicts[v]).map(v =>
  `<div class="health-card"><div>${chip(QA_VERDICT[v][0], QA_VERDICT[v][1])}</div><p class="small" style="margin:6px 0 0"><b>${r.verdicts[v]}</b> agents</p></div>`).join('') : '');
 const sSel = $('qa-filter-strategy'), keep = sSel.value;
 const strategies = [...new Set((r.rows || []).map(x => x.strategy_id))].sort();
 sSel.innerHTML = '<option value="ALL">All</option>' + strategies.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
 sSel.value = strategies.includes(keep) ? keep : 'ALL';
 const wantV = $('qa-filter-verdict').value, wantS = sSel.value, current = $('qa-filter-status').value === 'CURRENT';
 // Variant = the agent's label (setup / mode / filter), listed for the chosen strategy.
 const vSel = $('qa-filter-variant'), keepV = vSel.value;
 const variants = [...new Set((r.rows || []).filter(x => (wantS === 'ALL' || x.strategy_id === wantS)
  && (!current || x.agent_status !== 'REMOVED')).map(x => x.label))].sort();
 vSel.innerHTML = '<option value="ALL">All' + (variants.length ? ` (${variants.length})` : '') + '</option>'
  + variants.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
 vSel.value = variants.includes(keepV) ? keepV : 'ALL';
 const wantL = vSel.value;
 const scoped = (r.rows || []).filter(x => (wantS === 'ALL' || x.strategy_id === wantS) && (wantL === 'ALL' || x.label === wantL)
  && (!current || x.agent_status !== 'REMOVED'));
 renderQuantVariants(scoped);
 const rows = scoped.filter(x => wantV === 'ALL' || x.backtest.verdict === wantV);
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
// One line per variant: every pair's backtest of that variant added together (trade-weighted).
function renderQuantVariants(rows) {
 const by = new Map();
 for (const x of rows) {
  const b = x.backtest || {}, g = by.get(x.label) || { label: x.label, pairs: 0, trades: 0, wins: 0, net: 0, pos: 0, edge: 0, losing: 0, lt: 0, ltNet: 0 };
  g.pairs++; g.trades += b.trades || 0; g.wins += (b.win_rate || 0) * (b.trades || 0); g.net += b.net_r || 0;
  g.pos += (b.net_r || 0) > 0; g.edge += b.verdict === 'EDGE (keep testing live)'; g.losing += b.verdict === 'LOSING (switch off)';
  if (x.forward) { g.lt += x.forward.trades || 0; g.ltNet += x.forward.net_r || 0; }
  by.set(x.label, g);
 }
 const list = [...by.values()].sort((a, b) => b.net - a.net);
 $('qa-variants-wrap').hidden = !list.length;
 setHTML('qa-variants', list.map(g => {
  const avg = g.trades ? g.net / g.trades : null;
  return `<tr><td><b>${esc(g.label)}</b></td><td>${g.pairs}</td><td>${g.trades}</td><td>${g.trades ? Math.round(100 * g.wins / g.trades) + '%' : '—'}</td>`
   + `<td class="${qaCls(g.net)}"><b>${qaR(g.net)}</b></td><td class="${qaCls(avg)}">${qaR(avg)}</td><td>${g.pos} / ${g.pairs}</td>`
   + `<td>${g.edge ? chip(g.edge + ' edge', 'good') : ''} ${g.losing ? chip(g.losing + ' losing', 'bad') : ''}${!g.edge && !g.losing ? '<span class="muted">—</span>' : ''}</td>`
   + `<td>${g.lt ? `<span class="${qaCls(g.ltNet)}">${qaR(g.ltNet)}</span><small class="muted"> · ${g.lt} trades</small>` : '<span class="muted">no trades</span>'}</td></tr>`;
 }).join(''));
}
for (const id of ['qa-filter-verdict', 'qa-filter-variant', 'qa-filter-status']) $(id).addEventListener('change', renderQuant);
$('qa-filter-strategy').addEventListener('change', () => { $('qa-filter-variant').value = 'ALL'; renderQuant(); });

// ---- Run a backtest from the dashboard (a separate quant_agent.py process; one run at a time) ----
let qaRun = null, qaRunTimer = null, qaRunWas = null;
const qaClock = s => { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
async function refreshQuantRun() {
 if (demo) { $('qa-run-go').disabled = true; $('qa-run-hint').textContent = 'Not available in the sample view.'; return; }
 try {
  const res = await fetch('api/quant-run', { cache: 'no-store' });
  if (!res.ok) throw Error(res.status === 404 ? 'restart the bot to enable dashboard backtests' : 'HTTP ' + res.status);
  qaRun = await res.json();
 } catch (e) { $('qa-run-hint').textContent = 'Backtest runner unavailable: ' + e.message; $('qa-run-go').disabled = true; return; }
 renderQuantRun();
}
function qaFillOptions() {
 const opts = qaRun.options || [], sSel = $('qa-run-strategy'), keep = sSel.value;
 if (sSel.options.length !== opts.length) {
  sSel.innerHTML = opts.map(o => `<option value="${esc(o.id)}">${esc(o.name)} · ${o.agents} agent${o.agents === 1 ? '' : 's'}</option>`).join('');
  if (opts.some(o => o.id === keep)) sSel.value = keep;
 }
 const dSel = $('qa-run-days');
 if (!dSel.options.length) { dSel.innerHTML = (qaRun.days || []).map(d => `<option value="${d}">${d} days</option>`).join(''); dSel.value = '30'; }
 qaFillVariants();
}
const qaOpt = () => ((qaRun && qaRun.options) || []).find(x => x.id === $('qa-run-strategy').value);
// The chosen variant (or the whole strategy when "All variants"): {agents, symbols}.
function qaScope() {
 const o = qaOpt(); if (!o) return null;
 const v = (o.variants || []).find(x => x.label === $('qa-run-variant').value);
 return v || o;
}
function qaFillVariants() {
 const o = qaOpt(), vSel = $('qa-run-variant'), keep = vSel.value, list = (o && o.variants) || [];
 const sig = (o ? o.id : '') + '|' + list.map(v => v.label + v.agents).join('|');
 if (vSel.dataset.sig !== sig) {
  vSel.dataset.sig = sig;
  vSel.innerHTML = `<option value="ALL">All ${list.length} variants</option>`
   + list.map(v => `<option value="${esc(v.label)}">${esc(v.label)} · ${v.agents} agent${v.agents === 1 ? '' : 's'}</option>`).join('');
  vSel.value = list.some(v => v.label === keep) ? keep : 'ALL';
 }
 $('qa-run-variant-wrap').hidden = list.length < 2;
 qaFillSymbols();
}
function qaFillSymbols() {
 const o = qaScope(), ySel = $('qa-run-symbol'), keep = ySel.value;
 if (!o) { ySel.innerHTML = ''; return; }
 ySel.innerHTML = (o.symbols.length > 1 ? `<option value="ALL">All ${o.symbols.length} pairs</option>` : '') + o.symbols.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
 if ([...ySel.options].some(x => x.value === keep)) ySel.value = keep;
 qaHint();
}
function qaHint() {
 const o = qaScope(); if (!o) return;
 const all = $('qa-run-symbol').value === 'ALL', pairs = all ? o.symbols.length : 1, runs = all ? o.agents : Math.max(1, Math.round(o.agents / o.symbols.length));
 const days = +$('qa-run-days').value || 30, mins = Math.max(1, Math.round(pairs * days / 30));
 $('qa-run-hint').textContent = `${runs} agent run${runs === 1 ? '' : 's'} on ${pairs} pair${pairs === 1 ? '' : 's'} · roughly ${mins}–${mins * 3} min.`;
}
function renderQuantRun() {
 if (!qaRun) return;
 qaFillOptions();
 const j = qaRun.job, box = $('qa-run-status'), running = !!j && j.state === 'RUNNING';
 $('qa-run-go').disabled = running || !(qaRun.options || []).length;
 $('qa-run-stop').hidden = !running;
 for (const id of ['qa-run-strategy', 'qa-run-variant', 'qa-run-symbol', 'qa-run-days']) $(id).disabled = running;
 if (!(qaRun.options || []).length) $('qa-run-hint').textContent = 'No Live Test agents registered yet.';
 clearTimeout(qaRunTimer);
 if (!j) { box.hidden = true; return; }
 const what = `<b>${esc(j.variant || j.name || j.strategy)}</b> · ${j.symbol === 'ALL' ? 'all pairs' : esc(j.symbol)} · ${j.days} days`;
 const pct = j.progress_total ? Math.round(j.progress_done / j.progress_total * 100) : null;
 const prog = j.progress_total ? `${j.progress_done} of ${j.progress_total} agent runs done` : 'loading MT5 history';
 const log = (j.log || []).length ? `<pre>${esc(j.log.join('\n'))}</pre>` : '';
 box.hidden = false;
 if (running) {
  box.className = 'qa-run-status running';
  box.innerHTML = `<span class="qa-spin" aria-hidden="true"></span>Backtest running: ${what} · ${prog} · ${qaClock(j.elapsed)} elapsed.`
   + '<div class="small muted" style="margin-top:4px">You can leave this page; it keeps running in the background. Keep MT5 open. Results appear in the table below when it finishes.</div>'
   + `<div class="qa-bar${pct == null ? ' indeterminate' : ''}"><i style="width:${pct == null ? 30 : Math.max(3, pct)}%"></i></div>`;
 } else if (j.state === 'DONE') {
  box.className = 'qa-run-status done';
  box.innerHTML = `✓ Finished: ${what} in ${qaClock(j.elapsed)} · ${j.saved ?? 0} result${j.saved === 1 ? '' : 's'} saved. Results are in the table below.`;
 } else if (j.state === 'FAILED') {
  box.className = 'qa-run-status failed';
  box.innerHTML = `Backtest failed: ${what} (exit code ${esc(j.exit_code)}). Last output:` + log;
 } else if (j.state === 'STOPPED') {
  box.className = 'qa-run-status'; box.innerHTML = `Stopped: ${what} after ${qaClock(j.elapsed)}. Nothing was saved.`;
 } else {
  box.className = 'qa-run-status'; box.innerHTML = `The bot restarted while ${what} was running, so its progress can't be followed. If it finished, its results are in the table below.`;
 }
 if (qaRunWas === 'RUNNING' && !running && j.state === 'DONE') {     // just finished: show the new results for that strategy
  refreshQuant().then(() => {
   const f = $('qa-filter-strategy');
   if (![...f.options].some(o => o.value === j.strategy)) return;
   f.value = j.strategy; $('qa-filter-variant').value = 'ALL'; renderQuant();
   const v = $('qa-filter-variant');
   if (j.variant && [...v.options].some(o => o.value === j.variant)) { v.value = j.variant; renderQuant(); }
  });
 }
 qaRunWas = j.state;
 if (running) qaRunTimer = setTimeout(refreshQuantRun, 3000);
}
async function qaPost(body) {
 const res = await fetch('api/quant-run', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dashboard-Request': 'controls' }, body: JSON.stringify(body) });
 if (!res.ok) throw Error(res.status === 400 ? 'invalid choice' : (await res.text()) || 'HTTP ' + res.status);
}
$('qa-run-strategy').addEventListener('change', qaFillVariants);
$('qa-run-variant').addEventListener('change', qaFillSymbols);
$('qa-run-symbol').addEventListener('change', qaHint);
$('qa-run-days').addEventListener('change', qaHint);
$('qa-run-go').addEventListener('click', async () => {
 $('qa-run-go').disabled = true;
 const variant = $('qa-run-variant-wrap').hidden || $('qa-run-variant').value === 'ALL' ? null : $('qa-run-variant').value;
 try { await qaPost({ action: 'start', strategy: $('qa-run-strategy').value, variant, symbol: $('qa-run-symbol').value || 'ALL', days: +$('qa-run-days').value }); qaRunWas = 'RUNNING'; }
 catch (e) { $('qa-run-hint').textContent = 'Could not start: ' + e.message; }
 refreshQuantRun();
});
$('qa-run-stop').addEventListener('click', async () => {
 if (!confirm('Stop the running backtest? Nothing from this run will be saved.')) return;
 try { await qaPost({ action: 'stop' }); } catch (e) { $('qa-run-hint').textContent = 'Could not stop: ' + e.message; }
 refreshQuantRun();
});
