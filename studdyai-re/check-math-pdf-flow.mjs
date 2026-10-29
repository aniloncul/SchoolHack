// End-to-end check of the Mathematik-Agent: PDF upload -> tasks -> tutor turns.
// usage: node check-math-pdf-flow.mjs <outDir> [pdf]
import fs from 'node:fs';

const OUT = process.argv[2] || '/tmp/math-flow';
const PDF = process.argv[3] || '/tmp/math-homework.pdf';
const TASK_INDEX = Number(process.argv[4] || 0);
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
await send('DOM.enable', {}, sessionId);
await send('Network.enable', {}, sessionId);
await send('Emulation.setDeviceMetricsOverride',
  { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);

async function evaluate(expression) {
  const res = await send('Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true }, sessionId);
  if (res.exceptionDetails) {
    throw new Error(res.exceptionDetails.text + ' ' +
      JSON.stringify(res.exceptionDetails.exception?.description || ''));
  }
  return res.result.value;
}
async function shot(name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
  fs.writeFileSync(`${OUT}/${name}.png`, Buffer.from(data, 'base64'));
}
async function setFile(selector, file) {
  const doc = await send('DOM.getDocument', { depth: -1 }, sessionId);
  const { nodeId } = await send('DOM.querySelector',
    { nodeId: doc.root.nodeId, selector }, sessionId);
  if (!nodeId) throw new Error('input not found: ' + selector);
  await send('DOM.setFileInputFiles', { nodeId, files: [file] }, sessionId);
}
async function waitFor(expression, label, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await evaluate(expression);
    if (v) return v;
    await sleep(400);
  }
  throw new Error('timeout waiting for ' + label);
}

const state = () => evaluate(`(() => {
  const els = [...document.querySelectorAll('.board-el')];
  const asks = [...document.querySelectorAll('.q-choices')];
  return {
    start: !document.getElementById('start-screen').hidden,
    tasks: [...document.querySelectorAll('.task-card')].map(c => ({
      label: c.querySelector('.task-label').textContent,
      title: c.querySelector('.task-title').textContent,
      expr: c.querySelector('.task-expr').textContent.slice(0, 80)
    })),
    status: document.getElementById('start-status').textContent,
    board: els.length,
    latex: els.filter(e => e.classList.contains('el-latex')).map(e => e.textContent.trim().slice(0, 42)),
    svg: document.querySelectorAll('.el-draw svg').length,
    katexErrors: document.querySelectorAll('.katex-error').length,
    bubbles: [...document.querySelectorAll('.msg.tutor .bubble')].map(b => b.textContent.trim().slice(0, 90)),
    replies: [...document.querySelectorAll('.msg.user .bubble')].map(b => b.textContent.trim()),
    choices: asks.length ? [...asks[asks.length - 1].querySelectorAll('button')].map(b => b.textContent) : [],
    typing: document.querySelectorAll('#typing').length,
    caption: document.getElementById('board-caption').textContent,
    actions: [...document.querySelectorAll('.lesson-actions button')].map(b => b.textContent),
    warning: [...document.querySelectorAll('.msg.tutor.is-warning')].map(w => w.querySelector('.bubble').textContent.slice(0, 120))
  };
})()`);

await send('Page.navigate', { url: 'http://127.0.0.1:8000/login/student' }, sessionId);
await sleep(900);
await evaluate(`(() => {
  const f = document.querySelector('form');
  f.querySelector('input[name=username]').value = 'admin';
  f.querySelector('input[name=password]').value = 'admin';
  f.submit();
  return true;
})()`);
await sleep(1400);

await send('Page.navigate', { url: 'http://127.0.0.1:8000/student/mathematik' }, sessionId);
await sleep(1400);
console.log('title:', await evaluate('document.title'));
console.log('nav:', await evaluate(
  `[...document.querySelectorAll('header nav a')].map(a => a.textContent.trim()).join(' | ')`));
await shot('01-start');
console.log('start state:', JSON.stringify(await state(), null, 2));

// --- upload ---------------------------------------------------------------
await setFile('#pdf-input', PDF);
await waitFor(`document.querySelectorAll('.task-card').length > 0`, 'task cards');
await sleep(500);
await shot('02-tasks');
const afterUpload = await state();
console.log('after upload:', JSON.stringify(afterUpload, null, 2));

// --- start task 1 ---------------------------------------------------------
await evaluate(`document.querySelectorAll('.task-start')[${TASK_INDEX}].click()`);
await waitFor(`!document.getElementById('start-screen').hidden ? false : (document.querySelectorAll('.msg.tutor .bubble').length > 0 && document.querySelectorAll('#typing').length === 0)`, 'first turn', 90000);
await sleep(1200);
await shot('03-turn1');
const turn1 = await state();
console.log('turn1:', JSON.stringify(turn1, null, 2));

// --- answer up to 6 questions --------------------------------------------
const dbg = () => evaluate('window.__mathState()');
const typeAnswer = (value) => evaluate(`(() => {
  const el = document.getElementById('math-input');
  el.value = ${JSON.stringify(value)};
  document.getElementById('math-form').dispatchEvent(
    new Event('submit', { cancelable: true, bubbles: true }));
  return true;
})()`);
let guard = 0;
while (guard++ < 40) {
  await waitFor(`(() => { const s = window.__mathState();
    return !s.busy && (s.pending !== null || s.done ||
      document.querySelectorAll('.lesson-actions button').length > 0); })()`, 'idle turn');
  const s = await dbg();
  if (s.done) { console.log('done at turn', s.turn); break; }

  if (!s.pending) {
    const bubblesBefore = await evaluate('document.querySelectorAll(".msg .bubble").length');
    console.log('continue: click the lesson button');
    await evaluate(`document.querySelector('.lesson-actions button').click()`);
    await sleep(900);
    const bubblesAfter = await evaluate('document.querySelectorAll(".msg .bubble").length');
    if (bubblesAfter === bubblesBefore) {
      const again = await evaluate(`document.querySelectorAll('.lesson-actions button').length`);
      if (!again) break;
    }
    continue;
  }

  for (let attempt = 0; attempt < 3; attempt++) {
    const clicked = await evaluate(`(() => {
      const rows = [...document.querySelectorAll('.q-choices')];
      for (let i = rows.length - 1; i >= 0; i--) {
        const btn = [...rows[i].querySelectorAll('button')].find(b => !b.disabled);
        if (btn) { btn.click(); return btn.textContent; }
      }
      return null;
    })()`);
    if (clicked) console.log(`answer: click "${clicked}"`);
    else { console.log('answer: type "1"'); await typeAnswer('1'); }
    await sleep(900);
    const after = await dbg();
    if (after.pending === null || after.done) break;
  }
  await waitFor(`!window.__mathState().busy`, 'turn idle', 90000);
  await sleep(700);
  const st = await state();
  console.log('turn', st.bubbles.length, '| board', st.board, '| svg', st.svg,
    '| choices', st.choices.length, '| actions', st.actions.length, '| warn', st.warning.length);
}
await shot('04-after-answers');

const final = await state();
console.log('final:', JSON.stringify(final, null, 2));

// --- board must not overflow the viewport after "fit" --------------------
await evaluate(`document.getElementById('zoom-reset').click()`);
await sleep(700);
await shot('05-fit');
console.log('fit:', await evaluate(`(() => {
  const vp = document.getElementById('board-viewport').getBoundingClientRect();
  const boxes = [...document.querySelectorAll('.board-el')].map(e => e.getBoundingClientRect());
  return { zoom: document.getElementById('zoom-label').textContent,
           outside: boxes.filter(b => b.left < vp.left - 1 || b.right > vp.right + 1 ||
                                      b.top < vp.top - 1 || b.bottom > vp.bottom + 1).length };
})()`));

// --- mobile --------------------------------------------------------------
await send('Emulation.setDeviceMetricsOverride',
  { width: 414, height: 860, deviceScaleFactor: 2, mobile: true }, sessionId);
await sleep(900);
await shot('06-mobile');
const mobile = await evaluate(`(() => {
  const vp = document.getElementById('board-viewport').getBoundingClientRect();
  const log = document.getElementById('chat-log').getBoundingClientRect();
  return { vp: [Math.round(vp.width), Math.round(vp.height)],
           log: [Math.round(log.width), Math.round(log.top)],
           overflowX: document.documentElement.scrollWidth > window.innerWidth + 1 };
})()`);
console.log('mobile:', JSON.stringify(mobile));

const errors = events.filter((e) => e.method === 'Runtime.consoleAPICalled' &&
  e.params.type === 'error');
const exceptions = events.filter((e) => e.method === 'Runtime.exceptionThrown');
console.log('console errors:', errors.length, 'exceptions:', exceptions.length);
for (const e of errors.slice(0, 6)) {
  console.log('  console:', e.params.args.map((a) => a.value || a.description).join(' '));
}
for (const e of exceptions.slice(0, 6)) {
  console.log('  exc:', e.params.exceptionDetails.text,
    e.params.exceptionDetails.exception?.description?.split('\n')[0] || '');
}

await send('Target.closeTarget', { targetId });
ws.close();
