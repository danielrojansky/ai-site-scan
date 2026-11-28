// Accounts, purchases and saved reports: /api/account?do=<action>
// Every action needs a signed-in user: "Authorization: Bearer <Supabase access token>".
//   GET  me        account, credits, unlocked sites, saved reports (no report text)
//   GET  report    one saved report with its guide (?id=)
//   POST checkout  {plan: "single" | "pro", url}      -> {url} of a Polar checkout for this user
//   POST unlock    {url, scan_key}                     -> uses 1 credit (or a rescan) for this scan result
//   POST save      {report_id, score, summary, guide_md} -> stores the report and emails it (once)
//   POST portal    -> {url} of the Polar customer portal (receipts, cancel the subscription)
//   POST sync      -> adds credits from this user's paid Polar orders (if the webhook is late)
// One function for all actions keeps the project under Vercel's function count limit.
// Settings: SUPABASE_SECRET_KEY, POLAR_ACCESS_TOKEN (checkouts:write, customer_sessions:write, orders:read),
//           POLAR_PRODUCT_SINGLE, POLAR_PRODUCT_PRO, RESEND_API_KEY + EMAIL_FROM.

const { env, send, readJson, limited, clientIp, hostOf, siteUrl, esc, polar, sendEmail, emailReady } = require("./_lib");
const { db, dbReady, userFrom, grantFromOrder, productId, UUID } = require("./_db");

const PRO_REPORTS = () => Number(env("PRO_REPORTS_PER_MONTH", "10")) || 10;

async function me(user) {
  const now = Date.now();
  const [g, u, r] = await Promise.all([
    db().from("grants").select("plan,reports_total,reports_used,valid_until,revoked_at,created_at").eq("user_id", user.id),
    db().from("unlocks").select("host,rescans_left,rescans_until,grant_id,created_at").eq("user_id", user.id).order("created_at", { ascending: false }),
    db().from("reports").select("id,host,url,kind,score,created_at,emailed_at,scan_key").eq("user_id", user.id).not("guide_md", "is", null).order("created_at", { ascending: false }).limit(200),
  ]);
  const live = (g.data || []).filter((x) => !x.revoked_at && (!x.valid_until || Date.parse(x.valid_until) > now));
  const left = (plan) => live.filter((x) => x.plan === plan).reduce((n, x) => n + Math.max(0, x.reports_total - x.reports_used), 0);
  const pro = live.filter((x) => x.plan === "pro").sort((a, b) => Date.parse(b.valid_until) - Date.parse(a.valid_until))[0];
  const sites = {};
  for (const x of u.data || []) {
    const open = Date.parse(x.rescans_until) > now && (x.rescans_left === null || x.rescans_left > 0);
    const s = sites[x.host] || (sites[x.host] = { host: x.host, rescans_left: 0, rescans_until: null, unlimited: false });
    if (open) {
      if (x.rescans_left === null) s.unlimited = true;
      else s.rescans_left += x.rescans_left;
      if (!s.rescans_until || Date.parse(x.rescans_until) > Date.parse(s.rescans_until)) s.rescans_until = x.rescans_until;
    }
  }
  return {
    ok: true,
    email: user.email,
    credits: { single: left("single"), pro: left("pro"), pro_until: pro ? pro.valid_until : null },
    subscribed: !!pro,
    sites: Object.values(sites),
    reports: r.data || [],
  };
}

async function checkout(user, body) {
  const plan = body.plan === "pro" ? "pro" : "single";
  const product = productId(plan === "pro" ? "pro" : "single");
  if (!product) return [503, { error: "This plan is not on sale yet." }];
  const host = hostOf(body.url) || "";
  const r = await polar("/v1/checkouts/", {
    products: [product],
    external_customer_id: user.id,
    customer_email: user.email,
    success_url: `${siteUrl()}/?paid=${plan}&checkout_id={CHECKOUT_ID}`,
    return_url: `${siteUrl()}/`,
    allow_discount_codes: true,
    metadata: { user_id: user.id, plan, host },
  }, { auth: true });
  if (!r.ok || !r.data || !r.data.url) {
    console.error(`checkout failed status=${r.status} detail=${JSON.stringify(r.data && (r.data.detail || r.data.error)).slice(0, 300)}`);
    return [502, { error: "Could not open the checkout. Try again in a minute." }];
  }
  return [200, { ok: true, url: r.data.url }];
}

async function unlock(user, body) {
  const host = hostOf(body.url), key = String(body.scan_key || "").slice(0, 120);
  if (!host || !key) return [400, { error: "Scan a site first." }];
  const { data, error } = await db().rpc("claim_report", { p_user: user.id, p_host: host, p_url: String(body.url).slice(0, 2048), p_scan_key: key });
  if (error) { console.error("claim_report failed:", error.message); return [502, { error: "Could not unlock. Try again in a minute." }]; }
  const row = Array.isArray(data) ? data[0] : data;
  if (!row || row.status !== "ok") return [402, { ok: false, no_credit: true, error: "You have no reports left. Choose a plan." }];
  console.log(`unlock host=${host} kind=${row.kind} plan=${row.plan}`);
  return [200, { ok: true, report_id: row.report_id, kind: row.kind, plan: row.plan, host }];
}

function reportEmail(user, rep, guide) {
  const link = `${siteUrl()}/?view=reports&report=${rep.id}`;
  const score = rep.score == null ? "" : ` Score: ${rep.score} of 100.`;
  const subject = `Your AI Site Scan report for ${rep.host}`;
  const html = `<p>Hi,</p><p>Here is the full AI readiness report for <b>${esc(rep.host)}</b>.${esc(score)}</p>
<p>The fix guide is attached as a Markdown file. Give it to your developer or paste it into your AI coding tool.</p>
<p><a href="${esc(link)}">Open the report in your workspace</a>. Log in with ${esc(user.email)}.</p>
<p>Questions? Reply to this email.</p><p>AI Site Scan</p>`;
  const text = `Here is the full AI readiness report for ${rep.host}.${score}\nThe fix guide is attached as a Markdown file.\nOpen it in your workspace: ${link}\nLog in with ${user.email}.\n\nAI Site Scan`;
  return { to: user.email, subject, html, text, replyTo: env("SUPPORT_EMAIL", "support@getaisitescan.com"),
    attachments: [{ filename: `ai-fix-guide-${rep.host}.md`, content: Buffer.from(guide, "utf8").toString("base64") }],
    tags: [{ name: "kind", value: "report" }] };
}

async function save(user, body) {
  const id = String(body.report_id || ""), guide = String(body.guide_md || "");
  if (!UUID.test(id) || !guide) return [400, { error: "Missing report." }];
  if (guide.length > 400000) return [413, { error: "The report is too large." }];
  const score = Number.isFinite(Number(body.score)) ? Math.round(Number(body.score)) : null;
  let summary = body.summary && typeof body.summary === "object" ? body.summary : null;
  if (summary && JSON.stringify(summary).length > 20000) summary = null;
  const { data: rep, error } = await db().from("reports").update({ guide_md: guide, score, summary })
    .eq("id", id).eq("user_id", user.id).select("id,host,score,emailed_at").single();
  if (error || !rep) return [404, { error: "Report not found." }];
  let emailed = !!rep.emailed_at;
  if (!emailed && emailReady() && body.email !== false) {
    const r = await sendEmail(reportEmail(user, rep, guide));
    if (r.ok) { emailed = true; await db().from("reports").update({ emailed_at: new Date().toISOString() }).eq("id", id); }
    else console.error(`report email failed: ${r.error}`);
  }
  return [200, { ok: true, emailed }];
}

async function report(user, id) {
  if (!UUID.test(id)) return [400, { error: "Missing report." }];
  const { data, error } = await db().from("reports").select("id,host,url,kind,score,summary,guide_md,created_at").eq("id", id).eq("user_id", user.id).single();
  if (error || !data) return [404, { error: "Report not found." }];
  return [200, { ok: true, report: data }];
}

// Back from checkout: read this user's paid orders from Polar and add any credits the webhook
// has not added yet (same order = same credits, so this is safe to repeat). Scope orders:read.
async function sync(user) {
  const q = new URLSearchParams({ external_customer_id: user.id, limit: "20", sorting: "-created_at" });
  const r = await polar(`/v1/orders/?${q}`, null, { auth: true, method: "GET" });
  if (!r.ok || !r.data || !Array.isArray(r.data.items)) { console.warn(`sync orders failed status=${r.status}`); return [200, { ok: false }]; }
  const done = [];
  for (const o of r.data.items) {
    const paid = o.paid === true || o.status === "paid";
    const refunded = Number(o.total_amount || 0) > 0 && Number(o.refunded_amount || 0) >= Number(o.total_amount || 0);
    if (!paid || refunded) continue;
    done.push(await grantFromOrder(o));
  }
  console.log(`sync orders=${r.data.items.length} results=${done.join("|") || "none"}`);
  return [200, { ok: true, orders: r.data.items.length }];
}

async function portal(user) {
  const r = await polar("/v1/customer-sessions/", { external_customer_id: user.id, return_url: `${siteUrl()}/?view=reports` }, { auth: true });
  if (!r.ok || !r.data || !r.data.customer_portal_url) return [404, { error: "No purchases yet." }];
  return [200, { ok: true, url: r.data.customer_portal_url }];
}

module.exports = async (req, res) => {
  const action = String(new URL(req.url, "http://local").searchParams.get("do") || "");
  if (!dbReady()) return send(res, 503, { error: "Accounts are not set up yet." });
  if (limited("acct:" + clientIp(req), 120, 10 * 60 * 1000)) return send(res, 429, { error: "Too many requests. Wait a few minutes." });
  const user = await userFrom(req);
  if (!user) return send(res, 401, { error: "Please log in again." });
  try {
    if (req.method === "GET") {
      if (action === "me") return send(res, 200, await me(user));
      if (action === "report") { const [s, b] = await report(user, String(new URL(req.url, "http://local").searchParams.get("id") || "")); return send(res, s, b); }
      return send(res, 404, { error: "Unknown action." });
    }
    if (req.method !== "POST") return send(res, 405, { error: "Use GET or POST." });
    const body = await readJson(req, 450000);
    const fn = { checkout, unlock, save, portal, sync }[action];
    if (!fn) return send(res, 404, { error: "Unknown action." });
    const [s, b] = await fn(user, body);
    return send(res, s, b);
  } catch (e) {
    console.error(`account ${action} failed:`, (e && e.stack) || e);
    return send(res, e.status || 500, { error: e.status ? e.message : "Something went wrong. Try again." });
  }
};

module.exports.reportEmail = reportEmail;
