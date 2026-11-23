// Grympha Trading Desk dashboard · js/core.js
// Shared helpers ($, esc, fmt, date), global state, snapshot() and small shared utilities.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const demo=new URLSearchParams(location.search).get('demo')==='1';
let view='BBMA';
let data=null,selected='',paused=false,apiOnline=false,serverDelta=0,fetching=false;
const now=()=>Date.now()/1000+serverDelta;
const fmt=(p,d=3)=>p!==null&&p!==undefined&&p!==''&&Number.isFinite(Number(p))?Number(p).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const date=(s,full=false)=>{let d=new Date(typeof s==='number'?s*1000:s);return Number.isFinite(+d)?d.toLocaleString(undefined,full?{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}:{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';};
// MT5 server (broker) wall clock. Broker deal epochs are stored as server time, so reading them
// in UTC reproduces the broker clock. full=true adds the date; otherwise just HH:MM:SS.
const serverTime=(s,full=false)=>{if(s==null||s==='')return '—';const d=new Date(typeof s==='number'?s*1000:s);if(!Number.isFinite(+d))return '—';const z=n=>String(n).padStart(2,'0'),t=`${z(d.getUTCHours())}:${z(d.getUTCMinutes())}:${z(d.getUTCSeconds())}`;return full?`${z(d.getUTCMonth()+1)}-${z(d.getUTCDate())} ${t}`:t;};
const ageText=s=>s<0?'Clock difference':s<60?`${Math.floor(s)}s ago`:s<3600?`${Math.floor(s/60)}m ago`:`${Math.floor(s/3600)}h ago`;
const chip=(s,cls='')=>`<span class="chip ${cls}">${esc(s)}</span>`;
// v1.20: rewrite an element only when its markup actually changed (keeps hover/scroll/focus and saves layout work)
const setHTML=(id,html)=>{const el=$(id);if(el&&el._html!==html){el.innerHTML=html;el._html=html;}};
// v1.32: reusable client-side pagination for any table body. Keeps the current page across 5s refreshes.
const pagerState={},pagerRerender={};
function paginate(bodyId,pagerId,items,rowHtml,opts={}){
 const size=opts.pageSize||10,colspan=opts.colspan||1,empty=opts.empty||'No data.';
 const body=$(bodyId);if(!body)return;
 const list=items||[],total=list.length,pages=Math.max(1,Math.ceil(total/size));
 let page=pagerState[bodyId]||0;page=Math.min(Math.max(0,page),pages-1);pagerState[bodyId]=page;
 const start=page*size,slice=list.slice(start,start+size);
 setHTML(bodyId,slice.map((it,i)=>rowHtml(it,start+i)).join('')||`<tr><td colspan="${colspan}" class="empty">${empty}</td></tr>`);
 pagerRerender[bodyId]=()=>paginate(bodyId,pagerId,list,rowHtml,opts);
 const pager=pagerId&&$(pagerId);
 if(pager){
  if(total<=size){setHTML(pagerId,'');}
  else{
   const from=page*size+1,to=Math.min(total,(page+1)*size);
   setHTML(pagerId,`<button type="button" class="pager-btn" data-page-prev ${page===0?'disabled':''}>‹ Prev</button>`+
    `<span class="pager-info">${from}–${to} of ${total} · page ${page+1}/${pages}</span>`+
    `<button type="button" class="pager-btn" data-page-next ${page>=pages-1?'disabled':''}>Next ›</button>`);
  }
 }
}
function pagerGo(bodyId,delta){if(pagerState[bodyId]==null)return;pagerState[bodyId]+=delta;pagerRerender[bodyId]&&pagerRerender[bodyId]();}
function snapshot(){
 if(!data)return null;
 const t=now(),ttl=data.freshness_seconds||60,meta=data.meta||{};
 const online=apiOnline&&!paused&&!!meta.running&&t-(meta.heartbeat||0)<=ttl;
 const allSymbols=data.symbols.map(x=>({...x,strategy:x.strategy||'BBMA',fresh:online&&t-x.updated_epoch<=(x.freshness_seconds||ttl)&&x.updated_epoch>=(meta.started||0)}));
 const by=Object.fromEntries(allSymbols.map(x=>[`${x.strategy}|${x.symbol}`,x]));
 const allSignals=data.signals.map(x=>({...x,strategy:x.strategy||'BBMA'}));
 const conf=meta.strategies?.[view]||{symbols:meta.symbols||[],check_seconds:meta.poll_seconds||15,enabled:view==='BBMA'};
 return {...data,bot_online:online,allSymbols,allSignals,symbols:allSymbols.filter(x=>x.strategy===view),signals:allSignals.filter(x=>x.strategy===view),meta:{...meta,symbols:conf.symbols,poll_seconds:conf.check_seconds,view_enabled:conf.enabled}};
}
function renderComparison(s){
 const cfg=data.meta?.strategies||{};
 const common=(cfg.BBMA?.symbols||[]).filter(sym=>(cfg.CONFLUENCE?.symbols||[]).includes(sym));
 $('comparison').innerHTML=common.length?common.map(sym=>{
 const b=s.allSymbols.find(x=>x.strategy==='BBMA'&&x.symbol===sym),c=s.allSymbols.find(x=>x.strategy==='CONFLUENCE'&&x.symbol===sym);
 const fresh=b?.fresh&&c?.fresh&&b.health==='OK'&&c.health==='OK',bs=b?.condition?.side,cs=c?.condition?.side;
 const state=!fresh?'Waiting / stale':bs&&cs?(bs===cs?'Aligned bias':'Conflicting bias'):'No shared bias';
 return `<div class="compare-item"><strong>${esc(sym)}</strong><div class="directions"><span>BBMA: ${esc(fresh?bs||'NO BIAS':'Unavailable')}</span><span>Confluence: ${esc(fresh?cs||'HOLD':'Unavailable')}${fresh?' · '+(c.score??0)+'/5':''}</span></div>${chip(state,fresh&&bs&&cs?(bs===cs?'good':'warn'):'')}</div>`;
 }).join(''):'<span class="muted small">No instruments are enabled for both strategies.</span>';
}
function names(s){return [...new Set([...(s.meta.symbols||[]),...s.symbols.map(x=>x.symbol)])];}
function latest(s,sym){return s.signals.find(x=>x.symbol===sym);}
function stage(x,plan){
 if(!x?.fresh||x.health!=='OK')return ['Unavailable','Unavailable','WAITING'];
 const trade=(data.demo_orders||[]).find(d=>d.strategy===view&&d.symbol===x.symbol);
 return [x.condition?.name||'Unknown',view==='CONFLUENCE'?`${x.score??0}/5 · ${x.qualified?'Qualified':'Waiting'}`:view==='ORB'?(x.orb_status||'WAITING').replaceAll('_',' '):(x.setup?.type||plan?.setup||'Waiting').replaceAll('_',' '),trade?tradeAction(trade):'WAITING'];
}

function setOptions(el,items,includeAll){const signature=JSON.stringify([items,includeAll]);if(el.dataset.signature===signature)return;el.dataset.signature=signature;const old=el.value;el.innerHTML=(includeAll?'<option value="ALL">All pairs</option>':'')+items.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');if(items.includes(old)||old==='ALL'&&includeAll)el.value=old;}
