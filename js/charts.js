// Grympha Trading Desk dashboard · js/charts.js
// Overview level overlays, candlestick chart, trade-detail chart and dialog.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
function overviewLevels(s,symbol,strategy){
 // History remains available in trade details; Overview only overlays a current entry.
 const matching=row=>row.symbol===symbol&&(row.strategy||'BBMA')===strategy;
 const newest=rows=>[...rows].sort((a,b)=>(b.created||0)-(a.created||0));
 const orders=newest((s.demo_orders||[]).filter(matching));
 const active=new Set(['PRECHECK','SENDING','UNCERTAIN','SUBMITTED','PENDING','OPEN','OPEN_PARTIAL','CANCEL_UNCERTAIN','REVIEW_UNKNOWN']);
 const trade=orders.find(row=>active.has(row.status));
 if(trade)return trade; // Unresolved broker state is not evidence of a closure.
 const signal=newest((s.signals||[]).filter(matching))[0];
 if(!signal||!['PENDING','PAPER_RUNNING','PAPER_TP1','REFERENCE'].includes(signal.lifecycle))return {};
 // A terminal/blocked execution attempt must not reappear via its tracked signal.
 if(orders.some(row=>row.setup_key===signal.key))return {};
 const closed=orders.filter(row=>row.status==='CLOSED');
 const lastClose=Math.max(0,...closed.map(row=>row.exit_epoch||row.updated||row.created||0));
 if(lastClose&&(signal.created||0)<=lastClose)return {};
 if(signal.lifecycle==='PENDING'&&!signal.actionable)return {};
 if(signal.lifecycle==='REFERENCE'&&!signal.reference_current)return {};
 return {reference:signal.entry,sl:signal.stop_loss,tp1:signal.tp1,tp2:signal.tp2};
}
function chart(){
 const s=snapshot();if(!s)return;const x=s.symbols.find(x=>x.symbol===selected),levels=overviewLevels(s,selected,view);
 const frames=chartFrames(x),tfs=Object.keys(frames),fallback=frames[x?.chart_timeframe]?x.chart_timeframe:tfs.at(-1);
 const tf=frames[chartTf[view]]?chartTf[view]:fallback;
 $('chart-title').textContent=`${selected||'Price'} / MT5 chart levels`;
 $('chart-timeframe').textContent=(tf||x?.chart_timeframe||'M5')+' · Closed candles';
 const tfSig=tfs.join(',')+'|'+tf;
 if($('chart-tf').dataset.sig!==tfSig){$('chart-tf').dataset.sig=tfSig;$('chart-tf').innerHTML=tfs.map(k=>`<button type="button" data-tf="${esc(k)}" aria-pressed="${k===tf}">${esc(k)}</button>`).join('');}
 $('chart-tf').classList.toggle('hidden',tfs.length<2);
 $('chart-bands').setAttribute('aria-pressed',String(showBands));
 const hasLevels=Object.keys(levels).length>0;
 $('chart-footer').textContent=(x?`MT5 candles · ${date(x.updated_epoch,true)}${!x.fresh?' · STALE':''}`:'Waiting for MT5 candles')+(tfs.length<2&&x?' · Restart the bot to load H1 / M15 / M5 switching':'')+(hasLevels?'':' · No active entry — waiting for a new entry');
 setHTML('chart',candleChart(frames[tf]||[],levels,selected,{width:$('chart').clientWidth-24,digits:x?.quote?.digits,bid:x?.fresh&&x.health==='OK'?x.quote?.bid:null,tf,bands:showBands,levelName:view==='BBMA'?'Entry':'Ref'}));
}
// ---- v1.15 candlestick chart (self-contained SVG: works offline and under the dashboard CSP) ----
let chartTf={},showBands=true,chartGeom=null;
try{chartTf=JSON.parse(localStorage.getItem('gtd.chartTf')||'{}')||{};showBands=localStorage.getItem('gtd.chartBands')!=='0';}catch{}
function chartFrames(x){
 const multi=x?.charts&&typeof x.charts==='object'?Object.entries(x.charts).filter(([,v])=>Array.isArray(v)&&v.length):[];
 if(multi.length)return Object.fromEntries(multi);
 return x?.chart?.length?{[x.chart_timeframe||'M5']:x.chart}:{};
}
function niceStep(range,count){const raw=range/Math.max(1,count),mag=10**Math.floor(Math.log10(raw)),n=raw/mag;return (n<1.5?1:n<3?2:n<7?5:10)*mag;}
function candleChart(all,d,symbol,o={}){
 if(!all.length){chartGeom=null;return '<div class="empty">MT5 candles unavailable for this pair yet.</div>';}
 const W=Math.max(300,Math.min(1600,Math.round(o.width>0?o.width:1000))),narrow=W<600;
 const bars=all.slice(narrow?-45:-80),fin=v=>v!=null&&Number.isFinite(Number(v));
 const dg=Number.isInteger(o.digits)?o.digits:(String(bars.at(-1).close).split('.')[1]||'').length||2;
 const levels=[[o.levelName||'Entry',d.reference],['Fill',d.actual_entry],['SL',d.actual_sl??d.sl],['TP1',d.tp1],['TP2',d.actual_tp??d.tp2]].filter(([,v])=>fin(v)&&v>0);
 const H=Math.round(Math.max(260,Math.min(420,W*.42))),L=6,R=narrow?80:92,T=12,B=28,pw=W-L-R,ph=H-T-B;
 const vals=[...bars.flatMap(b=>[b.high,b.low]),...levels.map(v=>v[1])];
 if(o.bands)for(const b of bars)for(const k of ['upper','lower'])if(fin(b[k]))vals.push(b[k]);
 if(fin(o.bid))vals.push(o.bid);
 let lo=Math.min(...vals),hi=Math.max(...vals);const pad=(hi-lo||Math.abs(hi)*.001||1)*.06;lo-=pad;hi+=pad;
 const y=v=>T+(hi-v)/(hi-lo)*ph,step=pw/bars.length,cx=i=>L+(i+.5)*step,bw=Math.max(1,Math.min(16,step*.64));
 const p=v=>v.toFixed(1),price=v=>fmt(v,dg);
 let svg=`<svg class="candles" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(symbol)} ${esc(o.tf||'')} candlestick chart with trade levels"><rect width="${W}" height="${H}" fill="#0e1925"/>`;
 // price grid + right axis
 const gs=niceStep(hi-lo,6);for(let v=Math.ceil(lo/gs)*gs;v<=hi;v+=gs){svg+=`<line x1="${L}" x2="${L+pw}" y1="${p(y(v))}" y2="${p(y(v))}" stroke="#1c2a3a"/><text x="${L+pw+6}" y="${p(y(v)+4)}" fill="#6f829a" font-size="11">${price(v)}</text>`;}
 // time axis
 const every=Math.max(1,Math.round(bars.length/(narrow?4:7))),daily=['H4','D1'].includes(o.tf);
 bars.forEach((b,i)=>{if(i%every)return;const t=new Date(b.time*1000);const label=daily||t.getHours()===0&&t.getMinutes()===0?t.toLocaleDateString(undefined,{day:'numeric',month:'short'}):t.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});svg+=`<line x1="${p(cx(i))}" x2="${p(cx(i))}" y1="${T}" y2="${T+ph}" stroke="#16222f"/><text x="${p(Math.max(L+24,Math.min(L+pw-24,cx(i))))}" y="${H-9}" fill="#6f829a" font-size="11" text-anchor="middle">${esc(label)}</text>`;});
 // Bollinger Bands
 if(o.bands){
  const path=k=>{let s='',open=false;bars.forEach((b,i)=>{if(fin(b[k])){s+=(open?'L':'M')+p(cx(i))+' '+p(y(b[k]));open=true;}else open=false;});return s;};
  const idx=bars.map((b,i)=>i).filter(i=>fin(bars[i].upper)&&fin(bars[i].lower));
  if(idx.length>1)svg+=`<path d="M${idx.map(i=>p(cx(i))+' '+p(y(bars[i].upper))).join('L')}L${idx.slice().reverse().map(i=>p(cx(i))+' '+p(y(bars[i].lower))).join('L')}Z" fill="#6c94ce" fill-opacity=".07"/>`;
  svg+=`<path d="${path('upper')}" fill="none" stroke="#6c94ce" stroke-opacity=".7" stroke-width="1.2"/><path d="${path('lower')}" fill="none" stroke="#6c94ce" stroke-opacity=".7" stroke-width="1.2"/><path d="${path('mid')}" fill="none" stroke="#b58be0" stroke-opacity=".75" stroke-width="1.2" stroke-dasharray="4 3"/>`;
 }
 // candles
 bars.forEach((b,i)=>{const up=b.close>=b.open,c=up?'#5ee0be':'#e08797',top=y(Math.max(b.open,b.close)),h=Math.max(1,Math.abs(y(b.open)-y(b.close)));
  svg+=`<line x1="${p(cx(i))}" x2="${p(cx(i))}" y1="${p(y(b.high))}" y2="${p(y(b.low))}" stroke="${c}" stroke-width="1.2"/><rect x="${p(cx(i)-bw/2)}" y="${p(top)}" width="${p(bw)}" height="${p(h)}" fill="${up?'#0e1925':c}" stroke="${c}" stroke-width="1.2"/>`;});
 // trade levels + live price, labels de-overlapped in the right gutter
 const tags=levels.map(([k,v])=>({k,v,c:k==='SL'?'#e08797':k.startsWith('TP')?'#5ee0be':k==='Fill'?'#71b9ff':'#e9bc75',dash:'6 4'}));
 if(fin(o.bid))tags.push({k:'Bid',v:o.bid,c:'#c9d6e4',dash:'2 3'});
 tags.sort((a,b)=>y(a.v)-y(b.v));let last=-1e9;
 for(const t of tags){t.ly=Math.max(y(t.v),last+17);last=t.ly;}
 for(const t of tags){const ly=Math.min(t.ly,T+ph-2);svg+=`<line x1="${L}" x2="${L+pw}" y1="${p(y(t.v))}" y2="${p(y(t.v))}" stroke="${t.c}" stroke-dasharray="${t.dash}" stroke-opacity=".9"/><rect x="${L+pw+2}" y="${p(ly-8)}" width="${R-4}" height="16" rx="3" fill="${t.c}"/><text x="${L+pw+6}" y="${p(ly+4)}" fill="#0b131d" font-size="10.5" font-weight="700">${esc(t.k)} ${price(t.v)}</text>`;}
 // crosshair (positioned on hover)
 svg+=`<g id="chart-cross" visibility="hidden" pointer-events="none"><line id="cross-x" y1="${T}" y2="${T+ph}" stroke="#8aa0b8" stroke-dasharray="3 3"/><line id="cross-y" x1="${L}" x2="${L+pw}" stroke="#8aa0b8" stroke-dasharray="3 3"/><rect id="cross-tag" x="${L+pw+2}" width="${R-4}" height="16" rx="3" fill="#2c3e53"/><text id="cross-price" x="${L+pw+6}" fill="#e8eef5" font-size="10.5"></text></g></svg><div class="chart-tip hidden" id="chart-tip" role="status"></div>`;
 chartGeom={bars,W,H,L,T,pw,ph,step,lo,hi,dg,cx};
 return svg;
}
function chartHover(e){
 const g=chartGeom,svg=$('chart').querySelector('svg.candles');if(!g||!svg)return;
 const r=svg.getBoundingClientRect(),vx=(e.clientX-r.left)/r.width*g.W,vy=(e.clientY-r.top)/r.height*g.H,i=Math.floor((vx-g.L)/g.step);
 const tip=$('chart-tip'),cross=$('chart-cross');
 if(i<0||i>=g.bars.length||vy<g.T||vy>g.T+g.ph){cross?.setAttribute('visibility','hidden');tip?.classList.add('hidden');return;}
 const b=g.bars[i],v=g.hi-(vy-g.T)/g.ph*(g.hi-g.lo),chg=b.open?(b.close-b.open)/b.open*100:0;
 $('cross-x').setAttribute('x1',g.cx(i));$('cross-x').setAttribute('x2',g.cx(i));$('cross-y').setAttribute('y1',vy);$('cross-y').setAttribute('y2',vy);
 $('cross-tag').setAttribute('y',vy-8);$('cross-price').setAttribute('y',vy+4);$('cross-price').textContent=fmt(v,g.dg);cross.setAttribute('visibility','visible');
 tip.innerHTML=`<b>${esc(new Date(b.time*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}))}</b><span>O ${fmt(b.open,g.dg)}</span><span>H ${fmt(b.high,g.dg)}</span><span>L ${fmt(b.low,g.dg)}</span><span>C ${fmt(b.close,g.dg)}</span><span class="${chg>=0?'buy':'sell'}">${chg>=0?'+':''}${chg.toFixed(2)}%</span>${Number.isFinite(b.mid)&&showBands?`<span class="muted">BB ${fmt(b.upper,g.dg)} / ${fmt(b.mid,g.dg)} / ${fmt(b.lower,g.dg)}</span>`:''}`;
 tip.classList.remove('hidden');
 const box=$('chart').getBoundingClientRect(),px=e.clientX-box.left,left=px>box.width/2?px-tip.offsetWidth-14:px+14;
 tip.style.left=Math.max(4,left)+'px';tip.style.top=Math.max(4,e.clientY-box.top-tip.offsetHeight-10)+'px';
}

function details(key){tradeDetails(key);}

function rebaseDemo(obj){const shift=Date.now()/1000-obj.server_epoch;obj.server_epoch+=shift;obj.meta.daily_today=new Date(Date.now()+480*60000).toISOString().slice(0,10);if(obj.meta.news_filter?.updated_epoch)obj.meta.news_filter.updated_epoch+=shift;if(obj.meta.news_filter?.fetched_epoch)obj.meta.news_filter.fetched_epoch+=shift;for(const d of obj.demo_orders||[]){if(d.context?.captured_epoch)d.context.captured_epoch+=shift;for(const b of d.context?.chart||[])b.time+=shift;}if(obj.meta.mt5_account?.updated_epoch)obj.meta.mt5_account.updated_epoch+=shift;if(obj.meta.demo_execution?.updated_epoch)obj.meta.demo_execution.updated_epoch+=shift;for(const d of obj.demo_orders||[])for(const k of ['created','updated','last_broker_epoch','submitted_epoch'])if(d[k])d[k]+=shift;if(obj.meta.mt5_positions)obj.meta.mt5_positions.updated_epoch+=shift;for(const k of ['heartbeat','started'])obj.meta[k]+=shift;for(const x of obj.symbols){x.updated_epoch+=shift;for(const k of ['analysis_utc','checked_utc'])if(x[k])x[k]=new Date(new Date(x[k]).getTime()+shift*1000).toISOString();for(const b of x.chart||[])b.time+=shift;}for(const x of obj.signals){x.created+=shift;x.expires+=shift;if(x.tracking)for(const k of ['last_quote_epoch','created_epoch','entry_epoch','closed_epoch'])if(x.tracking[k])x.tracking[k]+=shift;for(const k of ['expires_utc','confirmation_utc'])x[k]=new Date(new Date(x[k]).getTime()+shift*1000).toISOString();}return obj;}
function tradeAction(d){
 if(d.status==='CLOSED')return 'CLOSED';
 if(d.status==='PENDING')return 'PENDING';
 if(['OPEN','OPEN_PARTIAL'].includes(d.status)){
  if(['CLOSE_REVIEW','PROTECTION_REVIEW'].includes(d.decision))return 'REVIEW CLOSE';
  return d.profit==null?'WAITING':d.profit>=0?'RUNNING PROFIT':'RUNNING LOSS';
 }
 return 'WAITING';
}
function riskHTML(r){
 if(!r||r.error)return `<div class="hint">${esc(r?.error||'Risk estimate waits for a qualifying setup and broker account data.')}</div>`;
 return `<div class="risk-box"><b>Risk estimate · ${esc(r.currency)}</b><div class="detail-grid">${[['Lot',r.volume],['Risk % equity',r.risk_percent],['Estimated loss at SL',fmt(r.estimated_loss,2)],['Estimated profit TP1',fmt(r.estimated_tp1,2)],['Estimated profit TP2',fmt(r.estimated_tp2,2)],['Reward / risk TP2',fmt(r.rr2,2)+'R']].map(([k,v])=>`<div><span>${esc(k)}</span>${esc(v)}</div>`).join('')}</div><p class="muted small">${esc(r.basis||'Before costs and slippage')}</p></div>`;
}
function levelChart(bars,d,symbol){
 if(!bars.length)return '<div class="empty">MT5 candle snapshot unavailable for this record. Levels remain listed below.</div>';
 const levels=[['Plan',d.reference],['Fill',d.actual_entry],['SL',d.actual_sl??d.sl],['TP1',d.tp1],['TP2',d.actual_tp??d.tp2]].filter(([k,v])=>Number.isFinite(v)&&v>0);
 let lo=Math.min(...bars.map(b=>b.low),...levels.map(v=>v[1])),hi=Math.max(...bars.map(b=>b.high),...levels.map(v=>v[1]));const pad=(hi-lo||1)*.08;lo-=pad;hi+=pad;
 const W=850,H=310,y=v=>20+(hi-v)/(hi-lo)*250,xx=i=>12+(i+.5)*720/bars.length;
 let svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(symbol)} MT5 candles and trade levels"><rect width="850" height="310" fill="#101c2a"/>`;
 bars.forEach((b,i)=>{const color=b.close>=b.open?'#5ee0be':'#e08797';svg+=`<line x1="${xx(i)}" x2="${xx(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${color}"/><rect x="${xx(i)-2}" y="${y(Math.max(b.open,b.close))}" width="4" height="${Math.max(1,Math.abs(y(b.open)-y(b.close)))}" fill="${color}"/>`;});
 levels.forEach(([k,v],i)=>{const c=k==='SL'?'#e08797':k.startsWith('TP')?'#5ee0be':k==='Fill'?'#71b9ff':'#e9bc75';svg+=`<line x1="12" x2="740" y1="${y(v)}" y2="${y(v)}" stroke="${c}" stroke-dasharray="5 4"/><text x="746" y="${y(v)+4}" fill="${c}" font-size="11">${esc(k)} ${Number(v).toPrecision(7)}</text>`;});
 svg+=`<text x="14" y="300" fill="#dce7f4" font-size="11">${esc(symbol)} · MT5 candle snapshot ${date(bars.at(-1)?.time,true)}</text></svg>`;return svg;
}
const fullTrades=new Map();
function tradeDetails(key){
 // v1.20: /api/status omits entry-time candle captures; fetch the full record when a trade is opened.
 const listed=(data?.demo_orders||[]).find(d=>d.key===key);if(!listed)return;
 if(!listed.context_trimmed||demo){renderTradeDetails(listed);return;}
 const cached=fullTrades.get(key);
 renderTradeDetails(cached?{...cached,...listed,context:cached.context}:{...listed,context:{...listed.context,chart:null}});
 if(cached)return;
 fetch('api/trade?key='+encodeURIComponent(key),{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json();}).then(full=>{
  fullTrades.set(key,full);if(fullTrades.size>30)fullTrades.delete(fullTrades.keys().next().value);
  if($('details').open&&$('details').dataset.tradeKey===key)renderTradeDetails({...full,...listed,context:full.context});
 }).catch(err=>{const box=$('trade-chart');if(box&&$('details').dataset.tradeKey===key)box.innerHTML='<div class="empty">Chart snapshot could not be loaded ('+esc(err.message)+').</div>';});
}
function renderTradeDetails(d){
 const key=d.key;
 const c=d.context||{},reason={0:'Desktop/manual',1:'Mobile',2:'Web',3:'Expert / algorithm',4:'Stop loss',5:'Take profit',6:'Stop-out',7:'Rollover',8:'Variation margin',9:'Split',10:'Corporate action'};
 $('detail-content').innerHTML=`<div class="eyebrow">${esc(d.strategy)} · ${esc(d.symbol)} · ${tradeAction(d)}</div><h2>${esc(d.side)} / Trade detail</h2><p class="muted small">Broker snapshot ${date(d.last_broker_epoch||d.updated,true)}${d.status!=='CLOSED'&&(!apiOnline||paused||now()-(d.last_broker_epoch||d.updated)>60)?' · LAST REPORTED / STALE':''}</p><div class="detail-grid">${[['1 · Market condition',c.condition?.name||'Unavailable in older record'],['2 · Setup',c.setup||'Unavailable in older record'],['3 · Planned entry',d.reference],['Actual fill',d.actual_entry??'Not confirmed'],['Order ticket',d.order_ticket||'—'],['Position ticket',d.position_ticket||'—'],['Volume',d.volume],['SL (broker / original)',d.actual_sl??d.sl],['TP1',d.tp1],['TP2 (broker / original)',d.actual_tp??d.tp2],['Exit price',d.exit_price??'—'],['Exit reason',d.status==='CLOSED'?(reason[d.close_reason]||'Broker reason '+d.close_reason):'Not closed'],['Broker realised P/L',d.realised==null?'—':fmt(d.realised,2)+' '+d.currency],['Broker floating P/L (remaining)',d.profit==null?'—':fmt(d.profit,2)+' '+d.currency],['Broker realised to date / costs',d.realised_to_date==null?'—':fmt(d.realised_to_date,2)+' '+d.currency]].map(([k,v])=>`<div><span>${esc(k)}</span>${esc(v??'—')}</div>`).join('')}</div><p>${esc(d.note||'')}</p>${riskHTML(d.risk)}<h3>Chart levels</h3><p class="muted small">Candles captured at setup submission, ${date(c.captured_epoch,true)}. Fill and current broker SL/TP overlaid. This is a chart from MT5 data, not a terminal screenshot.</p><div id="trade-chart">${c.chart===null?'<div class="empty">Loading chart snapshot…</div>':levelChart(c.chart||[],d,d.symbol)}</div>${c.chart?.length?'<button class="button" id="save-trade-chart">Download chart PNG</button>':''}<h3>Management activity</h3><p>${esc(d.management_note||'No management action recorded.')}</p><div>${Object.entries(d.management||{}).map(([k,v])=>`<p><b>${esc(k)}</b> · ${esc(v.status)} ${esc(v.note||'')}</p>`).join('')}</div>`;
 $('details').dataset.tradeKey=key;if(!$('details').open)$('details').showModal();
 if($('save-trade-chart'))$('save-trade-chart').onclick=()=>{const svg=$('trade-chart').querySelector('svg'),img=new Image();img.onload=()=>{const canvas=document.createElement('canvas');canvas.width=1700;canvas.height=620;canvas.getContext('2d').drawImage(img,0,0,1700,620);canvas.toBlob(blob=>{if(!blob)return;const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download=d.symbol+'_trade_chart.png';document.body.appendChild(a);a.click();a.remove();$('save-trade-chart').textContent='Chart PNG prepared';setTimeout(()=>URL.revokeObjectURL(url),10000);},'image/png');};img.onerror=()=>{$('save-trade-chart').textContent='Chart export unavailable';};img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(svg));};
}
