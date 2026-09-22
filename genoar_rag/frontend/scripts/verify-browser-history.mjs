/**
 * Browser-level checks that the jest suite cannot make.
 *
 * The unit tests can show that the search hook calls push rather than replace,
 * but not that Back actually restores the previous search: that depends on the
 * router's real history behaviour. Likewise, the series row highlight depends on
 * which id reaches the table after the page resolves an accession.
 *
 * Driven over the Chrome DevTools Protocol so the repo needs no browser
 * automation dependency.
 *
 * Prerequisites: the frontend and backend running, and a Chrome or Chromium
 * listening for CDP. Start the browser with whichever binary the machine has:
 *
 *   # macOS
 *   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
 *     --headless=new --remote-debugging-port=9222 \
 *     --user-data-dir=/tmp/genoar-verify-profile about:blank &
 *   # Linux
 *   chromium --headless=new --remote-debugging-port=9222 \
 *     --user-data-dir=/tmp/genoar-verify-profile about:blank &
 *
 *   npm run verify:browser        # or: node scripts/verify-browser-history.mjs
 *
 * Environment overrides, for a stack on other ports or a different corpus:
 *   CDP_URL / CDP_PORT            default http://localhost:9222
 *   APP_URL / FRONTEND_PORT       default http://localhost:3005
 *   VERIFY_RUN_ID                 default SRR10753462
 *   VERIFY_GEO_ACCESSION          default GSM4230367
 *
 * The defaults are real corpus samples: GSM4230367 resolves to SRR10753462 in
 * GSE142489, which has sibling runs, so the series table has rows to highlight.
 * Any replacement pair must be the same sample and belong to a series.
 *
 * Exits non-zero if any check fails.
 */

// Ports, host and fixtures are configurable so this can run against a stack on
// other ports or a corpus with different samples.
const CDP = process.env.CDP_URL ?? `http://localhost:${process.env.CDP_PORT ?? 9222}`;
const APP = process.env.APP_URL ?? `http://localhost:${process.env.FRONTEND_PORT ?? 3005}`;
// A sample that belongs to a series, given as its run id and its GEO accession.
const RUN_ID = process.env.VERIFY_RUN_ID ?? "SRR10753462";
const GEO_ACCESSION = process.env.VERIFY_GEO_ACCESSION ?? "GSM4230367";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function firstPageTarget() {
  for (let i = 0; i < 30; i++) {
    try {
      const targets = await (await fetch(`${CDP}/json`)).json();
      const page = targets.find((t) => t.type === "page");
      if (page) return page;
    } catch {
      /* browser still starting */
    }
    await sleep(500);
  }
  throw new Error("no CDP page target");
}

class Session {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", (e) => {
      const msg = JSON.parse(e.data);
      const resolve = this.pending.get(msg.id);
      if (resolve) {
        this.pending.delete(msg.id);
        resolve(msg);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve) => this.pending.set(id, resolve));
  }
  async evaluate(expression) {
    const r = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (r.result?.exceptionDetails) {
      throw new Error(r.result.exceptionDetails.text ?? "evaluate failed");
    }
    return r.result?.result?.value;
  }
  async goto(url) {
    await this.send("Page.navigate", { url });
    // Wait for the client-side app to finish its first data fetch.
    for (let i = 0; i < 60; i++) {
      await sleep(500);
      const ready = await this.evaluate("document.readyState === 'complete'");
      if (ready) break;
    }
    await sleep(2500);
  }
  /** Poll until `expr` is truthy, so we never assert on a half-updated page. */
  async waitFor(expr, label, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.evaluate(expr)) return true;
      await sleep(300);
    }
    throw new Error(`timed out waiting for: ${label}`);
  }
}

async function connect() {
  const target = await firstPageTarget();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true });
    ws.addEventListener("error", rej, { once: true });
  });
  const s = new Session(ws);
  await s.send("Page.enable");
  await s.send("Runtime.enable");
  return s;
}

const results = [];
function record(name, passed, detail) {
  results.push({ name, passed, detail });
  console.log(`${passed ? "PASS" : "FAIL"}  ${name}`);
  if (detail) console.log(`      ${detail}`);
}

const s = await connect();

// ---------------------------------------------------------------------------
// 1. Browser Back restores the previous search
// ---------------------------------------------------------------------------
await s.goto(`${APP}/search?search_mode=semantic&organism=Homo+sapiens`);
await s.waitFor('!!document.querySelector(\'[data-testid="species-mouse"]\')', "species toggle");

const before = await s.evaluate("location.search");
const historyBefore = await s.evaluate("history.length");

await s.evaluate('document.querySelector(\'[data-testid="species-mouse"]\').click()');
await s.waitFor("location.search.includes('Mus')", "URL to switch to mouse");

const afterClick = await s.evaluate("location.search");
const historyAfter = await s.evaluate("history.length");

record(
  "species toggle adds a history entry",
  historyAfter > historyBefore,
  `history.length ${historyBefore} -> ${historyAfter}`,
);

await s.evaluate("history.back()");
await s.waitFor("location.search.includes('Homo')", "URL to return to human");
const afterBack = await s.evaluate("location.search");

record(
  "Back restores the previous search",
  afterBack.includes("Homo+sapiens") || afterBack.includes("Homo%20sapiens"),
  `${before} -> ${afterClick} -> back -> ${afterBack}`,
);

// The species button state has to follow the restored URL, not just the address bar.
const speciesActive = await s.evaluate(`
  (() => {
    const b = document.querySelector('[data-testid="species-human"]');
    return b ? b.getAttribute('aria-pressed') : null;
  })()
`);
record("restored state reaches the UI", speciesActive === "true", `species-human aria-pressed=${speciesActive}`);

await s.evaluate("history.forward()");
await s.waitFor("location.search.includes('Mus')", "URL to go forward to mouse");
record("Forward returns to the later search", true, await s.evaluate("location.search"));

// ---------------------------------------------------------------------------
// 2. Series row highlights when reached by GEO accession
// ---------------------------------------------------------------------------
for (const [label, accession, expectedRun] of [
  ["by run id", RUN_ID, RUN_ID],
  ["by GEO accession", GEO_ACCESSION, RUN_ID],
]) {
  await s.goto(`${APP}/sample/${accession}`);
  await s.waitFor(
    `document.body.innerText.includes('Samples in This Series')`,
    `series table for ${accession}`,
  );
  const marked = await s.evaluate(`
    (() => {
      const rows = [...document.querySelectorAll('tr')];
      const row = rows.find(r => r.innerText.includes('Current Selection'));
      return row ? row.innerText.replace(/\\s+/g, ' ').trim() : null;
    })()
  `);
  record(
    `series highlight ${label}`,
    !!marked && marked.includes(expectedRun),
    `marked row: ${marked ?? "(none)"}`,
  );
}

console.log("\n--- summary ---");
const failed = results.filter((r) => !r.passed);
console.log(`${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
