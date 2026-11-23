// Grympha Trading Desk dashboard · js/history.js
// Trading History (daily deals) and Trade Review / performance export.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
let dailyData=null,dailyRequest=0,dailyFollowToday=true;
function renderDaily(){
 const d=dailyData,valid=d?.status==='READY',scope=$('daily-scope').value;
 $('trading-history-date').value=$('daily-date').value;
 $('trading-history-date').max=$('daily-date').max;
 $('trading-history-scope').value=scope;
 const deals=d?.deals||[];
 const pairFilter=$('trading-history-pair');
 const configured=Object.values(data?.meta?.strategies||{}).flatMap(x=>x.symbols||[]);
 setOptions(pairFilter,[...new Set([...configured,...deals.map(e=>e.symbol).filter(Boolean)])].sort(),true);
 const rows=deals.filter(e=>(pairFilter.value==='ALL'||e.symbol===pairFilter.value)&&
  (scope==='ALL'||scope==='BOT'&&['BBMA','CONFLUENCE','ORB'].includes(e.strategy)||e.strategy===scope));
 const exits=new Set(rows.filter(e=>[1,2,3].includes(e.entry)&&e.position_id>0).map(e=>e.position_id));
 const gross=rows.reduce((v,e)=>v+e.profit,0),costs=rows.reduce((v,e)=>v+e.commission+e.swap+e.fee,0),net=gross+costs;
 const stale=valid&&(!apiOnline||paused||!snapshot()?.bot_online||now()-(d.updated_epoch||0)>(d.date===d.today?Math.max(90,(data?.meta?.poll_seconds||15)*4):360));
 $('daily-title').textContent=d?.date===d?.today?'Today History':'History · '+(d?.date||$('daily-date').value||'Select date');
 $('trading-history-summary').textContent=(d?.date||$('daily-date').value||'Select date')+' · '+$('daily-scope').selectedOptions[0].textContent+' · '+(valid?rows.length+' trading deals (latest 200 shown)':'Waiting for broker history')+' · '+$('trading-history-pair').selectedOptions[0].textContent;
 $('daily-count').textContent=valid?exits.size:'—';
 $('daily-net').textContent=valid?(net>=0?'+':'')+fmt(net,2)+' '+d.currency:'—';
 $('daily-net').className=valid?(net>=0?'buy':'sell'):'';
 $('daily-gross').textContent=valid?fmt(gross,2)+' '+d.currency:'—';
 $('daily-costs').textContent=valid?'Commission / swap / fees: '+fmt(costs,2)+' '+d.currency:'—';
 $('daily-state').textContent=!d?'Waiting':d.status==='LOADING'?'Loading':valid?(stale?'Last reported / stale':'Loaded'):'Unavailable';
 if(d?.timezone_offset_minutes!=null){const offset=d.timezone_offset_minutes,abs=Math.abs(offset);$('daily-zone').textContent='Day boundary UTC'+(offset>=0?'+':'-')+String(Math.floor(abs/60)).padStart(2,'0')+':'+String(abs%60).padStart(2,'0')+' · Read '+date(d.updated_epoch,true);}
 $('daily-note').textContent=d?.error||d?.note||'Waiting for selected date to be read from MT5. Partial closes count once per position/day.';
 const zone=d?.timezone_offset_minutes??480;
 const localTime=epoch=>new Date((epoch+zone*60)*1000).toISOString().slice(11,19);
 if(!valid){setHTML('daily-deals','<tr><td colspan="6" class="empty">'+esc(d?.error||'Loading selected-day broker history; allow one bot check cycle.')+'</td></tr>');setHTML('daily-deals-pager','');}
 else paginate('daily-deals','daily-deals-pager',rows.slice(-200).reverse(),e=>`<tr><td>${localTime(e.epoch)}</td><td>${esc(e.ticket)}<small>Position ${esc(e.position_id)}</small></td><td>${esc(e.symbol)}<small>${esc(e.strategy)}</small></td><td>${esc(({0:'ENTRY',1:'EXIT / PARTIAL',2:'REVERSAL',3:'CLOSE BY'})[e.entry]||e.entry)}<small>${esc(e.side)} deal</small></td><td>${esc(e.volume)}<small>${esc(e.price)}</small></td><td class="${e.net>=0?'buy':'sell'}">${fmt(e.net,2)} ${esc(d.currency)}</td></tr>`,{pageSize:10,colspan:6,empty:'No trading deals for this date and scope.'});
}
async function refreshDaily(){
 if(!data)return;
 const today=data.meta?.daily_today;
 if(dailyFollowToday&&today)$('daily-date').value=today;
 if(today)$('daily-date').max=today;
 const selected=$('daily-date').value,request=++dailyRequest;
 try{
  let result;
  if(demo){const day=selected||today,begin=Date.parse(day+'T00:00:00+08:00')/1000;const sampleDeals=day===today?[10,-5,15,7].map((profit,i)=>({ticket:9001+i,position_id:8001+i,symbol:i%2?'AUDUSD':'XAUUSD',strategy:i%2?'CONFLUENCE':'BBMA',side:'SELL',entry:1,epoch:begin+36000+i*600,volume:.01,price:1.1,profit,commission:-.5,swap:0,fee:0,net:profit-.5})):[];result={status:'READY',date:day,today,currency:'USD',timezone_offset_minutes:480,updated_epoch:now(),deals:sampleDeals,note:'ILLUSTRATIVE SAMPLE: four positions, net +25 USD. No MT5 account history connected. Other selected dates have no sample data.'};}
  else{const response=await fetch('api/daily'+(selected?'?date='+encodeURIComponent(selected):''),{cache:'no-store'});if(!response.ok)throw Error('History request failed: HTTP '+response.status);result=await response.json();}
  if(request!==dailyRequest)return;
  dailyData=result;if(!$('daily-date').value)$('daily-date').value=result.date;if(result.today)$('daily-date').max=result.today;renderDaily();
 }catch(error){if(request!==dailyRequest)return;dailyData={status:'UNAVAILABLE',date:selected,error:error.message,deals:[]};renderDaily();}
}
$('daily-date').onchange=()=>{dailyFollowToday=false;dailyData=null;renderDaily();refreshDaily();};
$('daily-today').onclick=()=>{dailyFollowToday=true;$('daily-date').value=data?.meta?.daily_today||'';dailyData=null;renderDaily();refreshDaily();};
$('daily-scope').onchange=renderDaily;
$('trading-history-date').onchange=()=>{$('daily-date').value=$('trading-history-date').value;$('daily-date').onchange();};
$('trading-history-today').onclick=()=>$('daily-today').onclick();
$('trading-history-pair').onchange=renderDaily;
$('trading-history-scope').onchange=()=>{$('daily-scope').value=$('trading-history-scope').value;renderDaily();};

let reviewData=null,reviewRequest=0;
let reviewDates={};
function renderReview(){
 const target=$('review-rows');if(!target)return;
 const symbols=[...new Set([...Object.values(data?.meta?.strategies||{}).flatMap(x=>x.symbols||[]),...(reviewData?.reviews||[]).map(x=>x.symbol)])];
 setOptions($('review-symbol'),symbols,true);
 const r=reviewData;
 $('review-state').textContent=!r?'Waiting for review data':r.error?'UNAVAILABLE · '+r.error:(apiOnline&&!paused&&r.updated_epoch&&now()-r.updated_epoch<=120?r.status:'LAST REPORTED / WAITING')+' · '+(r.total||0)+' closed trades reviewed';
 $('review-note').textContent=r?.note||'Observer only. No automatic changes to rules, SL/TP or orders.';
 $('review-totals').innerHTML=Object.entries(r?.currencies||{}).map(([currency,c])=>`<div class="review-total"><span>${esc(currency)} · ${c.count} trades</span><strong class="${c.net>=0?'buy':'sell'}">${fmt(c.net,2)} ${esc(currency)}</strong><small>${c.wins} wins · ${c.losses} losses · ${c.flat} flat</small></div>`).join('');
 const pairs=r?.pairs||[];
 $('review-pairs').innerHTML=pairs.length?pairs.map(p=>{
  const known=p.wins+p.losses+p.flat;
  const rate=known?fmt(100*p.wins/known,1)+'%':'—';
  const net=p.net==null?'—':fmt(p.net,2)+' '+esc(p.currency);
  const missing=p.unknown?' ('+p.unknown+' unknown)':'';
  return `<tr><td>${esc(p.symbol)}</td><td>${p.total}</td><td>${p.tp}</td><td>${p.be??0}</td><td>${p.sl}</td><td>${p.other}</td><td>${p.wins} / ${p.losses} / ${p.flat}${missing}</td><td>${rate}</td><td>${net}</td></tr>`;
 }).join(''):'<tr><td colspan="9" class="empty">'+(r&&!r.error?'No closed trades match these filters.':'Waiting for review data.')+'</td></tr>';
 $('review-patterns').innerHTML=(r?.patterns||[]).map(p=>`<article class="review-pattern"><b>${esc(p.symbol)} · ${esc(p.strategy)} · ${esc(p.setup)} · ${esc(p.side)}</b> ${chip(p.status,p.status==='UNDER REVIEW'?'warn':'')}<p class="small">${p.count} trades · ${p.wins} wins / ${p.losses} losses · Net ${fmt(p.net,2)} ${esc(p.currency)} · SL losses ${p.sl_losses} · Sampled TP1 then loss ${p.tp1_then_loss}</p>${p.suggestions.map(t=>`<p>${esc(t)}</p>`).join('')}<small class="muted">${esc(p.evidence)}</small></article>`).join('')||'<p class="muted">COLLECTING DATA · No closed bot-ledger trades available for pattern review.</p>';
 paginate('review-rows','review-pager',(r?.reviews||[]),t=>`<tr data-review="${esc(t.key)}" role="button" tabindex="0"><td>${date(t.closed_epoch,true)}</td><td><b>${esc(t.symbol)}</b><small>${esc(t.strategy)}</small></td><td>${esc(t.setup)}<small>${esc(t.side)}</small></td><td class="${t.outcome==='LOSS'?'sell':'buy'}">${t.realised==null?'Unavailable':fmt(t.realised,2)+' '+esc(t.currency)}<small>${esc(t.outcome)}</small></td><td>${t.sampled_mfe_r==null?'—':fmt(t.sampled_mfe_r,2)+'R'} / ${t.sampled_mae_r==null?'—':fmt(t.sampled_mae_r,2)+'R'}<small>${t.samples} samples · lower bounds</small></td><td>${esc(t.exit_reason)}<small>${esc(t.tags.join(' · ')||t.review_status)}</small></td><td>↗</td></tr>`,{pageSize:10,colspan:7,empty:'No reviewed closed bot trades for these filters.'});
}
async function refreshReview(){
 const request=++reviewRequest;
 try{
  if(demo){reviewData={status:'COLLECTING DATA',total:0,reviews:[],patterns:[],currencies:{},note:'Sample view: no actual trade research data.'};renderReview();return;}
  const q=reviewQuery();
  const res=await fetch('api/review?'+q,{cache:'no-store'});if(!res.ok)throw Error('HTTP '+res.status+' · install the complete v1.10 update');
  const result=await res.json();if(request!==reviewRequest)return;reviewData=result;renderReview();
 }catch(e){if(request!==reviewRequest)return;reviewData={error:e.message,reviews:[],patterns:[],currencies:{}};renderReview();}
}
function reviewDetails(key){
 const t=reviewData?.reviews?.find(t=>t.key===key);if(!t)return;
 const fields=[['Condition',t.before?.condition?.name||'Not captured'],['Setup',t.setup],['Side',t.side],['Planned entry',t.reference],['Actual fill',t.entry],['Original SL',t.sl],['Last broker SL',t.final_sl],['Last broker TP',t.final_tp],['TP1',t.tp1],['TP2',t.tp2],['Exit',t.exit_price],['Exit reason',t.exit_reason],['Net result',t.realised==null?'Unknown':fmt(t.realised,2)+' '+t.currency],['Adverse fill (R)',t.slippage_r==null?'Unknown':fmt(t.slippage_r,3)],['Net / estimated initial risk',t.realised_r_estimate==null?'Unavailable':fmt(t.realised_r_estimate,2)+'R'],['Sampled MFE / MAE',`${t.sampled_mfe_r==null?'Unknown':fmt(t.sampled_mfe_r,2)+'R'} / ${t.sampled_mae_r==null?'Unknown':fmt(t.sampled_mae_r,2)+'R'}`],['UTC entry hour',t.utc_hour??'Not captured']];
 $('detail-content').innerHTML=`<div class="eyebrow">OBSERVER ONLY · ${esc(t.strategy)} · ${esc(t.symbol)}</div><h2>Trade review · ${esc(t.outcome)}</h2><div class="detail-grid">${fields.map(([k,v])=>`<div><span>${esc(k)}</span>${esc(v??'Unknown')}</div>`).join('')}</div><h3>Data quality</h3>${t.data_quality.map(x=>`<p>${esc(x)}</p>`).join('')}<p class="small muted">Sampled excursions are lower bounds, not complete tick histories. This review does not establish the cause of a loss.</p><h3>Limited rule audit</h3>${Object.entries(t.limited_audit).map(([k,v])=>`<p>${esc(k.replaceAll('_',' '))}: <b>${v==null?'UNKNOWN':v?'PASS':'REVIEW'}</b></p>`).join('')}<h3>Captured entry context</h3><pre class="review-json">${esc(JSON.stringify(t.before,null,2))}</pre><h3>Management record</h3><pre class="review-json">${esc(JSON.stringify(t.management,null,2))}</pre><h3>Sampled SL / TP / volume changes</h3><pre class="review-json">${esc(JSON.stringify(t.protection_samples||[],null,2))}</pre>`;
 $('details').showModal();
}
function reviewQuery(){
 return new URLSearchParams({strategy:$('review-strategy').value,symbol:$('review-symbol').value,...reviewDates});
}
async function downloadPerformance(kind){
 const note=$('review-export-note');
 if(demo){note.textContent='Connect the bot to download a performance report.';return;}
 if(($('review-from').value||'')!==(reviewDates.from||'')||($('review-to').value||'')!==(reviewDates.to||'')){
  note.textContent='Apply dates before downloading the report.';
  return;
 }
 const button=$('review-'+kind);
 button.disabled=true;
 note.textContent='Preparing '+kind.toUpperCase()+' report…';
 try{
  const response=await fetch('api/performance.'+kind+'?'+reviewQuery(),{cache:'no-store'});
  if(!response.ok)throw Error(await response.text());
  const url=URL.createObjectURL(await response.blob());
  const link=document.createElement('a');
  link.href=url;
  link.download='grympha_performance_report.'+kind;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  note.textContent=kind.toUpperCase()+' report downloaded.';
 }catch(error){note.textContent='Download failed. '+error.message;}
 finally{button.disabled=false;}
}
$('review-csv').onclick=()=>downloadPerformance('csv');
$('review-pdf').onclick=()=>downloadPerformance('pdf');
function applyReviewDates(){
 const from=$('review-from').value,to=$('review-to').value;
 if(from&&to&&from>to){
  $('review-range-note').textContent='From must be on or before To. The previous filter is still active.';
  return;
 }
 reviewDates={...(from?{from}:{}),...(to?{to}:{})};
 $('review-range-note').textContent=(from||to?`Close date: ${from||'Any earlier date'} to ${to||'Any later date'}`:'Close date: All dates')+' · MYT (UTC+8), inclusive';
 reviewData=null;
 renderReview();
 refreshReview();
}
$('review-range-note').textContent='Close date: All dates · MYT (UTC+8), inclusive';
$('review-range').onsubmit=e=>{e.preventDefault();applyReviewDates();};
$('review-clear').onclick=()=>{$('review-from').value='';$('review-to').value='';applyReviewDates();};
$('review-strategy').onchange=()=>{reviewData=null;renderReview();refreshReview();};
$('review-symbol').onchange=()=>{reviewData=null;refreshReview();};

