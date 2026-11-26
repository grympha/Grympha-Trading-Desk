// Grympha Trading Desk dashboard · js/equity.js
// Equity curve: account equity samples and cumulative closed P/L.
// Load order (dashboard.html): core → overview → charts → settings → history → research → alerts → equity → app. All files share one global scope.
'use strict';
// ---- v1.18 equity curve (account equity samples + cumulative closed-trade P/L) ----
let equityData=null,equityFetched=0,equityLoading=false,equityGeom=null,eqMode='equity',eqRange='1W',eqModeChosen=false;
try{const v=JSON.parse(localStorage.getItem('gtd.equity')||'{}');if(['equity','closed'].includes(v.mode)){eqMode=v.mode;eqModeChosen=true;}if(['1D','1W','1M','3M','ALL'].includes(v.range))eqRange=v.range;}catch{}
const saveEquityPrefs=()=>{try{localStorage.setItem('gtd.equity',JSON.stringify({mode:eqModeChosen?eqMode:null,range:eqRange}));}catch{}};
function sampleEquity(range){
 // Illustrative series for ?demo=1 only. Clearly labelled as sample data.
 const span={'1D':86400,'1W':604800,'1M':2678400,'3M':7948800,'ALL':15552000}[range],end=Date.now()/1000,n=240,points=[],closed=[];
 let eq=10000,bal=10000,seed=7,rand=()=>((seed=seed*16807%2147483647)/2147483647)-.5,tot=0;
 for(let i=0;i<n;i++){const t=end-span+span*i/(n-1);eq+=rand()*18+.6;if(i%24===23){const v=Math.round((eq-bal)*100)/100;bal=eq;tot+=v;closed.push([t,Math.round(tot*100)/100,v,'BBMA','XAUUSD']);}points.push([t,Math.round(eq*100)/100,Math.round(bal*100)/100]);}
 return {status:'READY',range,currency:'USD',account_type:'DEMO',recording_since:end-span,points,closed,closed_before_range:0,same_account:true,sample:true};
}
async function refreshEquity(force=false){
 if(equityLoading||(!force&&Date.now()-equityFetched<60000))return;
 equityLoading=true;
 try{
  if(demo)equityData=sampleEquity(eqRange);
  else{const res=await fetch('api/equity?range='+encodeURIComponent(eqRange),{cache:'no-store'});if(!res.ok)throw Error(res.status===404?'Restart the bot/dashboard to enable equity history (v1.18 backend).':'HTTP '+res.status);equityData=await res.json();}
  equityFetched=Date.now();
 }catch(err){equityData={status:'ERROR',error:err.message||'Equity history unavailable'};}
 finally{equityLoading=false;renderEquity();}
}
function renderEquity(){
 const d=equityData;
 for(const b of document.querySelectorAll('[data-eq-mode]'))b.setAttribute('aria-pressed',String(b.dataset.eqMode===eqMode));
 for(const b of document.querySelectorAll('[data-eq-range]'))b.setAttribute('aria-pressed',String(b.dataset.eqRange===eqRange));
 if(!d){return;}
 if(d.status!=='READY'){$('equity-stats').innerHTML='';$('equity-chart').innerHTML=`<div class="empty">${esc(d.error||'Equity history unavailable.')}</div>`;equityGeom=null;return;}
 // Until a few equity samples exist, show the closed-trade curve (which has history) unless the user picked a mode.
 if(!eqModeChosen&&eqMode==='equity'&&d.points.length<2&&d.closed.length)eqMode='closed',renderEquityButtons();
 if(!eqModeChosen&&eqMode==='closed'&&d.points.length>=30)eqMode='equity',renderEquityButtons();
 const cur=d.currency||'',money=(v,sign)=>(sign&&v>=0?'+':'')+fmt(v,2)+' '+cur;
 let series=[],base=null,note='';
 if(eqMode==='equity'){
  series=d.points.map(p=>({t:p[0],v:p[1],b:p[2]}));
  note=(d.sample?'SAMPLE DATA · ':'')+'MT5 equity (includes floating P/L), sampled once per minute while the bot runs'+(d.recording_since?' · recording since '+date(d.recording_since,true):'')+'. Dashed line = balance.';
 }else{
  series=d.closed.map(c=>({t:c[0],v:c[1],trade:c[2],strategy:c[3],symbol:c[4]}));
  if(series.length)series.unshift({t:Math.min(series[0].t,d.start_epoch||series[0].t),v:0});
  base=0;note=(d.sample?'SAMPLE DATA · ':'')+'Running total of broker-confirmed net P/L from closed bot trades in this range (Trade Review records). Manual trades are not included.'+(d.same_account===false?' · Trade Review is linked to a different account than the equity samples.':'');
 }
 $('equity-note').textContent=note;
 if(series.length<2){
  $('equity-stats').innerHTML='';equityGeom=null;
  $('equity-chart').innerHTML=`<div class="empty">${eqMode==='equity'?'Equity recording has started. The curve appears after a few minutes of the bot running'+(d.closed.length?' — switch to <b>Closed P/L</b> to see trade history now.':'.'):'No closed bot trades in this range yet.'}</div>`;return;
 }
 const first=series[0].v,last=series.at(-1).v,change=last-first;
 let peak=-Infinity,dd=0;for(const p of series){peak=Math.max(peak,p.v);dd=Math.min(dd,p.v-peak);}
 const stats=eqMode==='equity'
  ?[['Start',money(first)],['Now',money(last)],['Change',money(change,true)+(first?` (${change>=0?'+':''}${(change/first*100).toFixed(2)}%)`:''),change>=0?'buy':'sell'],['Max drawdown',dd<0?money(dd)+(peak>0?` (${(dd/peak*100).toFixed(2)}%)`:''):'None','sell'],['Balance',money(series.at(-1).b)]]
  :(()=>{const t=series.filter(p=>p.trade!=null),w=t.filter(p=>p.trade>0).length;return [['Closed trades',String(t.length)],['Win rate',t.length?Math.round(w/t.length*100)+'%':'—'],['Net P/L',money(last,true),last>=0?'buy':'sell'],['Max drawdown',dd<0?money(dd):'None','sell'],['Best / worst',t.length?money(Math.max(...t.map(p=>p.trade)),true)+' / '+money(Math.min(...t.map(p=>p.trade)),true):'—']];})();
 $('equity-stats').innerHTML=stats.map(([k,v,c])=>`<div><small>${k}</small><strong class="${c&&v!=='None'?c:''}">${esc(v)}</strong></div>`).join('');
 $('equity-chart').innerHTML=equityChart(series,{width:$('equity-chart').clientWidth-24,base,up:change>=0,balance:eqMode==='equity',step:eqMode==='closed',cur});
}
function renderEquityButtons(){for(const b of document.querySelectorAll('[data-eq-mode]'))b.setAttribute('aria-pressed',String(b.dataset.eqMode===eqMode));}
function equityChart(series,o){
 const W=Math.max(300,Math.min(1600,Math.round(o.width>0?o.width:1000))),H=Math.round(Math.max(200,Math.min(300,W*.27))),L=6,R=W<600?80:92,T=12,B=26,pw=W-L-R,ph=H-T-B,t0=series[0].t,t1=series.at(-1).t||t0+1;
 const vals=series.map(p=>p.v).concat(o.balance?series.map(p=>p.b).filter(Number.isFinite):[]).concat(o.base!=null?[o.base]:[]);
 let lo=Math.min(...vals),hi=Math.max(...vals);const pad=(hi-lo||Math.abs(hi)*.002||1)*.08;lo-=pad;hi+=pad;
 const x=t=>L+(t-t0)/Math.max(1,t1-t0)*pw,y=v=>T+(hi-v)/(hi-lo)*ph,p=v=>v.toFixed(1),col=o.up?'#5ee0be':'#e08797';
 let line='',bal='';
 series.forEach((pt,i)=>{const X=p(x(pt.t)),Y=p(y(pt.v));if(!i)line+=`M${X} ${Y}`;else if(o.step)line+=`H${X}V${Y}`;else line+=`L${X} ${Y}`;if(o.balance&&Number.isFinite(pt.b))bal+=(bal?'L':'M')+X+' '+p(y(pt.b));});
 const floor=o.base!=null?y(Math.max(lo,Math.min(hi,o.base))):T+ph;
 let svg=`<svg class="equity-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Equity curve"><defs><linearGradient id="eq-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".28"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><rect width="${W}" height="${H}" fill="#0e251f"/>`;
 const gs=niceStep(hi-lo,5);for(let v=Math.ceil(lo/gs)*gs;v<=hi;v+=gs)svg+=`<line x1="${L}" x2="${L+pw}" y1="${p(y(v))}" y2="${p(y(v))}" stroke="#1c3a33"/><text x="${L+pw+6}" y="${p(y(v)+4)}" fill="#6f9a8f" font-size="11">${fmt(v,v%1?2:0)}</text>`;
 const span=t1-t0,ticks=W<600?3:5;for(let i=0;i<=ticks;i++){const t=t0+span*i/ticks,d=new Date(t*1000),label=span>2*86400?d.toLocaleDateString(undefined,{day:'numeric',month:'short'}):d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});svg+=`<text x="${p(Math.min(L+pw-20,Math.max(L+20,x(t))))}" y="${H-8}" fill="#6f9a8f" font-size="11" text-anchor="middle">${esc(label)}</text>`;}
 if(o.base!=null&&o.base>lo&&o.base<hi)svg+=`<line x1="${L}" x2="${L+pw}" y1="${p(y(o.base))}" y2="${p(y(o.base))}" stroke="#4b7268" stroke-dasharray="3 3"/>`;
 svg+=`<path d="${line}V${p(floor)}H${p(x(t0))}Z" fill="url(#eq-fill)"/>`;
 if(bal)svg+=`<path d="${bal}" fill="none" stroke="#9fc8be" stroke-opacity=".6" stroke-dasharray="5 4" stroke-width="1.2"/>`;
 svg+=`<path d="${line}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"/>`;
 if(o.step)series.forEach(pt=>{if(pt.trade!=null)svg+=`<circle cx="${p(x(pt.t))}" cy="${p(y(pt.v))}" r="3" fill="${pt.trade>=0?'#5ee0be':'#e08797'}"/>`;});
 const lastPt=series.at(-1);svg+=`<rect x="${L+pw+2}" y="${p(y(lastPt.v)-8)}" width="${R-4}" height="16" rx="3" fill="${col}"/><text x="${L+pw+6}" y="${p(y(lastPt.v)+4)}" fill="#0b1d18" font-size="10.5" font-weight="700">${fmt(lastPt.v,2)}</text>`;
 svg+=`<g id="eq-cross" visibility="hidden" pointer-events="none"><line id="eq-cross-x" y1="${T}" y2="${T+ph}" stroke="#8ab8ac" stroke-dasharray="3 3"/><circle id="eq-cross-dot" r="4" fill="${col}" stroke="#0e251f" stroke-width="2"/></g></svg><div class="chart-tip hidden" id="equity-tip" role="status"></div>`;
 equityGeom={series,W,H,x,y,L,pw,T,ph,cur:o.cur,step:o.step,balance:o.balance};
 return svg;
}
function equityHover(e){
 const g=equityGeom,svg=$('equity-chart').querySelector('svg');if(!g||!svg)return;
 const r=svg.getBoundingClientRect(),vx=(e.clientX-r.left)/r.width*g.W,tip=$('equity-tip');
 if(vx<g.L||vx>g.L+g.pw){$('eq-cross').setAttribute('visibility','hidden');tip.classList.add('hidden');return;}
 let best=0;for(let i=0;i<g.series.length;i++){if(g.x(g.series[i].t)<=vx)best=i;else break;}
 if(!g.step){const n=g.series[best+1];if(n&&Math.abs(g.x(n.t)-vx)<Math.abs(g.x(g.series[best].t)-vx))best++;}
 const pt=g.series[best],X=g.x(pt.t),Y=g.y(pt.v);
 $('eq-cross-x').setAttribute('x1',X);$('eq-cross-x').setAttribute('x2',X);$('eq-cross-dot').setAttribute('cx',X);$('eq-cross-dot').setAttribute('cy',Y);$('eq-cross').setAttribute('visibility','visible');
 tip.innerHTML=`<b>${esc(new Date(pt.t*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}))}</b><span>${g.step?'Cumulative':'Equity'} ${fmt(pt.v,2)} ${esc(g.cur)}</span>${g.balance&&Number.isFinite(pt.b)?`<span class="muted">Balance ${fmt(pt.b,2)} · floating ${pt.v-pt.b>=0?'+':''}${fmt(pt.v-pt.b,2)}</span>`:''}${pt.trade!=null?`<span class="${pt.trade>=0?'buy':'sell'}">${esc(pt.symbol||'')} ${esc(pt.strategy||'')} ${pt.trade>=0?'+':''}${fmt(pt.trade,2)}</span>`:''}`;
 tip.classList.remove('hidden');
 const box=$('equity-chart').getBoundingClientRect(),px=e.clientX-box.left;
 tip.style.left=Math.max(4,px>box.width/2?px-tip.offsetWidth-14:px+14)+'px';tip.style.top=Math.max(4,(Y/g.H)*box.height-tip.offsetHeight-10)+'px';
}
$('equity-mode').addEventListener('click',e=>{const b=e.target.closest('[data-eq-mode]');if(!b)return;eqMode=b.dataset.eqMode;eqModeChosen=true;saveEquityPrefs();renderEquity();});
$('equity-range').addEventListener('click',e=>{const b=e.target.closest('[data-eq-range]');if(!b||b.dataset.eqRange===eqRange)return;eqRange=b.dataset.eqRange;saveEquityPrefs();renderEquity();refreshEquity(true);});
$('equity-chart').addEventListener('mousemove',equityHover);
$('equity-chart').addEventListener('mouseleave',()=>{$('eq-cross')?.setAttribute('visibility','hidden');$('equity-tip')?.classList.add('hidden');});
renderEquityButtons();
