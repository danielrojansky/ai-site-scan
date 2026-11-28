// Supabase helpers for the account functions. The underscore keeps Vercel from making it an endpoint.
// SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are public (the page uses them too).
// SUPABASE_SECRET_KEY is secret: it bypasses row security, so only server code uses it.

const { createClient } = require("@supabase/supabase-js");
const { env } = require("./_lib");

const SUPABASE_URL = () => env("SUPABASE_URL", "https://rhbhkeuvftqwjjoyzseh.supabase.co");
const SUPABASE_PUBLISHABLE_KEY = () => env("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_u1fOBzszAheUn_atnjFr2Q_9iAHbICs");
const dbReady = () => !!env("SUPABASE_SECRET_KEY");

let admin = null;
function db() {
  if (!admin) admin = createClient(SUPABASE_URL(), env("SUPABASE_SECRET_KEY"), { auth: { persistSession: false, autoRefreshToken: false } });
  return admin;
}

// The signed-in user from "Authorization: Bearer <Supabase access token>", or null.
async function userFrom(req) {
  const h = String(req.headers.authorization || "");
  const token = h.startsWith("Bearer ") ? h.slice(7).trim() : "";
  if (!token || token.length > 4000) return null;
  try {
    const { data, error } = await db().auth.getUser(token);
    if (error || !data || !data.user || !data.user.email) return null;
    return data.user;
  } catch {
    return null;
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Report credits from a paid order. single: 1 report; pro: PRO_REPORTS_PER_MONTH for the paid month.
async function grantFromOrder(o) {
  if (!dbReady()) return "no database";
  const meta = o.metadata && typeof o.metadata === "object" ? o.metadata : {};
  const user = (o.customer && o.customer.external_id) || meta.user_id;
  if (!user || !UUID.test(String(user))) return "no user";
  const pro = o.product_id && o.product_id === productId("pro");
  const single = o.product_id && o.product_id === productId("single");
  if (!pro && !single) return "other product";
  if (pro && !["subscription_create", "subscription_cycle"].includes(o.billing_reason)) return "skipped " + o.billing_reason;
  const end = o.subscription && o.subscription.current_period_end ? Date.parse(o.subscription.current_period_end) : Date.now() + 31 * 864e5;
  const { error } = await module.exports.db().rpc("grant_order", {
    p_user: String(user), p_plan: pro ? "pro" : "single", p_order: String(o.id), p_subscription: o.subscription_id || null,
    p_total: pro ? Number(env("PRO_REPORTS_PER_MONTH", "10")) || 10 : 1,
    p_valid_until: pro ? new Date(end + 3 * 864e5).toISOString() : null,  // 3 days of grace for a late renewal
  });
  return error ? "error " + error.message : pro ? "pro granted" : "single granted";
}

/* Product IDs of the two Polar products (Vercel settings). */
function productId(plan){
  return env(plan === "pro" ? "POLAR_PRODUCT_PRO" : "POLAR_PRODUCT_SINGLE");
}

module.exports = { productId, db, dbReady, userFrom, grantFromOrder, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, UUID };
