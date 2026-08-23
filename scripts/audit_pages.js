// Run axe-core against a set of built pages in a real browser, and measure what each one costs to load.
//
//     echo '<job json>' | node scripts/audit_pages.js
//
// Driven by scripts/audit_a11y.py, which decides which pages to sample and what to do with the verdict.
//
// Why this file exists rather than `npm i puppeteer`. Two reasons, and the second is the real one.
// This repository has no dependency tree — Python and a bare `node` — and a build gate should not be the
// thing that introduces one. And an accessibility engine needs a browser with layout: without one, the
// contrast rule cannot run at all, which is the rule that matters most to the readers this gate exists for.
// A DOM shim would pass a page whose grey-on-grey nobody can read. So: the system Chrome, spoken to over the
// DevTools protocol, through the WebSocket that Node has had built in since 22. About a hundred lines, no
// install, and the same engine Lighthouse uses.
//
// The performance half comes from the same session rather than a second tool. For a site that ships no
// framework and no third-party anything, a synthetic score would compress the only four numbers worth
// knowing — bytes over the wire, requests, DOM nodes, and whether anything blocks the first paint — into one
// that hides which of them moved. They are reported separately, and the caller sets a budget per page kind.

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const read = (s) => new Promise((r) => { let b = ''; s.on('data', (c) => (b += c)); s.on('end', () => r(b)); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function http(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

// A CDP session over one WebSocket: send a command, await its reply by id; events go to listeners.
class Session {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.listeners = [];
    ws.addEventListener('message', (e) => {
      const m = JSON.parse(e.data);
      if (m.id !== undefined) {
        const p = this.pending.get(m.id);
        if (p) {
          this.pending.delete(m.id);
          m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
        }
      } else {
        for (const fn of this.listeners) fn(m);
      }
    });
  }
  // Retried, because a page's socket sometimes is not accepting yet — and because a machine with other
  // browsers on it will lose one occasionally. A single attempt turned that into "the audit could not run",
  // which is indistinguishable from a real failure and is the sort of thing that gets a gate switched off.
  static async open(url, tries = 4) {
    for (let i = 1; ; i++) {
      const ws = new WebSocket(url);
      try {
        await new Promise((res, rej) => {
          ws.addEventListener('open', res, { once: true });
          ws.addEventListener('error', () => rej(new Error(`cannot open ${url}`)), { once: true });
        });
        return new Session(ws);
      } catch (e) {
        try { ws.close(); } catch { /* never opened */ }
        if (i >= tries) throw e;
        await sleep(300 * i);
      }
    }
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(fn) { this.listeners.push(fn); }
  close() { try { this.ws.close(); } catch { /* already gone */ } }
}

async function auditOne(session, page, axeSource, opts) {
  // A page can be listed for weight alone. The largest archive index is 217,364 elements and axe needs 189
  // seconds and a raised heap to finish it — for a verdict identical to the smaller archive index beside it,
  // because they are the same template with more rows. So the rules run on the representative one and the
  // worst case is still weighed, which is the half that actually differs.
  const net = { requests: 0, bytes: 0, failed: [] };
  let loaded = false;
  session.on((m) => {
    if (m.method === 'Network.requestWillBeSent') net.requests += 1;
    else if (m.method === 'Network.loadingFinished') net.bytes += m.params.encodedDataLength || 0;
    else if (m.method === 'Network.loadingFailed') net.failed.push(m.params.errorText || 'failed');
    else if (m.method === 'Page.loadEventFired') loaded = true;
  });
  await session.send('Network.enable');
  await session.send('Page.enable');
  // Every page's script errors, not just the one page the motion check looks at. The cycle indexes have been
  // throwing a TypeError on load — the SQL console's setup keyed off #q, which a cycle index also has — and
  // the two script blocks after it never ran on ten pages. The gate already treats a thrown exception as a
  // hard failure; it simply was not listening anywhere but on page one.
  await session.send('Runtime.enable');
  const errors = [];
  session.on((m) => {
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails || {};
      errors.push((d.exception && d.exception.description) || d.text || 'exception');
    }
  });
  // The bytes were a cache hit. /json/new?<url> navigates the tab before this session exists, so the first
  // load's events are gone, and the navigate below was served from memory with encodedDataLength ≈ 0 — a
  // 1.5 MB page recorded as 702 bytes, and a budget that could only fire on payloads too big to cache.
  await session.send('Network.setCacheDisabled', { cacheDisabled: true });
  await session.send('Emulation.setDeviceMetricsOverride',
                     { width: opts.width, height: opts.height, deviceScaleFactor: 1, mobile: opts.mobile });
  // Audit the page a reduced-motion reader gets, and the one everybody else ends up with.
  //
  // The boot script fades panels in from opacity 0 and bails out under prefers-reduced-motion, which is
  // correct. Measuring without this line caught the terminal at about 9% opacity and reported #181819 on
  // #040404 — a contrast of 1.15 on text that is 7.95 once it has arrived. Four phantom serious violations
  // on every page, and they came and went with how many pages the batch held, because a slower batch left
  // less idle time per page. A gate that fails on its own timing teaches people to ignore it.
  await session.send('Emulation.setEmulatedMedia',
                     { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await session.send('Page.navigate', { url: page.url });
  for (let i = 0; i < 300 && !loaded; i++) await sleep(50);   // 15 s, then audit whatever is there
  await sleep(250);                                            // let deferred script settle
  // and belt and braces: nothing mid-transition, whatever the media query did
  await session.send('Runtime.evaluate', {
    expression: `(async () => { const a = document.getAnimations ? document.getAnimations() : [];
                 a.forEach(x => { try { x.finish(); } catch {} }); })()`,
    awaitPromise: true,
  });

  // THE CONTRAST RULE COULD NOT SEE ANYTHING, AND SAID NOTHING ABOUT IT.
  //
  // `.kz-main` carries a one-pixel dot grid as a background-image, and it wraps all page content. axe will
  // not judge contrast over a gradient it cannot resolve — it files the node under `incomplete` with the
  // reason "background color could not be determined due to a background gradient" and moves on. 143 of 153
  // text nodes on a typical page, and `judge()` read only `violations`. So the one rule this whole harness
  // drives a real browser for had never returned a verdict on this site: text at a contrast of 1.95 passed.
  //
  // Turning the decorative image off is safe and exact here — the stylesheet declares exactly one
  // background-image, that dot grid at 5% white, and no built page carries an inline one. With it off the
  // rule measures the real ground, and the real ground is fine: zero contrast violations everywhere.
  await session.send('Runtime.evaluate', {
    expression: `(() => { const s = document.createElement('style');
      s.textContent = '*{background-image:none !important}'; document.head.appendChild(s); })()`,
  });
  if (page.weightOnly) {
    const facts0 = await session.send('Runtime.evaluate', {
      expression: `JSON.stringify({ nodes: document.getElementsByTagName('*').length, title: document.title,
        lang: document.documentElement.lang || null, blocking: 0,
        scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth })`,
      returnByValue: true,
    });
    return { kind: page.kind, path: page.path, ...JSON.parse(facts0.result.value), net, errors,
             weightOnly: true, violations: [], incomplete: [], contrastPasses: null, axeVersion: null };
  }
  await session.send('Runtime.evaluate', { expression: axeSource, returnByValue: false });
  const run = await session.send('Runtime.evaluate', {
    // `passes` is collected so the gate can prove the rule ran. A stubbed axe, a failed injection or a page
    // that seizes window.axe all return an empty `violations`, which is indistinguishable from a clean page.
    // A floor on the number of nodes colour-contrast actually judged is not.
    // The three rules added by id are shipped disabled or deprecated in 4.13 and catch real defects: a data
    // table whose header row is <td> reads as an undifferentiated grid, and a duplicate id on an anchor
    // breaks fragment navigation and aria-labelledby. Both passed the gate before this line.
    expression: `axe.run(document, {
        resultTypes: ['violations', 'incomplete', 'passes'],
        runOnly: { type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','best-practice'] },
        rules: { 'td-has-header': {enabled:true}, 'duplicate-id': {enabled:true}, 'duplicate-id-active': {enabled:true} }
      }).then(r => JSON.stringify({
        axeVersion: axe.version,
        contrastPasses: (r.passes.find(x => x.id === 'color-contrast') || {nodes: []}).nodes.length,
        violations: r.violations.map(v => ({
          id: v.id, impact: v.impact, help: v.help, count: v.nodes.length,
          nodes: v.nodes.slice(0, 4).map(n => ({ target: n.target.join(' '), summary: (n.failureSummary||'').slice(0, 300) }))
        })),
        incomplete: r.incomplete.map(v => ({ id: v.id, impact: v.impact, count: v.nodes.length }))
      }))`,
    awaitPromise: true, returnByValue: true, timeout: 120000,
  });
  const facts = await session.send('Runtime.evaluate', {
    expression: `JSON.stringify({
        nodes: document.getElementsByTagName('*').length,
        title: document.title,
        lang: document.documentElement.lang || null,
        blocking: [...document.querySelectorAll('script[src]:not([defer]):not([async]), link[rel=stylesheet]')].length,
        scrollW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth
      })`, returnByValue: true,
  });
  if (typeof run.result.value !== 'string') {
    throw new Error('axe returned nothing evaluable — it did not run');
  }
  const axeOut = JSON.parse(run.result.value);
  return { kind: page.kind, path: page.path, ...JSON.parse(facts.result.value), net, errors, ...axeOut };
}

// Auditing under reduced motion means the fade-in is never exercised, and a boot script that throws halfway
// would leave every panel at opacity 0 — a blank page that passes every rule, because there is nothing on it
// to fail. So one page is loaded the way most readers get it, and checked for having arrived.
async function motionCheck(session, page) {
  let loaded = false;
  session.on((m) => { if (m.method === 'Page.loadEventFired') loaded = true; });
  await session.send('Page.enable');
  await session.send('Runtime.enable');
  const errors = [];
  session.on((m) => {
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(m.params.exceptionDetails?.exception?.description
                  || m.params.exceptionDetails?.text || 'exception');
    }
  });
  await session.send('Emulation.setEmulatedMedia', { features: [] });   // no reduced-motion: let it animate
  await session.send('Page.navigate', { url: page.url });
  for (let i = 0; i < 200 && !loaded; i++) await sleep(50);
  await sleep(3000);                                                    // the longest stagger is well under this
  const r = await session.send('Runtime.evaluate', {
    expression: `JSON.stringify((() => {
        const els = [...document.querySelectorAll('.panel, .counts .c, .fresh, .kz-terminal')];
        const faded = els.filter(e => parseFloat(getComputedStyle(e).opacity) < 0.99);
        return { total: els.length, faded: faded.length,
                 worst: faded.slice(0, 3).map(e => (e.className || e.tagName) + ' @ ' + getComputedStyle(e).opacity) };
      })())`, returnByValue: true,
  });
  return { kind: page.kind, path: page.path, motion: JSON.parse(r.result.value), errors };
}

(async () => {
  const job = JSON.parse(await read(process.stdin));
  const axeSource = fs.readFileSync(job.axe, 'utf8');
  const port = 9200 + (process.pid % 700);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'karzat-audit-'));
  const chrome = spawn(job.chrome, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--hide-scrollbars', '--force-color-profile=srgb',
    '--allow-file-access-from-files', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: 'ignore' });

  const out = { pages: [], error: null };
  try {
    let version = null;
    for (let i = 0; i < 100 && !version; i++) {           // Chrome needs a moment to open the port
      try { version = await http(`http://127.0.0.1:${port}/json/version`); } catch { await sleep(100); }
    }
    if (!version) throw new Error('Chrome did not open its debugging port');
    out.browser = version.Browser;

    const work = job.pages.map((p) => ({ page: p, fn: auditOne }));
    if (job.motionCheck) work.push({ page: job.pages[0], fn: motionCheck, motion: true });
    for (const { page, fn, motion } of work) {
      let target = null;
      for (let i = 1; i <= 4 && !target; i++) {
        target = await http(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(page.url)}`)
          .catch(async () => {                            // newer Chrome wants PUT for /json/new
            const r = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(page.url)}`, { method: 'PUT' });
            return r.ok ? r.json() : null;
          })
          .catch(() => null);
        if (!target) await sleep(400 * i);
      }
      if (!target) {
        out.pages.push({ kind: page.kind, path: page.path, error: 'Chrome would not open a tab for this page' });
        continue;
      }
      const session = await Session.open(target.webSocketDebuggerUrl);
      const sink = motion ? (out.motion = {}) : null;
      try {
        const r = await fn(session, page, axeSource, job.viewport);
        if (motion) Object.assign(sink, r); else out.pages.push(r);
      } catch (e) {
        const err = { kind: page.kind, path: page.path, error: String(e && e.message || e) };
        if (motion) Object.assign(sink, err); else out.pages.push(err);
      } finally {
        session.close();
        await fetch(`http://127.0.0.1:${port}/json/close/${target.id}`).catch(() => {});
      }
    }
  } catch (e) {
    out.error = String(e && e.message || e);
  } finally {
    chrome.kill('SIGTERM');
    await sleep(200);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* the OS will */ }
  }
  // Write, then let the process end on its own. `process.exit()` straight after a write is the classic way
  // to lose it: stdout to a pipe is asynchronous, so exiting can truncate or drop the payload entirely. It
  // did — the desktop pass came back and the mobile one reported "the driver printed nothing", every time,
  // which reads exactly like a browser that failed rather than a report that was thrown away on the way out.
  process.exitCode = out.error ? 1 : 0;
  await new Promise((r) => process.stdout.write(JSON.stringify(out), r));
})();
