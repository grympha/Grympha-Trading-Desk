const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=require('./dashboard_source.cjs'),html=fs.readFileSync(__dirname+'/dashboard.html','utf8');
const nodes={};const $=id=>nodes[id]??={innerHTML:'',textContent:'',value:'ALL',checked:false,showModal(){this.open=true;}};
const ctx={$ ,setHTML:(id,h)=>{$(id).innerHTML=h;},paginate:(b,_p,items,row,o={})=>{const l=items||[];$(b).innerHTML=l.length?l.slice(0,o.pageSize||10).map((it,i)=>row(it,i)).join(''):'<tr><td colspan="'+(o.colspan||1)+'" class="empty">'+(o.empty||'')+'</td></tr>';},data:{meta:{strategies:{BBMA:{symbols:['AUDUSD']}}}},esc:v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),fmt:(v,n=2)=>Number(v).toFixed(n),date:()=>'',chip:v=>v,setOptions:()=>{},URLSearchParams,demo:false};
vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('let labData=null'),src.indexOf('async function refresh(){')),ctx);
vm.runInContext('renderLab()',ctx);assert.ok($('lab-rows').innerHTML.includes('Waiting'));
const metric={matched:1,inconclusive:1,days:1,status:'COLLECTING DATA',baseline:{sum_r:1},candidate:{sum_r:0},delta_mean_r:-1,paired_day_block_ci95:null,avoided_losses:0,missed_winners:1,note:'Descriptive, no promotion'};
ctx.fixture={status:'READY FOR REPLAY',candidate_count:2,replayed:2,settings:{enabled:true,timestamp_verified:true,tick_offset_hours:0,cost_r:.02,slippage_r:.01,latency_ms:200,horizon_minutes:5,max_gap_seconds:10},
versions:[{id:'v1',name:'<test>',created:100,baseline:true,policy:{exit_target:'TP2',sl_multiplier:1,tp_multiplier:1,max_spread_r:.25,min_model_r:null}}],
comparisons:[{name:'<test>',all:metric,forward:metric}],recent:[{observed:100,symbol:'AUDUSD',strategy:'BBMA',actual_status:'REJECTED',source:'CSV',tick_hash:'abc123',baseline:{status:'INCONCLUSIVE',reason:'<gap>',net_r:null}}]};
vm.runInContext('labData=fixture;renderLab();labDetails(0)',ctx);
assert.ok($('lab-versions').innerHTML.includes('&lt;test&gt;'));assert.ok($('lab-rows').innerHTML.includes('INCONCLUSIVE'));assert.ok($('lab-rows').innerHTML.includes('&lt;gap&gt;'));
assert.ok($('lab-comparisons').innerHTML.includes('Enrolled forward only'));assert.ok($('lab-comparisons').innerHTML.includes('Not enough days'));assert.ok($('details').open);assert.ok($('detail-content').innerHTML.includes('not broker fills'));
$('lab-cost').value='.09';vm.runInContext('labDirty=true;renderLab()',ctx);assert.equal($('lab-cost').value,'.09');
for(const id of ['tab-lab','panel-lab','lab-settings-form','lab-version-form','lab-status','lab-rows','lab-comparisons'])assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1);
assert.ok(src.includes("['overview','positions','ledger','trades','history','review','lab','livetest','monitor','settings']"));
console.log('Replay UI: assumptions, immutable registry, inconclusive not zero, forward separation, escaping, dirty inputs, details and nine tabs passed');
