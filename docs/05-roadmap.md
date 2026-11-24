# AI Site Scan: known limits and roadmap

## Known limits

1. **One page per scan.** The app scans the page you enter, plus site files (robots.txt, llms.txt, sitemap). It does not crawl the whole site.
2. **Bot test is an imitation.** It sends the user-agent names of 14 bots from Vercel's servers. Sites that verify bots by IP address (many do for Googlebot, Bingbot and Applebot) can refuse the imitation and still let the real bot in. Only the 8 OpenAI, Anthropic and Perplexity bots are reported as a problem.
3. **The AI view does not run JavaScript.** Google and some AI agents do. Their view can be richer.
4. **Rate limit is in memory per function instance.** It is not shared and resets on cold start.
5. **History is per browser.** It does not move between devices.
6. **Some sites block all cloud traffic.** Then the scan can only report the block.
7. **Engine text is in English and sometimes technical.** We clean it, but some items keep engine wording.
8. **The score is one tool's opinion.** It shows readiness, not a promise of AI citations.

## Next ideas (not started)

| Idea | Value | Effort |
|---|---|---|
| Vercel Firewall rate-limit rule | Real abuse protection | Low |
| Scan several key pages (from the sitemap) | Site-wide view | Medium |
| "Live AI test": ask an AI model what it knows about the site | Shows real-world effect | Medium, has cost |
| Accounts + server history across devices | Track progress anywhere | Medium to high |
| Scheduled rescans with email alerts | Catch regressions after deploys | Medium |
| GitHub Action that fails a build below a score | For developers | Low |
| Hebrew interface | Local market | Medium |
| Scan a few key pages from the sitemap and compare them | Shows whether the problems are site-wide | Medium |
| Check that links in llms.txt and the sitemap really answer 200 | Finds broken pages AI would be sent to | Low to medium |
| Show which bots the site blocks in robots.txt next to what the server does, as one picture | Clearer story for non-technical owners | Low |
| Optional "deep scan": check internal links for broken ones (engine supports it) | More issues found, more requests to the site | Low |
| Paid full report and fix guide: keep "What the AI agent missed" free, put the detailed fixes and the Markdown guide behind checkout | The business model the new section is built for. Needs a payment provider, an account or receipt link, and a decision on what stays free | Medium to high |
| PDF export of the report | Share with clients | Low |
| Own domain (for example aisitescan.com) | Branding, trust | Low |
| Fix the Duet121 site (SSR or prerender) using its fix guide | Duet121 scored 45, 0 words visible to AI | Depends on its stack |
