// Daily self-check: GET /api/cron-selfcheck (Vercel cron, 05:00 UTC = 07:00 in Israel in winter).
// Scans our own site with a fresh scan, checks /api/config and the database. Emails OWNER_EMAIL when
// the score drops below SELFCHECK_MIN_SCORE (default 90), something does not answer, or a paid
// feature is off (paywall, Pro, email): a missing setting must not stop sales without anyone noticing.
// Settings: CRON_SECRET (Vercel sends it as "Authorization: Bearer ..."), OWNER_EMAIL, SITE_URL.

const { env, send, limited, clientIp, hostOf, originOf, siteUrl, esc, fetchScan, sendEmail } = require("./_lib");
const { db, dbReady } = require("./_db");

module.exports = async (req, res) => {
  const secret = env("CRON_SECRET");
  if (secret && req.headers.authorization !== `Bearer ${secret}`) return send(res, 401, { error: "Unauthorized." });
  if (!secret && limited("cron:" + clientIp(req), 3, 60 * 60 * 1000)) return send(res, 429, { error: "Too many runs." });

  const origin = originOf(req);
  const site = hostOf(siteUrl()) || hostOf(origin);
  const min = Number(env("SELFCHECK_MIN_SCORE", "90")) || 90;
  const problems = [];

  const scan = await fetchScan(site, { origin, fresh: true });
  const score = scan.ok ? scan.data.score : null;
  if (!scan.ok) problems.push(`The scan of ${site} failed (HTTP ${scan.status || "no answer"}).`);
  else if (score < min) problems.push(`${site} scored ${score}, below ${min}.`);

  try {
    const c = await fetch(origin + "/api/config", { signal: AbortSignal.timeout(8000) });
    if (!c.ok) problems.push(`/api/config answered HTTP ${c.status}.`);
    else {
      const cfg = await c.json().catch(() => ({}));
      if (!cfg.paywall) problems.push("The paywall is off: check SUPABASE_SECRET_KEY and POLAR_PRODUCT_SINGLE in Vercel.");
      else if (!cfg.pro) problems.push("Pro is off: check POLAR_PRODUCT_PRO in Vercel.");
      if (!cfg.email) problems.push("Email is off: check RESEND_API_KEY and EMAIL_FROM in Vercel.");
    }
  } catch {
    problems.push("/api/config did not answer.");
  }

  if (dbReady()) {
    try {
      const { error } = await db().from("grants").select("id", { count: "exact", head: true });
      if (error) problems.push(`The database answered with an error: ${error.message}`);
    } catch (e) {
      problems.push(`The database did not answer: ${(e && e.message) || e}`);
    }
  }

  const owner = env("OWNER_EMAIL");
  if (problems.length && owner) {
    await sendEmail({
      to: owner,
      subject: `AI Site Scan self-check: ${problems.length} problem${problems.length > 1 ? "s" : ""}`,
      html: `<p><b>The daily self-check found ${problems.length} problem${problems.length > 1 ? "s" : ""}.</b></p><ul>${problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul><p>Check it: <a href="${esc(siteUrl())}/?url=${encodeURIComponent(site)}">${esc(site)}</a></p>`,
      text: `The daily self-check found ${problems.length} problem(s):\n- ${problems.join("\n- ")}\n\nCheck it: ${siteUrl()}/?url=${site}`,
      tags: [{ name: "kind", value: "selfcheck" }],
    });
  }
  console.log(`selfcheck site=${site} score=${score} problems=${problems.length}`);
  return send(res, 200, { ok: !problems.length, site, score, problems });
};
