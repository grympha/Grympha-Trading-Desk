const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=require('./dashboard_source.cjs'),html=fs.readFileSync(__dirname+'/dashboard.html','utf8');
const nodes={};const $=id=>nodes[id]??={innerHTML:'',textContent:'',value:'',checked:false,showModal(){this.open=true;}};
const setHTML=(id,h)=>{$(id).innerHTML=h;};
const paginate=(bodyId,_pagerId,items,rowHtml,opts={})=>{setHTML(bodyId,(items||[]).slice(0,opts.pageSize||10).map(rowHtml).join(''));};
const context={$ ,setHTML,paginate,apiOnline:true,paused:false,now:()=>500,esc:v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),fmt:(v,n=2)=>Number(v).toFixed(n),date:()=>'',demo:false};
vm.createContext(context);vm.runInContext(source.slice(source.indexOf('let platformData=null'),source.indexOf('async function refresh(){')),context);
vm.runInContext('renderPlatform()',context);assert.equal($('portfolio-total').textContent,'UNKNOWN');assert.ok($('portfolio-rows').innerHTML.includes('unavailable'));
context.fixture={bot_online:true,controls:{enabled:true,total_percent:3,pair_percent:1.5,currency_percent:2},
portfolio:{status:'UNKNOWN',updated_epoch:490,equity:10000,total_risk:null,known_risk:12,currency:'USD',unknown:['Missing SL'],rows:[{symbol:'<bad>',strategy:'MANUAL',kind:'POSITION',side:'BUY',volume:.1,risk:null,unknown:['No SL']}],by_currency:{USD:{gross:12,long:12,short:0}},basis:'current equity'},
current_check:{reasons:['Unknown exposure']},last_check:{status:'BLOCKED'},health:{components:{'BBMA Scanner':{status:'HEALTHY',updated_epoch:490,note:'<safe>'}}},watchdog:{status:'WATCHING',updated_epoch:499},alerts:[{status:'BOT EXITED',updated_epoch:400}],
audit:{total:2,checks:1,sends:1,rejections:0,uncertain:0,average_send_ms:3,events:[{id:1,event:'ORDER_SEND',epoch:490,symbol:'AUDUSD',strategy:'BBMA',retcode:10009,latency_ms:3,slippage_points:2,request:{comment:'<safe>'}}]}};
vm.runInContext('platformData=fixture;renderPlatform();auditDetails("1")',context);
assert.equal($('portfolio-mode').textContent,'ENFORCING');assert.equal($('portfolio-entry').textContent,'BLOCKED');assert.equal($('portfolio-total').textContent,'UNKNOWN');
assert.ok($('portfolio-rows').innerHTML.includes('&lt;bad&gt;'));assert.ok($('health-components').innerHTML.includes('&lt;safe&gt;'));assert.ok($('details').open);assert.ok($('detail-content').innerHTML.includes('&lt;safe&gt;'));
$('portfolio-total-limit').value='4.25';vm.runInContext('portfolioDirty=true;renderPlatform()',context);assert.equal($('portfolio-total-limit').value,'4.25');
context.apiOnline=false;context.fixture.bot_online=false;vm.runInContext('renderPlatform()',context);assert.ok($('health-components').innerHTML.includes('LAST REPORTED'));
for(const id of ['tab-monitor','panel-monitor','portfolio-form','portfolio-enabled','portfolio-total-limit','portfolio-pair-limit','portfolio-currency-limit','health-components','audit-rows','health-alerts'])assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1);
console.log('Portfolio/health/audit UI: unknown not zero, enforcement mode, escaping, stale state, dirty inputs, dialog and tab passed');
