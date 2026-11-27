// Grympha Trading Desk dashboard · js/settings.js
// Settings page: risk & lot controls, pair on/off, news filter.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
let controlsDirty=false;
function controlChanges(cur,next){
 const names={max_positions:'Trade limit (positions + orders)',risk_enabled:'Risk-based lot sizing',risk_percent:'Risk per trade %',partial_enabled:'Partial close at TP1',partial_percent:'Partial close volume %',breakeven_enabled:'Break-even at TP1',breakeven_offset_points:'BE offset (points)',trailing_enabled:'Trailing SL',trailing_start_r:'Trailing start (R)',trailing_distance_r:'Trailing distance (R)'};
 const show=v=>v===true?'ON':v===false?'OFF':v==null||v===''?'—':String(v);
 const out=[];
 for(const k of controlKeys){if(!(k in cur))continue;const a=cur[k],b=next[k];if(typeof b==='boolean'?!!a!==b:Number(a)!==Number(b))out.push(`• ${names[k]||k}: ${show(a)} → ${show(b)}`);}
 const ol=cur.pair_lots||{},nl=next.pair_lots||{};
 for(const sym of [...new Set([...Object.keys(ol),...Object.keys(nl)])].sort())if(Number(ol[sym])!==Number(nl[sym])&&!(ol[sym]==null&&nl[sym]==null))out.push(`• ${sym} fixed lot: ${ol[sym]??'auto'} → ${nl[sym]??'auto'}`);
 const ow=cur.trade_windows||{},nw=next.trade_windows||{};
 for(const s of ['BBMA','CONFLUENCE','ORB']){const a=ow[s]||{},b=nw[s]||{},as=a.enabled?`${twServerToLocal(a.start)}–${twServerToLocal(a.end)}`:'OFF',bs=b.enabled?`${twServerToLocal(b.start)}–${twServerToLocal(b.end)}`:'OFF';if(as!==bs)out.push(`• ${s} trade window (your PC time): ${as} → ${bs}`);}
 return out;
}
document.querySelector('.settings-nav')?.addEventListener('click',e=>{const b=e.target.closest('[data-jump]');if(b)$(b.dataset.jump)?.scrollIntoView({block:'start'});});
const controlKeys=['max_positions','risk_enabled','risk_percent','partial_enabled','partial_percent','breakeven_enabled','breakeven_offset_points','trailing_enabled','trailing_start_r','trailing_distance_r'];
let pairControlsDirty=false,pairControlsSaving=false,pairControlsRendered='';
const pairStrategies=['BBMA','CONFLUENCE','ORB'];
function renderPairControls(s){
 const configs=s?.meta?.strategies||{};
 const disabled=s?.meta?.disabled_pairs||{};
 const symbols=[...new Set(pairStrategies.flatMap(k=>configs[k]?.symbols||[]))];
 const fingerprint=JSON.stringify([configs,disabled]);
 if(pairControlsDirty||pairControlsSaving||fingerprint===pairControlsRendered)return;
 pairControlsRendered=fingerprint;
 $('pair-control-rows').innerHTML=symbols.map(symbol=>`<tr><th scope="row">${esc(symbol)}</th>${pairStrategies.map(strategy=>{
  const config=configs[strategy];
  if(!config?.symbols?.includes(symbol))return '<td>—</td>';
  if(config.enabled===false)return '<td>Strategy disabled</td>';
  const checked=!(disabled[strategy]||[]).includes(symbol);
  return `<td><label><input type="checkbox" data-pair-strategy="${strategy}" value="${esc(symbol)}" aria-label="${esc(symbol)} ${strategy}" ${checked?'checked':''} ${demo?'disabled':''}> <span>${checked?'ON':'OFF'}</span></label></td>`;
 }).join('')}</tr>`).join('')||'<tr><td colspan="3">Waiting for configured pairs.</td></tr>';
}
$('pair-controls-form').oninput=e=>{
 if(!e.target.dataset.pairStrategy)return;
 pairControlsDirty=true;
 e.target.nextElementSibling.textContent=e.target.checked?'ON':'OFF';
 $('pair-controls-save').disabled=false;
};
$('pair-controls-form').onsubmit=async e=>{
 e.preventDefault();
 if(demo||pairControlsSaving||!pairControlsDirty)return;
 const switches=[...$('pair-control-rows').querySelectorAll('[data-pair-strategy]')];
 const body=Object.fromEntries(pairStrategies.map(k=>[k,switches.filter(el=>el.dataset.pairStrategy===k&&!el.checked).map(el=>el.value)]));
 const button=$('pair-controls-save'),message=$('pair-controls-message');
 pairControlsSaving=true;
 button.disabled=true;
 switches.forEach(el=>{el.disabled=true;});
 try{
  const response=await fetch('api/pair-controls',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify(body)});
  if(!response.ok)throw Error('HTTP '+response.status);
  pairControlsDirty=false;
  message.textContent='Saved. Pending entry cancellations can be checked in the Demo Ledger.';
 }catch(error){message.textContent='Pair controls not saved. '+error.message;}
 finally{
  pairControlsSaving=false;
  switches.forEach(el=>{el.disabled=false;});
  button.disabled=!pairControlsDirty;
 }
 if(!pairControlsDirty){
  pairControlsRendered='';
  await refresh();
 }
};
function renderControls(s){
 if(controlsDirty)return;
 const c=s.meta.trade_controls||{max_positions:s.meta.demo_execution?.max_positions??6,risk_enabled:false,risk_percent:.5,partial_enabled:false,partial_percent:50,breakeven_enabled:false,breakeven_offset_points:0,trailing_enabled:false,trailing_start_r:1,trailing_distance_r:1};
 for(const k of controlKeys){const v=k==='max_positions'?(c.max_positions??s.meta.demo_execution?.max_positions??6):c[k];if(typeof v==='boolean')$(k).checked=v;else $(k).value=v;}
 const lots=c.pair_lots||{},strategies=data?.meta?.strategies||{};
 const symbols=[...new Set([...Object.values(strategies).flatMap(x=>x.symbols||[]),...(data?.meta?.symbols||[]),...Object.keys(lots)])];
 $('pair-lots').innerHTML=symbols.map(sym=>`<label>${esc(sym)}<input data-pair-lot="${esc(sym)}" aria-label="${esc(sym)} fixed lot for both strategies" type="number" min="0.00000001" max="100" step="any" placeholder="Use global settings" value="${lots[sym]??''}"></label>`).join('');
 renderTradeWindows(c);
}
const WINDOW_STRATEGIES=['BBMA','CONFLUENCE','ORB'];
// Trade windows are STORED and ENFORCED in MT5 server (broker) time; the UI shows the
// viewer's local PC time. Conversion only: the bot's enforcement is unchanged.
// delta = minutes to ADD to a server time to get local PC time.
function twDelta(){const broker=(data?.meta?.tick_offset||0)*60;const pc=-new Date().getTimezoneOffset();return pc-broker;}
function twHhmmToMin(t){const p=String(t||'00:00').split(':');return(((Number(p[0])||0)*60+(Number(p[1])||0))%1440+1440)%1440;}
function twMinToHhmm(m){m=((m%1440)+1440)%1440;return String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');}
const twServerToLocal=t=>twMinToHhmm(twHhmmToMin(t)+twDelta());
const twLocalToServer=t=>twMinToHhmm(twHhmmToMin(t)-twDelta());
function twZoneLabel(){const d=twDelta(),sign=d<0?'-':'+',a=Math.abs(d);const bh=data?.meta?.tick_offset||0;
 return ` (broker is UTC${bh>=0?'+':''}${bh}; your PC is ${a?sign+Math.floor(a/60)+'h'+(a%60?String(a%60).padStart(2,'0'):''):'the same'} ${d<0?'behind':d>0?'ahead':''} — times are stored in broker time)`;}
function renderTradeWindows(c){
 const target=$('trade-windows');if(!target)return;
 const w=c.trade_windows||{};
 const note=$('tw-zone-note');if(note)note.textContent=twZoneLabel();
 target.innerHTML=WINDOW_STRATEGIES.map(s=>{const x=w[s]||{enabled:false,start:'00:00',end:'00:00'};
  return `<div class="trade-window-row"><label class="tw-strategy"><input type="checkbox" data-tw-enabled="${s}" ${x.enabled?'checked':''} ${demo?'disabled':''}> <b>${s}</b></label><label>From<input type="time" data-tw-start="${s}" value="${esc(twServerToLocal(x.start||'00:00'))}" ${demo?'disabled':''}></label><label>To<input type="time" data-tw-end="${s}" value="${esc(twServerToLocal(x.end||'00:00'))}" ${demo?'disabled':''}></label></div>`;}).join('');
}
function readTradeWindows(){
 return Object.fromEntries(WINDOW_STRATEGIES.map(s=>[s,{enabled:document.querySelector(`[data-tw-enabled="${s}"]`)?.checked||false,start:twLocalToServer(document.querySelector(`[data-tw-start="${s}"]`)?.value||'00:00'),end:twLocalToServer(document.querySelector(`[data-tw-end="${s}"]`)?.value||'00:00')}]));
}
function renderNews(s){
 const n=s.meta.news_filter,health=$('news-health'),body=$('news-events');
 health.title=n?`${n.provider||'Calendar'} · ${n.fetched_epoch?'Last fetched '+date(n.fetched_epoch,true):'Not loaded'}`:'Calendar not loaded';
 if(!n){health.textContent='UNAVAILABLE';body.textContent='Calendar unavailable';return;}
 if(!n.enabled){health.textContent='DISABLED';body.textContent='News filter disabled';return;}
 const stale=n.error||!n.fetched_epoch||now()-n.fetched_epoch>900||(n.updated_epoch&&now()-n.updated_epoch>180)||s.bot_online===false||!apiOnline||paused;
 if(stale){health.textContent='UNAVAILABLE';body.textContent='Calendar unavailable';return;}
 const offset=n.day_offset_minutes??480,day=Math.floor((now()+offset*60)/86400);
 const symbols=Object.keys(n.symbols||{}),watched=new Set();
 for(const symbol of symbols){const match=symbol.toUpperCase().match(/(XAU|USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD)(USD|EUR|GBP|JPY|AUD|CAD|CHF|NZD)/);if(match)for(const c of match.slice(1))if(c!=='XAU')watched.add(c);}
 const seen=new Set();
 const events=(n.events||[]).filter(e=>Number.isFinite(e.epoch)&&Math.floor((e.epoch+offset*60)/86400)===day&&(!watched.size||watched.has(e.currency))).sort((a,b)=>a.epoch-b.epoch).filter(e=>{const key=[e.currency,e.title,e.epoch].join('|');if(seen.has(key))return false;seen.add(key);return true;});
 health.textContent=events.length?`${events.length} TODAY`:'NO NEWS TODAY';
 if(!events.length){body.textContent='No news today';return;}
 const clock=epoch=>{const d=new Date((epoch+offset*60)*1000);return d.toISOString().slice(11,16);};
 const abs=Math.abs(offset),zone='UTC'+(offset>=0?'+':'-')+String(Math.floor(abs/60)).padStart(2,'0')+':'+String(abs%60).padStart(2,'0');
 body.innerHTML=events.map(e=>`<p class="small"><b>${e.estimated_time?'Time TBC':clock(e.epoch)+' '+zone}</b> · ${esc(e.currency)} · ${esc(e.title)}</p>`).join('');
}
$('controls-form').oninput=()=>{controlsDirty=true;};
$('controls-form').onsubmit=async e=>{e.preventDefault();if(demo){$('controls-message').textContent='Sample view: settings are not sent or saved.';return;}const body={};for(const k of controlKeys)body[k]=$(k).type==='checkbox'?$(k).checked:Number($(k).value);body.pair_lots=Object.fromEntries([...document.querySelectorAll('[data-pair-lot]')].filter(el=>el.value.trim()!=='').map(el=>[el.dataset.pairLot,Number(el.value)]));body.trade_windows=readTradeWindows();const changes=controlChanges(data?.meta?.trade_controls||{},body);if(changes.length&&!confirm('Save these trading changes?\n\n'+changes.join('\n')+'\n\nApplies to NEW entries (management also to open bot positions).'+(document.body.classList.contains('real-account')?'\n\n⚠ This is a REAL account.':''))){$('controls-message').textContent='Not saved. No changes were sent.';return;}try{const r=await fetch('api/controls',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify(body)});if(!r.ok)throw Error('HTTP '+r.status+(r.status===501?' · Backend outdated. Install complete v1.9 package.':r.status===403?' · Open dashboard using http://127.0.0.1:8765.':''));controlsDirty=false;$('controls-message').textContent='Saved. Management applies next broker loop; pair lots and risk sizing apply to new entries. Existing positions and pending orders unchanged.';await refresh();}catch(error){$('controls-message').textContent='Controls not saved. '+(error.message||'Check connection and numeric ranges.');}};

// ---- v1.23 Strategy setups (BBMA setups on/off, Confluence minimum score) ----
let strategyDirty=false;
const ORB_DEFAULTS={trade:false,skip_weekdays:[2,3],skip_months:[12],signals_per_day:1,sl_multiplier:0.9,buy_sl_buffer_points:60,sell_sl_buffer_points:100,tp_mode:'dynamic',fixed_rr:2};
function strategySettings(s){
 const st=s?.meta?.strategy_settings;
 if(st)return {saved:true,...st,orb:{...ORB_DEFAULTS,...(st.orb||{})}};
 const score=Number((s?.symbols||[]).find(x=>x.min_score)?.min_score??4);
 return {saved:false,bbma:{csa_reentry:true,momentum_reentry:true,mhv:!!s?.meta?.mhv_enabled},confluence:{min_score:[3,4,5].includes(score)?score:4,pair_scores:{}},orb:{...ORB_DEFAULTS}};
}
// ---- v1.43 Live Test agents trading live (switched on from their Live Test card) ----
let agentsLiveRendered='',agentsLiveStatus={},agentsLiveFetched=0;
async function setAgentLive(agentId,on){
 const r=await fetch('api/agent-execution',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify({agent_id:agentId,on})});
 if(!r.ok)throw Error(r.status===404?'Restart the dashboard to enable this (v1.43 backend).':'HTTP '+r.status);
 agentsLiveFetched=0;await refresh();
}
function renderAgentsLive(s){
 const on=s?.meta?.agent_execution||{},ids=Object.keys(on);
 if(ids.length&&!demo&&Date.now()-agentsLiveFetched>30000){   // Live Test status (a paused agent opens no trades)
  agentsLiveFetched=Date.now();
  fetch('api/agent-execution',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(j=>{if(!j)return;agentsLiveStatus=Object.fromEntries(j.agents.map(a=>[a.agent_id,a.live_test_status]));agentsLiveRendered='';renderAgentsLive(data);}).catch(()=>{});
 }
 const status=id=>agentsLiveStatus[id];
 const fp=JSON.stringify([on,ids.map(status)]);if(fp===agentsLiveRendered)return;agentsLiveRendered=fp;
 $('set-agents').hidden=!ids.length;$('agents-live-count').textContent=`${ids.length} ON`;
 const rows=ids.map(id=>({id,...on[id]})).sort((a,b)=>(a.symbol||'').localeCompare(b.symbol||'')||(a.label||'').localeCompare(b.label||''));
 $('agents-live-rows').innerHTML=rows.map(a=>`<tr><td><b>${esc(a.label||a.strategy_id)}</b></td><td>${esc(a.symbol||'')}</td><td>${{RUNNING:'<span class="chip good">RUNNING</span>',PAUSED:'<span class="chip warn">PAUSED · no entries</span>',REMOVED:'<span class="chip bad">REMOVED · no entries</span>'}[status(a.id)]||'—'}</td><td>${a.enabled_epoch?esc(new Date(a.enabled_epoch*1000).toLocaleString()):'—'}</td><td><button type="button" class="button" data-agent-off="${esc(a.id)}" ${demo?'disabled':''}>Stop trading live</button></td></tr>`).join('');
}
$('agents-live-rows').addEventListener('click',async e=>{
 const b=e.target.closest('[data-agent-off]');if(!b)return;
 const a=(data?.meta?.agent_execution||{})[b.dataset.agentOff]||{};
 if(!confirm(`Stop live trading for ${a.label||'this agent'} · ${a.symbol||''}?\n\nNo new live entries. An open live trade keeps copying the simulation until it closes. The agent keeps running in Live Test.`))return;
 try{b.disabled=true;await setAgentLive(b.dataset.agentOff,false);$('agents-live-message').textContent='Stopped. The agent no longer opens live trades.';}
 catch(err){b.disabled=false;alert('Not changed: '+err.message);}
});

// Confluence score per pair: '' = follow the default score
function renderPairScores(s,st){
 const pairs=s?.meta?.strategies?.CONFLUENCE?.symbols||[],own=st.confluence.pair_scores||{};
 $('st-pair-scores').innerHTML=pairs.length?pairs.map(p=>{const v=own[p]??'';return `<label class="${v?'own':''}"><b>${esc(p)}</b><select data-pair-score="${esc(p)}" aria-label="${esc(p)} Confluence minimum score" ${demo?'disabled':''}><option value="" ${v===''?'selected':''}>Default (${st.confluence.min_score}/5)</option>${[3,4,5].map(n=>`<option value="${n}" ${v===n?'selected':''}>${n}/5</option>`).join('')}</select></label>`;}).join(''):'<span class="small">Waiting for Confluence pairs.</span>';
}
function readPairScores(){
 return Object.fromEntries([...document.querySelectorAll('[data-pair-score]')].filter(el=>el.value!=='').map(el=>[el.dataset.pairScore,Number(el.value)]));
}
function renderStrategySettings(s){
 if(strategyDirty)return;
 const st=strategySettings(s);
 $('st-csa').checked=st.bbma.csa_reentry;$('st-mom').checked=st.bbma.momentum_reentry;$('st-mhv').checked=st.bbma.mhv;
 for(const r of document.querySelectorAll('[name=st-score]'))r.checked=Number(r.value)===st.confluence.min_score;
 renderPairScores(s,st);
 $('st-orb-trade').checked=!!st.orb.trade;renderOrbControls(st.orb);
 $('strategy-source').textContent=st.saved?'Saved in dashboard · applies from the bot\'s next check':'Using config.json defaults · not changed here yet';
}
$('strategy-form').oninput=e=>{
 strategyDirty=true;$('strategy-message').textContent='Unsaved changes';
 if(e.target.name==='st-score')for(const o of document.querySelectorAll('[data-pair-score] option[value=""]'))o.textContent=`Default (${e.target.value}/5)`;
 if(e.target.dataset.pairScore)e.target.parentElement.classList.toggle('own',e.target.value!=='');
};
$('strategy-form').onsubmit=async e=>{
 e.preventDefault();
 if(demo){$('strategy-message').textContent='Sample view: settings are not sent or saved.';return;}
 const score=Number(document.querySelector('[name=st-score]:checked')?.value||4);
 const body={bbma:{csa_reentry:$('st-csa').checked,momentum_reentry:$('st-mom').checked,mhv:$('st-mhv').checked},confluence:{min_score:score,pair_scores:Object.fromEntries(Object.entries(readPairScores()).filter(([,v])=>v!==score))},orb:readOrbControls()};
 if(!body.bbma.csa_reentry&&!body.bbma.momentum_reentry&&!body.bbma.mhv){$('strategy-message').textContent='Keep at least one BBMA setup on (or switch BBMA off per pair in Pairs on / off).';return;}
 const cur=strategySettings(data),names={csa_reentry:'CSA re-entry',momentum_reentry:'Momentum re-entry',mhv:'MHV reversal'},changes=[];
 for(const k of Object.keys(names))if(cur.bbma[k]!==body.bbma[k])changes.push(`• BBMA ${names[k]}: ${cur.bbma[k]?'ON':'OFF'} → ${body.bbma[k]?'ON':'OFF'}`);
 if(cur.confluence.min_score!==score)changes.push(`• Confluence minimum score: ${cur.confluence.min_score}/5 → ${score}/5`);
 const was=cur.confluence.pair_scores||{},now_=body.confluence.pair_scores;
 for(const p of [...new Set([...Object.keys(was),...Object.keys(now_)])].sort())if(was[p]!==now_[p])changes.push(`• Confluence ${p}: ${was[p]?was[p]+'/5':'default'} → ${now_[p]?now_[p]+'/5':'default ('+score+'/5)'}`);
 changes.push(...orbChanges(cur.orb,body.orb));
 if(!!cur.orb?.trade!==body.orb.trade)changes.push(`• ORB trading: ${cur.orb?.trade?'ON':'OFF (analysis only)'} → ${body.orb.trade?'ON — ORB may place orders':'OFF (analysis only)'}`);
 if(!changes.length){strategyDirty=false;$('strategy-message').textContent='No changes.';return;}
 if(!confirm('Change which setups can create NEW entries?\n\n'+changes.join('\n')+'\n\nOpen positions and pending orders are not changed.'+(document.body.classList.contains('real-account')?'\n\n⚠ This is a REAL account.':''))){$('strategy-message').textContent='Not saved.';return;}
 try{
  const r=await fetch('api/strategy-settings',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify(body)});
  if(!r.ok)throw Error(r.status===404?'Restart the bot to enable this setting (v1.23 backend).':(await r.text())||('HTTP '+r.status));
  strategyDirty=false;$('strategy-message').textContent='Saved. The bot uses it from its next check.';await refresh();
 }catch(err){$('strategy-message').textContent='Not saved: '+err.message;}
};

// ---- v1.27 ORB rule settings ----
const DAY_NAMES=['Mon','Tue','Wed','Thu','Fri'],MONTH_NAMES=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function renderOrbControls(o){
 for(const el of document.querySelectorAll('[data-orb-day]'))el.checked=!o.skip_weekdays.includes(Number(el.dataset.orbDay));
 for(const el of document.querySelectorAll('[data-orb-month]'))el.checked=o.skip_months.includes(Number(el.dataset.orbMonth));
 $('orb-signals').value=String(o.signals_per_day);$('orb-buy-buf').value=o.buy_sl_buffer_points;$('orb-sell-buf').value=o.sell_sl_buffer_points;
 $('orb-sl-mult').value=o.sl_multiplier;$('orb-fixed-rr').value=o.fixed_rr;
 for(const r of document.querySelectorAll('[name=orb-tp]'))r.checked=r.value===o.tp_mode;
 $('orb-rules').textContent=orbSummary(o);
}
function readOrbControls(){
 return {trade:$('st-orb-trade').checked,
  skip_weekdays:[...document.querySelectorAll('[data-orb-day]')].filter(el=>!el.checked).map(el=>Number(el.dataset.orbDay)),
  skip_months:[...document.querySelectorAll('[data-orb-month]')].filter(el=>el.checked).map(el=>Number(el.dataset.orbMonth)),
  signals_per_day:Number($('orb-signals').value),sl_multiplier:Number($('orb-sl-mult').value),
  buy_sl_buffer_points:Number($('orb-buy-buf').value),sell_sl_buffer_points:Number($('orb-sell-buf').value),
  tp_mode:document.querySelector('[name=orb-tp]:checked')?.value||'dynamic',fixed_rr:Number($('orb-fixed-rr').value)};
}
function orbSummary(o){
 const days=DAY_NAMES.filter((_,i)=>!o.skip_weekdays.includes(i)),skipM=o.skip_months.map(m=>MONTH_NAMES[m-1]);
 return `Current rules: trades ${days.join(', ')}${skipM.length?' · skips '+skipM.join(', '):' · every month'} · ${o.signals_per_day} signal${o.signals_per_day>1?'s':''} per day · `+
  `SL ${o.buy_sl_buffer_points}/${o.sell_sl_buffer_points} pts beyond the range ×${o.sl_multiplier} · TP ${o.tp_mode==='fixed'?'fixed '+o.fixed_rr+'R':'dynamic 1.5–3R'} · news: shared News filter.`;
}
function orbChanges(a,b){
 const out=[],list=(xs,names,off)=>xs.length?xs.map(x=>names[x-off]).join(', '):'none';
 if(String(a.skip_weekdays)!==String(b.skip_weekdays))out.push(`• ORB skipped days: ${list(a.skip_weekdays,DAY_NAMES,0)} → ${list(b.skip_weekdays,DAY_NAMES,0)}`);
 if(String(a.skip_months)!==String(b.skip_months))out.push(`• ORB skipped months: ${list(a.skip_months,MONTH_NAMES,1)} → ${list(b.skip_months,MONTH_NAMES,1)}`);
 if(a.signals_per_day!==b.signals_per_day)out.push(`• ORB signals per day: ${a.signals_per_day} → ${b.signals_per_day}`);
 if(a.buy_sl_buffer_points!==b.buy_sl_buffer_points||a.sell_sl_buffer_points!==b.sell_sl_buffer_points)out.push(`• ORB SL buffer BUY/SELL: ${a.buy_sl_buffer_points}/${a.sell_sl_buffer_points} → ${b.buy_sl_buffer_points}/${b.sell_sl_buffer_points} pts`);
 if(a.sl_multiplier!==b.sl_multiplier)out.push(`• ORB SL distance factor: ×${a.sl_multiplier} → ×${b.sl_multiplier}`);
 if(a.tp_mode!==b.tp_mode||(b.tp_mode==='fixed'&&a.fixed_rr!==b.fixed_rr))out.push(`• ORB take-profit: ${a.tp_mode==='fixed'?'fixed '+a.fixed_rr+'R':'dynamic 1.5–3R'} → ${b.tp_mode==='fixed'?'fixed '+b.fixed_rr+'R':'dynamic 1.5–3R'}`);
 return out;
}
document.addEventListener('input',e=>{if(e.target.closest('.strategy-orb'))$('orb-rules').textContent=orbSummary(readOrbControls());});

// ---- v1.28 Telegram notifications ----
const TG_DEFAULTS={entry_signal:true,order_placed:true,filled:true,tp1:true,sl_moved:false,tp_hit:true,sl_hit:true,closed_other:true,order_rejected:true,health:true,setup_board:false,signal_agent:false};
let tgDirty=false;
function renderTelegram(s){
 const t=s.meta.telegram||{},st=s.meta.telegram_status||{},prefs={...TG_DEFAULTS,...(s.meta.telegram_prefs||{})};
 $('tg-status').textContent=!t.configured?'NOT SET UP':t.enabled?'ON · chat '+(t.chat_hint||''):'OFF';
 $('tg-status').className='chip '+(!t.configured?'warn':t.enabled?'good':'');
 $('tg-help').innerHTML=!t.configured?'Telegram is not set up yet. In the <b>launcher</b>, fill the <b>Telegram alerts</b> row (bot token from @BotFather, then <b>Find my chat ID</b>), tick <b>Enable</b> and press <b>Save</b>. Then choose events here.':
  (t.enabled?'Telegram is on. Bot token and chat ID are managed in the launcher and never shown here. Choose which events send a message:':'Telegram is set up but switched <b>off</b> in the launcher. Tick <b>Enable</b> there to receive these messages.');
 $('tg-last').textContent=(st.last_sent_epoch?'Last alert sent '+date(st.last_sent_epoch,true)+' · '+(st.sent||0)+' sent':'No alert sent yet')+(st.last_error?' · last problem: '+st.last_error:'');
 if(!tgDirty)for(const el of document.querySelectorAll('[data-tg]'))el.checked=!!prefs[el.dataset.tg];
}
$('tg-form').oninput=()=>{tgDirty=true;$('tg-message').textContent='Unsaved changes';};
$('tg-form').onsubmit=async e=>{
 e.preventDefault();
 if(demo){$('tg-message').textContent='Sample view: settings are not sent or saved.';return;}
 const body=Object.fromEntries([...document.querySelectorAll('[data-tg]')].map(el=>[el.dataset.tg,el.checked]));
 try{const r=await fetch('api/telegram-prefs',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify(body)});
  if(!r.ok)throw Error(r.status===404?'Restart the bot to enable this (v1.28 backend).':(await r.text())||'HTTP '+r.status);
  tgDirty=false;$('tg-message').textContent='Saved. Applies from the next alert.';await refresh();}
 catch(err){$('tg-message').textContent='Not saved: '+err.message;}
};
$('tg-test').onclick=async()=>{
 if(demo){$('tg-message').textContent='Sample view: nothing sent.';return;}
 $('tg-message').textContent='Sending…';
 try{const r=await fetch('api/telegram-test',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:'{}'});
  $('tg-message').textContent=r.ok?'Test message sent ✓ · check Telegram':'Not sent: '+((await r.text())||'HTTP '+r.status);}
 catch(err){$('tg-message').textContent='Not sent: '+err.message;}
};
