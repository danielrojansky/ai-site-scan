# AI Site Scan: known limits and roadmap

## Known limits

1. **One page per scan.** The app scans the page you enter, plus site files (robots.txt, llms.txt, sitemap). It does not crawl the whole site.
2. **Bot test is an imitation.** It sends the ClaudeBot, GPTBot and PerplexityBot user-agent names from Vercel's servers. Sites that verify bots by IP address can treat the real bots differently.
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
| PDF export of the report | Share with clients | Low |
| Own domain (for example aisitescan.com) | Branding, trust | Low |
| Fix the Duet121 site (SSR or prerender) using its fix guide | Duet121 scored 45, 0 words visible to AI | Depends on its stack |
