// Grympha Trading Desk dashboard · js/app.js
// refresh() loop, global click/key handlers, timers, page tabs. LOADED LAST: it starts the dashboard.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
async function refresh(){if(fetching||paused)return;fetching=true;try{
 const res=await fetch(demo?'demo.json':'api/status',{cache:'no-store'});if(!res.ok)throw Error(`HTTP ${res.status}`);
 const obj=await res.json();if(!Array.isArray(obj.symbols)||!Array.isArray(obj.signals))throw Error('Invalid response');
 $('build-version').textContent='UI v1.29 · Backend v'+(obj.version||'unknown');data=demo?rebaseDemo(obj):obj;serverDelta=data.server_epoch-Date.now()/1000;apiOnline=true;$('error-banner').classList.add('hidden');lastStatusFetch=Date.now();
 if(document.hidden){checkAlerts();return;} // hidden tab: keep alerts, skip drawing (redrawn on return)
 render();chart();checkAlerts();refreshPageData(currentPage());
 }catch(err){apiOnline=false;$('error-banner').textContent='Dashboard connection lost. Displayed data may be old; entry ideas are withheld until updates resume.';$('error-banner').classList.remove('hidden');render();chart();checkAlerts();}finally{fetching=false;}}
for(const [id,strategy] of [['view-bbma','BBMA'],['view-confluence','CONFLUENCE'],['view-orb','ORB']])$(id).onclick=()=>{view=strategy;selected='';render();chart();};
$('demo-banner').classList.toggle('hidden',!demo);
$('zone').textContent=Intl.DateTimeFormat().resolvedOptions().timeZone||'Local time';
$('refresh').onclick=()=>{if(paused){paused=false;$('pause').textContent='Pause updates';$('pause').setAttribute('aria-pressed','false');}refresh();};
$('pause').onclick=()=>{paused=!paused;$('pause').textContent=paused?'Resume updates':'Pause updates';$('pause').setAttribute('aria-pressed',String(paused));render();chart();if(!paused)refresh();};
$('chart-symbol').onchange=()=>{selected=$('chart-symbol').value;render();chart();};
$('chart-tf').addEventListener('click',e=>{const btn=e.target.closest('[data-tf]');if(!btn)return;chartTf[view]=btn.dataset.tf;try{localStorage.setItem('gtd.chartTf',JSON.stringify(chartTf));}catch{}chart();});
$('chart-bands').onclick=()=>{showBands=!showBands;try{localStorage.setItem('gtd.chartBands',showBands?'1':'0');}catch{}chart();};
$('chart').addEventListener('mousemove',chartHover);
$('chart').addEventListener('mouseleave',()=>{$('chart-cross')?.setAttribute('visibility','hidden');$('chart-tip')?.classList.add('hidden');});
for(const id of ['filter-strategy','filter-symbol','filter-status'])$(id).onchange=()=>{const s=snapshot();if(s)renderHistory(s);};
$('mw-table').onclick=()=>setMarketLayout('table');$('mw-cards').onclick=()=>setMarketLayout('cards');
$('market-rows').addEventListener('click',e=>{const r=e.target.closest('[data-symbol]');if(r){selected=r.dataset.symbol;render();chart();}});
$('market-rows').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){const r=e.target.closest('[data-symbol]');if(r){e.preventDefault();selected=r.dataset.symbol;render();chart();}}});
$('cards').addEventListener('click',e=>{const c=e.target.closest('[data-symbol]');if(c){selected=c.dataset.symbol;render();chart();}});
$('cards').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){const c=e.target.closest('[data-symbol]');if(c){e.preventDefault();selected=c.dataset.symbol;render();chart();}}});
document.addEventListener('click',e=>{const pageBtn=e.target.closest('[data-page-prev],[data-page-next]');if(pageBtn){const pager=pageBtn.closest('[data-pager]');if(pager){pagerGo(pager.dataset.pager,pageBtn.hasAttribute('data-page-next')?1:-1);}return;}const replay=e.target.closest('[data-lab]');if(replay){labDetails(replay.dataset.lab);return;}const audit=e.target.closest('[data-audit]');if(audit){auditDetails(audit.dataset.audit);return;}const review=e.target.closest('[data-review]');if(review){reviewDetails(review.dataset.review);return;}const r=e.target.closest('[data-trade]');if(r)tradeDetails(r.dataset.trade);});
document.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){const replay=e.target.closest('tr[data-lab]');if(replay){e.preventDefault();labDetails(replay.dataset.lab);return;}const audit=e.target.closest('tr[data-audit]');if(audit){e.preventDefault();auditDetails(audit.dataset.audit);return;}const review=e.target.closest('tr[data-review]');if(review){e.preventDefault();reviewDetails(review.dataset.review);return;}const r=e.target.closest('tr[data-trade]');if(r){e.preventDefault();tradeDetails(r.dataset.trade);}}});
$('close-dialog').onclick=()=>$('details').close();
$('export').onclick=()=>{const fields=['strategy','symbol','side','status','actual_entry','reference','volume','sl','tp1','tp2','profit','realised','currency','close_reason'];const q=v=>'"'+String(v??'').replaceAll('"','""')+'"';const rows=(data?.demo_orders||[]).filter(d=>$('filter-strategy').value==='ALL'||d.strategy===($('filter-strategy').value==='CURRENT'?view:$('filter-strategy').value));const csv=[fields.join(','),...rows.map(d=>fields.map(k=>q(String(d[k]??'').replace(/^([=+@-])/,"'$1"))).join(','))].join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download='mt5_trades.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};

let lastTemporalState='';
setInterval(()=>{ if(document.hidden)return;$('clock').textContent=new Date().toLocaleTimeString();const s=snapshot();if(s){const signature=JSON.stringify([s.bot_online,view,s.allSymbols.map(x=>x.fresh),[s.meta.mt5_account?.updated_epoch,Math.floor(now()/10)]]);if(signature!==lastTemporalState){lastTemporalState=signature;render();chart();}}},1000);
// v1.20: poll every 5 s while visible; every 15 s while the browser tab is hidden (alerts still fire).
let lastStatusFetch=0;
setInterval(()=>{if(!paused&&(!document.hidden||Date.now()-lastStatusFetch>=15000))refresh();},5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!paused){render();chart();refresh();}});
const pageRuns={};
const due=(name,ms)=>{const t=Date.now();if(t-(pageRuns[name]||0)<ms)return false;pageRuns[name]=t;return true;};
function currentPage(){const h=location.hash.slice(1);return pageNames.includes(h)?h:'overview';}
// Extra endpoints only for the page on screen. Overview needs daily P/L, open risk and equity, but not every 5 s.
function refreshPageData(page,now=false){
 if(!data)return;
 if(page==='trades'||page==='overview'&&(now||due('daily',30000)))refreshDaily();
 if(page==='monitor'||page==='overview'&&(now||due('platform',30000)))refreshPlatform();
 if(page==='overview')refreshEquity(now);
 if(page==='review'){refreshReview();refreshAI();}
 if(page==='lab')refreshLab();
}
refresh();

// Page tabs preserve filters, current strategy and background polling.
const pageNames=['overview','positions','ledger','trades','history','review','lab','monitor','settings'];
function showPage(){
 const requested=location.hash.slice(1),page=pageNames.includes(requested)?requested:'overview';
 for(const name of pageNames){const active=name===page,tab=$('tab-'+name);$('panel-'+name).hidden=!active;tab.classList.toggle('selected',active);tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;}
 if(page==='overview')chart();
 render();refreshPageData(page,true);
}
for(const [index,name] of pageNames.entries()){
 const tab=$('tab-'+name);
 tab.addEventListener('keydown',e=>{let target=null;if(e.key==='ArrowRight'||e.key==='ArrowDown')target=(index+1)%pageNames.length;if(e.key==='ArrowLeft'||e.key==='ArrowUp')target=(index+pageNames.length-1)%pageNames.length;if(e.key==='Home')target=0;if(e.key==='End')target=pageNames.length-1;if(target!==null){e.preventDefault();location.hash=pageNames[target];showPage();$('tab-'+pageNames[target]).focus();}});
}
window.addEventListener('hashchange',showPage);
showPage();

// ---- v1.21 mobile: charts follow the screen width; tables become labelled cards on phones ----
let resizeTimer=0,lastChartWidth=0;
window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{const w=$('chart').clientWidth;if(Math.abs(w-lastChartWidth)<8)return;lastChartWidth=w;chart();renderEquity();},200);});
function labelTableCells(){
 for(const table of document.querySelectorAll('.table-wrap table')){
  const heads=[...table.querySelectorAll('thead th')].map(th=>(th.childNodes[0]?.textContent||th.textContent).trim());
  if(!heads.length)continue;
  for(const tr of table.tBodies[0]?.rows||[])[...tr.cells].forEach((td,i)=>{if(!td.hasAttribute('colspan')&&heads[i]&&td.dataset.label!==heads[i])td.dataset.label=heads[i];});
 }
}
let labelQueued=false;
new MutationObserver(()=>{if(labelQueued)return;labelQueued=true;requestAnimationFrame(()=>{labelQueued=false;labelTableCells();});}).observe($('dashboard'),{childList:true,subtree:true});
labelTableCells();
