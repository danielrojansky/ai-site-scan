// WebMCP API: GET /api/webmcp?url=https://example.com
// Opens the page in headless Chrome with a small stand-in for the WebMCP API (document.modelContext and
// navigator.modelContext) and reports the tools the page registers. Forms with a toolname attribute count too.
// Real browsers do not offer WebMCP yet, so a page can only be asked by pretending to be one that does.
// The stand-in sends every tool out of the page the moment it is registered. A page that is too busy to be read
// afterwards (heavy scripts in a small Chrome) still gives the tools it registered before that.
// It only loads public addresses (same idea as the SSRF protection in scan.py).
const dns = require("node:dns").promises;
const net = require("node:net");
const MAX_URL = 2048;
const MAX_TOOLS = 50;
// Every wait is bounded, so the function always answers (in JSON) before the 40 s limit, even for a heavy page.
const GOTO_MS = 12000;      // time for the first answer from the page
const IDLE_MS = 6000;       // then wait for the network to go quiet, at most this long
const SETTLE_MS = 1500;     // tools are registered when the page scripts run
const EVAL_MS = 4000;       // reading the forms must not hang on a busy page
const WATCHDOG_MS = 30000;  // last resort: answer with what we have and stop Chrome

function isPrivateIp(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split(".").map(Number);
    return (
      a === 0 || a === 10 || a === 127 || a >= 224 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) ||
      (a === 198 && (b === 18 || b === 19))
    );
  }
  const v = ip.toLowerCase();
  if (v.startsWith("::ffff:")) return isPrivateIp(v.slice(7));
  return v === "::" || v === "::1" || v.startsWith("fc") || v.startsWith("fd") || v.startsWith("fe80");
}
async function isPublicHost(hostname) {
  const host = hostname.replace(/^\[|\]$/g, "");
  if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".internal")) return false;
  if (net.isIP(host)) return !isPrivateIp(host);
  try {
    const addrs = await dns.lookup(host, { all: true });
    return addrs.length > 0 && addrs.every((a) => !isPrivateIp(a.address));
  } catch {
    return false;
  }
}
function send(res, code, body, headers = {}) {
  res.statusCode = code;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  for (const [k, v] of Object.entries(headers)) res.setHeader(k, v);
  if (!headers["Cache-Control"]) res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(body));
}

// Runs before any script of the page. It records every tool a page registers, sends it out through the
// __webmcpReport binding at once, and answers like the real API would.
const STAND_IN = `(() => {
  const found = [], via = [];
  const note = (t, where) => {
    try {
      if (t && typeof t.name === "string" && !found.some((x) => x.name === t.name)) {
        const rec = { name: t.name, title: typeof t.title === "string" ? t.title : undefined,
                      description: typeof t.description === "string" ? t.description : undefined, hasSchema: !!t.inputSchema };
        found.push(rec);
        if (!via.includes(where)) via.push(where);
        try { const p = window.__webmcpReport && window.__webmcpReport(rec, where); if (p && p.catch) p.catch(() => {}); } catch (e) {}
      }
    } catch (e) {}
  };
  const make = (where) => ({
    registerTool(t) { note(t, where); return Promise.resolve(); },
    unregisterTool() { return Promise.resolve(); },
    provideContext(c) { try { ((c && c.tools) || []).forEach((t) => note(t, where)); } catch (e) {} return Promise.resolve(); },
    clearContext() { return Promise.resolve(); },
    addEventListener() {}, removeEventListener() {},
  });
  const put = (o, where) => { try { if (!o.modelContext) Object.defineProperty(o, "modelContext", { value: make(where), configurable: true }); } catch (e) {} };
  put(document, "document.modelContext");
  put(navigator, "navigator.modelContext");
  window.__webmcp = { found, via };
})();`;

const short = (v, n) => (typeof v === "string" ? v.slice(0, n) : undefined);

module.exports = async function handler(req, res) {
  let target;
  try {
    const q = new URL(req.url, "http://x").searchParams.get("url") || "";
    if (!q || q.length > MAX_URL) return send(res, 400, { error: "Missing url" });
    target = new URL(/^https?:\/\//i.test(q) ? q : "https://" + q);
    if (!["http:", "https:"].includes(target.protocol)) return send(res, 400, { error: "Only http and https" });
  } catch {
    return send(res, 400, { error: "Invalid url" });
  }
  if (!(await isPublicHost(target.hostname))) return send(res, 400, { error: "URL not allowed" });

  let browser;
  let answered = false;
  const reported = [];     // tools sent out of the page as they were registered
  const reportedVia = [];
  const reply = (code, body, headers) => {
    if (!answered) { answered = true; send(res, code, body, headers); }
  };
  const killChrome = () => {
    try { const proc = browser && browser.process(); if (proc) proc.kill("SIGKILL"); } catch {}
  };
  // The answer from what we have. `read` is true when the page could be read afterwards (forms, late tools).
  const answer = (read, extraTools = [], extraVia = []) => {
    const names = new Set(reported.map((t) => t.name));
    const tools = [...reported, ...extraTools.filter((t) => !names.has(t.name))].slice(0, MAX_TOOLS);
    const imperative = tools.some((t) => !t.declarative), declarative = tools.some((t) => t.declarative);
    const method = imperative && declarative ? "both" : imperative ? "imperative_api" : declarative ? "declarative_html" : null;
    const clean = tools.map(({ name, title, description, hasSchema }) => ({ name, title, description, hasSchema }));
    const body = { toolCount: clean.length, tools: clean, detectionMethod: method, via: reportedVia[0] || extraVia[0] || (declarative ? "form attributes" : null) };
    // No tools and the page never let us read it: we do not know, so say so (the page shows "could not confirm").
    if (!read && !clean.length) body.partial = true;
    return body;
  };
  const watchdog = setTimeout(() => {
    reply(200, answer(false));   // not cached: a busy page may answer next time
    killChrome();
  }, WATCHDOG_MS);

  try {
    const chromium = (await import("@sparticuz/chromium")).default;
    const puppeteer = (await import("puppeteer-core")).default;
    const local = !!process.env.CHROME_PATH;  // a test on a developer machine uses its own Chrome
    browser = await puppeteer.launch({
      args: local ? ["--no-sandbox"] : chromium.args,
      defaultViewport: { width: 1280, height: 900 },
      executablePath: process.env.CHROME_PATH || (await chromium.executablePath()),
      headless: true,
    });
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 AISiteScan/1.0"
    );
    await page.exposeFunction("__webmcpReport", (rec, where) => {
      try {
        if (rec && typeof rec.name === "string" && reported.length < MAX_TOOLS && !reported.some((t) => t.name === rec.name)) {
          reported.push({ name: short(rec.name, 200), title: short(rec.title, 200), description: short(rec.description, 400), hasSchema: !!rec.hasSchema });
          if (typeof where === "string" && !reportedVia.includes(where)) reportedVia.push(where.slice(0, 40));
        }
      } catch {}
    });
    await page.evaluateOnNewDocument(STAND_IN);
    // Block requests to private addresses (redirects, iframes, scripts) and heavy media.
    const hostOk = new Map();
    await page.setRequestInterception(true);
    page.on("request", async (r) => {
      try {
        const u = new URL(r.url());
        if (u.protocol === "data:" || u.protocol === "blob:") return r.continue();
        if (!["http:", "https:"].includes(u.protocol)) return r.abort();
        if (["media", "image", "font", "websocket", "eventsource"].includes(r.resourceType())) return r.abort();
        if (!hostOk.has(u.hostname)) hostOk.set(u.hostname, isPublicHost(u.hostname));
        return (await hostOk.get(u.hostname)) ? r.continue() : r.abort();
      } catch {
        try { r.abort(); } catch {}
      }
    });
    try {
      await page.goto(target.href, { waitUntil: "domcontentloaded", timeout: GOTO_MS });
    } catch {
      // Slow pages: use what has loaded so far.
    }
    await page.waitForNetworkIdle({ concurrency: 2, idleTime: 700, timeout: IDLE_MS }).catch(() => {});
    await new Promise((r) => setTimeout(r, SETTLE_MS));
    // Forms with a toolname attribute need the page itself. A busy page may not answer: then only the tools it sent out count.
    let seen = null;
    try {
      seen = await Promise.race([
        page.evaluate(() => {
          const w = window.__webmcp || { found: [], via: [] };
          const forms = [...document.querySelectorAll("form[toolname]")].map((f) => ({
            name: f.getAttribute("toolname") || "",
            description: f.getAttribute("tooldescription") || undefined,
            hasSchema: true,
            declarative: true,
          })).filter((t) => t.name);
          return { imperative: w.found, via: w.via, declarative: forms };
        }),
        new Promise((_, reject) => setTimeout(() => reject(new Error("The page did not answer")), EVAL_MS)),
      ]);
    } catch {
      seen = null;
    }
    const body = seen
      ? answer(true, [...seen.imperative.map((t) => ({ ...t, name: short(t.name, 200), title: short(t.title, 200), description: short(t.description, 400) })), ...seen.declarative], seen.via)
      : answer(false);
    // A complete answer can be cached at the edge. A partial one is not.
    return reply(200, body, body.partial ? {} : { "Cache-Control": "public, max-age=300, s-maxage=600" });
  } catch (e) {
    console.error("webmcp failed", e && e.message);
    // Chrome failed to start or crashed: the tools sent out before that still count.
    if (reported.length) return reply(200, answer(false));
    return reply(502, { error: "Could not check WebMCP: " + String((e && e.message) || e).slice(0, 300) });
  } finally {
    clearTimeout(watchdog);
    if (browser) {
      await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
      killChrome();
    }
  }
};
