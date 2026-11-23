// Grympha Trading Desk dashboard · js/research.js
// AI shadow scoring, System Health / portfolio guard, Research Lab replay.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
let aiData=null,aiLoading=false;
function renderAI(){
 if(!$('ai-state'))return;
 const a=aiData;
 $('ai-state').textContent=a?.error?'UNAVAILABLE':(a?.status||'NOT TRAINED')+' · SHADOW';
 $('ai-counts').textContent=`All pairs / both strategies · ${a?.candidates||0} unique candidates · ${a?.labelled||0} known broker outcomes across ${a?.labelled_days||0} UTC days · Training collection threshold: ${a?.minimum_labels||300} labels across ${a?.minimum_days||20} UTC days`;
 $('ai-note').textContent=a?.error||a?.note||'Collecting data. No model estimate is invented; execution rules remain unchanged.';
 const m=a?.model,h=m?.holdout;
 $('ai-model').innerHTML=m?`<p><b>Ridge regression ${esc(m.id)}</b> · ${esc(m.evaluation_status)}</p><p class="small">Training ${m.training_rows} rows · Holdout ${h.count} / model takes ${h.taken}<br>Executed-only holdout sum R: original ${fmt(h.baseline_sum_r,2)} / shadow ${fmt(h.shadow_sum_r,2)} · Drawdown R: original ${fmt(h.baseline_drawdown_r,2)} / shadow ${fmt(h.shadow_drawdown_r,2)}<br>Prediction error R: ${fmt(h.mae,3)} / constant baseline ${fmt(h.baseline_mae,3)}</p><p class="muted small">${esc(m.selection_bias)} Research screen is not approval. No account-equity backtest or causal improvement claim.</p>`:`<p class="muted">${esc(a?.last_training?.status||'COLLECTING DATA')} · No trained model yet.</p>`;
 paginate('ai-rows','ai-pager',(a?.recent||[]),r=>`<tr><td>${date(r.observed,true)}</td><td>${esc(r.symbol)}<small>${esc(r.strategy)}</small></td><td>${r.prediction.estimated_r==null?'NOT TRAINED':fmt(r.prediction.estimated_r,3)+'R'}<small>${esc(r.prediction.model_id||'No model')}</small></td><td>${esc(r.prediction.suggestion)}<small>SHADOW only</small></td><td>${esc(r.execution_status)}</td><td>${r.label_r==null?'UNKNOWN':fmt(r.label_r,3)+'R'}</td></tr>`,{pageSize:10,colspan:6,empty:'No eligible setup candidates captured yet.'});
}
async function refreshAI(){
 if(aiLoading)return;aiLoading=true;
 try{
  if(demo){aiData={status:'NOT TRAINED',candidates:0,labelled:0,recent:[],note:'Sample view: no actual AI training data.'};renderAI();return;}
  const response=await fetch('api/ai-shadow',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status+' · install complete v1.11 update');
  aiData=await response.json();renderAI();
 }catch(e){aiData={error:e.message,recent:[]};renderAI();}finally{aiLoading=false;}
}

let platformData=null,platformLoading=false,portfolioDirty=false;
function renderPlatform(){
 if(!$('portfolio-mode'))return;
 const p=platformData,r=p?.portfolio,c=p?.controls||{},stale=!apiOnline||paused||!p?.bot_online||!r?.updated_epoch||now()-r.updated_epoch>(p?.freshness_seconds||90);
 $('portfolio-mode').textContent=p?.error?'UNAVAILABLE':c.enabled?'ENFORCING':'MONITOR ONLY';
 $('portfolio-total').textContent=r?.total_risk==null?'UNKNOWN':fmt(r.total_risk,2)+' '+esc(r.currency)+' / '+fmt(r.total_percent,2)+'%';
 $('portfolio-unknown').textContent=r?String(r.unknown?.length||0)+(stale?' · STALE':''):'—';
 $('portfolio-entry').textContent=p?.last_check?.status||'No entry checked';
 $('portfolio-overview-note').textContent=p?.error||`${stale?'LAST REPORTED / WAITING · ':''}Known subtotal ${fmt(r?.known_risk||0,2)} ${r?.currency||''}. Unknown values are not zero. Includes all account positions/pending orders; no hedge netting.`;
 if(!portfolioDirty){$('portfolio-enabled').checked=!!c.enabled;$('portfolio-total-limit').value=c.total_percent??3;$('portfolio-pair-limit').value=c.pair_percent??1.5;$('portfolio-currency-limit').value=c.currency_percent??2;}
 $('portfolio-reasons').textContent=[...(p?.current_check?.reasons||[]),...(r?.unknown||[])].join(' · ')||'No current risk issues reported.';
 $('portfolio-basis').textContent=r?.basis||'Waiting for an account-wide risk snapshot.';
 $('portfolio-currencies').innerHTML=Object.entries(r?.by_currency||{}).map(([ccy,d])=>`<div class="health-card"><b>${esc(ccy)}</b><strong>Gross ${fmt(d.gross,2)} ${esc(r.currency)}</strong><small>Long ${fmt(d.long,2)} / short ${fmt(d.short,2)} · Stop-risk proxy; not notional</small></div>`).join('')||'<p class="muted">No known currency concentration data.</p>';
 $('portfolio-rows').innerHTML=(r?.rows||[]).map(x=>`<tr><td><b>${esc(x.symbol)}</b><small>${esc(x.strategy)}</small></td><td>${esc(x.kind)}<small>${esc(x.ticket??'—')}</small></td><td>${esc(x.side)}<small>${esc(x.volume??'UNKNOWN')}</small></td><td>${x.risk==null?'UNKNOWN':fmt(x.risk,2)+' '+esc(r.currency)}</td><td>${esc(x.unknown.join(' · ')||'Calculated')}</td></tr>`).join('')||'<tr><td colspan="5">'+(r?.status==='KNOWN'?'No account positions or pending orders.':'Account exposure unavailable; no empty-account state assumed.')+'</td></tr>';
 const health=p?.health?.components||{};
 const agents=['BBMA Scanner','BBMA Executor','BBMA Trade Monitor','CONFLUENCE Scanner','CONFLUENCE Executor','CONFLUENCE Trade Monitor','ORB Scanner','ORB Executor','ORB Trade Monitor','Risk Check','Dashboard + Journal','Research'];
 $('health-components').innerHTML=agents.map(name=>[name,health[name]||{status:'WAITING',note:'Waiting for first heartbeat'}]).map(([name,h])=>`<div class="health-card"><b>${esc(name)}</b><strong>${esc(!h.updated_epoch?'WAITING':!p?.bot_online||!apiOnline||paused?'LAST REPORTED':h.status!=='DISABLED'&&now()-h.updated_epoch>(h.freshness_seconds||p?.freshness_seconds||90)?'STALE':h.status)}</strong><small>${esc(h.note)} · ${h.updated_epoch?date(h.updated_epoch,true):'—'}</small></div>`).join('')||'<p class="muted">Waiting for module heartbeats.</p>';
 const w=p?.watchdog;
 $('watchdog-state').textContent=w?'External watchdog: '+(now()-w.updated_epoch>10?'LAST REPORTED / STALE · ':'')+w.status+' · '+date(w.updated_epoch,true):'External watchdog: unavailable, disabled or not yet sampled';
 $('health-alerts').innerHTML=(p?.alerts||[]).slice().reverse().map(a=>`<p>${date(a.updated_epoch,true)} · ${esc(a.status)}</p>`).join('');
 $('platform-updated').textContent=p?.error||'Broker risk snapshot '+date(r?.updated_epoch,true)+(stale?' · STALE':'');
 const a=p?.audit;
 $('audit-summary').textContent=a?`${a.total} events · ${a.checks} checks · ${a.sends} sends · ${a.rejections} send rejections · ${a.uncertain} ambiguous sends · Mean send ${a.average_send_ms==null?'—':fmt(a.average_send_ms,1)+' ms'}`:'No execution audit available.';
 paginate('audit-rows','audit-pager',a?.events||[],e=>`<tr data-audit="${e.id}" role="button" tabindex="0"><td>${date(e.epoch,true)}</td><td>${esc(e.symbol||'—')}<small>${esc(e.strategy||'')}</small></td><td>${esc(e.event)}<small>${esc(e.operation||'')}</small></td><td>${esc(e.retcode??e.status??'—')}</td><td>${e.latency_ms==null?'—':fmt(e.latency_ms,1)+' ms'}<small>${e.slippage_points==null?'':fmt(e.slippage_points,2)+' signed points'}</small></td><td>↗</td></tr>`,{pageSize:10,colspan:6,empty:'No current-account execution events yet.'});
}
async function refreshPlatform(){
 if(platformLoading)return;platformLoading=true;
 try{
  if(demo){platformData={error:'Sample view: portfolio and execution health require the running bot.'};renderPlatform();return;}
  const res=await fetch('api/platform',{cache:'no-store'});if(!res.ok)throw Error('HTTP '+res.status+' · install complete v1.12 update');
  platformData=await res.json();renderPlatform();
 }catch(e){platformData={error:e.message};renderPlatform();}finally{platformLoading=false;}
}
function auditDetails(id){
 const row=platformData?.audit?.events?.find(e=>String(e.id)===String(id));if(!row)return;
 $('detail-content').innerHTML=`<div class="eyebrow">EXECUTION AUDIT · ${esc(row.symbol||'')}</div><h2>${esc(row.event)}</h2><pre class="review-json">${esc(JSON.stringify(row,null,2))}</pre>`;$('details').showModal();
}
$('portfolio-form').oninput=()=>{portfolioDirty=true;};
$('portfolio-form').onsubmit=async e=>{
 e.preventDefault();if(demo){$('portfolio-message').textContent='Sample view: controls not saved.';return;}
 const body={enabled:$('portfolio-enabled').checked,total_percent:Number($('portfolio-total-limit').value),pair_percent:Number($('portfolio-pair-limit').value),currency_percent:Number($('portfolio-currency-limit').value)};
 try{
  const res=await fetch('api/portfolio-controls',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify(body)});
  if(!res.ok)throw Error('HTTP '+res.status);
  portfolioDirty=false;$('portfolio-message').textContent='Saved. Applies to new entry checks only. Existing positions/orders unchanged.';await refreshPlatform();
 }catch(err){$('portfolio-message').textContent='Not saved: '+err.message;}
};

let labData=null,labRequest=0,labDirty=false;
function renderLab(){
 if(!$('lab-status'))return;
 const r=labData,a=r?.settings||{};
 $('lab-status').textContent=r?.error?'UNAVAILABLE · '+r.error:(r?.status||'WAITING')+' · '+(r?.candidate_count||0)+' candidates / '+(r?.replayed||0)+' datasets evaluated (incl. inconclusive) · '+(r?.health?.status||'Waiting for worker')+(r?.health?.last_error?' · '+r.health.last_error:'');
 $('lab-note').textContent=r?.note||'SIMULATED only. No results are inferred from missing ticks.';
 const symbols=[...new Set([...Object.values(data?.meta?.strategies||{}).flatMap(x=>x.symbols||[]),...(r?.recent||[]).map(x=>x.symbol)])];setOptions($('lab-symbol'),symbols,true);
 if(!labDirty){$('lab-enabled').checked=a.enabled??true;$('lab-verified').checked=!!a.timestamp_verified;$('lab-offset').value=a.tick_offset_hours??'';$('lab-query-offset').value=a.query_offset_hours??0;$('lab-cost').value=a.cost_r??'';$('lab-slip').value=a.slippage_r??0;$('lab-latency').value=a.latency_ms??200;$('lab-horizon').value=a.horizon_minutes??120;$('lab-gap').value=a.max_gap_seconds??10;}
 $('lab-versions').innerHTML=(r?.versions||[]).map(v=>`<article class="health-card"><b>${esc(v.name)}</b><small>${esc(v.id)} · ${v.baseline?'BASELINE':'CANDIDATE'} · frozen ${date(v.created,true)}</small><p>Exit ${esc(v.policy.exit_target)} · SL ×${v.policy.sl_multiplier} · TP ×${v.policy.tp_multiplier}<br>Max spread ${v.policy.max_spread_r}R · Model filter ${v.policy.min_model_r??'off'}</p></article>`).join('')||'<p class="muted">Start the updated bot to register the baseline and first candidate version.</p>';
 function metric(x){return x==null?'—':fmt(x,3)+'R';}
 $('lab-comparisons').innerHTML=(r?.comparisons||[]).map(v=>{const f=v.forward,e=v.all;return `<article class="review-pattern"><b>${esc(v.name)}</b> ${chip(f.status)}<p>Exploratory + forward: ${e.matched} matched / ${e.inconclusive} inconclusive · Baseline sum ${metric(e.baseline.sum_r)} / Candidate ${metric(e.candidate.sum_r)} · Delta per candidate ${metric(e.delta_mean_r)}</p><p><b>Enrolled forward only:</b> ${f.matched} candidates / ${f.days} UTC days · Baseline ${metric(f.baseline.sum_r)} / Candidate ${metric(f.candidate.sum_r)} · Delta ${metric(f.delta_mean_r)}</p><p>Forward paired day-block 95% interval: ${f.paired_day_block_ci95?f.paired_day_block_ci95.map(metric).join(' to '):'Not enough days'} · Avoided losses ${f.avoided_losses} / missed winners ${f.missed_winners}</p><p class="small muted">${esc(f.note)}</p></article>`;}).join('')||'<p class="muted">No paired version results yet.</p>';
 paginate('lab-rows','lab-pager',(r?.recent||[]),(x,i)=>`<tr data-lab="${i}" role="button" tabindex="0"><td>${date(x.observed,true)}</td><td><b>${esc(x.symbol)}</b><small>${esc(x.strategy)}</small></td><td>${esc(x.actual_status)}</td><td>${esc(x.baseline?.status||'WAITING')}<small>${esc(x.baseline?.reason||'No baseline evaluation')}</small></td><td>${metric(x.baseline?.net_r)}</td><td>${esc(x.source)}<small>${esc(x.tick_hash.slice(0,12))}</small></td></tr>`,{pageSize:10,colspan:6,empty:'Waiting for a complete future tick window and verified assumptions.'});
}
function labDetails(index){
 const row=labData?.recent?.[Number(index)];if(!row)return;
 $('detail-content').innerHTML=`<div class="eyebrow">SIMULATED REPLAY · ${esc(row.symbol)} · ${esc(row.strategy)}</div><h2>Frozen dataset &amp; policy outcomes</h2><p class="small muted">Actual decision ${esc(row.actual_status)}. These hypothetical outcomes are not broker fills or live instructions.</p><pre class="review-json">${esc(JSON.stringify({assumptions_id:labData.assumptions_id,assumptions:labData.settings,...row},null,2))}</pre>`;$('details').showModal();
}
async function refreshLab(){
 const request=++labRequest;
 try{
  if(demo){labData={status:'SAMPLE / NO ACTUAL REPLAY',versions:[],comparisons:[],recent:[]};renderLab();return;}
  const query=new URLSearchParams({strategy:$('lab-strategy').value,symbol:$('lab-symbol').value});
  const response=await fetch('api/replay?'+query,{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status+' · install complete v1.13 update');
  const result=await response.json();if(request!==labRequest)return;labData=result;renderLab();
 }catch(e){if(request!==labRequest)return;labData={error:e.message};renderLab();}
}
async function labPost(path,body){
 const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Request':'controls'},body:JSON.stringify(body)});
 if(!res.ok)throw Error('HTTP '+res.status+' · verify field ranges / version limit');return res.json();
}
$('lab-settings-form').oninput=()=>{labDirty=true;};
$('lab-settings-form').onsubmit=async e=>{
 e.preventDefault();if(demo){$('lab-message').textContent='Sample view: not saved.';return;}
 const body={enabled:$('lab-enabled').checked,timestamp_verified:$('lab-verified').checked,tick_offset_hours:$('lab-offset').value===''?null:Number($('lab-offset').value),query_offset_hours:Number($('lab-query-offset').value),cost_r:$('lab-cost').value===''?null:Number($('lab-cost').value),slippage_r:Number($('lab-slip').value),latency_ms:Number($('lab-latency').value),horizon_minutes:Number($('lab-horizon').value),max_gap_seconds:Number($('lab-gap').value)};
 try{await labPost('api/replay-settings',body);labDirty=false;$('lab-message').textContent='Frozen assumption profile saved. Applies to research only; changed profiles start a separate comparison.';await refreshLab();}catch(err){$('lab-message').textContent='Not saved: '+err.message;}
};
$('lab-version-form').onsubmit=async e=>{
 e.preventDefault();if(demo){$('lab-version-message').textContent='Sample view: not saved.';return;}
 const body={name:$('lab-version-name').value,policy:{exit_target:$('lab-target').value,sl_multiplier:Number($('lab-sl-mult').value),tp_multiplier:Number($('lab-tp-mult').value),max_spread_r:Number($('lab-spread').value),min_model_r:$('lab-model-min').value===''?null:Number($('lab-model-min').value)}};
 try{await labPost('api/replay-version',body);$('lab-version-message').textContent='Immutable research version registered. No live settings changed. Older candidates remain exploratory.';await refreshLab();}catch(err){$('lab-version-message').textContent='Not saved: '+err.message;}
};
for(const id of ['lab-strategy','lab-symbol'])$(id).onchange=()=>{labData=null;refreshLab();};

