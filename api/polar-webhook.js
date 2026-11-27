// Polar webhook: POST /api/polar-webhook
// On "order.paid": emails you the sale and sends a "purchase" event to PostHog (server side, so ad blockers cannot hide it).
// Settings: POLAR_WEBHOOK_SECRET (required), OWNER_EMAIL (sale alerts), RESEND_API_KEY + EMAIL_FROM, PUBLIC_POSTHOG_KEY.
// In Polar: Settings > Webhooks > Add endpoint, format Raw, URL https://getaisitescan.com/api/polar-webhook, event order.paid.

const { env, send, readRaw, verifyWebhook, capture, sendEmail, esc } = require("./_lib");

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

  if (evt && evt.type === "order.paid" && evt.data) {
    const f = orderFacts(evt.data);
    const owner = env("OWNER_EMAIL");
    const jobs = [
      capture("purchase", f.distinct, { order_id: f.id, product: f.product, amount: f.amount, revenue: f.amount, currency: f.currency, country: f.country, ...f.utm }),
    ];
    if (owner) {
      const src = Object.keys(f.utm).length ? Object.entries(f.utm).map(([k, v]) => `${k}=${v}`).join(", ") : "no UTM tags";
      jobs.push(
        sendEmail({
          to: owner,
          subject: `New sale: ${f.currency} ${f.amount.toFixed(2)}, ${f.product}`,
          html: `<p><b>New sale on AI Site Scan.</b></p><ul><li>Product: ${esc(f.product)}</li><li>Amount: ${esc(f.currency)} ${f.amount.toFixed(2)}</li><li>Customer: ${esc(f.name)} ${esc(f.email)}</li><li>Country: ${esc(f.country || "unknown")}</li><li>Source: ${esc(src)}</li><li>Order: ${esc(f.id)}</li></ul>`,
          text: `New sale on AI Site Scan\nProduct: ${f.product}\nAmount: ${f.currency} ${f.amount.toFixed(2)}\nCustomer: ${f.name} ${f.email}\nCountry: ${f.country || "unknown"}\nSource: ${src}\nOrder: ${f.id}`,
          tags: [{ name: "kind", value: "sale" }],
        })
      );
    }
    await Promise.allSettled(jobs);
    console.log(`order.paid id=${f.id} amount=${f.amount} ${f.currency} product=${f.product}`);
  }
  return send(res, 202, { ok: true });
};

module.exports.orderFacts = orderFacts;
