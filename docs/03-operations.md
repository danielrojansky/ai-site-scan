# AI Site Scan: operations

## Deploy

- Push to `main` on GitHub. Vercel builds and deploys to production automatically (about 30 to 70 seconds).
- Production URL: https://getaisitescan.com (the old https://ai-site-scan.vercel.app still works)
- Deployment protection is off, so the site is public.
- Do **not** use "Add New > Import" in Vercel for this repo. It creates a second project. Git is already connected in Project > Settings > Git.

## Configuration (`vercel.json`)

| Setting | Value | Why |
|---|---|---|
| `framework` | `null` (Other) | Stops Vercel from picking the Python framework preset, which fails with `PYTHON_ENTRYPOINT_NOT_FOUND` |
| `api/scan.py` | 60 s, 1024 MB | Full audit plus 14 bot probes, all in parallel. The scan itself stops at 50 s |
| `api/agent.py` | 40 s, 1024 MB | About 25 small requests and 5 DNS lookups. The check stops at 28 s |
| `api/webmcp.js` | 40 s, 2048 MB, includes `node_modules/@sparticuz/chromium/bin/**` | Chrome plus the page's own scripts. It waits up to 20 s for the page to settle |
| `api/shot.js` | 45 s, 2048 MB, includes `node_modules/@sparticuz/chromium/bin/**` | Chrome needs memory and its binary files |

Environment variables (optional):
- `RATE_MAX`: scans per IP per 10 minutes (default 10).
- `AGENT_RATE_MAX`: agent readiness checks per IP per 10 minutes (default 20).

Version 3 settings. Each feature stays off until its settings exist. Add them in Vercel > Project > Settings > Environment Variables, then redeploy. The secrets are set for Production only, so previews have no paywall.

| Variable | Used by | Value |
|---|---|---|
| `SITE_URL` | all | `https://getaisitescan.com` |
| `SUPPORT_EMAIL` | config | `support@getaisitescan.com` |
| `SUPABASE_SECRET_KEY` | account, webhook, config | Supabase secret key (project ai-site-scan). Turns accounts and the paywall on |
| `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | account, config | Optional: defaults point to the ai-site-scan project |
| `POLAR_PRODUCT_SINGLE`, `POLAR_PRODUCT_PRO` | account, webhook, email, config | Product IDs of "Full report" ($19 once, `af3b2092-7a79-4eec-a833-3c7884af041c`) and "Pro" ($29 a month, `dfb0dc4a-8115-4be4-b36c-6779d4340f36`) |
| `POLAR_ACCESS_TOKEN` | account | Organization token with scopes `checkouts:write`, `customer_sessions:write`, `orders:read` |
| `POLAR_WEBHOOK_SECRET` | polar-webhook | Secret of the webhook endpoint (events order.paid, order.refunded) |
| `POLAR_SERVER` | all Polar calls | `sandbox` for tests, empty for production |
| `POLAR_API_VERSION` | all Polar calls | Optional, default `2026-10` |
| `PRICE_SINGLE`, `PRICE_PRO`, `PRO_REPORTS_PER_MONTH` | config, webhook | Optional, default 19, 29 and 10. Must match Polar |
| `LOGIN_GOOGLE` | config | `1` after Google login is set up in Supabase |
| `RESEND_API_KEY`, `EMAIL_FROM` | email, webhook, cron | Resend key and a sender on a verified domain |
| `OWNER_EMAIL` | webhook, cron | Where sale and self-check emails go |
| `PUBLIC_POSTHOG_KEY`, `PUBLIC_POSTHOG_HOST` | config | PostHog project key and host |
| `GADS_ID`, `GADS_PURCHASE_LABEL`, `GADS_CHECKOUT_LABEL`, `GADS_LEAD_LABEL` | config | Google Ads tag and conversion labels |
| `CRON_SECRET` | cron-selfcheck | Random string. Vercel sends it to the cron call. Not set yet: without it anyone can start the self-check (3 runs per hour per address) |
| `SELFCHECK_MIN_SCORE` | cron-selfcheck | Optional, default 90 |

## Accounts and payments (v3)

- Login: Supabase Auth, a 6-digit email code and Google. The page loads supabase-js only when needed, from our own copy `/vendor/supabase-js-2.119.2.js` with an integrity hash (cached for 1 year). Supabase sends its emails through Resend (custom SMTP). The templates "Confirm sign up" and "Magic link or OTP" both show `{{ .Token }}`.
- Database (Supabase project `ai-site-scan`, us-east-1): `grants` (credits from Polar orders), `unlocks` (a site unlocked by one credit, with its rescans), `reports` (saved reports). Row security: users read only their own rows. The server writes through `grant_order`, `revoke_order` and `claim_report` (see `supabase/migrations/`).
- Products in Polar: "Full report" $19 one-time (1 credit; the site gets 1 rescan within 30 days) and "Pro" $29 a month (10 credits per paid month, valid until the period end + 3 days; rescans of its sites are included). No benefits needed.
- Flow: log in > `/api/account?do=checkout` creates a Polar checkout for this user (`external_customer_id` = user id, email filled in) > success URL `/?paid=<plan>&checkout_id={CHECKOUT_ID}` > the page calls `sync` (reads this user's paid orders, in case the webhook is late) and `me` until a credit shows > `unlock` uses it for the scan on screen > `save` stores the guide and emails it with the guide attached.
- Webhook `order.paid` gives credits (a database error answers 500 so Polar retries). `order.refunded` (full) removes the order's credits and sites.
- If a webhook is missed: the return from checkout reads the buyer's paid orders (`sync`), and so does opening My reports (once per visit).
- Test with the 100% discount code `F53KRI8C` (shown as REVIEW100 in Polar). Never test with a real card. Polar asks for a card on the Pro subscription even at $0, so Pro cannot be tested this way.
- "Scan again" in My reports uses the site's included rescan in one click (the page remembers the request for 2 minutes).
- Analytics across the checkout: the page sends its PostHog ID and first-visit `utm_*` tags with the checkout; Polar keeps them in the order metadata, and the webhook uses them for the PostHog purchase event and the sale email.
- Support: Polar expects a reply within 48 hours when it loops us into a customer thread.

## Run and test locally

```bash
pip install -r requirements.txt
npm install
npx vercel dev            # serves index.html and both functions
```

Notes for local tests:
- The engine refuses private addresses, so local test pages on 127.0.0.1 need the IP check patched in a test script only. Never ship that patch.
- For the screenshot and WebMCP functions outside Vercel, set `CHROME_PATH` to a local Chrome (they are plain handlers: a small Node server can call them).
- A browser test with Playwright can mock `/api/scan` and `/api/shot` and run the whole UI flow (scan, export, history, reopen, new scan).
- Offline tests for the checks: `python -m unittest discover -s tests` (108 tests, under a second, no network).
- Account, payment and email tests: `node tests/test_accounts.js` (19 tests with a fake Supabase, Polar, Resend and PostHog).
- Browser test of the paid flow: add `127.0.0.1 local.aisitescan.test` to `/etc/hosts`, run `python3 tests/devserver.py . 8765`, then `python3 tests/ui_flow.py` (Playwright with Chromium).
- A small dev server (a `ThreadingHTTPServer` that serves `index.html` and routes `/api/scan` to `api/scan.handler`) is the quickest way to test the real scan and the page together, without `vercel dev`. Set `RATE_MAX=1000` for it.
- To compare engine speed or scores, call `scan.scan(url)` in a Python shell. `scan.scan` runs the parallel path.

## Monitoring

- Vercel dashboard: Project `ai-site-scan` > Logs. Python errors are printed with a traceback to stderr. The Pro plan keeps 1 day of runtime logs.
- Each scan logs one access line. Payments log `order.paid id=... product=... credits=...` (a $0 order ends with `test`).
- Daily self-check at 05:00 UTC (`/api/cron-selfcheck`): a fresh scan of our own site, `/api/config` and the database. It emails `OWNER_EMAIL` when the score is under 90, something does not answer, or the paywall, Pro or email is off.
- PostHog (US): page views, scans, paywall views, logins, checkouts and purchases. A $0 order sends no purchase event.
- Polar: orders, customers, refunds. Supabase: Authentication > Users, Table Editor (`grants`, `unlocks`, `reports`, `webhook_events`).

## Troubleshooting (problems already seen and fixed)

| Symptom | Cause | Fix |
|---|---|---|
| Build fails with `PYTHON_ENTRYPOINT_NOT_FOUND` | Project imported with the Python framework preset | `framework: null` in `vercel.json` |
| `FUNCTION_INVOCATION_FAILED` for unknown domains | On Vercel, a failed DNS lookup raises a plain `OSError`, not `socket.gaierror` | `socket.getaddrinfo` wrapper in `scan.py` converts it |
| Screenshot 502: "require() of ES Module" | `puppeteer-core` 25 is ESM only | Load it with `await import("puppeteer-core")` |
| Screenshot never loads (old version) | Free WordPress mShots service did not work from the user's phone | Replaced with own headless Chrome function |
| Page shows nothing, console "Invalid or unexpected token" | The text `</script>` inside the inline script ended the script tag | Write it as `<\/script>` |
| Score 0 and "HTTP 403" | The site blocks all cloud traffic (for example nytimes.com) | Expected. The report says "The site blocked the scan" |
| Page answers HTTP 202 and the AI view is empty (for example amazon.com) | AWS WAF bot check page | Expected. Detected as "The site blocked the scan" |
| Scan slow on blocked sites (old version) | The fetcher retried 429 and 5xx answers 3 times with backoff | The async fetcher does not retry. Only the page request retries once |
| "Sitemap does not work" for a site that has one | The site refuses our cloud server (403, 429) or times out | Not reported: only 404 and non-sitemap answers are |
| An agent readiness row says it could not check, or DNS-AID says the lookup failed | The site refused our cloud server for that path (403, 429, a timeout), or both DNS-over-HTTPS services failed | Expected now and then. The row is left out of the count. Rescan. Test the path with curl from another network |
| The WebMCP row says "The browser check did not run" | The Chrome function failed to start or crashed | The static result stays. Open the panel again after a rescan |
| The WebMCP row says the page was too busy | A very heavy page froze in our small Chrome before it registered any tool (vercel.com did, before the fix that sends tools out as they register) | The row is left out of the count. Expected on the heaviest pages |
| Googlebot, Bingbot or Applebot show "Refused" | The site checks bot IP addresses and refuses our imitation | Normal. Not reported as a problem. The note under the bot cards says so |

## Versioning and release

- Version number is `APP_VERSION` in `api/scan.py` and in `index.html` (footer shows it). Keep them equal. Minor number up for each production push, major for big changes.
- Safe release: push a branch, open the Vercel preview URL, check `/api/scan?url=example.com` and the page, then merge to `main`.
- If `import _checks` fails in production, check that `api/_checks.py` is in the deployment and that `scan.py` puts its own folder on `sys.path` (it does).

## Costs

- Vercel Pro plan (since 28 Nov 2026), so commercial use is allowed. A Vercel Firewall rule limits each IP to 60 requests a minute on `/api/*` (the Polar webhook is excluded). Supabase, Resend, PostHog and Google Cloud run on free tiers. Polar takes its fee per sale. The screenshot function is the heaviest part (Chrome, 2 GB, a few seconds per image). Edge caching limits repeat cost.
