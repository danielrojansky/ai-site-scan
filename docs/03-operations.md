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
| `api/scan.py` | 60 s, 1024 MB | Full audit plus 4 extra fetches |
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

## Costs

- Vercel Hobby plan. No paid services. The screenshot function is the heaviest part (Chrome, 2 GB, a few seconds per image). Edge caching limits repeat cost.
