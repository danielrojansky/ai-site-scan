// Screenshot API: GET /api/shot?url=https://example.com
// Opens the page in headless Chrome and returns a JPEG of the top of the page.
// It only loads public addresses (same idea as the SSRF protection in scan.py).

const dns = require("node:dns").promises;
const net = require("node:net");

const MAX_URL = 2048;
const WIDTH = 1280;
const HEIGHT = 1600;

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

function fail(res, code, msg) {
  res.statusCode = code;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify({ error: msg }));
}

module.exports = async function handler(req, res) {
  let target;
  try {
    const q = new URL(req.url, "http://x").searchParams.get("url") || "";
    if (!q || q.length > MAX_URL) return fail(res, 400, "Missing url");
    target = new URL(/^https?:\/\//i.test(q) ? q : "https://" + q);
    if (!["http:", "https:"].includes(target.protocol)) return fail(res, 400, "Only http and https");
  } catch {
    return fail(res, 400, "Invalid url");
  }
  if (!(await isPublicHost(target.hostname))) return fail(res, 400, "URL not allowed");

  let browser;
  try {
    const chromium = (await import("@sparticuz/chromium")).default;
    const puppeteer = (await import("puppeteer-core")).default;
    browser = await puppeteer.launch({
      args: chromium.args,
      defaultViewport: { width: WIDTH, height: HEIGHT },
      executablePath: process.env.CHROME_PATH || (await chromium.executablePath()),
      headless: true,
    });
    const page = await browser.newPage();
    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 AISiteScan/1.0"
    );

    // Block requests to private addresses (redirects, iframes, images) and heavy media.
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

    try {
      await page.goto(target.href, { waitUntil: "networkidle2", timeout: 20000 });
    } catch {
      // Slow pages: take the shot with what has loaded so far.
    }
    await new Promise((r) => setTimeout(r, 800));
    const img = await page.screenshot({ type: "jpeg", quality: 70, clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT } });

    res.statusCode = 200;
    res.setHeader("Content-Type", "image/jpeg");
    // Cache at the Vercel edge for 10 minutes, so repeat views are instant.
    res.setHeader("Cache-Control", "public, max-age=300, s-maxage=600");
    res.end(Buffer.from(img));
  } catch (e) {
    console.error("shot failed", e && e.message);
    return fail(res, 502, "Could not take a screenshot: " + String((e && e.message) || e).slice(0, 300));
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
};
