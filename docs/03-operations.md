# AI Site Scan: operations

## Deploy

- Push to `main` on GitHub. Vercel builds and deploys to production automatically (about 30 to 70 seconds).
- Production URL: https://ai-site-scan.vercel.app
- Deployment protection is off, so the site is public.
- Do **not** use "Add New > Import" in Vercel for this repo. It creates a second project. Git is already connected in Project > Settings > Git.

## Configuration (`vercel.json`)

| Setting | Value | Why |
|---|---|---|
| `framework` | `null` (Other) | Stops Vercel from picking the Python framework preset, which fails with `PYTHON_ENTRYPOINT_NOT_FOUND` |
| `api/scan.py` | 60 s, 1024 MB | Full audit plus 14 bot probes, all in parallel. The scan itself stops at 50 s |
| `api/shot.js` | 45 s, 2048 MB, includes `node_modules/@sparticuz/chromium/bin/**` | Chrome needs memory and its binary files |

Environment variables (optional):
- `RATE_MAX`: scans per IP per 10 minutes (default 10).

## Run and test locally

```bash
pip install -r requirements.txt
npm install
npx vercel dev            # serves index.html and both functions
```

Notes for local tests:
- The engine refuses private addresses, so local test pages on 127.0.0.1 need the IP check patched in a test script only. Never ship that patch.
- For the screenshot function outside Vercel, set `CHROME_PATH` to a local Chrome.
- A browser test with Playwright can mock `/api/scan` and `/api/shot` and run the whole UI flow (scan, export, history, reopen, new scan).
- Offline tests for the checks: `python -m unittest discover -s tests` (60 tests, under a second, no network).
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
| Googlebot, Bingbot or Applebot show "Refused" | The site checks bot IP addresses and refuses our imitation | Normal. Not reported as a problem. The note under the bot cards says so |

## Versioning and release

- Version number is `APP_VERSION` in `api/scan.py` and in `index.html` (footer shows it). Keep them equal. Minor number up for each production push, major for big changes.
- Safe release: push a branch, open the Vercel preview URL, check `/api/scan?url=example.com` and the page, then merge to `main`.
- If `import _checks` fails in production, check that `api/_checks.py` is in the deployment and that `scan.py` puts its own folder on `sys.path` (it does).

## Costs

- Vercel Hobby plan. No paid services. The screenshot function is the heaviest part (Chrome, 2 GB, a few seconds per image). Edge caching limits repeat cost.
