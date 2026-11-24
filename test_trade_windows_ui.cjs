// Trade-window local-time conversion: the UI shows local PC time, but windows are
// stored and enforced in MT5 server (broker) time. Verify the conversion helpers
// and that a server->local->server round-trip is lossless.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const settings=fs.readFileSync(__dirname+'/js/settings.js','utf8');
// Slice just the conversion helpers (twDelta .. twLocalToServer), before twZoneLabel.
const slice=settings.slice(settings.indexOf('function twDelta()'),settings.indexOf('function twZoneLabel'));
assert.ok(slice.includes('twServerToLocal')&&slice.includes('twLocalToServer'),'helpers present');

function makeCtx(brokerHours,pcOffsetMinutes){
 // getTimezoneOffset() returns minutes BEHIND UTC (UTC+8 -> -480).
 class FakeDate{constructor(){}getTimezoneOffset(){return pcOffsetMinutes;}}
 const ctx={data:{meta:{tick_offset:brokerHours}},Date:FakeDate};
 vm.createContext(ctx);
 // `const` arrow fns stay lexically scoped in vm; export them onto the context.
 vm.runInContext(slice+';this.twServerToLocal=twServerToLocal;this.twLocalToServer=twLocalToServer;',ctx);
 return ctx;
}

// Broker UTC+2, PC UTC+8 (getTimezoneOffset=-480): local = server + 6h.
let c=makeCtx(2,-480);
assert.equal(c.twDelta(),360,'delta is +360 minutes (6h)');
assert.equal(c.twServerToLocal('10:00'),'16:00','CONFLUENCE start 10:00 server -> 16:00 local');
assert.equal(c.twServerToLocal('21:00'),'03:00','CONFLUENCE end 21:00 server -> 03:00 local (wraps midnight)');
assert.equal(c.twLocalToServer('16:00'),'10:00','16:00 local -> 10:00 server');
assert.equal(c.twLocalToServer('03:00'),'21:00','03:00 local -> 21:00 server');
// Round-trip every half hour is lossless.
for(let m=0;m<1440;m+=30){const hh=String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');
 assert.equal(c.twLocalToServer(c.twServerToLocal(hh)),hh,'round-trip '+hh);}

// Broker UTC+3 (summer DST), PC UTC+8: delta = 480-180 = 300 (5h).
let d=makeCtx(3,-480);
assert.equal(d.twDelta(),300,'DST: delta is +300 minutes (5h)');
assert.equal(d.twServerToLocal('10:00'),'15:00','DST: 10:00 server -> 15:00 local');

// Same timezone as broker: no shift.
let e=makeCtx(2,-120);
assert.equal(e.twDelta(),0,'same zone: no shift');
assert.equal(e.twServerToLocal('09:30'),'09:30','same zone unchanged');

console.log('Trade-window local-time conversion: delta, server<->local, midnight wrap, DST and round-trip passed');
