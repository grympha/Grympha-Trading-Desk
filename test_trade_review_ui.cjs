const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=require('./dashboard_source.cjs'),html=fs.readFileSync(__dirname+'/dashboard.html','utf8');
const nodes={};const $=id=>nodes[id]??=( {innerHTML:'',textContent:'',value:'ALL',dataset:{},showModal(){this.open=true;}} );
const ctx={$ ,setHTML:(id,h)=>{$(id).innerHTML=h;},paginate:(b,_p,items,row,o={})=>{const l=items||[];$(b).innerHTML=l.length?l.slice(0,o.pageSize||10).map((it,i)=>row(it,i)).join(''):'<tr><td colspan="'+(o.colspan||1)+'" class="empty">'+(o.empty||'')+'</td></tr>';},data:{meta:{strategies:{BBMA:{symbols:['AUDUSD']}}}},apiOnline:true,paused:false,now:()=>200,demo:false,
 esc:v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),fmt:(v,n=2)=>Number(v).toFixed(n),date:()=>'',chip:v=>String(v),
 setOptions:(el,items)=>{el.items=items;},URLSearchParams};
vm.createContext(ctx);
vm.runInContext(src.slice(src.indexOf('let reviewData=null'),src.indexOf('async function refresh(){')),ctx);
vm.runInContext('renderReview()',ctx);assert.ok($('review-state').textContent.includes('Waiting'));
ctx.fixture={status:'OBSERVING',updated_epoch:190,total:1,currencies:{USD:{net:-20,count:1,wins:0,losses:1,flat:0}},
 patterns:[{strategy:'BBMA',symbol:'AUDUSD',setup:'<unsafe>',side:'BUY',status:'COLLECTING DATA',count:1,wins:0,losses:1,net:-20,currency:'USD',sl_losses:1,tp1_then_loss:0,suggestions:['<test>'],evidence:'descriptive'}],
 reviews:[{key:'key1',strategy:'BBMA',symbol:'AUDUSD',setup:'<unsafe>',side:'BUY',closed_epoch:180,realised:-20,currency:'USD',outcome:'LOSS',sampled_mfe_r:null,sampled_mae_r:null,samples:0,exit_reason:'Stop loss',tags:[],review_status:'COLLECTING DATA',data_quality:['Legacy'],limited_audit:{one:null},before:{},management:{}}]};
vm.runInContext('reviewData=fixture;renderReview();reviewDetails("key1")',ctx);
assert.ok($('review-totals').innerHTML.includes('-20.00'));assert.ok($('review-patterns').innerHTML.includes('&lt;unsafe&gt;'));
assert.ok(!$('review-patterns').innerHTML.includes('<unsafe>'));assert.ok($('review-rows').innerHTML.includes('lower bounds'));
assert.ok($('details').open);assert.ok($('detail-content').innerHTML.includes('UNKNOWN'));
ctx.apiOnline=false;vm.runInContext('renderReview()',ctx);assert.ok($('review-state').textContent.includes('LAST REPORTED'));
for(const id of ['tab-review','panel-review','review-state','review-rows','review-strategy','review-symbol'])assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1);
assert.ok(src.includes("['overview','positions','ledger','trades','history','review','lab','livetest','monitor','settings']"));
console.log('Trade Review rendering, escaping, stale state, modal and nine-tab wiring passed');
