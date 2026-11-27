// Unlock the full fix guide with a Polar license key: POST /api/unlock {key, host, activation_id?}
// One activation = one site. The activation label is the site's host (example.com).
// Settings: POLAR_ORG_ID (required), POLAR_SERVER=sandbox (for tests),
//           POLAR_ACCESS_TOKEN (optional: reuses a site's activation from a new browser),
//           POLAR_BENEFIT_IDS (optional, comma list: only keys from these benefits unlock).

const { env, send, readJson, limited, clientIp, hostOf, polar } = require("./_lib");

const ERR = {
  404: "This key was not found. Check it and try again.",
  403: "This key has no sites left, or it was refunded or disabled.",
  422: "This key does not look right. Copy it again from your email or the Polar portal.",
};

function benefitOk(data) {
  const ids = env("POLAR_BENEFIT_IDS").split(",").map((s) => s.trim()).filter(Boolean);
  return !ids.length || (data && ids.includes(data.benefit_id));
}

function answer(res, data, host) {
  return send(res, 200, {
    ok: true,
    host,
    activation_id: data.activation ? data.activation.id : null,
    sites_limit: data.limit_activations ?? null,
    expires_at: data.expires_at ?? null,
  });
}

module.exports = async (req, res) => {
  if (req.method !== "POST") return send(res, 405, { error: "Use POST." });
  const org = env("POLAR_ORG_ID");
  if (!org) return send(res, 503, { error: "Payments are not set up yet." });
  if (limited("unlock:" + clientIp(req), 20, 10 * 60 * 1000)) return send(res, 429, { error: "Too many tries. Wait a few minutes and try again." });

  let body;
  try {
    body = await readJson(req, 10000);
  } catch (e) {
    return send(res, e.status || 400, { error: e.message });
  }
  const key = String(body.key || "").trim(), host = hostOf(body.host);
  if (!key || key.length > 200) return send(res, 400, { error: "Paste your license key." });
  if (!host) return send(res, 400, { error: "Scan a site first, then unlock it." });

  try {
    // 1. This browser already activated the key for this site: validate that activation.
    if (body.activation_id) {
      const v = await polar("/v1/customer-portal/license-keys/validate", { key, organization_id: org, activation_id: String(body.activation_id) });
      if (v.ok && v.data && v.data.activation && v.data.activation.label === host && benefitOk(v.data)) return answer(res, v.data, host);
    }

    // 2. With a Polar token, reuse an activation this key already has for the site (a new browser).
    let activationId = null;
    if (env("POLAR_ACCESS_TOKEN")) {
      const v = await polar("/v1/customer-portal/license-keys/validate", { key, organization_id: org });
      if (v.ok && v.data && v.data.id) {
        if (!benefitOk(v.data)) return send(res, 403, { error: "This key is for a different product." });
        const k = await polar(`/v1/license-keys/${encodeURIComponent(v.data.id)}`, null, { auth: true, method: "GET" });
        const found = k.ok && k.data && Array.isArray(k.data.activations) ? k.data.activations.find((a) => a && a.label === host) : null;
        if (found) activationId = found.id;
      }
    }

    // 3. Otherwise use one site of the key.
    if (!activationId) {
      const a = await polar("/v1/customer-portal/license-keys/activate", { key, organization_id: org, label: host, meta: { host } });
      if (!a.ok) return send(res, ERR[a.status] ? (a.status === 422 ? 400 : a.status) : 502, { error: ERR[a.status] || "Could not check the key. Try again in a minute." });
      activationId = a.data && a.data.id;
    }

    const v = await polar("/v1/customer-portal/license-keys/validate", { key, organization_id: org, activation_id: activationId });
    if (!v.ok || !v.data || v.data.status !== "granted") return send(res, ERR[v.status] ? v.status : 403, { error: ERR[v.status] || ERR[403] });
    if (!benefitOk(v.data)) return send(res, 403, { error: "This key is for a different product." });
    console.log(`unlock host=${host} limit=${v.data.limit_activations ?? "none"}`);
    return answer(res, v.data, host);
  } catch (e) {
    console.error("unlock failed:", (e && e.stack) || e);
    return send(res, 502, { error: "Could not check the key. Try again in a minute." });
  }
};
