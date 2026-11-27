// Grympha Trading Desk dashboard · js/scout.js
// Market Scout tab: hourly read-only market report (cost, volatility, trend, news). Nothing is traded.
// Shares the global scope (core.js helpers: $, esc, fmt, date, chip, paginate, setHTML).
'use strict';
let scoutData = null;
async function refreshScout() {
 try {
  if (demo) { scoutData = { status: 'WAITING', rows: [], recommendations: [], skipped: [], note: 'Sample view: no market data.' }; renderScout(); return; }
  const res = await fetch('api/market-scout', { cache: 'no-store' });
  if (!res.ok) throw Error('HTTP ' + res.status);
  scoutData = await res.json(); renderScout();
 } catch (e) { scoutData = { error: e.message, rows: [], recommendations: [], skipped: [] }; renderScout(); }
}
const MS_ROLE = { LIVE: 'Live', LIVE_TEST: 'Live Test', CANDIDATE: 'Candidate' };
const MS_ORDER = { LIVE: 0, LIVE_TEST: 1, CANDIDATE: 2 };
const MS_FILTER = { MINE: 'My pairs', LIVE: 'Live pairs', LIVE_TEST: 'Live-Test-only pairs', CANDIDATE: 'Candidates', ALL: 'All markets' };
const MS_KIND ={ NEWS: 'warn', COST: 'bad', VOLATILITY: 'warn', TREND: 'good', ADD: 'good' };
function msPct(v) { return v == null ? '—' : (v * 100).toFixed(v < 0.1 ? 1 : 0) + '%'; }
function msWhen(m) { return m < 120 ? 'in ' + m + ' min' : 'in ' + (m / 60).toFixed(1) + ' h'; }
function renderScout() {
 const r = scoutData; if (!r) return;
 $('ms-note').textContent = r.error ? 'Market Scout unavailable: ' + r.error
  : r.status === 'WAITING' ? r.note
  : `Last sweep ${date(r.generated_epoch, true)} · ${r.rows.length} markets in ${r.sweep_seconds}s · repeats every ${r.interval_minutes} min. `
    + (r.news_error ? 'News: ' + r.news_error + '. ' : '') + 'Read-only report; it never trades or changes Market Watch.';
 setHTML('ms-recs', (r.recommendations || []).length ? r.recommendations.map(x =>
  `<div class="health-card"><div>${chip(x.kind, MS_KIND[x.kind] || '')}</div><p class="small" style="margin:6px 0 0">${esc(x.text)}</p></div>`).join('')
  : '<p class="muted small">Nothing stands out right now.</p>');
 const want = $('ms-filter-role').value;
 const all = r.rows || [], count = k => all.filter(x => k === 'ALL' || (k === 'MINE' ? x.role !== 'CANDIDATE' : x.role === k)).length;
 for (const o of $('ms-filter-role').options) o.textContent = MS_FILTER[o.value] + ' (' + count(o.value) + ')';
 // Your pairs first (Live, then Live Test), best score first inside each group; candidates last.
 const rows = all.filter(x => want === 'ALL' || (want === 'MINE' ? x.role !== 'CANDIDATE' : x.role === want))
  .sort((a, b) => (MS_ORDER[a.role] ?? 9) - (MS_ORDER[b.role] ?? 9) || b.score - a.score || a.symbol.localeCompare(b.symbol));
 paginate('ms-rows', 'ms-rows-pager', rows, x => {
  const v = x.volatility || {}, t = x.trend || {}, n = (x.news || [])[0];
  const sc = x.score >= 65 ? 'good' : x.score < 40 ? 'bad' : '';
  const cost = x.cost_pct, costCls = cost == null ? '' : cost <= 0.08 ? 'buy' : cost >= 0.2 ? 'sell' : '';
  const sx = x.spread_x, sxCls = sx == null ? '' : sx >= 2.5 ? 'sell' : '';
  const vCls = v.regime === 'EXTREME' ? 'bad' : v.regime === 'ACTIVE' ? 'warn' : v.regime === 'QUIET' ? '' : 'good';
  const tCls = (t.label || '').startsWith('STRONG') ? 'good' : '';
  return `<tr><td><b>${esc(x.symbol)}</b></td><td>${esc(MS_ROLE[x.role] || x.role)}</td><td>${chip(x.score, sc)}</td>`
   + `<td class="${costCls}">${msPct(cost)}</td><td class="${sxCls}">${sx == null ? '<span class="muted">closed</span>' : sx.toFixed(1) + '×'}</td>`
   + `<td>${chip(v.regime || '—', vCls)}<small class="muted"> ${v.ratio == null ? '' : v.ratio.toFixed(1) + '×'}</small></td>`
   + `<td>${chip(t.label || '—', tCls)}</td>`
   + `<td class="small">${n ? esc(`${n.currency} · ${n.title} · ${msWhen(n.minutes)}`) : '<span class="muted">none in 24 h</span>'}</td></tr>`;
 }, { pageSize: 20, colspan: 8, empty: 'No markets match this filter.' });
 $('ms-skipped').textContent = (r.skipped || []).length ? 'Skipped: ' + r.skipped.map(s => `${s.symbol} (${s.reason})`).join(', ') : '';
}
$('ms-filter-role').addEventListener('change', renderScout);
