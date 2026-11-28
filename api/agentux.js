// Agent usability API: GET /api/agentux?url=https://example.com
// Opens the page in headless Chrome at 1280 x 800, the way a browser agent does, and returns:
// the accessibility tree an agent reads, what covers the screen at load (with a screenshot), and the checks
// from _agentux.js with a grade. It does not change the 0 to 100 score.
// It only loads public addresses (same idea as the SSRF protection in scan.py).
const dns = require("node:dns").promises;
const net = require("node:net");
const { snapshot, motionProbe, evaluate } = require("./_agentux.js");

const MAX_URL = 2048;
const W = 1280, H = 800;
// Every wait is bounded, so the function always answers before the 40 s limit.
const GOTO_MS = 12000, IDLE_MS = 6000, SETTLE_MS = 1200, EVAL_MS = 6000, MOTION_MS = 2500, WATCHDOG_MS = 33000;

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
  if (process.env.AGENTUX_ALLOW_LOCAL === "1" && (host === "127.0.0.1" || host.endsWith(".test"))) return true;  // tests only
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
const within = (p, ms, what) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(what + " took too long")), ms))]);

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

  let browser, answered = false;
  const reply = (code, body, headers) => { if (!answered) { answered = true; send(res, code, body, headers); } };
  const killChrome = () => { try { const p = browser && browser.process(); if (p) p.kill("SIGKILL"); } catch {} };
  const watchdog = setTimeout(() => { reply(504, { error: "The page took too long to load in our browser." }); killChrome(); }, WATCHDOG_MS);

  try {
    const chromium = (await import("@sparticuz/chromium")).default;
    const puppeteer = (await import("puppeteer-core")).default;
    const local = !!process.env.CHROME_PATH;
    browser = await puppeteer.launch({
      args: local ? ["--no-sandbox"] : chromium.args,
      defaultViewport: { width: W, height: H },
      executablePath: process.env.CHROME_PATH || (await chromium.executablePath()),
      headless: true,
    });
    const page = await browser.newPage();
    await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 AISiteScan/1.0");
    const hostOk = new Map();
    await page.setRequestInterception(true);
    page.on("request", async (r) => {
      try {
        const u = new URL(r.url());
        if (u.protocol === "data:" || u.protocol === "blob:") return r.continue();
        if (!["http:", "https:"].includes(u.protocol)) return r.abort();
        if (["media", "websocket", "eventsource"].includes(r.resourceType())) return r.abort();
        if (!hostOk.has(u.hostname)) hostOk.set(u.hostname, isPublicHost(u.hostname));
        return (await hostOk.get(u.hostname)) ? r.continue() : r.abort();
      } catch {
        try { r.abort(); } catch {}
      }
    });
    let status = null;
    try {
      const resp = await page.goto(target.href, { waitUntil: "domcontentloaded", timeout: GOTO_MS });
      status = resp ? resp.status() : null;
    } catch {
      // Slow pages: use what has loaded so far.
    }
    await page.waitForNetworkIdle({ concurrency: 2, idleTime: 700, timeout: IDLE_MS }).catch(() => {});
    await new Promise((r) => setTimeout(r, SETTLE_MS));

    const snap = await within(page.evaluate(snapshot), EVAL_MS, "Reading the page");
    const m1 = await within(page.evaluate(motionProbe), EVAL_MS, "Reading the page").catch(() => []);
    const shot = await within(page.screenshot({ type: "jpeg", quality: 55, encoding: "base64", clip: { x: 0, y: 0, width: W, height: H } }), EVAL_MS, "The screenshot").catch(() => null);
    let motion = { candidates: m1.length, moving: 0 };
    if (m1.length) {
      await new Promise((r) => setTimeout(r, MOTION_MS));
      const m2 = await within(page.evaluate(motionProbe), EVAL_MS, "Reading the page").catch(() => m1);
      motion.moving = m1.filter((v, i) => m2[i] !== undefined && m2[i] !== v).length;
    }
    const body = evaluate(snap, { motion });
    const blocked = (status && status >= 400) || /just a moment|attention required|access denied|captcha/i.test(snap.title || "");
    Object.assign(body, { status, blocked: !!blocked, screenshot: shot ? "data:image/jpeg;base64," + shot : null, checked_at: new Date().toISOString() });
    return reply(200, body, blocked ? {} : { "Cache-Control": "public, max-age=300, s-maxage=600" });
  } catch (e) {
    console.error("agentux failed", e && e.message);
    return reply(502, { error: "Could not open the page in our browser: " + String((e && e.message) || e).slice(0, 300) });
  } finally {
    clearTimeout(watchdog);
    if (browser) {
      await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
      killChrome();
    }
  }
};
