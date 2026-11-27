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

Version 2.5 settings. Each feature stays off until its settings exist. Add them in Vercel > Project > Settings > Environment Variables, then redeploy.

| Variable | Used by | Value |
|---|---|---|
| `SITE_URL` | all | `https://getaisitescan.com` |
| `SUPPORT_EMAIL` | config | `support@getaisitescan.com` |
| `POLAR_ORG_ID` | unlock, claim | Polar organization ID |
| `POLAR_CHECKOUT_SINGLE`, `POLAR_CHECKOUT_PACK` | config | Checkout link URLs of the two products |
| `POLAR_PORTAL_URL` | config | `https://polar.sh/<org-slug>/portal`, or only the org slug |
| `POLAR_ACCESS_TOKEN` | claim, unlock | Organization access token with scopes `checkouts:read`, `customer_sessions:write`, `license_keys:read` |
| `POLAR_WEBHOOK_SECRET` | polar-webhook | Secret of the webhook endpoint |
| `POLAR_BENEFIT_IDS` | unlock, claim | Optional: comma list of the two License Key benefit IDs |
| `POLAR_SERVER` | all Polar calls | `sandbox` for tests, empty for production |
| `POLAR_API_VERSION` | all Polar calls | Optional, default `2026-10`. Upgrade before Polar removes it |
| `PRICE_SINGLE`, `PRICE_PACK` | config | Optional, default 19 and 49. Must match Polar |
| `RESEND_API_KEY`, `EMAIL_FROM` | email, webhook, cron | Resend key and a sender on a verified domain |
| `OWNER_EMAIL` | webhook, cron | Where sale and self-check emails go |
| `PUBLIC_POSTHOG_KEY`, `PUBLIC_POSTHOG_HOST` | config | PostHog project key and host |
| `GADS_ID`, `GADS_PURCHASE_LABEL`, `GADS_CHECKOUT_LABEL`, `GADS_LEAD_LABEL` | config | Google Ads tag and conversion labels |
| `CRON_SECRET` | cron-selfcheck | Random string. Vercel sends it to the cron call |
| `SELFCHECK_MIN_SCORE` | cron-selfcheck | Optional, default 90 |

## Payments (Polar)

- Two one-time products, each with a License Key benefit: 1 activation (single site) and 10 activations (pack). No expiry, no usage limit, customer cannot deactivate.
- Checkout link success URLs: `https://getaisitescan.com/?paid=single&checkout_id={CHECKOUT_ID}` and `...?paid=pack&checkout_id={CHECKOUT_ID}`.
- Webhook endpoint: `https://getaisitescan.com/api/polar-webhook`, format Raw, event `order.paid`, API version 2026-10.
- Flow: buy (new tab) > Polar checkout > success URL > `/api/claim` reads the key > `/api/unlock` activates it for the site (label = host) > the guide opens. If claim is off or slow, the buyer pastes the key from Polar's email.
- Test in the Polar sandbox (`POLAR_SERVER=sandbox` on a preview deployment) or with a 100% discount code in production. Never test with a real card: Polar treats it as card testing.
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
- A small dev server (a `ThreadingHTTPServer` that serves `index.html` and routes `/api/scan` to `api/scan.handler`) is the quickest way to test the real scan and the page together, without `vercel dev`. Set `RATE_MAX=1000` for it.
- To compare engine speed or scores, call `scan.scan(url)` in a Python shell. `scan.scan` runs the parallel path.

## Monitoring

- Vercel dashboard: Project `ai-site-scan` > Logs. Python errors are printed with a traceback to stderr.
- Each scan logs one access line.

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

- Vercel Hobby plan. Hobby is for personal, non-commercial use: move to Pro before the first sale. No other paid services until the Polar, Resend and PostHog settings are added (all have free tiers). The screenshot function is the heaviest part (Chrome, 2 GB, a few seconds per image). Edge caching limits repeat cost.
