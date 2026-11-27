// Automatic key delivery after checkout: GET /api/claim?checkout_id=...
// Polar sends the buyer back to /?paid=single&checkout_id={CHECKOUT_ID}. The page calls this
// endpoint, gets the buyer's new license key and unlocks the guide with no copy and paste.
//   1. GET  /v1/checkouts/{id}                 (organization token, scope checkouts:read)
//   2. POST /v1/customer-sessions/             (organization token, scope customer_sessions:write)
//   3. GET  /v1/customer-portal/license-keys/  (the customer session token from step 2)
// Settings: POLAR_ORG_ID and POLAR_ACCESS_TOKEN. Without the token the endpoint answers 503
// and the page asks the buyer to paste the key from Polar's email or customer portal.
// The checkout ID works like a receipt link: only the buyer gets it, in the redirect URL.

const { env, send, limited, clientIp, siteUrl, polar } = require("./_lib");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const later = (a, b) => (Date.parse(a) || 0) >= (Date.parse(b) || 0) - 60 * 1000;

module.exports = async (req, res) => {
  if (req.method !== "GET") return send(res, 405, { error: "Use GET." });
  const org = env("POLAR_ORG_ID");
  if (!org || !env("POLAR_ACCESS_TOKEN")) return send(res, 503, { error: "Paste the license key from Polar's email.", manual: true });
  if (limited("claim:" + clientIp(req), 40, 10 * 60 * 1000)) return send(res, 429, { error: "Too many tries. Wait a few minutes." });

  const id = String(new URL(req.url, "http://local").searchParams.get("checkout_id") || "").trim();
  if (!UUID.test(id)) return send(res, 400, { error: "This link has no valid checkout ID." });

  try {
    const c = await polar(`/v1/checkouts/${id}`, null, { auth: true, method: "GET" });
    if (c.status === 404) return send(res, 404, { error: "This checkout was not found." });
    if (!c.ok || !c.data) return send(res, 502, { error: "Could not reach Polar. Trying again…", pending: true });
    const ck = c.data;
    if (ck.organization_id && ck.organization_id !== org) return send(res, 404, { error: "This checkout was not found." });
    if (ck.status !== "succeeded" || !ck.customer_id) {
      const pending = ck.status === "open" || ck.status === "confirmed" || (ck.status === "succeeded" && !ck.customer_id);
      return send(res, 200, { ok: false, pending, status: ck.status });
    }

    const s = await polar("/v1/customer-sessions/", { customer_id: ck.customer_id, return_url: siteUrl() + "/" }, { auth: true });
    if (!s.ok || !s.data || !s.data.token) return send(res, 502, { error: "Could not reach Polar. Trying again…", pending: true });

    const k = await polar("/v1/customer-portal/license-keys/?limit=100", null, { auth: s.data.token, method: "GET" });
    const benefits = env("POLAR_BENEFIT_IDS").split(",").map((x) => x.trim()).filter(Boolean);
    const keys = (k.ok && k.data && Array.isArray(k.data.items) ? k.data.items : [])
      .filter((x) => x && x.key && x.status === "granted" && (!benefits.length || benefits.includes(x.benefit_id)))
      .filter((x) => !ck.created_at || later(x.created_at, ck.created_at))  // the key from this order, not an older one
      .sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
    // Polar grants the key a few seconds after the payment: the page asks again.
    if (!keys.length) return send(res, 200, { ok: false, pending: true, status: "granting", portal: s.data.customer_portal_url || null });

    const key = keys[0];
    console.log(`claim checkout=${id.slice(0, 8)} limit=${key.limit_activations ?? "none"}`);
    return send(res, 200, { ok: true, key: key.key, sites_limit: key.limit_activations ?? null, portal: s.data.customer_portal_url || null });
  } catch (e) {
    console.error("claim failed:", (e && e.stack) || e);
    return send(res, 502, { error: "Could not reach Polar. Trying again…", pending: true });
  }
};
