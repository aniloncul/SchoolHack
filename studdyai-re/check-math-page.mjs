// Smoke test for the Mathematik-Agent page via CDP (read-only screenshots + DOM checks).
// usage: node check-math-page.mjs <outDir>
import fs from 'node:fs';

const OUT = process.argv[2] || '/tmp/math-shots';
fs.mkdirSync(OUT, { recursive: true });

const base = 'http://127.0.0.1:9222';
const version = await (await fetch(base + '/json/version')).json();
const ws = new WebSocket(version.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const events = [];

ws.addEventListener('message', (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
  } else if (msg.method) {
    events.push(msg);
  }
});

await new Promise((r) => ws.addEventListener('open', r, { once: true }));

function send(method, params = {}, sessionId) {
  const mid = ++id;
  const payload = { id: mid, method, params };
  if (sessionId) payload.sessionId = sessionId;
  ws.send(JSON.stringify(payload));
  return new Promise((resolve, reject) => pending.set(mid, { resolve, reject }));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });

await send('Page.enable', {}, sessionId);
await send('Runtime.enable', {}, sessionId);
await send('Emulation.setDeviceMetricsOverride',
  { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);

async function evaluate(expression) {
  const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
  if (res.exceptionDetails) throw new Error(res.exceptionDetails.text + ' ' + JSON.stringify(res.exceptionDetails.exception?.description || ''));
  return res.result.value;
}

async function shot(name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
  fs.writeFileSync(`${OUT}/${name}.png`, Buffer.from(data, 'base64'));
}

// 1) login
await send('Page.navigate', { url: 'http://127.0.0.1:8000/login/student' }, sessionId);
await sleep(900);
await evaluate(`(() => {
  const f = document.querySelector('form');
  f.querySelector('input[name=username]').value = 'admin';
  f.querySelector('input[name=password]').value = 'admin';
  f.submit();
  return true;
})()`);
await sleep(1200);

// 2) open the page
await send('Page.navigate', { url: 'http://127.0.0.1:8000/student/mathematik' }, sessionId);
await sleep(1200);
console.log('title:', await evaluate('document.title'));
console.log('nav:', await evaluate(`[...document.querySelectorAll('header nav a')].map(a => a.textContent.trim()).join(' | ')`));
console.log('active:', await evaluate(`(() => { const a = document.querySelector('header nav a.is-active'); return a ? a.textContent.trim() : null; })()`));
console.log('katex:', await evaluate('typeof window.katex'));
await shot('01-empty');

// 3) ask a question through the UI
await evaluate(`(() => {
  const el = document.getElementById('math-input');
  el.value = '2x^2+3x+7 = 4x^2-5x-3';
  document.getElementById('math-form').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  return true;
})()`);
await sleep(6500);
await shot('02-lesson');

const state = await evaluate(`(() => {
  const els = [...document.querySelectorAll('.board-el')];
  return {
    count: els.length,
    latex: els.filter(e => e.classList.contains('el-latex')).map(e => e.textContent.slice(0, 40)),
    svg: document.querySelectorAll('.el-draw svg').length,
    spaces: [...document.querySelectorAll('.board-space')].map(s => ({
      label: s.querySelector('.board-space-label').textContent,
      w: Math.round(s.getBoundingClientRect().width)
    })),
    messages: [...document.querySelectorAll('.msg.tutor .bubble')].map(b => b.textContent.slice(0, 60)),
    katexErrors: document.querySelectorAll('.katex-error').length,
    zoom: document.getElementById('zoom-label').textContent
  };
})()`);
console.log(JSON.stringify(state, null, 2));

// 4) SVG content sanity
const svgBox = await evaluate(`(() => {
  const svg = document.querySelector('.el-draw svg');
  if (!svg) return null;
  return { w: svg.getAttribute('width'), h: svg.getAttribute('height'), paths: svg.querySelectorAll('polyline').length,
           points: svg.querySelectorAll('circle').length, texts: svg.querySelectorAll('text').length };
})()`);
console.log('svg:', JSON.stringify(svgBox));

// 5) zoom + follow interactions
await evaluate(`document.getElementById('zoom-in').click()`);
await sleep(300);
console.log('zoom after +:', await evaluate('document.getElementById("zoom-label").textContent'));

// 6) second question (inequality) to test re-layout
await evaluate(`(() => {
  const el = document.getElementById('math-input');
  el.value = '-0.1x^2+3x-6 > -0.2x^2+5x, 0<=x<=24';
  document.getElementById('math-form').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  return true;
})()`);
await sleep(7000);
await shot('03-second');

console.log('spaces2:', await evaluate(`[...document.querySelectorAll('.board-space')].map(s => s.querySelector('.board-space-label').textContent).join(',')`));

// 7) third question: parametric (a) — best case for the layout engine
await evaluate(`(() => {
  const el = document.getElementById('math-input');
  el.value = 'f(x)=-4x^2+15ax ; g(x)=2x^2-9ax+18a^2';
  document.getElementById('math-form').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  return true;
})()`);
await sleep(8000);
await shot('04-parametric');
console.log('parametric:', await evaluate(`(() => {
  const els = [...document.querySelectorAll('.board-el')];
  return { count: els.length, overflow: els.filter(e => e.scrollWidth > e.clientWidth + 1).length,
           texts: els.filter(e => e.classList.contains('el-latex')).map(e => e.textContent.slice(0, 34)) };
})()`));

// 7b) "show the whole board" button must frame every element
await evaluate(`document.getElementById('zoom-reset').click()`);
await sleep(800);
await shot('04b-parametric-fit');
console.log('fit:', await evaluate(`(() => {
  const vp = document.getElementById('board-viewport').getBoundingClientRect();
  const boxes = [...document.querySelectorAll('.board-el')].map(e => e.getBoundingClientRect());
  return { zoom: document.getElementById('zoom-label').textContent,
           outside: boxes.filter(b => b.left < vp.left - 1 || b.right > vp.right + 1 ||
                                      b.top < vp.top - 1 || b.bottom > vp.bottom + 1).length };
})()`));

// 8) fourth question: broken input must warn, never invent an answer
await evaluate(`(() => {
  const el = document.getElementById('math-input');
  el.value = '2x^^2 +';
  document.getElementById('math-form').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  return true;
})()`);
await sleep(3500);
await shot('05-broken');
console.log('broken:', await evaluate(`(() => {
  const warn = document.querySelector('.msg.tutor.is-warning');
  return { warning: !!warn, text: warn ? warn.querySelector('.bubble').textContent.slice(0, 90) : null,
           meta: warn ? (warn.querySelector('.msg-meta') || {}).textContent : null,
           boardElements: document.querySelectorAll('.board-el').length };
})()`));

// 9) mobile viewport
await send('Emulation.setDeviceMetricsOverride',
  { width: 414, height: 860, deviceScaleFactor: 2, mobile: true }, sessionId);
await sleep(800);
await shot('06-mobile');

// 8) console errors
const errors = events.filter((e) => e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error');
console.log('console errors:', errors.length);
for (const e of errors.slice(0, 5)) console.log('  ', e.params.args.map((a) => a.value || a.description).join(' '));

await send('Target.closeTarget', { targetId });
ws.close();
