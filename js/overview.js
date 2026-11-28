// Grympha Trading Desk dashboard · js/overview.js
// Overview page: render(), status bar, market watch cards/table, account, cockpit, ledger, entry brief, journal table.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
const VIEW_TEXT={BBMA:{tf:'H1 context · M15 setup · M5 confirmation',desc:'Each pair must pass all three stages before an entry is shown.',brief:'Entry brief',level:'Entry',steps:['Market condition','Recognise setup','Entry point']},CONFLUENCE:{tf:'H4 trend · H1 alignment · M15 RSI/MACD',desc:'Independent five-factor score. A quote reference is not an entry confirmation.',brief:'Confluence analysis',level:'Quote reference',steps:['H4 market condition','Confluence score','Quote reference']},ORB:{tf:'Opening range · M5 breakout · retest · H4 EMA20',desc:'XAUUSD only. Range 01:00–06:00 server, entries 07:00–17:00. Rules: Settings → Strategy setups.',brief:'ORB analysis',level:'Quote reference',steps:['H4 trend (EMA20)','Range · breakout · retest','Entry']}};
function render(){
 const s=snapshot();if(!s)return;
 const ns=names(s);if(!ns.includes(selected))selected=ns[0]||'';
 setOptions($('chart-symbol'),ns,false);$('chart-symbol').value=selected;
 setOptions($('filter-symbol'),[...new Set([...ns,...s.allSignals.map(x=>x.symbol)])],true);
 $('cards').classList.toggle('single',ns.length===1);

 // v1.20: shared parts always; page-specific parts only for the page on screen (showPage() renders on switch)
 const page=typeof currentPage==='function'?currentPage():'overview';
 renderDemo(s);renderDesk(s);renderAccount(s);
 if(page==='positions')renderActual(s);
 if(page==='settings'){renderControls(s);renderPairControls(s);renderNews(s);renderStrategySettings(s);renderAgentsLive(s);renderTelegram(s);}
 if(page==='review'){renderReview();renderAI();}
 if(page==='monitor'){renderPlatform();renderBackup(s);}
 if(page==='lab')renderLab();
 if(page==='trades')renderDaily();
 renderComparison(s);renderStatusBar(s);renderDuplicate(s);
 $('view-bbma').classList.toggle('active',view==='BBMA');$('view-bbma').setAttribute('aria-pressed',String(view==='BBMA'));
 $('view-confluence').classList.toggle('active',view==='CONFLUENCE');$('view-confluence').setAttribute('aria-pressed',String(view==='CONFLUENCE'));
 $('view-orb').classList.toggle('active',view==='ORB');$('view-orb').setAttribute('aria-pressed',String(view==='ORB'));
 $('idea-label').textContent='ACTIVE MT5 TRADES';
 $('idea-note').textContent='Broker positions and pending orders';
 const vt=VIEW_TEXT[view]||VIEW_TEXT.BBMA;
 $('timeframe-note').textContent=vt.tf;
 $('strategy-description').textContent=vt.desc;
 $('brief-title').textContent=vt.brief;
 $('chart-level-label').textContent=vt.level;
 $('connection').textContent=demo?'Sample data':paused?'Updates paused':!apiOnline?'Dashboard disconnected':s.bot_online?'Bot online':'Bot offline / stale';
 $('connection').className=`pill ${demo||paused?'warn':s.bot_online?'good':'bad'}`;
 $('bot-status').textContent=demo?'Sample mode':paused?'Paused':s.bot_online?'Monitoring':'Not updating';
 $('bot-status').className=s.bot_online&&!demo?'buy':'';
 $('heartbeat').textContent=s.meta.heartbeat?`Heartbeat ${ageText(now()-s.meta.heartbeat)}`:'Start the bot to begin';
 $('watch-count').textContent=ns.length;
 $('pending-count').textContent=(s.demo_orders||[]).filter(d=>d.strategy===view&&['PENDING','OPEN','OPEN_PARTIAL'].includes(d.status)).length;
 const last=s.symbols.length?Math.max(...s.symbols.map(x=>x.updated_epoch)):0;
 $('last-check').textContent=last?date(last):'—';
 $('cadence').textContent=`Bot checks ${s.meta.poll_seconds||15}s · dashboard refreshes 5s`;
 {const st=strategySettings(s);$('mode').textContent=view==='ORB'?(st.orb?.trade?'ORB · TRADING ON':'ORB · ANALYSIS ONLY'):view==='CONFLUENCE'?`CONFLUENCE · MIN ${st.confluence.min_score}/5`+(Object.keys(st.confluence.pair_scores||{}).length?` · ${Object.keys(st.confluence.pair_scores).length} PAIR OVERRIDES`:''):[st.bbma.csa_reentry&&'CSA',st.bbma.momentum_reentry&&'MOMENTUM',st.bbma.mhv&&'MHV'].filter(Boolean).join(' + ')||'NO SETUPS';}
 setHTML('cards',ns.length?ns.map(sym=>{
 const x=s.symbols.find(x=>x.symbol===sym),p=latest(s,sym),st=stage(x,p),ok=x?.fresh&&x.health==='OK';
 const trend=ok?x.condition?.side:null,style=trend==='BUY'?'good':trend==='SELL'?'bad':'';
 const status=!x?'Waiting for bot':!x.fresh?'STALE / OFFLINE':x.health==='MARKET_CLOSED'?'MARKET CLOSED':x.health!=='OK'?'FEED BLOCKED':x.condition?.name||'WAITING';
 return `<article class="pair-card ${sym===selected?'active':''}" data-symbol="${esc(sym)}" tabindex="0" role="button" aria-label="Inspect ${esc(sym)}"><div class="pair-top"><div class="pair-name">${esc(sym)}<small>${sym==='XAUUSD'?'Gold / US Dollar':sym==='BTCUSD'?'Bitcoin / US Dollar · 24/7':sym==='ETHUSD'?'Ethereum / US Dollar · 24/7':sym==='XAGUSD'?'Silver / US Dollar':sym==='GBPJPY'?'British Pound / Japanese Yen':sym==='USDJPY'?'US Dollar / Japanese Yen':'MT5 instrument'}</small></div>${chip(status,ok?style:'warn')}</div><div class="quote-row"><span class="price">${x?.quote?fmt(x.quote.bid,x.quote.digits):'—'}</span><span class="quote-spread">${x?.quote?`Bid · spread ${fmt(x.quote.spread,x.quote.digits)}`:'Waiting for valid quote'}</span></div><ol class="steps">${st.map((text,i)=>`<li><span class="step-label"><span class="num ${ok&&(i===0&&x.condition?.side||i===1&&(x.setup||p?.actionable)||i===2&&(p?.actionable||p?.reference_current))?'done':''}">${i+1}</span>${(VIEW_TEXT[view]||VIEW_TEXT.BBMA).steps[i]}</span><b>${esc(text)}</b></li>`).join('')}</ol><div class="status-note">${esc(x&&!x.fresh?'Last bot check is stale or updates are paused. Current signal labels are withheld.':x?.reason||(view==='CONFLUENCE'?'Waiting for the independent Confluence analysis.':'Start bbma_bot.py --dashboard to publish live decisions.'))}</div><div class="card-time"><span>${x?`Checked ${ageText(now()-x.updated_epoch)}`:'No data yet'}</span><span>${sym===selected?'Viewing details ↓':'Inspect pair ↗'}</span></div></article>`;
 }).join(''):'<div class="empty">No bot data yet. Start the bot with --dashboard.</div>');
 renderMarketTable(s,ns);
 const layout=marketLayout(ns);
 $('cards').classList.toggle('hidden',layout==='table');$('market-table').classList.toggle('hidden',layout!=='table');
 document.querySelector('.comparison-panel').classList.toggle('hidden',layout==='table');
 $('mw-table').setAttribute('aria-pressed',String(layout==='table'));$('mw-cards').setAttribute('aria-pressed',String(layout!=='table'));
 renderBrief(s);if(page==='history')renderHistory(s);
}
// ---- v1.14 trader cockpit: status bar + compact market watch ----
let mwLayout=null;try{mwLayout=localStorage.getItem('gtd.marketLayout');}catch{}
function marketLayout(ns){return mwLayout==='table'||mwLayout==='cards'?mwLayout:(ns.length>4?'table':'cards');}
function setMarketLayout(value){mwLayout=value;try{localStorage.setItem('gtd.marketLayout',value);}catch{}render();chart();}
function renderStatusBar(s){
 const a=s.meta.mt5_account||{},mode=s.meta.demo_execution||{enabled:false,status:'DISABLED'};
 const modeType=String(mode.account_type||'').toUpperCase(),type=String(a.account_type||(['DEMO','REAL'].includes(modeType)?modeType:'')).toUpperCase();
 const real=type==='REAL'||modeType==='REAL',running=!!mode.enabled&&s.bot_online&&mode.status==='ENABLED';
 $('account-mode').textContent=demo?'SAMPLE DATA':real?'REAL ACCOUNT':type?type+' ACCOUNT':'ACCOUNT UNKNOWN';
 $('account-mode').className='mode-badge '+(demo?'sample':real?'real':type==='DEMO'?'demo':'unknown');
 const noUpdate=!!mode.enabled&&mode.status==='ENABLED'&&!running;
 $('exec-state').textContent=running?'EXECUTION ON':noUpdate?'EXECUTION · NO UPDATE':mode.enabled?'EXECUTION BLOCKED':'ANALYSIS ONLY';
 $('exec-state').className='exec-state '+(running?(real?'on-real':'on'):noUpdate?'unknown':mode.enabled?'blocked':'off');
 document.body.classList.toggle('real-account',real&&!demo);
 const src=s.meta.mt5_positions,fresh=!!(apiOnline&&!paused&&s.bot_online&&src&&!src.error&&now()-src.updated_epoch<=60);
 const positions=src?.positions||[],totals={};
 for(const x of positions){const v=Number(x.profit)+Number(x.swap||0);if(Number.isFinite(v)&&x.currency)totals[x.currency]=(totals[x.currency]||0)+v;}
 const entries=Object.entries(totals),el=$('sb-floating');
 el.textContent=!fresh?'—':entries.length?entries.map(([c,v])=>(v>=0?'+':'')+fmt(v,2)+' '+c).join(' / '):fmt(0,2)+' '+(a.currency||'');
 el.className=fresh&&entries.length===1?(entries[0][1]>=0?'buy':'sell'):'';
 $('sb-floating-note').textContent=!fresh?'Broker snapshot unavailable':`${positions.length} open position${positions.length===1?'':'s'} · incl. swap`;
}
function strategyState(s,sym,strategy){
 const x=s.allSymbols.find(y=>y.strategy===strategy&&y.symbol===sym);
 if(!x)return {x,ok:false,label:'Not scanned',detail:'',rank:-1,side:null};
 const ok=x.fresh&&x.health==='OK';
 if(!ok)return {x,ok,label:!x.fresh?'STALE':x.health==='MARKET_CLOSED'?'MARKET CLOSED':'FEED BLOCKED',detail:'',rank:-1,side:null};
 const p=s.allSignals.find(y=>y.strategy===strategy&&y.symbol===sym);
 const trade=(data.demo_orders||[]).find(d=>d.strategy===strategy&&d.symbol===sym&&['PENDING','OPEN','OPEN_PARTIAL'].includes(d.status));
 const side=x.condition?.side||null,action=trade?tradeAction(trade):p?.actionable?'ENTRY READY':'WAITING';
 const setup=strategy==='CONFLUENCE'?`${x.score??0}/5${x.qualified?' · qualified':''}`:(x.setup?.type||p?.setup||'No setup').replaceAll('_',' ');
 const rank=trade?3:p?.actionable||x.qualified||x.setup?2:side?1:0;
 return {x,ok,side,rank,label:x.condition?.name||'WAITING',detail:`${setup} · ${action}`,trade};
}
function renderMarketTable(s,ns){
 const order=new Map(ns.map((sym,i)=>[sym,i]));
 const rows=ns.map(sym=>({sym,b:strategyState(s,sym,'BBMA'),c:strategyState(s,sym,'CONFLUENCE'),o:strategyState(s,sym,'ORB')}))
  .sort((u,v)=>Math.max(v.b.rank,v.c.rank,v.o.rank)-Math.max(u.b.rank,u.c.rank,u.o.rank)||order.get(u.sym)-order.get(v.sym));
 const cell=st=>!st.x?'<span class="muted">Not scanned</span>':!st.ok?chip(st.label,'warn'):`${chip(st.side||st.label,st.side==='BUY'?'good':st.side==='SELL'?'bad':'')}<small>${esc(st.label)} · ${esc(st.detail)}</small>`;
 setHTML('market-rows',rows.length?rows.map(({sym,b,c,o})=>{
  const x=s.symbols.find(y=>y.symbol===sym)||b.x||c.x||o.x,q=x?.quote,live=Math.max(b.rank,c.rank,o.rank);
  const agree=b.ok&&c.ok&&b.side&&c.side?(b.side===c.side?chip('ALIGNED','good'):chip('CONFLICT','warn')):'<span class="muted small">—</span>';
  const checked=x?ageText(now()-x.updated_epoch):'—';
  return `<tr data-symbol="${esc(sym)}" tabindex="0" role="button" aria-label="Inspect ${esc(sym)}" class="${sym===selected?'active':''} ${live>=3?'live-trade':live===2?'has-setup':''}"><td><b>${esc(sym)}</b>${live>=3?'<small class="buy">Trade active</small>':live===2?'<small class="reference-note">Setup forming</small>':''}</td><td>${q?fmt(q.bid,q.digits):'—'}<small>${q?'spread '+fmt(q.spread,q.digits):'no quote'}</small></td><td>${cell(b)}</td><td>${cell(c)}</td><td>${o.x?cell(o):'<span class="muted small">—</span>'}</td><td>${agree}</td><td>${checked}</td></tr>`;
 }).join(''):'<tr><td colspan="7" class="empty">No bot data yet. Start the bot with --dashboard.</td></tr>');
}
function renderActual(s){
 const source=s.meta.mt5_positions,t=now(),fresh=apiOnline&&!paused&&s.bot_online&&source&&t-source.updated_epoch<=60;
 $('actual-health').textContent=!source?'Waiting for read-only MT5 data':source.error?source.error:!fresh?'STALE · last read '+date(source.updated_epoch):'Read '+ageText(t-source.updated_epoch);
 const rows=source?.positions||[];
 $('actual-positions').innerHTML=rows.length?rows.map(p=>`<tr><td><b>${esc(p.symbol)}</b><small>Ticket ${esc(p.ticket)}</small></td><td>${esc(p.side)}<small>${esc(p.volume)} lots</small></td><td>${esc(p.entry)}</td><td>${esc(p.current)}</td><td class="${p.profit>=0?'buy':'sell'}">${p.profit>=0?'+':''}${Number(p.profit).toFixed(2)} ${esc(p.currency)}${!fresh?' · STALE':''}<small>Swap ${Number(p.swap||0).toFixed(2)} · commissions not included</small></td><td>${esc(p.attribution||'UNASSIGNED')}<small>${esc(p.decision||'No automatic strategy link or close')}</small></td></tr>`).join(''):`<tr><td colspan="6" class="empty">${!source||source.error?'Broker position data unavailable. No empty-account state is assumed.':!fresh?'Last broker snapshot is stale. Current open positions are unknown.':'No open MT5 positions in the watched instruments.'}</td></tr>`;
}
function renderAccount(s){
 const a=s.meta.mt5_account||{},age=now()-(a.updated_epoch||0);
 const fresh=apiOnline&&!paused&&s.meta.running&&a.updated_epoch>=s.meta.started&&age>=-5&&age<=Math.max(60,(data.meta.poll_seconds||15)*3);
 const usable=fresh&&a.status==='HEALTHY';
 const status=!apiOnline?'Dashboard disconnected':paused?'Updates paused':!fresh?'Stale / unavailable':usable?'Healthy':a.status==='DISCONNECTED'?'Disconnected':'Unavailable';
 $('mt5-health').textContent=(demo?'Sample · ':'')+status;
 $('mt5-broker').textContent=usable?a.broker||'—':'—';
 $('mt5-server').textContent=usable?a.server||'—':'—';
 $('mt5-balance').textContent=usable?fmt(a.balance,2)+' '+(a.currency||''):'—';
 $('mt5-equity').textContent=usable?fmt(a.equity,2)+' '+(a.currency||''):'—';
 $('mt5-account-type').textContent=usable?'Account · '+(a.account_type||'UNKNOWN'):'Account data unavailable';
 $('mt5-account-updated').textContent=a.updated_epoch?'Read '+ageText(age):'Waiting for MT5';
}
let earlyExitDirty=false;
const earlyExitSwitches={BBMA:$('auto-early-exit-bbma'),CONFLUENCE:$('auto-early-exit-confluence'),ORB:$('auto-early-exit-orb')};
$('early-exit-form').oninput=()=>{earlyExitDirty=true;};
$('early-exit-form').onsubmit=async e=>{
 e.preventDefault();
 if(demo)return;
 const button=e.currentTarget.querySelector('button');
 const settings=Object.fromEntries(Object.entries(earlyExitSwitches).map(([strategy,input])=>[strategy,input.checked]));
 const old=data?.meta?.auto_early_exit,turningOn=Object.entries(settings).filter(([k,v])=>v&&!(old===true||old?.[k]===true)).map(([k])=>k);
 if(turningOn.length&&!confirm('Turn ON Auto Early Exit for '+turningOn.join(' and ')+'?\n\nThe bot will CLOSE the remaining bot-owned position after a confirmed M15 reversal.'+(document.body.classList.contains('real-account')?'\n\n⚠ This is a REAL account.':''))){$('early-exit-message').textContent='Not saved.';return;}
 button.disabled=true;
 for(const input of Object.values(earlyExitSwitches))input.disabled=true;
 try{
  const response=await fetch('api/early-exit',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify(settings)});
  if(!response.ok)throw Error('HTTP '+response.status);
  earlyExitDirty=false;
  $('early-exit-message').textContent='Saved. BBMA: '+(settings.BBMA?'Auto Exit':'Review Only')+' · Confluence: '+(settings.CONFLUENCE?'Auto Exit':'Review Only')+' · ORB: '+(settings.ORB?'Auto Exit':'Review Only');
  await refresh();
 }catch(err){$('early-exit-message').textContent='Setting not saved. '+err.message;}
 finally{button.disabled=false;for(const input of Object.values(earlyExitSwitches))input.disabled=false;}
};
function renderDesk(s){
 const ledger=s.demo_orders||[];
 const active=ledger.filter(r=>['OPEN','OPEN_PARTIAL'].includes(r.status));
 const pending=ledger.filter(r=>r.status==='PENDING');
 const alerts=active.filter(r=>['WATCH','EXIT_CONFIRMED','DATA_UNAVAILABLE'].includes(r.trade_monitor?.status));
 const online=s.bot_online&&apiOnline&&!paused&&s.meta.demo_execution?.status==='ENABLED';
 const pnl=Object.entries(active.reduce((totals,r)=>{
  if(Number.isFinite(r.profit)&&r.currency)totals[r.currency]=(totals[r.currency]||0)+r.profit;
  return totals;
 },{})).map(([currency,value])=>fmt(value,2)+' '+currency).join(' / ');
 const d=dailyData,today=d?.status==='READY'&&d.date===d.today,deals=today?d.deals||[]:[];
 const net=deals.reduce((v,e)=>v+e.profit+e.commission+e.swap+e.fee,0),closed=new Set(deals.filter(e=>[1,2,3].includes(e.entry)&&e.position_id>0).map(e=>e.position_id)).size;
 const r=platformData?.portfolio,limit=platformData?.controls,riskKnown=!!(r&&r.total_percent!=null&&!platformData?.error);
 const riskTone=!riskKnown||!limit?.total_percent?'':r.total_percent>=limit.total_percent?'sell':r.total_percent>=limit.total_percent*0.75?'warn-text':'';
 const floatSum=active.reduce((v,x)=>v+(Number.isFinite(x.profit)?x.profit:0),0),pnlTone=pnl&&!pnl.includes(' / ')?(floatSum>=0?'buy':'sell'):'';
 const metrics=[
  ['Today net P/L',today?(net>=0?'+':'')+fmt(net,2)+' '+(d.currency||''):'—',today?`${closed} closed position${closed===1?'':'s'} · after costs`:'Waiting for broker history',today?(net>=0?'buy':'sell'):'',true],
  ['Bot floating P/L',pnl||'—',active.length?'Open bot positions':'No open bot positions',pnlTone,false],
  ['Open risk',riskKnown?fmt(r.total_percent,2)+'%':r?'UNKNOWN':'—',riskKnown?`of equity · limit ${fmt(limit?.total_percent,1)}% · ${limit?.enabled?'enforced':'monitor only'}`:'Portfolio guard data unavailable',riskTone,true],
  ['Bot positions',active.length,'Running now','',false],
  ['Pending orders',pending.length,'Waiting to fill','',false],
  ['Needs attention',alerts.length,alerts.length?'See list below':'All clear',alerts.length?'warn-text':'',false]];
 $('desk-metrics').innerHTML=metrics.map(([label,value,note,tone,always])=>{const show=online||always;return `<div><small>${label}</small><strong class="${show?tone:''}">${show?esc(value):'—'}</strong><span>${esc(show?note:'Execution offline')}</span></div>`;}).join('');
 $('desk-alerts').innerHTML=!online?'<p>Waiting for a current broker snapshot.</p>':alerts.length?alerts.map(r=>`<a href="#positions"><b>${esc(r.symbol)} · ${esc(r.strategy)}</b> ${chip(r.trade_monitor.status,'warn')} <span>${esc(r.trade_monitor.reason)}</span></a>`).join(''):'<p>No bot positions need review.</p>';
 $('position-monitor').innerHTML=active.map(r=>{
  const m=r.trade_monitor||{};
  const current=online&&now()-(r.last_broker_epoch||0)<=60;
  return `<tr data-trade="${esc(r.key)}" tabindex="0" role="button"><td><b>${esc(r.symbol)}</b><small>${esc(r.strategy)} · ${esc(r.side)} · #${esc(r.position_ticket)}</small></td><td>${chip(current?(m.status||'WAITING'):'LAST REPORTED')}</td><td>${esc(m.reason||'Waiting for monitor data.')}<small>${esc(m.path||'')}</small>${structuralTargetNote(r)}</td><td>${m.remaining_r==null?'—':fmt(m.remaining_r,2)+'R'}</td></tr>`;
 }).join('')||'<tr><td colspan="4">No bot-owned positions in the current ledger.</td></tr>';
}
let tpModeDirty=false;
$('bbma-tp-form').oninput=()=>{tpModeDirty=true;};
$('bbma-tp-form').onsubmit=async e=>{
 e.preventDefault();
 if(demo)return;
 const select=$('bbma-tp-mode'),button=e.currentTarget.querySelector('button');
 select.disabled=button.disabled=true;
 try{
  const response=await fetch('api/bbma-tp-mode',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify({mode:select.value})});
  if(!response.ok)throw Error('HTTP '+response.status);
  $('bbma-tp-message').textContent='Saved. Applies to new BBMA setups.';
  await refresh();
  tpModeDirty=false;
 }catch(err){$('bbma-tp-message').textContent='Setting not saved. '+err.message;}
 finally{select.disabled=button.disabled=false;}
};
function structuralTargetNote(plan){
 const tp=plan?.structural_tp;
 if(!tp?.mandatory)return '';   // Live Test agent rows carry only {single_target}
 const mandatory=tp.mandatory,final=tp.final;
 return `<small>Structural · ${esc(tp.timeframe)} · ${tp.single_target?'Full TP':'Mandatory TP'} ${esc(mandatory.source)} @ ${esc(mandatory.price)}${tp.single_target?'':' · Final TP '+esc(final.source)+' @ '+esc(final.price)}</small>`;
}
function renderDemo(s){
 if(!tpModeDirty)$('bbma-tp-mode').value=s.meta.bbma_tp_mode||'existing';
 for(const [strategy,input] of Object.entries(earlyExitSwitches)){
  const setting=s.meta.auto_early_exit;
  if(!earlyExitDirty)input.checked=setting===true||setting?.[strategy]===true;
  input.disabled=demo||$('early-exit-form').querySelector('button').disabled;
 }
 const mode=s.meta.demo_execution||{enabled:false,status:'DISABLED'},running=!!mode.enabled&&s.bot_online&&mode.status==='ENABLED';
 $('execution-banner').textContent=mode.enabled?`MT5 ${mode.account_type||'DEMO'} ${mode.status} · ${s.meta.trade_controls?.risk_enabled?'Risk '+s.meta.trade_controls.risk_percent+'% equity':(mode.lot??'—')+' lots'} · pair overrides ${Object.keys(s.meta.trade_controls?.pair_lots||{}).length} · max ${s.meta.trade_controls?.max_positions??mode.max_positions??6} positions/orders · Early Exit by strategy · Settings${mode.error?' · '+mode.error:''}`:'ANALYSIS ONLY · Demo execution requires the explicit --demo-execution command.';
 $('execution-banner').className='exec-detail '+(running?'execution-on':mode.enabled?'execution-blocked':'');
 $('execution-mode').textContent=mode.status;
 $('execution-footer').textContent=running?'MT5 '+(mode.account_type||'DEMO')+' execution enabled':'Analysis only · no new MT5 orders';
 const rows=s.demo_orders||[];
 paginate('demo-orders','demo-orders-pager',rows,d=>{const fresh=running&&now()-(d.last_broker_epoch||d.updated)<=60;
 const value=d.status==='CLOSED'?d.realised:d.profit;
 const decision=['OPEN','OPEN_PARTIAL'].includes(d.status)&&(!fresh||d.quote_fresh===false)?'REVIEW_DATA':d.decision||'REVIEW_DATA';
 return `<tr data-trade="${esc(d.key)}" tabindex="0" role="button" aria-label="Trade details ${esc(d.symbol)}"><td><b>${esc(d.symbol)}</b><small>${esc(d.strategy)} · ${esc(d.side)}</small></td><td>${chip(tradeAction(d),['OPEN','OPEN_PARTIAL'].includes(d.status)?'good':'warn')}<small>${esc(d.status)}</small></td><td>${esc(d.order_ticket||'—')}<small>Position ${esc(d.position_ticket||'—')}</small></td><td>${esc(d.volume)} lots<small>${d.actual_entry==null?'No confirmed fill':esc(d.actual_entry)}</small></td><td class="${(value||0)>=0?'buy':'sell'}">${value==null?'—':(value>=0?'+':'')+Number(value).toFixed(2)+' '+esc(d.currency||'')}<small>${d.status==='CLOSED'?'Broker realised':value==null?'No confirmed P/L':fresh&&d.quote_fresh!==false?'Broker floating':'Last reported / stale'}</small></td><td>${chip(decision,decision==='CLOSE_REVIEW'||decision==='PROTECTION_REVIEW'?'warn':'')}<small>${d.tp1_observed?'TP1 reached':''}</small></td><td>${esc(d.note||'')}${structuralTargetNote(d)}${d.trade_monitor?`<small><b>${esc(d.trade_monitor.status)}</b> · ${esc(d.trade_monitor.reason)}${!fresh&&['OPEN','OPEN_PARTIAL'].includes(d.status)?' · Last reported':''}</small><small>${esc(d.context?.setup||'')} · ${esc(d.trade_monitor.path||'')}${d.trade_monitor.remaining_r==null?'':' · '+fmt(d.trade_monitor.remaining_r,2)+'R to TP'}</small>`:''}${d.early_exit_order?`<small>Auto exit · ${esc(d.early_exit_order.status)}</small>`:''}${d.weekend_close_order?`<small>Weekend close · ${esc(d.weekend_close_order.status)}</small>`:''}${d.early_exit_review?`<small>Original TP/SL follow-up · ${esc(d.early_exit_review.outcome)} · sampled</small>`:''}</td></tr>`;},{pageSize:10,colspan:7,empty:'No demo order requests yet. Only new qualifying setups can submit orders.'});
}
function factors(x,digits){
 if(!x?.factors)return '';
 return `<div class="factors"><div class="scoreline"><div><b>Five-factor score</b><br><small class="muted">Threshold ${x.min_score??4}/5 · not a win probability</small></div><strong>${x.score}/5</strong></div>${x.factors.map(f=>`<div class="factor"><span>${esc(f.name)}<br><small>${esc(f.detail)}</small></span><b class="${f.passed?'buy':'muted'}">${f.passed?'PASS':'—'}</b></div>`).join('')}<div class="small muted">H1 support ${fmt(x.support,digits)} · resistance ${fmt(x.resistance,digits)}<br>M15 ATR ${fmt(x.atr,digits)}<br>Analysis ${date(x.analysis_utc,true)}</div></div>`;
}
function renderBrief(s){
 const x=s.symbols.find(x=>x.symbol===selected),p=latest(s,selected),digits=x?.quote?.digits??5;
 const d=(s.demo_orders||[]).find(d=>d.strategy===view&&d.symbol===selected);
 $('brief-side').textContent=d?tradeAction(d):'WAITING';
 let html=view==='CONFLUENCE'?factors(x,digits):view==='ORB'?orbBrief(x,digits):'';
 html+=`<div class="brief-context"><b>1 · Condition</b><p>${esc(x?.condition?.name||'Waiting')}</p><b>2 · Setup</b><p>${esc((p?.setup||x?.setup?.type||'Waiting').replaceAll('_',' '))}</p><b>3 · Entry</b><p>${esc(d?tradeAction(d):'WAITING')} · ${esc(x?.reason||'Waiting for analysis')}</p></div>`;
 if(p)html+=`<div class="levels">${[['Planned entry',p.entry],['SL',p.stop_loss],['TP1',p.tp1],['TP2',p.tp2]].map(([k,v])=>`<div class="level"><span>${esc(k)}</span><strong>${fmt(v,digits)}</strong></div>`).join('')}</div>`;
 html+=riskHTML(x?.risk_preview);
 if(p)html+=structuralTargetNote(p);
 if(d)html+=`<p>Actual fill: ${fmt(d.actual_entry,digits)} · ${esc(d.volume)} lots</p><button class="button" data-trade="${esc(d.key)}">Open trade detail</button>`;
 $('brief').innerHTML=html;
}

function renderHistory(s){
 const st=$('filter-status').value,fs=$('filter-symbol').value,strategy=$('filter-strategy').value;
 const rows=(s.demo_orders||[]).filter(d=>(strategy==='ALL'||d.strategy===(strategy==='CURRENT'?view:strategy))&&(fs==='ALL'||d.symbol===fs)&&(st==='ALL'||tradeAction(d)===st));
 $('journal-count').textContent='/ '+rows.length+' shown';
 paginate('signals','signals-pager',rows,d=>`<tr data-trade="${esc(d.key)}" role="button" tabindex="0"><td>${date(d.created,true)}</td><td>${esc(d.strategy)}</td><td><b>${esc(d.symbol)}</b><small>${esc(d.context?.setup||'')}</small></td><td>${esc(d.side)}</td><td>${esc(d.actual_entry??d.reference)}</td><td>${esc(d.actual_sl??d.sl)}</td><td>${esc(d.tp1)}<small>${esc(d.actual_tp??d.tp2)}</small></td><td>${fmt(d.status==='CLOSED'?d.realised:d.profit,2)} ${esc(d.currency||'')}</td><td>${chip(tradeAction(d))}<small>${esc(d.note||'')}</small></td><td>↗</td></tr>`,{pageSize:10,colspan:10,empty:'No broker trade records match this view.'});
}

// ---- v1.25 another bot copy on the same account ----
function renderDuplicate(s){
 const d=s.meta.duplicate_bot,trades=d?.status==='WARNING'?d.trades||[]:[];
 $('duplicate-banner').classList.toggle('hidden',!trades.length);
 if(!trades.length)return;
 $('duplicate-text').textContent=` ${trades.length} trade(s) carry this bot's identity but were not placed by this copy: `+trades.map(t=>`${t.symbol} ${t.side} ${t.volume} (${t.strategy}, #${t.ticket})`).join(', ')+'. New entries are PAUSED. Existing trades and their SL/TP are not touched. Stop the bot on the other PC/VPS, then acknowledge.';
 $('duplicate-ack').dataset.tickets=JSON.stringify(trades.map(t=>t.ticket));
}
$('duplicate-ack').onclick=async()=>{
 if(demo){$('duplicate-msg').textContent='Sample view: nothing sent.';return;}
 const tickets=JSON.parse($('duplicate-ack').dataset.tickets||'[]');if(!tickets.length)return;
 if(!confirm('Acknowledge '+tickets.length+' trade(s) from another bot copy and resume new entries?\n\nOnly do this after stopping the other bot. These trades stay open with their own SL/TP; this bot will not manage them.'))return;
 try{const r=await fetch('api/duplicate-ack',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify({tickets})});
  if(!r.ok)throw Error(r.status===404?'Restart the bot to enable this (v1.25 backend).':'HTTP '+r.status);
  $('duplicate-msg').textContent='Acknowledged. New entries resume.';await refresh();}
 catch(err){$('duplicate-msg').textContent='Not saved: '+err.message;}
};

// ---- v1.26 ORB agent ----
function orbBrief(x,digits){
 if(!x)return '<div class="hint">ORB scans XAUUSD only. Select XAUUSD to see the opening range.</div>';
 const r=x.range||{},pending=x.pending||{};
 return `<div class="factors"><div class="scoreline"><div><b>ORB status</b><br><small class="muted">${esc(x.session?('Session '+x.session):'')}</small></div><strong>${esc((x.orb_status||'—').replaceAll('_',' '))}</strong></div>`+
  `<div class="factor"><span>Range high</span><b>${r.high!=null?fmt(r.high,digits):'—'}</b></div><div class="factor"><span>Range low</span><b>${r.low!=null?fmt(r.low,digits):'—'}</b></div>`+
  `<div class="factor"><span>Range size</span><b>${r.points!=null?Math.round(r.points)+' pts':'—'}</b></div>`+
  `<div class="factor"><span>Breakout waiting for retest</span><b>${pending.BUY?'BUY':''}${pending.BUY&&pending.SELL?' / ':''}${pending.SELL?'SELL':''}${!pending.BUY&&!pending.SELL?'None':''}</b></div>`+
  `<div class="factor"><span>H4 trend (EMA20)</span><b>${esc(x.condition?.side||'—')}</b></div></div>`;
}

// ---- v1.29 database backup ----
function renderBackup(s){
 const b=s.meta.db_backup||{},age=b.last_ok_epoch?now()-b.last_ok_epoch:null,every=(b.every_hours||24)*3600;
 const state=b.running?'BACKING UP':!b.last_ok_epoch?'NO BACKUP YET':b.last_error&&b.last_error_epoch>b.last_ok_epoch?'LAST TRY FAILED':age>every*2?'OVERDUE':'OK';
 $('backup-state').textContent=state;
 $('backup-state').className='chip '+(state==='OK'?'good':state==='BACKING UP'?'':'warn');
 $('backup-detail').textContent=b.last_ok_epoch?`Last backup ${date(b.last_ok_epoch,true)} (${ageText(age)}) · ${b.last_folder} · ${((b.last_size||0)/1e6).toFixed(1)} MB · ${b.kept||1} of ${b.keep||14} kept · every ${b.every_hours||24} h${b.folder?' · '+b.folder:''}`+(b.last_error&&b.last_error_epoch>b.last_ok_epoch?' · last problem: '+b.last_error:''):
  'Daily verified copy of trades, history and settings (not the Telegram token). The first backup runs shortly after the bot starts.';
}
$('backup-now').onclick=async()=>{
 if(demo){$('backup-msg').textContent='Sample view: nothing backed up.';return;}
 $('backup-msg').textContent='Starting…';
 try{const r=await fetch('api/backup-now',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:'{}'});
  if(!r.ok)throw Error(r.status===404?'Restart the bot to enable this (v1.29 backend).':'HTTP '+r.status);
  const j=await r.json();$('backup-msg').textContent=j.started?'Backup started · status updates in a few seconds':'A backup is already running';setTimeout(refresh,4000);}
 catch(err){$('backup-msg').textContent='Not started: '+err.message;}
};
