const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=require('./dashboard_source.cjs'),html=fs.readFileSync(__dirname+'/dashboard.html','utf8');
const nodes={};const $=id=>nodes[id]??={textContent:'',innerHTML:''};
const ctx={$ ,setHTML:(id,h)=>{$(id).innerHTML=h;},paginate:(b,_p,items,row,o={})=>{const l=items||[];$(b).innerHTML=l.length?l.slice(0,o.pageSize||10).map((it,i)=>row(it,i)).join(''):'<tr><td colspan="'+(o.colspan||1)+'" class="empty">'+(o.empty||'')+'</td></tr>';},esc:v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),fmt:(v,n=2)=>Number(v).toFixed(n),date:()=>'',demo:false};
vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('let aiData=null'),src.indexOf('async function refresh(){')),ctx);
vm.runInContext('renderAI()',ctx);assert.ok($('ai-state').textContent.includes('NOT TRAINED'));assert.ok($('ai-rows').innerHTML.includes('No eligible'));
ctx.fixture={status:'TRAINED SHADOW ONLY',candidates:301,labelled:300,labelled_days:30,
 model:{id:'<model>',evaluation_status:'RESEARCH SCREEN FAILED',training_rows:240,selection_bias:'Executed only',holdout:{count:60,taken:20,baseline_sum_r:10,shadow_sum_r:2,baseline_drawdown_r:4,shadow_drawdown_r:5,mae:.6,baseline_mae:.5}},
 recent:[{strategy:'BBMA',symbol:'AUDUSD',observed:100,prediction:{model_id:'<model>',suggestion:'MODEL SKIP',estimated_r:-.2},execution_status:'OPEN',label_r:null}]};
vm.runInContext('aiData=fixture;renderAI()',ctx);assert.ok($('ai-model').innerHTML.includes('&lt;model&gt;'));assert.ok($('ai-model').innerHTML.includes('RESEARCH SCREEN FAILED'));
assert.ok($('ai-rows').innerHTML.includes('MODEL SKIP'));assert.ok($('ai-rows').innerHTML.includes('OPEN'));assert.ok($('ai-rows').innerHTML.includes('UNKNOWN'));assert.ok($('ai-rows').innerHTML.includes('-0.200R'));
vm.runInContext('aiData={error:"Disconnected"};renderAI()',ctx);assert.equal($('ai-state').textContent,'UNAVAILABLE');
for(const id of ['ai-state','ai-counts','ai-note','ai-model','ai-rows'])assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1);
console.log('AI shadow UI: untrained, trained research-only, failed screen, unknown labels, escaping and errors passed');
