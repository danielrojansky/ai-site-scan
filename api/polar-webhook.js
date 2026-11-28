// Polar webhook: POST /api/polar-webhook
// On "order.paid": emails you the sale and sends a "purchase" event to PostHog (server side, so ad blockers cannot hide it).
// Settings: POLAR_WEBHOOK_SECRET (required), OWNER_EMAIL (sale alerts), RESEND_API_KEY + EMAIL_FROM, PUBLIC_POSTHOG_KEY.
// In Polar: Settings > Webhooks > Add endpoint, format Raw, URL https://getaisitescan.com/api/polar-webhook, events order.paid and order.refunded.
// order.paid also gives report credits (see grantFromOrder). A 500 makes Polar retry.

const { env, send, readRaw, verifyWebhook, capture, sendEmail, esc } = require("./_lib");
const { db, dbReady, grantFromOrder } = require("./_db");



/* Polar delivers at least once, so the same order.paid can come twice. Credits are already safe
   (grant_order ignores a repeated order). This claims the side effects (sale email, PostHog revenue)
   once per order: true = first time, false = seen before. If the database is down, send them anyway. */
async function firstTime(type, orderId) {
  if (!dbReady()) return true;
  const { data, error } = await db().from("webhook_events")
    .upsert({ id: `${type}:${orderId}`, type }, { onConflict: "id", ignoreDuplicates: true }).select("id");
  if (error) { console.error(`webhook_events ${type} ${orderId}: ${error.message}`); return true; }
  return Array.isArray(data) && data.length > 0;
}

const UTM = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"];

function orderFacts(o) {
  const cents = o.total_amount ?? o.amount ?? o.net_amount ?? 0;
  const amount = Math.round(Number(cents) || 0) / 100;
  const currency = String(o.currency || "usd").toUpperCase();
  const product = (o.product && o.product.name) || (Array.isArray(o.items) && o.items[0] && o.items[0].label) || "Fix guide";
  const meta = o.metadata && typeof o.metadata === "object" ? o.metadata : {};
  const customer = o.customer || {};
  const country = (o.billing_address && o.billing_address.country) || (customer.billing_address && customer.billing_address.country) || "";
  const utm = {};
  for (const k of UTM) if (meta[k]) utm[k] = String(meta[k]).slice(0, 200);
  const distinct = meta.reference_id ? String(meta.reference_id) : o.customer_id ? "polar:" + o.customer_id : "polar-order:" + o.id;
  return { id: o.id, amount, currency, product, email: customer.email || "", name: customer.name || "", country, utm, distinct };
}

module.exports = async (req, res) => {
  if (req.method !== "POST") return send(res, 405, { error: "Use POST." });
  const secret = env("POLAR_WEBHOOK_SECRET");
  if (!secret) return send(res, 503, { error: "Webhook secret is not set." });

  let raw;
  try {
    raw = await readRaw(req, 1000000);
  } catch (e) {
    return send(res, e.status || 400, { error: e.message });
  }
  if (!verifyWebhook(raw, req.headers, secret)) return send(res, 403, { error: "Bad signature." });

  let evt;
  try {
    evt = JSON.parse(raw.toString("utf8"));
  } catch {
    return send(res, 400, { error: "Not JSON." });
  }

  if (evt && evt.type === "order.refunded" && evt.data) {
    const o = evt.data;
    if (dbReady() && Number(o.refunded_amount || 0) >= Number(o.total_amount || 0)) {
      const { error } = await db().rpc("revoke_order", { p_order: String(o.id) });
      console.log(`order.refunded id=${o.id} revoked=${!error}${error ? " " + error.message : ""}`);
    } else console.log(`order.refunded id=${o.id} partial, kept`);
  }

  if (evt && evt.type === "order.paid" && evt.data) {
    const granted = await grantFromOrder(evt.data);
    if (granted.startsWith("error")) { console.error(`order.paid id=${evt.data.id} ${granted}`); return send(res, 500, { error: "Retry later." }); }
    const f = orderFacts(evt.data);
    if (!(await firstTime("order.paid", f.id))) {
      console.log(`order.paid id=${f.id} repeat, credits=${granted}, no email or event`);
      return send(res, 202, { ok: true, repeat: true });
    }
    const test = f.amount === 0; // a 100% discount code: real for the buyer, not revenue
    const owner = env("OWNER_EMAIL");
    const jobs = test ? [] : [
      capture("purchase", f.distinct, { order_id: f.id, product: f.product, amount: f.amount, revenue: f.amount, currency: f.currency, country: f.country, ...f.utm }),
    ];
    if (owner) {
      const src = Object.keys(f.utm).length ? Object.entries(f.utm).map(([k, v]) => `${k}=${v}`).join(", ") : "no UTM tags";
      jobs.push(
        sendEmail({
          to: owner,
          subject: `${test ? "Test order (free)" : "New sale"}: ${f.currency} ${f.amount.toFixed(2)}, ${f.product}`,
          html: `<p><b>${test ? "Free test order (100% discount) on AI Site Scan. Not revenue." : "New sale on AI Site Scan."}</b></p><ul><li>Product: ${esc(f.product)}</li><li>Amount: ${esc(f.currency)} ${f.amount.toFixed(2)}</li><li>Customer: ${esc(f.name)} ${esc(f.email)}</li><li>Country: ${esc(f.country || "unknown")}</li><li>Source: ${esc(src)}</li><li>Order: ${esc(f.id)}</li></ul>`,
          text: `${test ? "Free test order (100% discount) on AI Site Scan. Not revenue." : "New sale on AI Site Scan"}\nProduct: ${f.product}\nAmount: ${f.currency} ${f.amount.toFixed(2)}\nCustomer: ${f.name} ${f.email}\nCountry: ${f.country || "unknown"}\nSource: ${src}\nOrder: ${f.id}`,
          tags: [{ name: "kind", value: test ? "test_order" : "sale" }],
        })
      );
    }
    await Promise.allSettled(jobs);
    console.log(`order.paid id=${f.id} amount=${f.amount} ${f.currency} product=${f.product} credits=${granted}${test ? " test" : ""}`);
  }
  return send(res, 202, { ok: true });
};

module.exports.orderFacts = orderFacts;
module.exports.firstTime = firstTime;
module.exports.grantFromOrder = grantFromOrder;
