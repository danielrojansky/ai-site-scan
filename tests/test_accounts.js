// Account and webhook tests with a fake Supabase, Polar, Resend and PostHog: node tests/test_accounts.js
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const path = require("node:path");
const ROOT = process.argv[2] || path.join(__dirname, "..");
process.env.SUPABASE_SECRET_KEY = "sb_secret_test";
process.env.POLAR_ACCESS_TOKEN = "polar_oat_test";
process.env.POLAR_PRODUCT_SINGLE = "11111111-1111-4111-8111-000000000001";
process.env.POLAR_PRODUCT_PRO = "11111111-1111-4111-8111-000000000002";
process.env.RESEND_API_KEY = "re_test"; process.env.EMAIL_FROM = "AI Site Scan <support@getaisitescan.com>";
process.env.POLAR_WEBHOOK_SECRET = "polar_whs_test";
process.env.OWNER_EMAIL = "owner@example.com"; process.env.PUBLIC_POSTHOG_KEY = "phc_test";
const seenEvents = new Set();
let FAKE_CFG = { paywall: true, pro: true, email: true };

const USER = { id: "22222222-2222-4222-8222-222222222222", email: "buyer@example.com" };
let rpcCalls = [], updates = [], rpcResult = {};
function q(table) {
  const st = { table, filters: [], data: null };
  const p = {
    select() { return p; }, eq(k, v) { st.filters.push([k, v]); return p; }, order() { return p; }, not() { return p; }, limit() { return p; },
    update(v) { updates.push({ table, v }); st.update = v; return p; },
    upsert(v) { st.upsert = v; return p; },
    single() { return Promise.resolve(st.update ? { data: { id: st.filters.find((f) => f[0] === "id")[1], host: "a.com", score: 80, emailed_at: null }, error: null } : { data: null }); },
    then(res) {
      if (table === "webhook_events") { const k = st.upsert.id; const first = !seenEvents.has(k); seenEvents.add(k); return res({ data: first ? [{ id: k }] : [], error: null }); }
      res({ data: table === "grants" ? [{ plan: "single", reports_total: 1, reports_used: 0, valid_until: null, revoked_at: null }] : [], error: null }); },
  };
  return p;
}
const fakeDb = { from: q, rpc: async (name, args) => { rpcCalls.push({ name, args }); return rpcResult[name] || { data: null, error: null }; } };
const dbmod = require(path.join(ROOT, "api", "_db.js"));
dbmod.db = () => fakeDb;
dbmod.userFrom = async (req) => (req.headers.authorization === "Bearer good" ? USER : null);
// grantFromOrder uses the module's own db(): re-bind through exports
const realGrant = dbmod.grantFromOrder;

let fetches = [];
global.fetch = async (url, opts = {}) => {
  const u = new URL(url); const body = opts.body ? JSON.parse(opts.body) : null;
  fetches.push({ url: u.toString(), path: u.pathname, method: opts.method || "GET", body, headers: opts.headers });
  if (u.pathname === "/api/scan") return { ok: true, status: 200, json: async () => ({ score: 95, url: "https://" + u.searchParams.get("url"), actions: [] }) };
  if (u.pathname === "/api/config") return { ok: true, status: 200, json: async () => FAKE_CFG };
  if (u.hostname.endsWith("posthog.com")) return { ok: true, status: 200, json: async () => ({}) };
  if (u.hostname === "api.resend.com") return { ok: true, status: 200, json: async () => ({ id: "em_1" }) };
  if (u.pathname === "/v1/checkouts/") return { ok: true, status: 201, json: async () => ({ url: "https://polar.sh/checkout/polar_c_x" }) };
  if (u.pathname === "/v1/orders/") return { ok: true, status: 200, json: async () => ({ items: [
    { id: "ord-1", paid: true, total_amount: 1900, refunded_amount: 0, product_id: process.env.POLAR_PRODUCT_SINGLE, customer: { external_id: USER.id }, billing_reason: "purchase" },
    { id: "ord-2", paid: true, total_amount: 1900, refunded_amount: 1900, product_id: process.env.POLAR_PRODUCT_SINGLE, customer: { external_id: USER.id } },
  ] }) };
  if (u.pathname === "/v1/customer-sessions/") return { ok: true, status: 201, json: async () => ({ customer_portal_url: "https://polar.sh/x/portal?t=1" }) };
  return { ok: false, status: 599, json: async () => ({}) };
};
const mkReq = ({ method = "GET", url, body, auth = "good", headers = {} }) => ({ method, url, headers: { authorization: auth ? "Bearer " + auth : "", "x-forwarded-for": "9.9.9." + Math.floor(Math.random() * 200), ...headers }, rawBody: Buffer.from(body == null ? "" : typeof body === "string" ? body : JSON.stringify(body)) });
const mkRes = () => ({ statusCode: 0, h: {}, b: "", setHeader(k, v) { this.h[k] = v; }, end(b) { this.b = b; }, json() { return JSON.parse(this.b); } });
const call = async (h, req) => { const r = mkRes(); await h(req, r); return r; };

(async () => {
  let n = 0; const t = async (name, fn) => { rpcCalls = []; updates = []; fetches = []; rpcResult = {}; await fn(); n++; console.log("ok", name); };
  const acct = require(path.join(ROOT, "api", "account.js"));

  await t("account: 401 without login", async () => {
    const r = await call(acct, mkReq({ url: "/api/account?do=me", auth: "" })); assert.equal(r.statusCode, 401);
  });
  await t("account: me sums credits", async () => {
    const r = await call(acct, mkReq({ url: "/api/account?do=me" })); const d = r.json();
    assert.equal(r.statusCode, 200); assert.equal(d.email, USER.email); assert.equal(d.credits.single, 1); assert.equal(d.subscribed, false);
  });
  await t("checkout: Polar session for this user, success URL with the checkout ID", async () => {
    const r = await call(acct, mkReq({ method: "POST", url: "/api/account?do=checkout", body: { plan: "pro", url: "https://www.a.com/x" } }));
    assert.equal(r.statusCode, 200); assert.match(r.json().url, /polar\.sh\/checkout/);
    const c = fetches.find((f) => f.path === "/v1/checkouts/");
    assert.deepEqual(c.body.products, [process.env.POLAR_PRODUCT_PRO]);
    assert.equal(c.body.external_customer_id, USER.id); assert.equal(c.body.customer_email, USER.email);
    assert.equal(c.body.success_url, "https://getaisitescan.com/?paid=pro&checkout_id={CHECKOUT_ID}");
    assert.equal(c.body.metadata.host, "a.com"); assert.equal(c.headers["Polar-Version"], "2026-10");
  });
  await t("unlock: credit used, report id back", async () => {
    rpcResult.claim_report = { data: [{ report_id: "33333333-3333-4333-8333-333333333333", kind: "full", plan: "single", status: "ok" }], error: null };
    const r = await call(acct, mkReq({ method: "POST", url: "/api/account?do=unlock", body: { url: "https://a.com", scan_key: "a.com|2026-11-28 10:00 UTC" } }));
    assert.equal(r.statusCode, 200); assert.equal(r.json().kind, "full");
    assert.deepEqual(rpcCalls[0].args, { p_user: USER.id, p_host: "a.com", p_url: "https://a.com", p_scan_key: "a.com|2026-11-28 10:00 UTC" });
  });
  await t("unlock: no credit is a 402", async () => {
    rpcResult.claim_report = { data: [{ status: "no_credit" }], error: null };
    const r = await call(acct, mkReq({ method: "POST", url: "/api/account?do=unlock", body: { url: "https://a.com", scan_key: "k" } }));
    assert.equal(r.statusCode, 402); assert.equal(r.json().no_credit, true);
  });
  await t("save: stores the guide and emails it with the file attached", async () => {
    const r = await call(acct, mkReq({ method: "POST", url: "/api/account?do=save", body: { report_id: "33333333-3333-4333-8333-333333333333", score: 80, summary: { band: "good" }, guide_md: "# Guide\n" } }));
    assert.equal(r.statusCode, 200); assert.equal(r.json().emailed, true);
    const em = fetches.find((f) => f.url.includes("resend")); assert.equal(em.body.to[0], USER.email);
    assert.equal(em.body.attachments[0].filename, "ai-fix-guide-a.com.md");
    assert.equal(Buffer.from(em.body.attachments[0].content, "base64").toString(), "# Guide\n");
    assert.ok(updates.some((u) => u.v.emailed_at));
  });
  await t("sync: grants paid orders, skips refunded ones", async () => {
    const r = await call(acct, mkReq({ method: "POST", url: "/api/account?do=sync", body: {} }));
    assert.equal(r.statusCode, 200);
    const g = rpcCalls.filter((c) => c.name === "grant_order");
    assert.equal(g.length, 1); assert.equal(g[0].args.p_order, "ord-1"); assert.equal(g[0].args.p_plan, "single"); assert.equal(g[0].args.p_total, 1);
    const o = fetches.find((f) => f.path === "/v1/orders/"); assert.match(o.url, /external_customer_id=22222222/);
  });
  await t("portal: signed-in portal link for this customer", async () => {
    const r = await call(acct, mkReq({ method: "POST", url: "/api/account?do=portal", body: {} }));
    assert.match(r.json().url, /portal/); assert.equal(fetches[0].body.external_customer_id, USER.id);
  });

  const hook = require(path.join(ROOT, "api", "polar-webhook.js"));
  const signed = (evt) => { const body = JSON.stringify(evt), id = "msg_" + Math.random(), ts = String(Math.floor(Date.now() / 1000));
    const sig = crypto.createHmac("sha256", Buffer.from(process.env.POLAR_WEBHOOK_SECRET, "utf8")).update(`${id}.${ts}.${body}`).digest("base64");
    return mkReq({ method: "POST", url: "/api/polar-webhook", body, auth: "", headers: { "webhook-id": id, "webhook-timestamp": ts, "webhook-signature": "v1," + sig } }); };
  await t("webhook: pro renewal gives 10 reports until period end + 3 days", async () => {
    const r = await call(hook, signed({ type: "order.paid", data: { id: "ord-p", total_amount: 2900, currency: "usd", product_id: process.env.POLAR_PRODUCT_PRO, billing_reason: "subscription_cycle", subscription_id: "sub-1", subscription: { current_period_end: "2026-12-28T00:00:00Z" }, customer: { external_id: USER.id, email: USER.email } } }));
    assert.equal(r.statusCode, 202, r.b);
    const g = rpcCalls.find((c) => c.name === "grant_order").args;
    assert.equal(g.p_plan, "pro"); assert.equal(g.p_total, 10); assert.equal(g.p_valid_until, "2026-12-31T00:00:00.000Z"); assert.equal(g.p_subscription, "sub-1");
  });
  await t("webhook: other products give nothing", async () => {
    const r = await call(hook, signed({ type: "order.paid", data: { id: "ord-x", total_amount: 4900, product_id: "old-pack", customer: { external_id: USER.id } } }));
    assert.equal(r.statusCode, 202); assert.ok(!rpcCalls.some((c) => c.name === "grant_order"));
  });
  await t("webhook: full refund revokes, partial keeps", async () => {
    await call(hook, signed({ type: "order.refunded", data: { id: "ord-1", total_amount: 1900, refunded_amount: 1900 } }));
    await call(hook, signed({ type: "order.refunded", data: { id: "ord-3", total_amount: 1900, refunded_amount: 500 } }));
    const rv = rpcCalls.filter((c) => c.name === "revoke_order"); assert.equal(rv.length, 1); assert.equal(rv[0].args.p_order, "ord-1");
  });
  await t("webhook: a database error makes Polar retry", async () => {
    rpcResult.grant_order = { data: null, error: { message: "down" } };
    const r = await call(hook, signed({ type: "order.paid", data: { id: "ord-s", total_amount: 1900, product_id: process.env.POLAR_PRODUCT_SINGLE, billing_reason: "purchase", customer: { external_id: USER.id } } }));
    assert.equal(r.statusCode, 500);
  });
  const paid = (id, cents) => signed({ type: "order.paid", data: { id, total_amount: cents, currency: "usd", product_id: process.env.POLAR_PRODUCT_SINGLE, billing_reason: "purchase", product: { name: "Full report" }, customer: { external_id: USER.id, email: USER.email } } });
  const mails = () => fetches.filter((f) => f.url.includes("resend.com")), events = () => fetches.filter((f) => f.url.includes("posthog.com"));
  await t("webhook: a repeated order.paid sends the sale email and PostHog event once", async () => {
    const a = await call(hook, paid("ord-dup", 1900)); const b = await call(hook, paid("ord-dup", 1900));
    assert.equal(a.statusCode, 202); assert.equal(b.statusCode, 202); assert.equal(b.json().repeat, true);
    assert.equal(rpcCalls.filter((c) => c.name === "grant_order").length, 2); // credits: the database ignores the repeat
    assert.equal(mails().length, 1); assert.equal(events().length, 1);
    assert.match(mails()[0].body.subject, /^New sale: USD 19\.00/);
  });
  await t("webhook: a $0 order is marked as a test and sends no revenue event", async () => {
    const r = await call(hook, paid("ord-free", 0)); assert.equal(r.statusCode, 202);
    assert.equal(events().length, 0); assert.match(mails()[0].body.subject, /^Test order \(free\)/);
    assert.ok(rpcCalls.some((c) => c.name === "grant_order")); // the buyer still gets the report
  });
  const mail = require(path.join(ROOT, "api", "email.js"));
  await t("lead email: links to the full fix guide while the paywall is on", async () => {
    const r = await call(mail, mkReq({ method: "POST", url: "/api/email", auth: "", body: { email: "lead@example.com", url: "example.com" } }));
    assert.equal(r.statusCode, 200, r.b);
    const sent = fetches.find((f) => f.url.includes("resend.com"));
    assert.ok(sent && sent.body.html.includes("buy=1"), "no buy link");
  });
  const cron = require(path.join(ROOT, "api", "cron-selfcheck.js"));
  await t("self-check: alerts when the paywall is off", async () => {
    FAKE_CFG = { paywall: false, pro: false, email: true };
    const r = await call(cron, mkReq({ url: "/api/cron-selfcheck", auth: "" }));
    const d = r.json(); assert.equal(d.ok, false); assert.ok(d.problems.some((p) => p.includes("paywall is off")), r.b);
    assert.ok(fetches.some((f) => f.url.includes("resend.com") && f.body.subject.includes("self-check")));
  });
  await t("self-check: quiet when everything is on", async () => {
    FAKE_CFG = { paywall: true, pro: true, email: true };
    const r = await call(cron, mkReq({ url: "/api/cron-selfcheck", auth: "" }));
    const d = r.json(); assert.equal(d.ok, true, r.b); assert.equal(d.score, 95);
    assert.ok(!fetches.some((f) => f.url.includes("resend.com")));
  });
  const cfg = require(path.join(ROOT, "api", "config.js"));
  await t("config: paywall on with products and database, public keys only", async () => {
    const r = mkRes(); cfg({ method: "GET" }, r); const d = JSON.parse(r.b);
    assert.equal(d.paywall, true); assert.equal(d.pro, true); assert.equal(d.prices.pro, 29); assert.match(d.supabase.key, /^sb_publishable_/);
    assert.ok(!r.b.includes("sb_secret") && !r.b.includes("polar_oat"));
  });
  console.log(`\n${n} account tests passed`);
})().catch((e) => { console.error(e); process.exit(1); });
