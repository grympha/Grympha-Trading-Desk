// Grympha command center (home page). Read-only: it only GETs /api/home and draws it.
// Three switchable 3D centrepieces (globe, war table, galaxy) share the HUD, panels and camera work.
'use strict';
const DW = 1920, DH = 1080;           // desktop design size; on a phone W x H is the 3D box instead
let W = DW, H = DH, PHONE = false;
const C = { teal:'#5ee0be', red:'#f28c99', amber:'#e9bc75', blue:'#6c94ce', gold:'#ccae76', goldSoft:'#e2cb98', muted:'#89ada4', text:'#e8f5f2', line:'rgba(94,224,190,.12)' };
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const STILL = /[?&]still/.test(location.search);
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]);
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const fmtPx = (v, d) => v == null ? '—' : d != null ? Number(v).toFixed(d) : v >= 1000 ? v.toFixed(1) : v >= 50 ? v.toFixed(2) : v >= 10 ? v.toFixed(3) : v.toFixed(5);
const signR = (v, d = 1) => v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d) + 'R';
const money = v => v == null ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// ---------- data ----------
let D = null, lastOk = 0, offline = false;
function sampleData(){   // shown only when the desk cannot be reached (page opened as a file, server down)
  const pairs = ['XAUUSD','XAGUSD','XAUEUR','EURUSD','GBPUSD','USDJPY','USDCHF','USDCAD','AUDUSD','NZDUSD','GBPJPY','EURJPY','AUDJPY','CHFJPY','EURAUD','EURNZD','GBPAUD','GBPCAD','BTCUSD','ETHUSD'];
  const ids = [['LIVE_BBMA',60],['LIVE_CONFLUENCE',20],['LIVE_ORB',3],['H4_LIQ_SWEEP_FVG',20],['H4_SWING_SWEEP_FVG_ENGULF',20],['CMS_SSR',20],['CMS_SR_FLIP',20],['CMS_FAKEOUT_V1',20],['CMS_SUPPLY_DEMAND',20],['CRT_H1_M5',20],['NY_SWEEP_LONDON',20],['INVERSE_FVG_RETEST',20],['PREV_CANDLE_BREAKOUT_H1',20],['COUNTER_TREND_SCALP',20],['AMD_PO3',3],['ORB_BREAKOUT_RETEST',1],['MOMENTUM_M5_M1',1],['ADVOCATE_EMA_PULLBACK',1]];
  let p = 4128; const candles = Array.from({ length: 64 }, (_, i) => { const o = p, c = o + (rnd() - .48) * 3.2; p = c; return { t: i, o, c, h: Math.max(o, c) + rnd() * 1.6, l: Math.min(o, c) - rnd() * 1.6 }; });
  return { status: 'SAMPLE', account: { mode: 'OFFLINE', equity: null, floating: null }, totals: { agents: 309, running: 309, pairs: 20, strategies: 18, trades_30d: 0, net_r_30d: null, signals_24h: 0 },
    strategies: ids.map(([id, n]) => ({ id, name: id.replace(/_/g, ' '), agents: n, running: n, pairs: pairs.slice(0, Math.min(20, n)), trades: 0, win_rate: null, net_r: 0, live: id.startsWith('LIVE_') })),
    pairs, pair_stats: pairs.map(s => ({ symbol: s, agents: 15, net_r: 0 })), quotes: [], candles, dist: [], heat: [], heat_days: 14, leaders: [], feed: [] };
}
async function fetchHome(){
  try {
    const r = await fetch('api/home', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    D = await r.json(); lastOk = Date.now(); offline = false;
  } catch (e) { if (!D) { D = sampleData(); offline = true; } }
  applyData();
}

// ---------- stage fit (16:9 letterboxed) ----------
const stage = $('stage');
let scale = 1;
// CSS zoom (not transform: scale) so the browser lays text and cards out at the real screen size and
// draws them sharp; a scaled transform rasterises at 1920x1080 and then stretches, which looks soft.
const ZOOM = CSS.supports('zoom', '2');
// Phone held upright: the 16:9 stage would shrink to ~21% (2-3 px text), so the page becomes a scrolling
// column instead (CSS #stage.phone) with the 3D scene in its own box. ?phone forces it for testing.
const sceneBox = $('scenebox');
const wantPhone = () => /[?&]phone\b/.test(location.search) || (innerWidth < innerHeight && innerWidth <= 820);
let phoneW = 0, phoneVH = 0;
function fit(){
  PHONE = wantPhone();
  stage.classList.toggle('phone', PHONE); document.documentElement.classList.toggle('phone', PHONE);
  if (PHONE) {
    scale = 1; stage.style.zoom = ''; stage.style.left = stage.style.top = stage.style.transform = '';
    if (innerWidth !== phoneW) { phoneW = innerWidth; phoneVH = innerHeight; }   // ignore the URL bar showing/hiding
    W = Math.max(1, sceneBox.clientWidth || innerWidth); H = Math.round(Math.min(W * 1.2, phoneVH * .62));
    sceneBox.style.height = H + 'px';
    return;
  }
  sceneBox.style.height = ''; W = DW; H = DH;
  scale = Math.min(innerWidth / W, innerHeight / H);
  const x = Math.round((innerWidth - W * scale) / 2), y = Math.round((innerHeight - H * scale) / 2);
  if (ZOOM) { stage.style.zoom = scale; stage.style.left = (x / scale) + 'px'; stage.style.top = (y / scale) + 'px'; stage.style.transform = ''; }
  else stage.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
}
addEventListener('resize', () => { fit(); if (window.__ready) { sizeGL(); sizeCanvases(); } }); fit();
const HAS3D = !!(window.THREE && (() => { try { return !!document.createElement('canvas').getContext('webgl'); } catch (e) { return false; } })());
if (!HAS3D) stage.insertAdjacentHTML('beforeend', '<div class="nogl">3D VIEW UNAVAILABLE<br>(this browser has WebGL turned off)</div>');

// ---------- three.js engine (shared by every scene) ----------
let renderer, composer = null, bloom = null, renderPass = null, camera, scene = null, DOT, ADD;
let V = null, cards = [], labels = [], PN = 0, pp, pv, dustGeo, DY = 13;
const holo = $('holo');
if (HAS3D) {
  renderer = new THREE.WebGLRenderer({ canvas: $('gl'), antialias: true });
  renderer.setClearColor(0x000000, 0);
  camera = new THREE.PerspectiveCamera(38, W / H, 0.1, 300);
  if (THREE.EffectComposer && THREE.UnrealBloomPass) {
    composer = new THREE.EffectComposer(renderer);
    renderPass = new THREE.RenderPass(new THREE.Scene(), camera); composer.addPass(renderPass);
    bloom = new THREE.UnrealBloomPass(new THREE.Vector2(W, H), .6, .35, .3); composer.addPass(bloom);   // glow without smearing
  }
  const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
  const gr = g.createRadialGradient(32,32,0,32,32,32); gr.addColorStop(0,'rgba(255,255,255,1)'); gr.addColorStop(.25,'rgba(255,255,255,.85)'); gr.addColorStop(1,'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0,0,64,64); DOT = new THREE.CanvasTexture(c); ADD = THREE.AdditiveBlending;
  // bokeh: big soft out-of-focus lights close to the lens, fixed to the camera
  const bk = new Float32Array(60 * 3); for (let i = 0; i < 60; i++) { bk[i*3] = (rnd() - .5) * 14; bk[i*3+1] = (rnd() - .5) * 8; bk[i*3+2] = 6 + rnd() * 5; }
  const bokehGeo = new THREE.BufferGeometry(); bokehGeo.setAttribute('position', new THREE.BufferAttribute(bk, 3));
  const bokeh = new THREE.Points(bokehGeo, new THREE.PointsMaterial({ map: DOT, transparent: true, depthWrite: false, blending: ADD, size: .55, color: 0x5ee0be, opacity: .1 }));
  camera.add(bokeh); bokeh.position.z = -14;
}
function sizeGL(){
  if (!HAS3D) return;
  const pr = PHONE ? Math.min(2, devicePixelRatio)                     // phone: cap at 2x to spare the GPU
    : Math.min(3, Math.max(1, devicePixelRatio * scale));              // one 3D pixel per screen pixel
  renderer.setPixelRatio(pr);
  renderer.setSize(W, H, false);
  // the phone box frames what sits between the desktop side panels (~52% of the width), at its own aspect
  camera.aspect = W / H;
  camera.fov = PHONE ? 2 * Math.atan(Math.tan(19 * Math.PI / 180) * (16 / 9) * .52 / camera.aspect) * 180 / Math.PI : 38;
  camera.updateProjectionMatrix();
  if (composer) { composer.setPixelRatio && composer.setPixelRatio(pr); composer.setSize(W, H); }
}
sizeGL();
const pointsMat = o => new THREE.PointsMaterial(Object.assign({ map: DOT, transparent: true, depthWrite: false, blending: ADD }, o));
const lineMat = (color, opacity) => new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: ADD, depthWrite: false });
const basicMat = (color, opacity, extra) => new THREE.MeshBasicMaterial(Object.assign({ color, transparent: true, opacity, blending: ADD, depthWrite: false }, extra || {}));
function glowSprite(color, size, opacity){ const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: DOT, color, transparent: true, opacity, depthWrite: false, blending: ADD })); s.scale.set(size, size, 1); return s; }
function circle(r, y, n = 128){ const p = []; for (let i = 0; i <= n; i++) { const a = i / n * Math.PI * 2; p.push(new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r)); } return new THREE.BufferGeometry().setFromPoints(p); }
function addDust([DX, dy, DZ, DZ0]){
  PN = 2200; DY = dy; pp = new Float32Array(PN * 3); pv = new Float32Array(PN);
  for (let i = 0; i < PN; i++) { pp[i*3] = (rnd() - .5) * DX; pp[i*3+1] = (rnd() - .5) * DY; pp[i*3+2] = (rnd() - .5) * DZ + DZ0; pv[i] = .05 + rnd() * .18; }
  dustGeo = new THREE.BufferGeometry(); dustGeo.setAttribute('position', new THREE.BufferAttribute(pp, 3));
  scene.add(new THREE.Points(dustGeo, pointsMat({ size: .045, color: 0x8fe9d2, opacity: .5 })));
}
function disposeScene(sc){
  sc.traverse(o => { if (o.geometry) o.geometry.dispose();
    if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { if (m.map && m.map !== DOT) m.map.dispose(); m.dispose(); }); });
}

// ---------- data helpers used by the scenes ----------
const STRATS = () => D.strategies || [];
const PAIRS = () => D.pairs || [];
const totalAgents = () => STRATS().reduce((s, x) => s + x.agents, 0);
const pairStat = sym => (D.pair_stats || []).find(p => p.symbol === sym) || { symbol: sym, agents: 0, net_r: 0, trades: 0 };

// holographic strategy cards (HTML projected from 3D; distance from the focus plane drives blur = shallow depth of field)
function cardHTML(s){
  const win = s.win_rate == null ? null : Math.round(s.win_rate * 100), net = s.net_r;
  return `<div class="n">${esc(s.name)}</div><div class="addr">lt://${esc(s.id.toLowerCase())}/<span class="sym">${esc(s.pairs[0] || '')}</span></div>`
    + `<div class="row"><span>AGENTS <b>${s.running}/${s.agents}</b></span><span>WIN <b>${win == null ? '—' : win + '%'}</b></span><span>30D <b class="${net > 0 ? 'up' : net < 0 ? 'down' : ''}">${s.trades ? signR(net) : '—'}</b></span></div>`
    + `<div class="bar"><i style="width:${win || 0}%"></i></div>`;
}
function makeCards(anchors){
  return STRATS().map((s, i) => {
    const el = document.createElement('div'); el.className = 'agent' + (s.live ? ' gold' : ''); el.style.setProperty('--d', (rnd() * 4).toFixed(2) + 's');
    el.innerHTML = cardHTML(s); holo.appendChild(el);
    return { el, id: s.id, anchor: anchors[i], k: 0 };
  });
}
function refreshCards(){
  const by = {}; STRATS().forEach(s => by[s.id] = s);
  cards.forEach(c => { const s = by[c.id]; if (s) { c.el.innerHTML = cardHTML(s); c.k = 0; } });
}
setInterval(() => { if (!D) return; const by = {}; STRATS().forEach(s => by[s.id] = s);
  cards.forEach(c => { const s = by[c.id], sym = c.el.querySelector('.sym'); if (s && sym && s.pairs.length > 1 && rnd() < .5) { c.k = (c.k + 1) % s.pairs.length; sym.textContent = s.pairs[c.k]; } }); }, 900);
function makeLabels(list){ return list.map(([text, anchor]) => { const e = document.createElement('div'); e.className = 'hub'; e.textContent = text; holo.appendChild(e); return { el: e, anchor }; }); }
let tmp, wp;
function project(v){ tmp.copy(v).project(camera); return [(tmp.x + 1) / 2 * W, (1 - tmp.y) / 2 * H, tmp.z]; }
function placeCards(){
  const focus = camera.position.distanceTo(V.focus);
  let occ = null;
  if (V.occ) { const c = project(V.occ.c), e = project(V.occ.c.clone().add(new THREE.Vector3(V.occ.r, 0, 0))); occ = { x: c[0], y: c[1], r: Math.abs(e[0] - c[0]), d: camera.position.distanceTo(V.occ.c) }; }
  for (const c of cards) {
    c.anchor.getWorldPosition(wp);
    const d = camera.position.distanceTo(wp), [x, y, z] = project(wp);
    if (z > 1) { c.el.style.opacity = '0'; continue; }
    const sc = Math.max(.5, Math.min(1.15, V.cardScale / d)) * (PHONE ? .6 : 1);
    const blur = Math.min(1.2, Math.max(0, Math.abs(d - focus) * .45 - 1.2));   // light depth of field; nearby cards stay crisp
    const behind = occ && d > occ.d && Math.hypot(x - occ.x, y - occ.y) < occ.r * 1.05;
    c.el.style.transform = `translate(${(x - 98).toFixed(1)}px,${(y - 38).toFixed(1)}px) scale(${sc.toFixed(3)})`;
    c.el.style.filter = blur > .15 ? `blur(${blur.toFixed(2)}px)` : 'none';
    c.el.style.opacity = (behind ? .12 : Math.max(.25, Math.min(1, 1.2 - (d - focus) * .2))).toFixed(2);
    c.el.style.zIndex = String(1000 - Math.round(d * 50));
  }
  for (const l of labels) {
    l.anchor.getWorldPosition(wp); const [x, y, z] = project(wp);
    const a = z > 1 ? 0 : (V.labelAlpha ? V.labelAlpha(wp) : 1);
    l.el.style.opacity = a.toFixed(2);
    l.el.style.transform = `translate(${(x + 6).toFixed(1)}px,${(y - 7).toFixed(1)}px)`;
  }
}
// pulses travelling along curves
function pulseSystem(curves, parent){
  const n = curves.length, pos = new Float32Array(n * 12), col = new Float32Array(n * 12);
  curves.forEach((a, i) => { for (let k = 0; k < 4; k++) { const c = new THREE.Color(a.color || 0xaafff0).multiplyScalar(1 - k * .22); col.set([c.r, c.g, c.b], (i * 4 + k) * 3); } });
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3)); geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  parent.add(new THREE.Points(geo, pointsMat({ size: .12, vertexColors: true })));
  return dt => { curves.forEach((a, i) => { a.t = (a.t + dt * a.speed) % 1; for (let q = 0; q < 4; q++) { const p = a.curve.getPoint(Math.max(0, a.t - q * .025)); pos.set([p.x, p.y, p.z], (i * 4 + q) * 3); } }); geo.attributes.position.needsUpdate = true; };
}
// market performance colour: teal = Live Test net R up over 30 days, red = down, gold = no trades yet
const perfColor = sym => { const r = pairStat(sym).net_r || 0; return r > 0 ? 0x5ee0be : r < 0 ? 0xf28c99 : 0xccae76; };

// ================================================================== A · GLOBE
function sceneGlobe(){
  const V = { name: 'globe', fog: .045, bloom: [.85, .55, .22], focus: new THREE.Vector3(0, .05, 0), occ: { c: new THREE.Vector3(0, .05, 0), r: 1.38 }, cardScale: 9.2, dust: [22, 13, 16, -2],
    caption: 'AGENT NETWORK · LIVE', sub: () => `${D.totals.agents} AGENTS · ${D.totals.pairs} MARKETS · ${D.totals.strategies} STRATEGIES` };
  const R = 1.38, pairs = PAIRS();
  const world = new THREE.Group(); world.position.set(0, .05, 0); scene.add(world);
  const globe = new THREE.Group(); world.add(globe); globe.rotation.z = .32;
  globe.add(new THREE.Mesh(new THREE.SphereGeometry(R * .985, 96, 96), new THREE.ShaderMaterial({
    uniforms: { c1: { value: new THREE.Color(0x041210) }, c2: { value: new THREE.Color(0x5ee0be) } },
    vertexShader: 'varying vec3 vN; varying vec3 vV; void main(){ vN=normalize(normalMatrix*normal); vec4 mv=modelViewMatrix*vec4(position,1.); vV=normalize(-mv.xyz); gl_Position=projectionMatrix*mv; }',
    fragmentShader: 'uniform vec3 c1; uniform vec3 c2; varying vec3 vN; varying vec3 vV; void main(){ float f=pow(1.-max(dot(vN,vV),0.),3.); gl_FragColor=vec4(mix(c1,c2*.30,pow(f,1.4)),1.); }' })));
  const atm = new THREE.Mesh(new THREE.SphereGeometry(R * 1.28, 64, 64), new THREE.ShaderMaterial({
    uniforms: { c: { value: new THREE.Color(0x5ee0be) } }, side: THREE.BackSide, blending: ADD, transparent: true, depthWrite: false,
    vertexShader: 'varying vec3 vN; void main(){ vN=normalize(normalMatrix*normal); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }',
    fragmentShader: 'uniform vec3 c; varying vec3 vN; void main(){ float i=pow(clamp(.58-dot(vN,vec3(0,0,1.)),0.,1.),4.); gl_FragColor=vec4(c*i*.38,1.); }' }));
  world.add(atm);
  const gmat = lineMat(0x5ee0be, .10);
  for (let lat = -75; lat <= 75; lat += 15) { const r = R * 1.002 * Math.cos(lat * Math.PI / 180), y = R * 1.002 * Math.sin(lat * Math.PI / 180); globe.add(new THREE.Line(circle(r, y), gmat)); }
  for (let lon = 0; lon < 180; lon += 15) { const pts = []; for (let a = 0; a <= 128; a++) { const t = a / 128 * Math.PI * 2; pts.push(new THREE.Vector3(R * 1.002 * Math.cos(t) * Math.cos(lon * Math.PI / 180), R * 1.002 * Math.sin(t), R * 1.002 * Math.cos(t) * Math.sin(lon * Math.PI / 180))); } globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gmat)); }
  const land = v => Math.sin(v.x*2.1+1.3)*Math.cos(v.y*2.7-.4)+Math.sin(v.z*1.9+v.x*1.1)+Math.cos(v.y*3.3+v.z*.8)*.6;
  const nodes = []; for (let i = 0, N = 2600; i < N; i++) { const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963; const v = new THREE.Vector3(Math.cos(th) * r, y, Math.sin(th) * r); if (land(v) > .35 || rnd() < .08) nodes.push(v.multiplyScalar(R * 1.01)); }
  const nodeGeo = new THREE.BufferGeometry().setFromPoints(nodes), ncol = new Float32Array(nodes.length * 3);
  nodes.forEach((v, i) => { const c = new THREE.Color(rnd() < .12 ? 0xccae76 : 0x5ee0be).multiplyScalar(.8 + rnd() * .7); ncol.set([c.r, c.g, c.b], i * 3); });
  nodeGeo.setAttribute('color', new THREE.BufferAttribute(ncol, 3)); globe.add(new THREE.Points(nodeGeo, pointsMat({ size: .06, vertexColors: true })));
  const segs = []; for (let i = 0; i < nodes.length; i += 3) { let best = null, bd = 1e9; for (let k = 0; k < 40; k++) { const j = Math.floor(rnd() * nodes.length), d = nodes[i].distanceToSquared(nodes[j]); if (j !== i && d < bd) { bd = d; best = j; } } if (best !== null && bd < .12) segs.push(nodes[i], nodes[best]); }
  globe.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(segs), lineMat(0x5ee0be, .22)));
  // one hub per traded market; arcs join markets that share a currency, coloured by Live Test result
  const hubs = pairs.map((p, i) => { const y = 1 - (i + .5) / pairs.length * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963 + .7; return new THREE.Vector3(Math.cos(th) * r, y * .85, Math.sin(th) * r).normalize().multiplyScalar(R * 1.015); });
  globe.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(hubs), pointsMat({ size: .17, color: 0xe2cb98 })));
  const curves = [], ccys = p => [p.slice(0, 3), p.slice(3, 6)];
  for (let i = 0; i < pairs.length && curves.length < 40; i++) for (let j = i + 1; j < pairs.length && curves.length < 40; j++) {
    if (!ccys(pairs[i]).some(c => ccys(pairs[j]).includes(c)) || rnd() < .45) continue;
    const a = hubs[i], b = hubs[j], mid = a.clone().add(b).multiplyScalar(.5); mid.normalize().multiplyScalar(R * (1 + a.distanceTo(b) * .3));
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b), col = perfColor(pairs[i]);
    globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(64)), lineMat(col, col === 0x5ee0be ? .34 : .4)));
    curves.push({ curve, t: rnd(), speed: .18 + rnd() * .35, color: col === 0x5ee0be ? 0xaafff0 : col === 0xf28c99 ? 0xffc2ca : 0xffe2a8 });
  }
  const pulses = pulseSystem(curves, globe);
  // three orbit rings carrying every agent as a moving point; the strategy cards ride the rings
  const RINGS = [{ r: 2.05, tiltX: 1.05, tiltZ: .32, speed: .16 }, { r: 2.45, tiltX: .82, tiltZ: -.38, speed: -.11 }, { r: 2.85, tiltX: 1.22, tiltZ: .08, speed: .075 }];
  const rings = RINGS.map(cfg => { const outer = new THREE.Group(); outer.rotation.set(cfg.tiltX, 0, cfg.tiltZ); world.add(outer); const spin = new THREE.Group(); outer.add(spin);
    const pts = []; for (let a = 0; a <= 256; a++) pts.push(new THREE.Vector3(Math.cos(a / 256 * Math.PI * 2) * cfg.r, Math.sin(a / 256 * Math.PI * 2) * cfg.r, 0));
    outer.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat(0x5ee0be, .16))); return { cfg, spin }; });
  const ap = RINGS.map(() => []); for (let i = 0; i < totalAgents(); i++) { const ri = i % 3, cfg = RINGS[ri], a = rnd() * Math.PI * 2, j = (rnd() - .5) * .16; ap[ri].push(new THREE.Vector3(Math.cos(a) * (cfg.r + j), Math.sin(a) * (cfg.r + j), (rnd() - .5) * .12)); }
  rings.forEach((g, i) => g.spin.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(ap[i]), pointsMat({ size: .07, color: i === 1 ? 0xe2cb98 : 0x9ff5df }))));
  const n = STRATS().length;
  cards = makeCards(STRATS().map((s, i) => { const ring = rings[i % 3], ang = (i / n) * Math.PI * 2 * 3 + i * .37, o = new THREE.Object3D(); o.position.set(Math.cos(ang) * ring.cfg.r, Math.sin(ang) * ring.cfg.r, 0); ring.spin.add(o); return o; }));
  labels = makeLabels(pairs.map((p, i) => { const o = new THREE.Object3D(); o.position.copy(hubs[i]).multiplyScalar(1.06); globe.add(o); return [p, o]; }));
  const wc = new THREE.Vector3(0, .05, 0), cd = new THREE.Vector3(), q = new THREE.Vector3();
  V.labelAlpha = p => { cd.copy(camera.position).sub(wc).normalize(); const f = q.copy(p).sub(wc).normalize().dot(cd); return f > .25 ? Math.min(1, (f - .25) * 2.5) : 0; };
  V.update = (t, dt, sp, k) => {
    camera.position.set(Math.sin(t * .07) * .55, .6 + Math.sin(t * .05) * .18 - k * .2, 15 - k * 5.6 + Math.sin(t * .11) * .15); camera.lookAt(0, -.05, 0);
    globe.rotation.y += dt * .42 * sp; atm.rotation.y = globe.rotation.y;
    rings.forEach(g => g.spin.rotation.z += dt * g.cfg.speed * sp);
    pulses(dt * sp);
  };
  return V;
}

// ================================================================== C · WAR TABLE
function sceneTable(){
  const V = { name: 'table', fog: .03, bloom: [.85, .5, .22], focus: new THREE.Vector3(0, .9, 0), occ: null, cardScale: 9.6, dust: [24, 10, 18, -1],
    caption: 'GLOBAL OPERATIONS TABLE', sub: () => `${Object.keys(per).length} FINANCIAL CENTRES · ${D.totals.pairs} MARKETS · ${D.totals.agents} AGENTS` };
  const table = new THREE.Group(); scene.add(table);
  const MW = 8.6, MH = 4.6, ll = (lon, lat, y = 0) => new THREE.Vector3(lon / 180 * MW / 2, y, -lat / 90 * MH / 2);
  const grid = new THREE.GridHelper(16, 80, 0x5ee0be, 0x5ee0be); grid.material.transparent = true; grid.material.opacity = .06; grid.material.depthWrite = false; table.add(grid);
  const CONT = [[-100,47,30,17],[-85,63,26,11],[-60,-15,15,27],[-68,-40,8,14],[10,50,17,10],[25,60,14,8],[20,5,20,30],[25,-25,12,10],[45,28,13,10],[78,22,11,10],[95,52,45,17],[108,27,17,12],[118,0,17,6],[135,-25,17,11],[138,37,4,7],[-3,54,3,5],[172,-41,3,6],[-42,72,12,7]];
  const inside = (lon, lat) => CONT.some(([x, y, rx, ry]) => ((lon - x) / rx) ** 2 + ((lat - y) / ry) ** 2 < 1 + Math.sin(lon * .7 + lat * .3) * .12);
  const md = [], mc = [];
  for (let lon = -178; lon < 180; lon += 2.2) for (let lat = -56; lat < 80; lat += 2.2) if (inside(lon, lat)) { md.push(ll(lon, lat, .01)); const c = new THREE.Color(0x5ee0be).multiplyScalar(.45 + rnd() * .5); mc.push(c.r, c.g, c.b); }
  const mapGeo = new THREE.BufferGeometry().setFromPoints(md); mapGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(mc), 3));
  table.add(new THREE.Points(mapGeo, pointsMat({ size: .065, vertexColors: true })));
  [5.4, 5.55, 5.9].forEach((r, i) => table.add(new THREE.Line(circle(r, 0), lineMat(i === 1 ? 0xccae76 : 0x5ee0be, i === 1 ? .55 : .25))));
  const rimT = []; for (let a = 0; a < 360; a += 2) rimT.push(new THREE.Vector3(Math.cos(a * Math.PI / 180) * 5.72, 0, Math.sin(a * Math.PI / 180) * 5.72));
  table.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(rimT), pointsMat({ size: .04, color: 0xe2cb98 })));
  const dc = document.createElement('canvas'); dc.width = dc.height = 256; { const g = dc.getContext('2d'), gr = g.createRadialGradient(128,128,0,128,128,128); gr.addColorStop(0,'rgba(94,224,190,.5)'); gr.addColorStop(.7,'rgba(94,224,190,.12)'); gr.addColorStop(1,'rgba(94,224,190,0)'); g.fillStyle = gr; g.fillRect(0,0,256,256); }
  const disc = new THREE.Mesh(new THREE.CircleGeometry(5.5, 96), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(dc), transparent: true, opacity: .35, blending: ADD, depthWrite: false })); disc.rotation.x = -Math.PI / 2; table.add(disc);
  const radar = new THREE.Mesh(new THREE.CircleGeometry(5.5, 48, 0, .6), basicMat(0x5ee0be, .13, { side: THREE.DoubleSide })); radar.rotation.x = -Math.PI / 2; radar.position.y = .02; table.add(radar);
  // financial centres: tower height = agents trading that currency (real Live Test registry); one arc per market
  const HUBS = { USD: [-72, 39, 'NEW YORK'], CAD: [-84, 49, 'TORONTO'], EUR: [13, 53, 'FRANKFURT'], GBP: [-6, 55, 'LONDON'], CHF: [7, 43, 'ZURICH'],
                 JPY: [140, 36, 'TOKYO'], AUD: [151, -34, 'SYDNEY'], NZD: [175, -41, 'WELLINGTON'], XAU: [121, 31, 'SHANGHAI'], BTC: [104, 1, 'SINGAPORE'] };
  const ccy = s => ({ XAG: 'XAU', ETH: 'BTC' })[s] || s;
  var per = {}; PAIRS().forEach(p => [p.slice(0, 3), p.slice(3, 6)].forEach(c => { c = ccy(c); if (HUBS[c]) per[c] = (per[c] || 0) + (pairStat(p).agents || 0); }));
  const maxPer = Math.max(1, ...Object.values(per));
  const tops = {}, hubAnchors = [];
  Object.entries(HUBS).forEach(([c, [lon, lat, city]]) => {
    if (per[c] == null) return;
    const base = ll(lon, lat), h = .3 + per[c] / maxPer * 1.5, segs = 4, sh = h / segs;
    for (let s = 0; s < segs; s++) {
      const geo = new THREE.BoxGeometry(.17, sh * .82, .17), m = new THREE.Mesh(geo, basicMat(0x5ee0be, .16 + s * .05)); m.position.set(base.x, sh * s + sh / 2, base.z); table.add(m);
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo), lineMat(s === segs - 1 ? 0xe2cb98 : 0x5ee0be, .85)); e.position.copy(m.position); table.add(e);
    }
    const top = glowSprite(0xe2cb98, .4, .6); top.position.set(base.x, h + .08, base.z); table.add(top);
    const ring = new THREE.Mesh(new THREE.RingGeometry(.2, .23, 48), basicMat(0xccae76, .7, { side: THREE.DoubleSide })); ring.rotation.x = -Math.PI / 2; ring.position.set(base.x, .02, base.z); table.add(ring);
    tops[c] = { pos: new THREE.Vector3(base.x, h, base.z), ring };
    const o = new THREE.Object3D(); o.position.set(base.x, h + .32, base.z); table.add(o); hubAnchors.push([`${city} · ${c} ${per[c]}`, o]);
  });
  const curves = [];
  PAIRS().forEach(p => { const a = tops[ccy(p.slice(0, 3))], b = tops[ccy(p.slice(3, 6))]; if (!a || !b || a === b) return;
    const A = new THREE.Vector3(a.pos.x, .05, a.pos.z), B = new THREE.Vector3(b.pos.x, .05, b.pos.z), mid = A.clone().add(B).multiplyScalar(.5); mid.y = .5 + A.distanceTo(B) * .32;
    const curve = new THREE.QuadraticBezierCurve3(A, mid, B), col = perfColor(p);
    table.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(64)), lineMat(col, col === 0x5ee0be ? .38 : .5)));
    curves.push({ curve, t: rnd(), speed: .2 + rnd() * .3, color: col === 0x5ee0be ? 0xaafff0 : col === 0xf28c99 ? 0xffc2ca : 0xffe2a8 }); });
  const pulses = pulseSystem(curves, table);
  labels = makeLabels(hubAnchors);
  const carousel = new THREE.Group(); carousel.position.y = 1.25; table.add(carousel);
  const n = STRATS().length;
  cards = makeCards(STRATS().map((s, i) => { const a = i / n * Math.PI * 2, o = new THREE.Object3D(); o.position.set(Math.cos(a) * 3.6, (i % 3 - 1) * .35, Math.sin(a) * 2.6); carousel.add(o); return o; }));
  V.update = (t, dt, sp, k) => {
    camera.position.set(Math.sin(t * .06) * .9, 11.5 - k * 3.6 + Math.sin(t * .1) * .1, 13.5 - k * 4.3); camera.lookAt(0, .1, -.35);
    table.rotation.y = Math.sin(t * .05) * .06;
    radar.rotation.z -= dt * .9 * sp;
    carousel.rotation.y += dt * .1 * sp;
    Object.values(tops).forEach((h, i) => { const f = (t * .6 + i * .13) % 1, s = 1 + f * 1.8; h.ring.scale.set(s, s, s); h.ring.material.opacity = .7 * (1 - f); });
    pulses(dt * sp);
  };
  return V;
}

// ================================================================== D · AGENT GALAXY
function sceneGalaxy(){
  const V = { name: 'galaxy', fog: .012, bloom: [.75, .55, .28], focus: new THREE.Vector3(0, 0, 0), occ: null, cardScale: 10.2, dust: [30, 16, 26, -4],
    caption: 'AGENT GALAXY', sub: () => `${D.totals.strategies} STRATEGY ARMS · ${D.totals.agents} AGENT STARS · ${D.totals.pairs} MARKETS` };
  const strats = STRATS(), pairs = PAIRS();
  const gal = new THREE.Group(); gal.rotation.z = .12; scene.add(gal);
  const inner = new THREE.Group(), outer = new THREE.Group(); gal.add(inner); gal.add(outer);
  const ARMS = Math.max(1, strats.length), TW = 1.05, RMAX = 4.3;
  const cIn = new THREE.Color(0xffe2a8), cMid = new THREE.Color(0x5ee0be), cOut = new THREE.Color(0x6c94ce);
  const armPoint = (arm, r, spread) => { const a = arm / ARMS * Math.PI * 2 + r * TW + (rnd() - .5) * spread / (r * .35 + .5); return new THREE.Vector3(Math.cos(a) * r, (rnd() - .5) * .22 / (r * .4 + .6), Math.sin(a) * r); };
  const starColor = r => (r < 1.6 ? cIn.clone().lerp(cMid, r / 1.6) : cMid.clone().lerp(cOut, Math.min(1, (r - 1.6) / (RMAX - 1.6)))).multiplyScalar(.3 + rnd() * .45);
  const fIn = [], cI = [], fOut = [], cO = [];
  for (let i = 0; i < 14000; i++) { const r = Math.pow(rnd(), .75) * RMAX + .08, p = armPoint(i % ARMS, r, .55), c = starColor(r);
    if (r < 1.5) { fIn.push(p); cI.push(c.r, c.g, c.b); } else { fOut.push(p); cO.push(c.r, c.g, c.b); } }
  [[fIn, cI, inner], [fOut, cO, outer]].forEach(([pts, col, parent]) => { const g = new THREE.BufferGeometry().setFromPoints(pts); g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(col), 3)); parent.add(new THREE.Points(g, pointsMat({ size: .042, vertexColors: true }))); });
  // one bright star per Live Test agent, on its strategy's arm
  const stars = [], sc = [];
  strats.forEach((s, arm) => { for (let j = 0; j < s.agents; j++) { stars.push(armPoint(arm, 1.4 + rnd() * (RMAX - 1.6), .25)); const c = new THREE.Color(s.live ? 0xffe2a8 : 0xc8fff0); sc.push(c.r, c.g, c.b); } });
  if (stars.length) { const g = new THREE.BufferGeometry().setFromPoints(stars); g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(sc), 3)); outer.add(new THREE.Points(g, pointsMat({ size: .11, vertexColors: true, opacity: .95 }))); }
  [[.5, 0xffffff, .7], [1.6, 0xe2cb98, .45], [4.5, 0x5ee0be, .08], [10, 0x6c94ce, .035]].forEach(([s, c, o]) => gal.add(glowSprite(c, s, o)));
  [[.015, .07], [.12, .012]].forEach(([r, o]) => gal.add(new THREE.Mesh(new THREE.CylinderGeometry(r, r * 2.5, 9, 24, 1, true), basicMat(0x9ff5df, o, { side: THREE.DoubleSide }))));
  for (let i = 0; i < 16; i++) { const p = armPoint(i % ARMS, 1.2 + rnd() * 2.8, .3), s = glowSprite([0x5ee0be, 0x6c94ce, 0xccae76][i % 3], 1.8 + rnd() * 2.4, .025 + rnd() * .03); s.position.copy(p); outer.add(s); }
  cards = makeCards(strats.map((s, arm) => { const r = RMAX * .9, a = arm / ARMS * Math.PI * 2 + r * TW, o = new THREE.Object3D(); o.position.set(Math.cos(a) * r, .28 + (arm % 2) * .3, Math.sin(a) * r); outer.add(o); return o; }));
  labels = makeLabels(pairs.map((p, i) => { const o = new THREE.Object3D(); o.position.copy(stars.length ? stars[Math.floor(i * stars.length / pairs.length)] : armPoint(i, 2.5, .2)); outer.add(o);
    const s = glowSprite(0xe2cb98, .22, .8); s.position.copy(o.position); outer.add(s); return [p, o]; }));
  V.labelAlpha = p => Math.max(.2, Math.min(1, 1.6 - camera.position.distanceTo(p) / 10));
  V.update = (t, dt, sp, k) => {
    camera.position.set(Math.sin(t * .05) * 1.2, 10 - k * 4.3 + Math.sin(t * .09) * .12, 15.5 - k * 6.3); camera.lookAt(0, -.3, 0);
    inner.rotation.y += dt * .3 * sp; outer.rotation.y += dt * .11 * sp;
  };
  return V;
}

// ---------- scene switching ----------
const SCENES = { globe: sceneGlobe, table: sceneTable, galaxy: sceneGalaxy };
let sceneName = (() => { const q = (location.search.match(/[?&]scene=(\w+)/) || [])[1]; if (SCENES[q]) return q; try { const s = localStorage.getItem('grympha-home-scene'); if (SCENES[s]) return s; } catch (e) {} return 'globe'; })();
let t0 = performance.now();
function buildScene(name){
  sceneName = name; stage.dataset.scene = name;
  document.querySelectorAll('.scenes button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.scene === name)));
  if (!HAS3D || !D) return;
  if (scene) { scene.remove(camera); disposeScene(scene); }       // the camera (and its bokeh) is reused
  holo.textContent = ''; cards = []; labels = [];
  seed = 7;
  scene = new THREE.Scene(); scene.add(camera);
  tmp = new THREE.Vector3(); wp = new THREE.Vector3();
  V = SCENES[name]();
  scene.fog = new THREE.FogExp2(0x06110e, V.fog);
  addDust(V.dust);
  if (renderPass) renderPass.scene = scene;
  if (bloom) { bloom.radius = V.bloom[1]; bloom.threshold = V.bloom[2]; }
  $('cap-big').textContent = V.caption; $('cap-sm').textContent = V.sub();
  t0 = performance.now();
}
function switchScene(name){
  if (!SCENES[name] || name === sceneName) return;
  try { localStorage.setItem('grympha-home-scene', name); } catch (e) {}
  if (STILL || REDUCED) { buildScene(name); return; }
  stage.classList.add('switching');
  setTimeout(() => { buildScene(name); stage.classList.remove('switching'); }, 450);
}
document.querySelectorAll('.scenes button').forEach(b => b.addEventListener('click', () => switchScene(b.dataset.scene)));
addEventListener('keydown', e => { if (e.altKey || e.ctrlKey || e.metaKey) return; const n = { '1': 'globe', '2': 'table', '3': 'galaxy' }[e.key]; if (n) switchScene(n); if (e.key === 'Enter' && document.activeElement === document.body) location.href = '/'; });

// ---------- 2D panels (real data) ----------
const cv = {}; ['c-candle','c-tick','c-dist','c-heat','c-lead','rain'].forEach(id => cv[id] = $(id));
function sizeCanvases(){
  const pr = Math.min(3, Math.max(1, devicePixelRatio * scale));
  for (const id in cv) { const c = cv[id], w = id === 'rain' ? W : c.parentElement.clientWidth, h = id === 'rain' ? H : c.parentElement.clientHeight - 34;
    c.width = Math.round(w * pr); c.height = Math.round(h * pr); c._w = w; c._h = h; c.getContext('2d').setTransform(pr, 0, 0, pr, 0, 0); }
}
sizeCanvases();
function glow(g, color, blur){ g.shadowColor = color; g.shadowBlur = blur; }
function emptyNote(g, w, h, text){ g.shadowBlur = 0; g.fillStyle = C.muted; g.font = '11px JetBrains Mono, monospace'; g.textAlign = 'center'; g.fillText(text, w / 2, h / 2); g.textAlign = 'left'; }

function drawCandles(){
  const c = cv['c-candle'], g = c.getContext('2d'), w = c._w, h = c._h, pad = 52, K = D.candles || [];
  g.clearRect(0, 0, w, h);
  if (K.length < 2) { emptyNote(g, w, h, 'NO XAUUSD CHART YET'); return; }
  const hi = Math.max(...K.map(k => k.h)), lo = Math.min(...K.map(k => k.l)), Y = v => 10 + (hi - v) / ((hi - lo) || 1) * (h - 34);
  g.strokeStyle = C.line; g.lineWidth = 1; g.font = '10px JetBrains Mono, monospace'; g.fillStyle = C.muted; g.shadowBlur = 0;
  for (let i = 0; i <= 4; i++) { const v = lo + (hi - lo) * i / 4, y = Y(v); g.beginPath(); g.moveTo(10, y); g.lineTo(w - pad, y); g.stroke(); g.fillText(v.toFixed(1), w - pad + 4, y + 3); }
  const bw = (w - pad - 14) / K.length;
  let e = K[0].c; const ema = K.map(k => (e = e + (k.c - e) * 2 / 21));
  K.forEach((k, i) => { const x = 12 + i * bw + bw / 2, up = k.c >= k.o, col = up ? C.teal : C.red;
    glow(g, col, 8); g.strokeStyle = col; g.fillStyle = col; g.beginPath(); g.moveTo(x, Y(k.h)); g.lineTo(x, Y(k.l)); g.stroke();
    const y1 = Y(Math.max(k.o, k.c)), y2 = Y(Math.min(k.o, k.c)); g.globalAlpha = up ? .9 : .75; g.fillRect(x - bw * .32, y1, bw * .64, Math.max(1, y2 - y1)); g.globalAlpha = 1; });
  glow(g, C.gold, 10); g.strokeStyle = C.gold; g.lineWidth = 1.6; g.beginPath(); ema.forEach((v, i) => { const x = 12 + i * bw + bw / 2; i ? g.lineTo(x, Y(v)) : g.moveTo(x, Y(v)); }); g.stroke();
  const q = (D.quotes || []).find(x => x.symbol === 'XAUUSD'), last = q ? q.bid : K[K.length - 1].c, ly = Y(Math.min(hi, Math.max(lo, last)));
  glow(g, C.teal, 14); g.setLineDash([4, 4]); g.strokeStyle = 'rgba(94,224,190,.7)'; g.lineWidth = 1; g.beginPath(); g.moveTo(10, ly); g.lineTo(w - pad, ly); g.stroke(); g.setLineDash([]);
  g.fillStyle = C.teal; g.fillRect(w - pad, ly - 9, pad - 4, 18); g.shadowBlur = 0; g.fillStyle = '#04251e'; g.font = '600 10px JetBrains Mono, monospace'; g.fillText(last.toFixed(1), w - pad + 3, ly + 4);
  K.forEach((k, i) => { const x = 12 + i * bw, vh = 4 + Math.min(26, Math.abs(k.c - k.o) / ((hi - lo) || 1) * 160); g.fillStyle = k.c >= k.o ? 'rgba(94,224,190,.22)' : 'rgba(242,140,153,.22)'; g.fillRect(x + bw * .18, h - 4 - vh, bw * .64, vh); });
  $('px-xau').textContent = last.toFixed(2);
}
const TK_ORDER = ['XAUUSD','EURUSD','GBPUSD','USDJPY','XAGUSD','GBPJPY','AUDUSD','BTCUSD','USDCAD','USDCHF','NZDUSD','ETHUSD'];
let TK = [], flash = {};
function pickTickers(){
  const qs = D.quotes || [], by = {}; qs.forEach(q => by[q.symbol] = q);
  const prev = {}; TK.forEach(t => prev[t.symbol] = t.bid);
  TK = TK_ORDER.filter(s => by[s]).concat(qs.filter(q => !TK_ORDER.includes(q.symbol) && q.spark.length).map(q => q.symbol)).slice(0, 8).map(s => by[s]);
  TK.forEach(t => { if (prev[t.symbol] != null && prev[t.symbol] !== t.bid) flash[t.symbol] = { a: 1, dir: Math.sign(t.bid - prev[t.symbol]) }; });
}
function drawTickers(){
  const c = cv['c-tick'], g = c.getContext('2d'), w = c._w, h = c._h;
  g.clearRect(0, 0, w, h); g.font = '600 12px JetBrains Mono, monospace';
  if (!TK.length) { emptyNote(g, w, h, 'NO QUOTES YET'); return; }
  const rh = (h - 6) / TK.length;
  TK.forEach((t, i) => { const y = 3 + i * rh, mid = y + rh / 2 + 4, ch = t.change_pct, col = ch == null ? C.muted : ch >= 0 ? C.teal : C.red, f = flash[t.symbol];
    if (f && f.a > 0) { g.fillStyle = `rgba(${f.dir >= 0 ? '94,224,190' : '242,140,153'},${(f.a * .16).toFixed(3)})`; g.fillRect(8, y + 1, w - 16, rh - 2); f.a = Math.max(0, f.a - .02); }
    g.shadowBlur = 0; g.fillStyle = C.text; g.fillText(t.symbol, 14, mid);
    glow(g, col, 8); g.fillStyle = f && f.a > .5 ? '#fff' : C.text; g.textAlign = 'right'; g.fillText(fmtPx(t.bid, t.digits), w - 170, mid);
    g.fillStyle = col; g.fillText(ch == null ? '—' : (ch >= 0 ? '+' : '') + ch.toFixed(2) + '%', w - 108, mid); g.textAlign = 'left';
    const sp = t.spark || []; if (sp.length > 1) { const sx = w - 96, sw = 80, mn = Math.min(...sp), mx = Math.max(...sp); g.strokeStyle = col; g.lineWidth = 1.3; g.beginPath();
      sp.forEach((p, k) => { const x = sx + k / (sp.length - 1) * sw, yy = y + rh - 6 - (mx === mn ? .5 : (p - mn) / (mx - mn)) * (rh - 12); k ? g.lineTo(x, yy) : g.moveTo(x, yy); }); g.stroke(); }
    g.shadowBlur = 0; g.strokeStyle = 'rgba(94,224,190,.07)'; g.beginPath(); g.moveTo(10, y + rh); g.lineTo(w - 10, y + rh); g.stroke(); });
}
function drawDist(t){
  const c = cv['c-dist'], g = c.getContext('2d'), w = c._w, h = c._h, B = D.dist || [];
  g.clearRect(0, 0, w, h);
  const total = B.reduce((s, b) => s + b.n, 0);
  if (!total) { emptyNote(g, w, h, 'NO CLOSED LIVE TEST TRADES IN 30 DAYS'); return; }
  // square-root height so a big -1R bar does not flatten the rest; the printed counts stay exact
  const n = B.length, bw = (w - 40) / n, mx = Math.sqrt(Math.max(...B.map(b => b.n))) * 1.1;
  g.font = '10px JetBrains Mono, monospace'; g.textAlign = 'center';
  B.forEach((b, i) => { const v = b.n, bh = Math.max(v ? 2 : 0, Math.sqrt(v) / mx * (h - 38)) * (1 + Math.sin(t * 1.3 + i) * .015), x = 20 + i * bw, col = i < 2 ? C.red : i === 2 ? C.amber : C.teal;
    const gr = g.createLinearGradient(0, h - 20 - bh, 0, h - 20); gr.addColorStop(0, col); gr.addColorStop(1, 'rgba(0,0,0,0)');
    glow(g, col, 12); g.fillStyle = gr; g.fillRect(x + 5, h - 20 - bh, bw - 10, bh); g.fillStyle = col; g.fillRect(x + 5, h - 20 - bh, bw - 10, 2);
    g.shadowBlur = 0; g.fillStyle = C.muted; g.fillText(b.label, x + bw / 2, h - 6); g.fillStyle = C.text; g.fillText(v, x + bw / 2, h - 25 - bh); });
  g.textAlign = 'left';
}
function drawHeat(t){
  const c = cv['c-heat'], g = c.getContext('2d'), w = c._w, h = c._h, rows = D.heat || [], N = D.heat_days || 14;
  g.clearRect(0, 0, w, h);
  if (!rows.length) { emptyNote(g, w, h, 'NO LIVE TEST TRADES IN 14 DAYS'); return; }
  const lx = 108, cw = (w - lx - 14) / N, ch = (h - 26) / Math.max(rows.length, 5);
  const vals = rows.flatMap(r => r.cells).filter(v => v).map(Math.abs).sort((a, b) => a - b), top = Math.max(2, vals[Math.floor(vals.length * .8)] || 2);
  g.font = '10px JetBrains Mono, monospace';
  rows.forEach((r, i) => { g.shadowBlur = 0; g.fillStyle = C.muted; g.fillText(r.name.slice(0, 15), 10, 6 + i * ch + ch / 2 + 3);
    r.cells.forEach((v, j) => { const a = Math.min(1, Math.abs(v) / top), live = j === N - 1 ? (1 + Math.sin(t * 2 + i) * .08) : 1;
      const col = !v ? 'rgba(137,173,164,.06)' : v > 0 ? `rgba(94,224,190,${((.15 + a * .8) * live).toFixed(2)})` : `rgba(242,140,153,${((.15 + a * .8) * live).toFixed(2)})`;
      if (a > .6) glow(g, v >= 0 ? C.teal : C.red, 10); else g.shadowBlur = 0;
      g.fillStyle = col; g.fillRect(lx + j * cw + 1.5, 6 + i * ch + 1.5, cw - 3, ch - 3); }); });
  g.shadowBlur = 0; g.fillStyle = C.muted; g.textAlign = 'center';
  for (let j = 0; j < N - 2; j += 3) g.fillText('D-' + (N - 1 - j), lx + j * cw + cw / 2, h - 6);
  g.fillText('TODAY', lx + (N - 1) * cw + cw / 2, h - 6); g.textAlign = 'left';
  const x = lx + (N - 1) * cw + 1; glow(g, C.gold, 12); g.strokeStyle = C.gold; g.lineWidth = 1; g.strokeRect(x, 6, cw - 2, rows.length * ch - 1); g.shadowBlur = 0;
}
function drawLead(t){
  const c = cv['c-lead'], g = c.getContext('2d'), w = c._w, h = c._h, L = D.leaders || [];
  g.clearRect(0, 0, w, h);
  if (!L.length) { emptyNote(g, w, h, 'NO CLOSED LIVE TEST TRADES IN 30 DAYS'); return; }
  const rh = (h - 8) / Math.max(L.length, 6), mx = Math.max(1, ...L.map(x => Math.abs(x.net_r))), zx = 250;
  g.font = '11px JetBrains Mono, monospace';
  L.forEach((x, i) => { const y = 4 + i * rh, v = x.net_r, bw = Math.abs(v) / mx * (w - zx - 70) * (1 + Math.sin(t + i) * .01), col = v >= 0 ? C.teal : C.red;
    g.shadowBlur = 0; g.fillStyle = C.text; g.fillText(x.symbol, 12, y + rh / 2 + 4); g.fillStyle = C.muted; g.fillText(x.strategy_id.toLowerCase().slice(0, 21), 76, y + rh / 2 + 4);
    const gr = g.createLinearGradient(zx, 0, zx + bw, 0); gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, col);
    glow(g, col, 10); g.fillStyle = gr; g.fillRect(zx, y + rh * .28, bw, rh * .44); g.fillStyle = col; g.fillText(signR(v), zx + bw + 6, y + rh / 2 + 4); });
  g.shadowBlur = 0;
}
const feedEl = $('feed'); let feedSeen = null;
function renderFeed(){
  const F = D.feed || [];
  if (!F.length) { feedEl.innerHTML = '<div class="empty-note" style="display:block;border:0">NO SIGNAL AGENT ENTRIES YET</div>'; return; }
  const key = f => `${f.epoch}|${f.strategy_id}|${f.symbol}`, seen = feedSeen; feedSeen = new Set(F.map(key));
  feedEl.innerHTML = F.map(f => { const d = f.epoch ? new Date(f.epoch * 1000).toLocaleTimeString('en-GB', { timeZone: 'Asia/Kuala_Lumpur' }) : '—', gr = /^[ABCX]$/.test(f.grade) ? f.grade : 'C', side = f.side === 'SELL' ? 'SELL' : 'BUY';
    return `<div class="${seen && !seen.has(key(f)) ? 'new' : ''}"><span class="t">${d}</span><span class="g ${gr}">${gr}</span><span class="ad">${esc((f.strategy_id || '').toLowerCase())}/${esc(f.symbol)}</span><span class="s ${side}">${side}</span></div>`; }).join('');
}
function renderTape(){
  const qs = D.quotes || [];
  $('tape').innerHTML = qs.length ? (h => h + h)(qs.map(q => { const ch = q.change_pct;
    return `<span><b>${esc(q.symbol)}</b>${fmtPx(q.bid, q.digits)} ${ch == null ? '<i class="f">·</i>' : `<i class="${ch >= 0 ? 'u' : 'd'}">${ch >= 0 ? '▲' : '▼'} ${Math.abs(ch).toFixed(2)}%</i>`}</span>`; }).join('')) : '';
}
function applyData(){
  const a = D.account || {}, T = D.totals || {};
  $('k-eq').textContent = money(a.equity);
  const fl = $('k-fl'); fl.textContent = a.floating == null ? '—' : (a.floating >= 0 ? '+' : '') + money(a.floating); fl.className = a.floating > 0 ? 'up' : a.floating < 0 ? 'down' : '';
  $('k-ag').textContent = T.running == null ? '—' : `${T.running}`;
  const td = $('k-td'), tv = a.today_closed;                    // today's closed P/L on the MT5 account
  td.textContent = tv == null ? '—' : (tv >= 0 ? '+' : '') + money(tv); td.className = tv > 0 ? 'up' : tv < 0 ? 'down' : '';
  const desk = (D.desk || '').toUpperCase();                     // several desks on one PC: which one this is
  $('desk-line').textContent = desk ? `${desk} · COMMAND CENTER` : 'TRADING DESK · COMMAND CENTER';
  document.title = (D.desk ? D.desk + ' — ' : '') + 'Grympha Command Center';
  const mode = offline ? 'OFFLINE' : (a.mode || 'ANALYSIS'), m = $('mode');
  m.textContent = D.bot_online === false && !offline ? mode + ' · BOT OFF' : mode; m.className = 'badge ' + ({ REAL: 'REAL', DEMO: '', ANALYSIS: 'ANALYSIS' }[mode] ?? 'OFF');
  pickTickers(); renderFeed(); renderTape();
  if (HAS3D && !scene) buildScene(sceneName); else { refreshCards(); if (V) $('cap-sm').textContent = V.sub(); }
  updateAge();
}
function updateAge(){
  const el = $('foot-age');
  if (offline) { el.className = 'stale'; el.textContent = 'DESK NOT REACHABLE · SAMPLE LAYOUT'; return; }
  const s = Math.round((Date.now() - lastOk) / 1000); el.className = s > 60 ? 'stale' : '';
  el.textContent = `UPDATED ${s < 5 ? 'NOW' : s + 's AGO'} · ${(D.totals || {}).trades_30d ?? 0} LIVE TEST TRADES · 30D`;
}

// data rain (behind panels, faint) + data streams from panels into the centrepiece
const drops = Array.from({ length: 70 }, () => ({ x: rnd() * W, y: rnd() * H, v: 60 + rnd() * 160, len: 8 + Math.floor(rnd() * 16) }));
function drawRain(dt){
  const c = cv.rain, g = c.getContext('2d'); g.clearRect(0, 0, W, H); g.font = '12px JetBrains Mono, monospace';
  drops.forEach(d => { d.y += d.v * dt; if (d.y - d.len * 14 > H) { d.y = -20; d.x = rnd() * W; }
    for (let k = 0; k < d.len; k++) { const a = (1 - k / d.len) * .9; g.fillStyle = k === 0 ? `rgba(220,255,245,${a})` : `rgba(94,224,190,${(a * .6).toFixed(2)})`;
      g.fillText('0123456789ABCDEF'[Math.floor(rnd() * 16)], d.x, d.y - k * 14); } });
}
const svg = $('streams');
svg.innerHTML = '<defs><filter id="gl" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
  + '<linearGradient id="sg" x1="0" x2="1"><stop offset="0" stop-color="#5ee0be" stop-opacity=".1"/><stop offset=".5" stop-color="#5ee0be" stop-opacity=".9"/><stop offset="1" stop-color="#ccae76" stop-opacity=".2"/></linearGradient></defs>'
  + [[488,250],[488,520],[488,800],[1432,240],[1432,500],[1432,780]].map(([x, y], i) => { const cx = 960, cy = 497, mx = (x + cx) / 2, my = y + (cy - y) * .2 + (i % 2 ? -60 : 60);
    const d = `M${x},${y} Q${mx},${my} ${cx + (x < cx ? -200 : 200)},${cy + (y - cy) * .25}`;
    return `<path d="${d}" fill="none" stroke="rgba(94,224,190,.12)" stroke-width="1"/><path class="flow" d="${d}" fill="none" stroke="url(#sg)" stroke-width="2" stroke-linecap="round" stroke-dasharray="3 22 40 400" filter="url(#gl)"/>`; }).join('');
const flows = [...svg.querySelectorAll('.flow')];

function clock(){ $('clock').firstChild.nodeValue = new Date().toLocaleTimeString('en-GB', { timeZone: 'Asia/Kuala_Lumpur' }); }
setInterval(clock, 1000); clock();

// ---------- boot + cinematic camera ----------
const boot = $('boot');
const steps = ['CONNECTING TO THE DESK', 'READING LIVE TEST REGISTRY', 'LINKING AGENTS', 'ONLINE'];
steps.forEach((s, i) => setTimeout(() => { if (STILL) return; $('boot-t').textContent = s; $('boot-p').style.width = ((i + 1) / steps.length * 100) + '%'; }, i * 420));
if (STILL) { boot.remove(); stage.classList.remove('intro'); document.querySelectorAll('.letterbox').forEach(e => e.remove()); }
else { setTimeout(() => boot.classList.add('done'), 1750); setTimeout(() => stage.classList.remove('intro'), 2300); }

let last = performance.now(), frame = 0;
const ease = x => 1 - Math.pow(1 - x, 3);
function loop(now){
  const t = (now - t0) / 1000, dt = Math.min(.05, (now - last) / 1000); last = now; frame++;
  const sp = REDUCED ? .25 : 1;
  if (HAS3D && V) {
    const k = STILL || REDUCED ? 1 : ease(Math.min(1, t / 20));          // slow push-in over 20 s after each scene change
    V.update(t, dt, sp, k);
    for (let i = 0; i < PN; i++) { pp[i*3+1] += pv[i] * dt * sp; if (pp[i*3+1] > DY / 2) pp[i*3+1] = -DY / 2; }
    dustGeo.attributes.position.needsUpdate = true;
    if (bloom) bloom.strength = V.bloom[0] + Math.sin(t * .8) * .08;
    composer ? composer.render() : renderer.render(scene, camera);
    placeCards();
  }
  flows.forEach((f, i) => f.style.strokeDashoffset = String(-((now / 1000 * 140 * sp + i * 70) % 465)));
  if (D && frame % 2 === 0) { drawCandles(); drawTickers(); drawDist(t); drawHeat(t); drawLead(t); if (!REDUCED && !PHONE) drawRain(dt * 2); }
  if (frame % 60 === 0 && D) updateAge();
  requestAnimationFrame(loop);
}
window.__ready = true;
fetchHome().then(() => requestAnimationFrame(loop));
setInterval(fetchHome, 15000);
