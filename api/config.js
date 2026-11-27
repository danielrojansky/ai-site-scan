// Public settings for the page: GET /api/config
// Only public values (no secrets). Each feature turns on when its settings exist in Vercel:
//   paywall  <- SUPABASE_SECRET_KEY + POLAR_PRODUCT_SINGLE (pro also needs POLAR_PRODUCT_PRO)
//   google   <- LOGIN_GOOGLE=1 after Google login is set up in Supabase
//   email    <- RESEND_API_KEY + EMAIL_FROM
//   posthog  <- PUBLIC_POSTHOG_KEY (+ PUBLIC_POSTHOG_HOST)
//   ads      <- GADS_ID (+ conversion labels)

const { env, send } = require("./_lib");
const { dbReady, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY } = require("./_db");

module.exports = (req, res) => {
  const plans = !!(dbReady() && env("POLAR_PRODUCT_SINGLE"));
  const cfg = {
    site: env("SITE_URL", "https://getaisitescan.com"),
    support: env("SUPPORT_EMAIL", "support@getaisitescan.com"),
    paywall: plans,                                  // accounts + products ready: the full guide needs a credit
    pro: !!(plans && env("POLAR_PRODUCT_PRO")),
    prices: { single: Number(env("PRICE_SINGLE", "19")) || 19, pro: Number(env("PRICE_PRO", "29")) || 29 },
    pro_reports: Number(env("PRO_REPORTS_PER_MONTH", "10")) || 10,
    supabase: { url: SUPABASE_URL(), key: SUPABASE_PUBLISHABLE_KEY(), google: env("LOGIN_GOOGLE", "") === "1" },
    email: !!(env("RESEND_API_KEY") && env("EMAIL_FROM")),
    posthog: env("PUBLIC_POSTHOG_KEY") ? { key: env("PUBLIC_POSTHOG_KEY"), host: env("PUBLIC_POSTHOG_HOST", "https://us.i.posthog.com") } : null,
    ads: env("GADS_ID")
      ? { id: env("GADS_ID"), purchase: env("GADS_PURCHASE_LABEL"), checkout: env("GADS_CHECKOUT_LABEL"), lead: env("GADS_LEAD_LABEL") }
      : null,
  };
  send(res, 200, cfg, { "Cache-Control": "public, max-age=60, s-maxage=300" });
};
