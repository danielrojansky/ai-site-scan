// Email a scan report and a ready llms.txt: POST /api/email {email, url, reminder}
// Sends one email now. With reminder=true it also schedules one reminder 2 days later (Resend scheduled send).
// The content comes only from our own scan of the site, so this cannot be used to send any other text.
// Settings: RESEND_API_KEY, EMAIL_FROM (for example "AI Site Scan <report@send.getaisitescan.com>"),
//           SUPPORT_EMAIL (reply-to), SITE_URL.

const { env, send, readJson, limited, clientIp, hostOf, originOf, siteUrl, esc, emailReady, sendEmail, fetchScan, buildLlms, topActions } = require("./_lib");

const EMAIL_RE = /^[^\s@"<>()[\],;:]{1,64}@[a-z0-9.-]+\.[a-z]{2,24}$/i;
const SEV = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };

function layout(inner, footer) {
  return `<!doctype html><html><body style="margin:0;background:#f5f6f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#14171c">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">
<div style="font-weight:700;font-size:16px;margin-bottom:14px">AI Site Scan</div>
<div style="background:#fff;border:1px solid #e3e6eb;border-radius:14px;padding:20px 22px;font-size:15px;line-height:1.55">${inner}</div>
<p style="color:#5d6673;font-size:12.5px;line-height:1.5;margin:14px 4px">${footer}</p>
</div></body></html>`;
}
const button = (href, label) =>
  `<a href="${esc(href)}" style="display:inline-block;background:#3b5bdb;color:#fff;text-decoration:none;font-weight:600;padding:10px 18px;border-radius:10px">${esc(label)}</a>`;

function reportEmail(d, host, links, withReminder) {
  const score = d ? d.score : null;
  const acts = d ? topActions(d, 3) : [];
  const llms = d ? buildLlms(d) : "";
  const list = acts.length
    ? `<ol style="padding-left:20px;margin:6px 0 14px">${acts.map((a) => `<li><b>${esc(a.label)}</b> <span style="color:#5d6673">(${esc(SEV[a.priority] || a.priority)}${a.gain ? `, +${a.gain} points` : ""})</span></li>`).join("")}</ol>`
    : "";
  const inner = `<p style="margin:0 0 10px">Here is your AI readiness report for <b>${esc(host)}</b>.</p>
${score != null ? `<p style="font-size:28px;font-weight:800;margin:6px 0 2px">${score}<span style="font-size:15px;color:#5d6673;font-weight:600"> of 100</span></p>` : ""}
${acts.length ? `<p style="margin:12px 0 4px;font-weight:600">Fix these first:</p>${list}` : ""}
<p style="margin:14px 0">${button(links.report, "Open your full report")}</p>
${links.buy ? `<p style="margin:0 0 14px">Want every fix with ready-to-paste code? <a href="${esc(links.buy)}">Get the full fix guide</a>.</p>` : ""}
${llms ? `<p style="margin:16px 0 4px;font-weight:600">Your llms.txt</p>
<p style="margin:0 0 6px;color:#5d6673">Save this as llms.txt in the root of your site (${esc(host)}/llms.txt). Replace each TODO with a short description.</p>
<pre style="background:#0f1720;color:#d6e2f0;border-radius:10px;padding:12px;font-size:12.5px;white-space:pre-wrap;overflow-wrap:anywhere">${esc(llms)}</pre>` : ""}`;
  const footer = `You get this email because you asked for your report on ${esc(links.site.replace(/^https?:\/\//, ""))}. ${withReminder ? "We will send one reminder in 2 days. " : ""}No other emails. Reply to this email to ask a question or to have your email deleted.`;
  const text = [
    `Your AI readiness report for ${host}`,
    score != null ? `Score: ${score} of 100` : "",
    acts.length ? "Fix these first:\n" + acts.map((a, i) => `${i + 1}. ${a.label} (${SEV[a.priority] || a.priority})`).join("\n") : "",
    `Full report: ${links.report}`,
    links.buy ? `Full fix guide: ${links.buy}` : "",
    llms ? `Your llms.txt (save it as ${host}/llms.txt):\n\n${llms}` : "",
    `You get this email because you asked for your report. ${withReminder ? "We will send one reminder in 2 days. " : ""}Reply to ask a question or to have your email deleted.`,
  ].filter(Boolean).join("\n\n");
  return { subject: score != null ? `${host}: AI readiness ${score} of 100` : `Your AI readiness report for ${host}`, html: layout(inner, footer), text };
}

function reminderEmail(d, host, links) {
  const acts = topActions(d, 3);
  const open = (d.actions || []).length;
  const inner = `<p style="margin:0 0 10px">2 days ago you checked <b>${esc(host)}</b>. It scored <b>${d.score} of 100</b>, with ${open} fix${open === 1 ? "" : "es"} to do.</p>
${acts.length ? `<ol style="padding-left:20px;margin:6px 0 14px">${acts.map((a) => `<li>${esc(a.label)}</li>`).join("")}</ol>` : ""}
<p style="margin:0 0 14px">Did you fix them? Check again to see your new score.</p>
<p style="margin:14px 0">${button(links.report, "Check " + host + " again")}</p>
${links.buy ? `<p style="margin:0">Prefer the shortcut? The <a href="${esc(links.buy)}">full fix guide</a> has every fix with ready-to-paste code, filled in for your site. 7-day refund. If you already bought it, thank you, and ignore this part.</p>` : ""}`;
  const footer = "This is the only reminder. You get it because you ticked the box when you asked for your report. Reply to have your email deleted.";
  const text = [
    `2 days ago you checked ${host}. It scored ${d.score} of 100, with ${open} fixes to do.`,
    acts.map((a, i) => `${i + 1}. ${a.label}`).join("\n"),
    `Check again: ${links.report}`,
    links.buy ? `Full fix guide: ${links.buy}` : "",
    "This is the only reminder. Reply to have your email deleted.",
  ].filter(Boolean).join("\n\n");
  return { subject: `${host}: ${open} AI readiness fix${open === 1 ? "" : "es"} still open?`, html: layout(inner, footer), text };
}

module.exports = async (req, res) => {
  if (req.method !== "POST") return send(res, 405, { error: "Use POST." });
  if (!emailReady()) return send(res, 503, { error: "Email is not set up yet." });
  const ip = clientIp(req);
  if (limited("email:ip:" + ip, 5, 60 * 60 * 1000)) return send(res, 429, { error: "Too many emails from your network. Try again later." });

  let body;
  try {
    body = await readJson(req, 5000);
  } catch (e) {
    return send(res, e.status || 400, { error: e.message });
  }
  const email = String(body.email || "").trim().toLowerCase();
  const host = hostOf(body.url);
  if (email.length > 254 || !EMAIL_RE.test(email)) return send(res, 400, { error: "Enter a valid email address." });
  if (!host) return send(res, 400, { error: "Scan a site first." });
  if (limited("email:to:" + email, 3, 24 * 60 * 60 * 1000)) return send(res, 429, { error: "This address already got its reports today." });

  const scan = await fetchScan(host, { origin: originOf(req) });
  const d = scan.ok ? scan.data : null;
  const site = siteUrl();
  const paywall = !!(env("POLAR_ORG_ID") && env("POLAR_CHECKOUT_SINGLE"));
  const links = {
    site,
    report: `${site}/?url=${encodeURIComponent(host)}&utm_source=email&utm_medium=email&utm_campaign=report`,
    buy: paywall ? `${site}/?url=${encodeURIComponent(host)}&buy=1&utm_source=email&utm_medium=email&utm_campaign=report` : "",
  };
  const wantReminder = body.reminder === true && !!d;
  const replyTo = env("SUPPORT_EMAIL") || undefined;

  const first = reportEmail(d, host, links, wantReminder);
  const r1 = await sendEmail({ to: email, ...first, replyTo, tags: [{ name: "kind", value: "report" }] });
  if (!r1.ok) {
    console.error("email send failed:", r1.error);
    return send(res, 502, { error: "Could not send the email. Try again later." });
  }

  let reminder = false;
  if (wantReminder) {
    const remindLinks = {
      ...links,
      report: links.report.replace("campaign=report", "campaign=reminder"),
      buy: links.buy ? links.buy.replace("campaign=report", "campaign=reminder") : "",
    };
    const second = reminderEmail(d, host, remindLinks);
    const at = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
    const r2 = await sendEmail({ to: email, ...second, scheduledAt: at, replyTo, tags: [{ name: "kind", value: "reminder" }] });
    reminder = r2.ok;
    if (!r2.ok) console.error("reminder schedule failed:", r2.error);
  }
  console.log(`email host=${host} scan=${scan.ok ? "ok" : scan.status} reminder=${reminder}`);
  return send(res, 200, { ok: true, reminder });
};

module.exports.reportEmail = reportEmail;
module.exports.reminderEmail = reminderEmail;
