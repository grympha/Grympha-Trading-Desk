// Run: node test_livetest_ui.cjs. Offline render test for the Live Test tab.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const src = fs.readFileSync(__dirname + '/js/livetest.js', 'utf8');
const html = fs.readFileSync(__dirname + '/dashboard.html', 'utf8');

const nodes = {};
const $ = id => nodes[id] ??= { innerHTML: '', value: 'ALL', options: [], addEventListener() {} };
const setHTML = (id, h) => { $(id).innerHTML = h; };
const paginate = (b, _p, items, row, o = {}) => {
 const l = items || [];
 $(b).innerHTML = l.length ? l.slice(0, o.pageSize || 10).map((it, i) => row(it, i)).join('')
  : '<tr><td colspan="' + (o.colspan || 1) + '" class="empty">' + (o.empty || '') + '</td></tr>';
};
const ctx = {
 $, setHTML, paginate, demo: false,
 esc: v => String(v ?? '').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
 fmt: (v, n = 2) => (v == null ? '—' : Number(v).toFixed(n)),
 chip: (v, c) => `<span class="chip ${c || ''}">${v}</span>`,
};
vm.createContext(ctx);
vm.runInContext(src, ctx);

ctx.fixture = {
 agents: [{
  id: 'PREV_CANDLE_BREAKOUT_H1|XAUUSD|abc', strategy_id: 'PREV_CANDLE_BREAKOUT_H1',
  label: 'Previous Candle Breakout H1', symbol: 'XAUUSD', config_id: 'abc', strategy_version: 'prev_candle_h1@1',
  status: 'RUNNING', feed_status: 'LIVE', last_tick_myt: '2024-01-01 09:00:00 MYT',
  last_signal: { side: 'BUY', reason: '<x>' },
  stats: { valid_trades: 3, win_rate: 0.5, net_r: 1.2, affected_by_data: 1 },
  open_positions: [{
   strategy_id: 'PREV_CANDLE_BREAKOUT_H1', symbol: 'XAUUSD', side: 'BUY', entry_price: 2000.7,
   current_price: 2001, initial_sl: 1997.5, initial_tp: 2006.7, unrealized_pnl: 0.3, unrealized_r: 0.1,
   age_seconds: 120, data_quality_status: 'OK'
  }]
 }],
 recent_trades: [{
  trade_id: 't1', strategy_id: 'PREV_CANDLE_BREAKOUT_H1', symbol: 'XAUUSD', side: 'BUY',
  entry_price: 2000.7, exit_price: 2006.7, exit_reason: 'TP', price_pnl: 6, pnl_r: 1.87,
  data_quality_status: 'OK', closed_myt: '2024-01-01 10:00:00 MYT'
 }],
 by_symbol: [{ symbol: 'XAUUSD', agents: 7, running: 2, feed: 'LIVE', signals: 4, no_fill: 1,
  closed: 3, win_rate: 0.5, net_r: 1.2, data_gap: 1 }],
 recent_signals: [{ epoch_myt: '2024-01-01 09:30:00 MYT', strategy_id: 'H4_LIQ_SWEEP_FVG',
  symbol: 'XAUUSD', side: 'BUY', event: 'NO_FILL', reason: 'TARGET_HIT_NO_RETEST', h4_target_price: 2010 }]
};
vm.runInContext('liveTestData = fixture; renderLiveTest();', ctx);

// agent card: running agent shows Pause, escapes the signal reason, shows stats
assert.ok($('lt-agents').innerHTML.includes('Previous Candle Breakout H1'));
assert.ok($('lt-agents').innerHTML.includes('Pause agent'));
assert.ok($('lt-agents').innerHTML.includes('&lt;x&gt;'), 'signal reason must be HTML-escaped');
// open position + trade rows render
assert.ok($('lt-positions').innerHTML.includes('XAUUSD'));
assert.ok($('lt-trades').innerHTML.includes('TP'));
assert.ok($('lt-trades').innerHTML.includes('1.87R'));
// per-symbol summary + signal log render
assert.ok($('lt-symbols').innerHTML.includes('XAUUSD'), 'per-symbol summary renders');
assert.ok($('lt-symbols').innerHTML.includes('50.0%'), 'win rate in per-symbol summary');
assert.ok($('lt-signals').innerHTML.includes('NO_FILL'), 'signal log renders NO_FILL');
assert.ok($('lt-signals').innerHTML.includes('TARGET_HIT_NO_RETEST'), 'signal log reason renders');

// empty state
vm.runInContext('liveTestData = {agents:[],recent_trades:[]}; renderLiveTest();', ctx);
assert.ok($('lt-positions').innerHTML.includes('No open simulated positions'));
assert.ok($('lt-trades').innerHTML.includes('No closed simulated trades'));

// error state surfaces in the note
vm.runInContext('liveTestData = {error:"boom",agents:[],recent_trades:[]}; renderLiveTest();', ctx);
assert.ok($('lt-note').innerHTML.includes('boom') || $('lt-note').textContent.includes('boom'));

// HTML wiring: each id present exactly once
for (const id of ['tab-livetest', 'panel-livetest', 'lt-agents', 'lt-positions', 'lt-positions-pager',
 'lt-trades', 'lt-trades-pager', 'lt-csv-all', 'lt-csv-filtered', 'lt-filter-strategy', 'lt-filter-status',
 'lt-start-all', 'lt-pause-all', 'lt-symbols', 'lt-signals', 'lt-signals-pager', 'lt-signals-csv']) {
 assert.equal((html.match(new RegExp('id="' + id + '"', 'g')) || []).length, 1, 'missing/duplicate id ' + id);
}
// Live Test is wired into the page tabs
assert.ok(/pageNames\s*=\s*\[[^\]]*'livetest'[^\]]*\]/.test(fs.readFileSync(__dirname + '/js/app.js', 'utf8')));
console.log('Live Test UI: agent cards, positions, trades, filters, empty/error states and tab wiring passed');
