// Shared helpers for the Node API functions (account, config, email, polar-webhook, cron-selfcheck).
// The underscore keeps Vercel from turning this file into an endpoint.
// Every outside service is optional: a function answers 503 when its settings are missing,
// so the site keeps working before Polar, Resend or PostHog are set up.

const crypto = require("node:crypto");

const env = (k, d = "") => String(process.env[k] ?? d).trim() || d;

function send(res, status, body, headers = {}) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", headers["Cache-Control"] || "no-store");
  for (const [k, v] of Object.entries(headers)) if (k !== "Cache-Control") res.setHeader(k, v);
  res.end(JSON.stringify(body));
}

// Read the request body ourselves. The webhook needs the exact bytes to check the signature.
async function readRaw(req, limit = 100000) {
  if (req.rawBody != null) return Buffer.isBuffer(req.rawBody) ? req.rawBody : Buffer.from(String(req.rawBody));
  const chunks = [];
  let size = 0;
  for await (const c of req) {
    const b = Buffer.isBuffer(c) ? c : Buffer.from(c);
    size += b.length;
    if (size > limit) throw Object.assign(new Error("The request is too large."), { status: 413 });
    chunks.push(b);
  }
  return Buffer.concat(chunks);
}

async function readJson(req, limit = 20000) {
  const raw = await readRaw(req, limit);
  if (!raw.length) return {};
  try {
    const v = JSON.parse(raw.toString("utf8"));
    return v && typeof v === "object" ? v : {};
  } catch {
    throw Object.assign(new Error("The request is not valid JSON."), { status: 400 });
  }
}

// Basic in-memory rate limit, per function instance (same idea as scan.py).
const buckets = new Map();
function limited(key, max, windowMs) {
  const now = Date.now();
  const hits = (buckets.get(key) || []).filter((t) => now - t < windowMs);
  if (hits.length >= max) {
    buckets.set(key, hits);
    return true;
  }
  hits.push(now);
  buckets.set(key, hits);
  if (buckets.size > 5000) for (const [k, v] of buckets) if (!v.length || now - v[v.length - 1] > windowMs) buckets.delete(k);
  return false;
}

const clientIp = (req) =>
  String(req.headers["x-forwarded-for"] || req.headers["x-real-ip"] || "").split(",")[0].trim() || "unknown";

// "https://www.Example.com/path" -> "example.com". Empty string when it is not a usable public host name.
function hostOf(input) {
  const s = String(input || "").trim();
  if (!s || s.length > 2048) return "";
  try {
    const u = new URL(/^https?:\/\//i.test(s) ? s : "https://" + s);
    if (!/^https?:$/.test(u.protocol)) return "";
    const h = u.hostname.toLowerCase().replace(/^www\./, "").replace(/\.$/, "");
    return /^(?=.{4,253}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,63}$/.test(h) ? h : "";
  } catch {
    return "";
  }
}

// The origin this function was called on, for calls back to our own API (works on previews too).
function originOf(req) {
  const h = String(req.headers["x-forwarded-host"] || req.headers.host || "").split(",")[0].trim();
  if (/^[a-z0-9.-]+(:\d+)?$/i.test(h)) return (/^(localhost|127\.)/.test(h) ? "http://" : "https://") + h;
  return env("SITE_URL", "https://getaisitescan.com").replace(/\/+$/, "");
}

const siteUrl = () => env("SITE_URL", "https://getaisitescan.com").replace(/\/+$/, "");

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/* ---------- Polar ---------- */
const polarBase = () => (env("POLAR_SERVER") === "sandbox" ? "https://sandbox-api.polar.sh" : "https://api.polar.sh");

// auth: true = the organization token (POLAR_ACCESS_TOKEN); a string = that bearer token
// (for example a customer session token). Every call pins the API version, because
// without the header Polar uses its "Current" version, which changes each quarter.
async function polar(path, body, { auth = false, method = "POST", timeout = 8000 } = {}) {
  const headers = { Accept: "application/json", "Polar-Version": env("POLAR_API_VERSION", "2026-10") };
  if (body) headers["Content-Type"] = "application/json";
  if (auth) headers.Authorization = "Bearer " + (typeof auth === "string" ? auth : env("POLAR_ACCESS_TOKEN"));
  const r = await fetch(polarBase() + path, { method, headers, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(timeout) });
  let data = null;
  try {
    data = await r.json();
  } catch {}
  return { ok: r.ok, status: r.status, data };
}

// Polar signs webhooks with Standard Webhooks headers. Its server uses the literal secret
// string as the HMAC key; the spec way (strip "whsec_", base64-decode) is accepted too.
function verifyWebhook(raw, headers, secret, toleranceS = 300) {
  const id = headers["webhook-id"], ts = headers["webhook-timestamp"], sig = headers["webhook-signature"];
  if (!secret || !id || !ts || !sig) return false;
  const t = Number(ts);
  if (!Number.isFinite(t) || Math.abs(Date.now() / 1000 - t) > toleranceS) return false;
  const content = Buffer.concat([Buffer.from(`${id}.${ts}.`, "utf8"), raw]);
  const keys = [Buffer.from(secret, "utf8")];
  const bare = secret.startsWith("whsec_") ? secret.slice(6) : secret;
  if (/^[A-Za-z0-9+/]+={0,2}$/.test(bare)) {
    const k = Buffer.from(bare, "base64");
    if (k.length >= 16) keys.push(k);
  }
  const expected = keys.map((k) => crypto.createHmac("sha256", k).update(content).digest());
  return String(sig).split(" ").some((part) => {
    const i = part.indexOf(",");
    if (i < 0 || part.slice(0, i) !== "v1") return false;
    const given = Buffer.from(part.slice(i + 1), "base64");
    return expected.some((e) => e.length === given.length && crypto.timingSafeEqual(e, given));
  });
}

/* ---------- PostHog (server-side events) ---------- */
async function capture(event, distinctId, properties = {}) {
  const key = env("PUBLIC_POSTHOG_KEY");
  if (!key || !distinctId) return false;
  const host = env("PUBLIC_POSTHOG_HOST", "https://us.i.posthog.com").replace(/\/+$/, "");
  try {
    const r = await fetch(host + "/i/v0/e/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key, event, distinct_id: String(distinctId).slice(0, 200), properties }),
      signal: AbortSignal.timeout(5000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

/* ---------- Resend (email) ---------- */
const emailReady = () => !!(env("RESEND_API_KEY") && env("EMAIL_FROM"));

async function sendEmail({ to, subject, html, text, scheduledAt, replyTo, tags, attachments }) {
  if (!emailReady()) return { ok: false, error: "Email is not set up." };
  const body = { from: env("EMAIL_FROM"), to: Array.isArray(to) ? to : [to], subject, html, text };
  if (scheduledAt) body.scheduled_at = scheduledAt;
  if (replyTo) body.reply_to = replyTo;
  if (tags) body.tags = tags;
  if (attachments) body.attachments = attachments;  // [{filename, content: base64}]
  try {
    const r = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: "Bearer " + env("RESEND_API_KEY"), "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10000),
    });
    let data = null;
    try {
      data = await r.json();
    } catch {}
    return { ok: r.ok, status: r.status, id: data && data.id, error: r.ok ? null : (data && (data.message || data.name)) || "HTTP " + r.status };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

/* ---------- our own scan ---------- */
async function fetchScan(host, { origin, fresh = false, timeout = 55000 } = {}) {
  const base = (origin || siteUrl()).replace(/\/+$/, "");
  try {
    const r = await fetch(`${base}/api/scan?url=${encodeURIComponent(host)}${fresh ? "&fresh=1" : ""}`, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(timeout),
    });
    let data = null;
    try {
      data = await r.json();
    } catch {}
    return { ok: r.ok && !!data && typeof data.score === "number", status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null, error: String((e && e.message) || e) };
  }
}

// A ready llms.txt for the scanned site, built from the scan's AI view (same rules as the page's fix guide).
function cleanName(t) {
  const s = String(t || "");
  return s.split(/\s[|\\\-–:]\s/)[0].trim() || s;
}
function internalLinks(md, origin) {
  const seen = new Set(), out = [];
  const re = /\[([^\]\n]{2,60})\]\(([^)\s]+)\)/g;
  let m;
  while ((m = re.exec(String(md || "")))) {
    let u;
    try {
      u = new URL(m[2], origin + "/");
    } catch {
      continue;
    }
    if (u.origin !== origin) continue;
    const key = u.pathname.replace(/\/$/, "") || "/";
    if (seen.has(key) || /\.(png|jpe?g|svg|gif|webp|pdf)$/i.test(key) || /^(image|button|input|select)/.test(m[1])) continue;
    seen.add(key);
    out.push({ text: m[1].trim(), url: u.origin + (key === "/" ? "/" : key) });
  }
  return out;
}
function buildLlms(d) {
  const ai = (d && d.ai_view) || {};
  let origin;
  try {
    origin = new URL(ai.final_url || d.url).origin;
  } catch {
    origin = String((d && d.url) || "");
  }
  let host = origin;
  try {
    host = new URL(origin).hostname.replace(/^www\./, "");
  } catch {}
  const name = cleanName(ai.og_title || ai.title || host);
  const desc = ai.description || ai.og_description || "";
  const links = internalLinks(ai.markdown || "", origin).slice(0, 12);
  const lines = links.length ? links.map((l) => `- [${l.text}](${l.url}): TODO short description`) : [`- [Home](${origin}/): TODO short description`];
  return [`# ${name}`, "", `> ${desc || "TODO: one or two sentences about what the site offers and for whom."}`, "", "## Main pages", "", ...lines, "", "## Optional", "", `- [About](${origin}/about): TODO`, ""].join("\n");
}

const RANK = { critical: 0, high: 1, medium: 2, low: 3 };
function topActions(d, n = 3) {
  return ((d && d.actions) || [])
    .toSorted((a, b) => (RANK[a.priority] ?? 9) - (RANK[b.priority] ?? 9) || (b.gain || 0) - (a.gain || 0))
    .slice(0, n);
}

module.exports = {
  env, send, readRaw, readJson, limited, clientIp, hostOf, originOf, siteUrl, esc,
  polar, verifyWebhook, capture, emailReady, sendEmail, fetchScan, buildLlms, internalLinks, cleanName, topActions,
};
