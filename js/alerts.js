// Grympha Trading Desk dashboard · js/alerts.js
// Browser alerts: bell panel, toasts, sounds, desktop notifications.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
// ---- v1.17 browser alerts: detected from successive dashboard snapshots (read-only; never sends anything) ----
const alertKinds={entry:'New entry ready',order:'Orders & fills',close:'Position closed',attention:'Needs attention',health:'Connection & health'};
let alertPrefs={sound:true,desktop:false,entry:true,order:true,close:true,attention:true,health:true};
try{Object.assign(alertPrefs,JSON.parse(localStorage.getItem('gtd.alerts')||'{}'));}catch{}
let alertLog=[],alertUnread=0,alertPrev=null,audioCtx=null;
const saveAlertPrefs=()=>{try{localStorage.setItem('gtd.alerts',JSON.stringify(alertPrefs));}catch{}};
function alertTone(kind,good){
 if(!alertPrefs.sound)return;
 try{
  audioCtx=audioCtx||new (window.AudioContext||window.webkitAudioContext)();
  if(audioCtx.state==='suspended')audioCtx.resume();
  const seq=kind==='health'?[[330,.16],[262,.16],[220,.28]]:kind==='close'?(good?[[660,.12],[880,.22]]:[[520,.14],[390,.26]]):kind==='attention'?[[740,.12],[740,.12],[740,.2]]:kind==='entry'?[[587,.11],[784,.11],[988,.2]]:[[700,.18]];
  let t=audioCtx.currentTime+.02;
  for(const [f,d] of seq){const o=audioCtx.createOscillator(),g=audioCtx.createGain();o.type='sine';o.frequency.value=f;g.gain.setValueAtTime(.0001,t);g.gain.exponentialRampToValueAtTime(.18,t+.015);g.gain.exponentialRampToValueAtTime(.0001,t+d);o.connect(g).connect(audioCtx.destination);o.start(t);o.stop(t+d+.02);t+=d+.04;}
 }catch{}
}
function pushAlert(kind,title,body,tone='info',good=true){
 if(!alertPrefs[kind])return;
 const item={kind,title,body,tone,time:Date.now()};
 alertLog.unshift(item);alertLog=alertLog.slice(0,50);
 if($('alerts-panel').classList.contains('hidden'))alertUnread++;
 renderAlerts();alertTone(kind,good);
 const t=document.createElement('div');t.className='toast-card '+tone;t.innerHTML=`<b>${esc(title)}</b><span>${esc(body)}</span>`;
 t.onclick=()=>t.remove();$('toasts').prepend(t);while($('toasts').children.length>4)$('toasts').lastChild.remove();
 setTimeout(()=>t.classList.add('out'),7000);setTimeout(()=>t.remove(),7600);
 if(alertPrefs.desktop&&'Notification' in window&&Notification.permission==='granted'&&(document.hidden||!document.hasFocus())){
  try{const n=new Notification(title,{body,tag:kind+':'+title,silent:alertPrefs.sound});n.onclick=()=>{window.focus();n.close();};}catch{}
 }
}
function renderAlerts(){
 $('alerts-count').textContent=alertUnread>9?'9+':String(alertUnread);$('alerts-count').classList.toggle('hidden',!alertUnread);
 $('alerts-list').innerHTML=alertLog.length?alertLog.map(a=>`<li class="${a.tone}"><span class="alert-time">${new Date(a.time).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}</span><b>${esc(a.title)}</b><span>${esc(a.body)}</span></li>`).join(''):'<li class="muted small">No alerts yet. New entries, fills, closes, review flags and connection problems will appear here.</li>';
 const perm='Notification' in window?Notification.permission:'unsupported';
 $('alerts-setup').innerHTML=`<label><input type="checkbox" data-pref="sound" ${alertPrefs.sound?'checked':''}> Sound</label><label><input type="checkbox" data-pref="desktop" ${alertPrefs.desktop&&perm==='granted'?'checked':''} ${perm==='unsupported'||perm==='denied'?'disabled':''}> Desktop notifications</label>`+
  (perm==='denied'?'<p class="small muted">Desktop notifications are blocked for this site in your browser settings.</p>':perm==='unsupported'?'<p class="small muted">This browser does not support desktop notifications.</p>':'')+
  `<button type="button" class="link-btn" id="alerts-test">Play test alert</button>`;
 $('alerts-prefs').innerHTML=Object.entries(alertKinds).map(([k,label])=>`<label><input type="checkbox" data-pref="${k}" ${alertPrefs[k]?'checked':''}> ${label}</label>`).join('');
}
function alertSnapshot(){
 const s=snapshot();if(!s)return null;
 const a=s.meta.mt5_account||{};
 return {online:!!s.bot_online,api:apiOnline,mt5:a.status||null,
  signals:new Map(s.allSignals.filter(x=>x.signal_kind==='REFERENCE'||x.strategy!=='BBMA'?x.lifecycle==='REFERENCE'&&x.reference_current:x.actionable).map(x=>[x.key,x])),
  orders:new Map((s.demo_orders||[]).map(d=>[d.key,{status:d.status,monitor:d.trade_monitor?.status||null,row:d}])),
  duplicates:(s.meta.duplicate_bot?.status==='WARNING'?s.meta.duplicate_bot.trades||[]:[]).map(t=>t.ticket).sort().join(',')};
}
function checkAlerts(){
 if(paused)return;
 const cur=alertSnapshot();if(!cur)return;
 const prev=alertPrev;alertPrev=cur;
 if(!prev)return; // first snapshot only primes state: no alerts for things already on screen
 const money=(v,c)=>Number.isFinite(v)?`${v>=0?'+':''}${fmt(v,2)} ${c||''}`.trim():'P/L pending';
 if(prev.api&&!cur.api)pushAlert('health','Dashboard connection lost','Displayed data may be old until updates resume.','bad',false);
 else if(!prev.api&&cur.api)pushAlert('health','Dashboard reconnected','Live updates resumed.','good');
 if(cur.api){
  if(prev.online&&!cur.online)pushAlert('health','Bot stopped updating','No fresh heartbeat. Check the bot window and MT5.','bad',false);
  else if(!prev.online&&cur.online&&prev.api)pushAlert('health','Bot back online','Heartbeat received.','good');
  if(prev.mt5==='HEALTHY'&&cur.mt5&&cur.mt5!=='HEALTHY')pushAlert('health','MT5 account '+cur.mt5.toLowerCase(),'Terminal or account read is not healthy.','bad',false);
  else if(prev.mt5&&prev.mt5!=='HEALTHY'&&cur.mt5==='HEALTHY')pushAlert('health','MT5 account healthy again','Account read restored.','good');
 }
 if(cur.duplicates&&cur.duplicates!==prev.duplicates)pushAlert('health','Another bot is trading this account','New entries are paused. Stop the other bot, then acknowledge in the dashboard.','bad',false);
 for(const [key,x] of cur.signals)if(!prev.signals.has(key))
  pushAlert('entry',`${x.symbol} ${x.side||''} ${x.strategy==='CONFLUENCE'?'Confluence reference':x.strategy==='ORB'?'ORB breakout-retest':'BBMA entry ready'}`.replace(/\s+/g,' '),`${x.strategy!=='BBMA'?'Quote ref':'Entry'} ${x.entry??'—'} · SL ${x.stop_loss??'—'} · TP1 ${x.tp1??'—'}`,'info');
 for(const [key,o] of cur.orders){
  const was=prev.orders.get(key),d=o.row,name=`${d.symbol} ${d.side||''} · ${d.strategy}`.replace(/\s+/g,' ');
  if(o.status!==was?.status){
   if(o.status==='PENDING')pushAlert('order','Order placed · '+name,`Pending ${d.volume??''} lots at ${d.reference??d.entry??'—'}`.replace(/\s+/g,' '),'info');
   else if(['OPEN','OPEN_PARTIAL'].includes(o.status)&&!['OPEN','OPEN_PARTIAL'].includes(was?.status))pushAlert('order','Position opened · '+name,`Filled ${d.volume??''} lots at ${d.actual_entry??'—'}`.replace(/\s+/g,' '),'good');
   else if(o.status==='OPEN_PARTIAL'&&was?.status==='OPEN')pushAlert('close','Partial close · '+name,'Part of the position was closed at TP1.','good');
   else if(o.status==='CLOSED'&&was){const v=Number.isFinite(d.realised)?d.realised:d.profit;pushAlert('close',`Closed ${money(v,d.currency)} · ${name}`,({4:'Stop loss hit',5:'Take profit hit',6:'Stop-out',3:'Closed by the bot',0:'Closed manually (desktop)',1:'Closed manually (mobile)',2:'Closed manually (web)'})[d.close_reason]||'Broker confirmed the close.',v>=0?'good':'bad',v>=0);}
   else if(['REJECTED','CHECK_REJECTED'].includes(o.status))pushAlert('order','Order rejected · '+name,d.note||d.broker_note||'See the Execution Ledger for the broker reason.','bad',false);
  }
  if(o.monitor&&o.monitor!==was?.monitor&&['WATCH','EXIT_CONFIRMED','DATA_UNAVAILABLE'].includes(o.monitor)&&['OPEN','OPEN_PARTIAL'].includes(o.status))
   pushAlert('attention',`${o.monitor==='EXIT_CONFIRMED'?'Exit signal':o.monitor==='WATCH'?'Watch':'Monitor data missing'} · ${name}`,d.trade_monitor?.reason||'Open Positions for details.','warn',false);
 }
}
$('alerts-btn').onclick=e=>{e.stopPropagation();const open=$('alerts-panel').classList.toggle('hidden')===false;$('alerts-btn').setAttribute('aria-expanded',String(open));if(open){alertUnread=0;renderAlerts();}};
document.addEventListener('click',e=>{if(!e.target.closest('.alerts-wrap')&&!$('alerts-panel').classList.contains('hidden')){$('alerts-panel').classList.add('hidden');$('alerts-btn').setAttribute('aria-expanded','false');}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('alerts-panel').classList.contains('hidden')){$('alerts-panel').classList.add('hidden');$('alerts-btn').setAttribute('aria-expanded','false');$('alerts-btn').focus();}});
$('alerts-panel').addEventListener('change',async e=>{
 const k=e.target.dataset.pref;if(!k)return;
 if(k==='desktop'&&e.target.checked&&'Notification' in window&&Notification.permission!=='granted'){
  try{await Notification.requestPermission();}catch{}
  alertPrefs.desktop=Notification.permission==='granted';
 }else alertPrefs[k]=e.target.checked;
 saveAlertPrefs();renderAlerts();
});
$('alerts-panel').addEventListener('click',e=>{
 if(e.target.id==='alerts-test'){const keep=alertPrefs.entry;alertPrefs.entry=true;pushAlert('entry','Test alert','This is what a new entry alert looks like.','info');alertPrefs.entry=keep;}
 if(e.target.id==='alerts-clear'){alertLog=[];alertUnread=0;renderAlerts();}
});
document.addEventListener('pointerdown',()=>{if(alertPrefs.sound&&!audioCtx){try{audioCtx=new (window.AudioContext||window.webkitAudioContext)();}catch{}}},{once:true});
renderAlerts();

