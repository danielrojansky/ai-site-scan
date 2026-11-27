// Public settings for the page: GET /api/config
// Only public values (no secrets). Each feature turns on when its settings exist in Vercel:
//   paywall  <- POLAR_ORG_ID + POLAR_CHECKOUT_SINGLE
//   email    <- RESEND_API_KEY + EMAIL_FROM
//   posthog  <- PUBLIC_POSTHOG_KEY (+ PUBLIC_POSTHOG_HOST)
//   ads      <- GADS_ID (+ conversion labels)

const { env, send } = require("./_lib");

// POLAR_PORTAL_URL may be the full portal address or only the organization slug
// ("daniel-consulting" becomes https://polar.sh/daniel-consulting/portal).
function portalUrl() {
  const v = env("POLAR_PORTAL_URL");
  if (!v || /^https?:\/\//i.test(v)) return v;
  const slug = v.replace(/^\/+|\/+$/g, "").replace(/\/portal$/i, "");
  if (!/^[a-z0-9][a-z0-9-]*$/i.test(slug)) return "";
  return `${env("POLAR_SERVER") === "sandbox" ? "https://sandbox.polar.sh" : "https://polar.sh"}/${slug}/portal`;
}

module.exports = (req, res) => {
  const single = env("POLAR_CHECKOUT_SINGLE"), pack = env("POLAR_CHECKOUT_PACK");
  const cfg = {
    site: env("SITE_URL", "https://getaisitescan.com"),
    support: env("SUPPORT_EMAIL", "support@getaisitescan.com"),
    paywall: !!(env("POLAR_ORG_ID") && single),
    checkout: { single, pack },
    prices: { single: Number(env("PRICE_SINGLE", "19")) || 19, pack: Number(env("PRICE_PACK", "49")) || 49 },
    portal: portalUrl(),
    email: !!(env("RESEND_API_KEY") && env("EMAIL_FROM")),
    posthog: env("PUBLIC_POSTHOG_KEY") ? { key: env("PUBLIC_POSTHOG_KEY"), host: env("PUBLIC_POSTHOG_HOST", "https://us.i.posthog.com") } : null,
    ads: env("GADS_ID")
      ? { id: env("GADS_ID"), purchase: env("GADS_PURCHASE_LABEL"), checkout: env("GADS_CHECKOUT_LABEL"), lead: env("GADS_LEAD_LABEL") }
      : null,
  };
  send(res, 200, cfg, { "Cache-Control": "public, max-age=60, s-maxage=300" });
};
