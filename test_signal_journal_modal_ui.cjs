// Regression: the Signal Journal trade-detail modal must CLOSE when the X is clicked.
// Bug was: tradeDetails() set data-trade ON THE <dialog>, and the global click
// delegate (app.js) does e.target.closest('[data-trade]'). Clicking the close button
// closed the dialog, then the bubbled click matched the dialog as a [data-trade]
// ancestor and immediately re-opened it. Fix: store the key as data-trade-key.
const fs=require('node:fs'),assert=require('node:assert/strict');
const charts=fs.readFileSync(__dirname+'/js/charts.js','utf8');
const app=fs.readFileSync(__dirname+'/js/app.js','utf8');

// 1) The dialog must no longer carry a plain data-trade attribute.
assert.ok(!/\$\('details'\)\.dataset\.trade\b(?!Key)/.test(charts),
 'charts.js must not set dataset.trade on the details dialog');
assert.ok(charts.includes("$('details').dataset.tradeKey=key"),
 'charts.js should mark the open trade with dataset.tradeKey');

// 2) The global delegate still opens trades via closest([data-trade]).
assert.ok(app.includes("closest('[data-trade]')"),"app.js keeps the [data-trade] delegate");

// 3) Behavioural model of the click path (dialog + close button + delegate).
function node(attrs={},parent=null){return {dataset:attrs,parentNode:parent,
 closest(sel){const key=sel.replace(/^\[data-/,'').replace(/\]$/,'');const camel=key.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
  let n=this;while(n){if(n.dataset&&camel in n.dataset)return n;n=n.parentNode;}return null;}};}

function simulate(dialogDatasetKey){
 let open=true;
 const dialog=node({[dialogDatasetKey]:'trade123'});
 dialog.open=true;dialog.close=()=>{open=false;dialog.open=false;};dialog.showModal=()=>{open=true;dialog.open=true;};
 const closeBtn=node({},dialog); // the X button: no data-trade of its own
 // (a) button's own handler (app.js: $('close-dialog').onclick)
 dialog.close();
 // (b) the same click bubbles to document -> global delegate (app.js:28)
 const r=closeBtn.closest('[data-trade]');
 if(r){ /* tradeDetails(key) -> reopens */ if(!dialog.open)dialog.showModal(); }
 return open;
}

// Old behaviour (dialog carried data-trade) => bug: ends up OPEN again.
assert.equal(simulate('trade'),true,'control: the old data-trade attribute reproduces the reopen bug');
// Fixed behaviour (dialog carries data-trade-key) => stays CLOSED.
assert.equal(simulate('tradeKey'),false,'fixed: close button closes the modal and it stays closed');

console.log('Signal Journal modal close: reopen bug reproduced on data-trade, fixed with data-trade-key');
