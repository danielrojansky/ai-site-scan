# AI Site Scan: known limits and roadmap

Last updated: 28 Nov 2026 (version 3.0.3).

## Known limits

1. **One page per scan.** The app scans the page you enter, plus site files (robots.txt, llms.txt, sitemap). It does not crawl the whole site.
2. **Bot test is an imitation.** It sends the user-agent names of 14 bots from Vercel's servers. Sites that verify bots by IP address (many do for Googlebot, Bingbot and Applebot) can refuse the imitation and still let the real bot in. Only the 8 OpenAI, Anthropic and Perplexity bots are reported as a problem.
3. **The AI view does not run JavaScript.** Google and some AI agents do. Their view can be richer.
4. **Rate limit is in memory per function instance.** It is not shared and resets on cold start.
5. **History is per browser.** It does not move between devices.
6. **Some sites block all cloud traffic.** Then the scan can only report the block.
7. **Engine text is in English and sometimes technical.** We clean it, but some items keep engine wording.
8. **The score is one tool's opinion.** It shows readiness, not a promise of AI citations.
9. **The location check is per page and a heuristic.** It reads the page you scan, so a page can lack the address that the homepage has. It knows Israeli towns and about 230 big cities. A business in a place that is not on the list is still found through a street address, an "Address:" label or an `<address>` block.
10. **Agent readiness is a reading of public standards.** A pass means the file or answer is there and valid. It does not mean any agent uses it. We do not call isitagentready.com. Differences from its website: a missing optional check shows a dash here and red with an "Optional" tag there; the Poppy and level 5 rules follow its public text and may differ at the edges; ap2 is not tested because no public rules exist; WebMCP is read from the homepage code first and then, when the panel opens, in a real browser (a page that freezes our small Chrome before it registers a tool shows as "unable"). Each check adds about 25 requests to the scanned site.
11. **A scan result is identified by site and minute.** Two scans of the same site in the same minute count as one result, so the second one opens without a new credit.
12. **Google login shows the Supabase address** (`rhbhkeuvftqwjjoyzseh.supabase.co`) on Google's screen. Showing getaisitescan.com needs Supabase's paid custom domain.
13. **Pro was not bought live.** Polar asks for a card on the subscription even with a 100% code. Its credit rules are covered by `tests/test_accounts.js`.
14. **Previews have no paywall.** The secrets are set for Production only.

## Next steps (28 Nov 2026)

To reach 100 on our own scan: add sameAs links to 3 of Wikipedia, Wikidata, LinkedIn and Crunchbase. Daniel provides his LinkedIn profile (founder sameAs) and creates a Wikidata item and a Crunchbase page.


| # | Step | Who | Effort |
|---|---|---|---|
| 1 | Done 28 Nov 2026: Vercel is on Pro | Daniel | Done |
| 2 | Set `CRON_SECRET` (a long random string) in Vercel, Production | Daniel | 2 minutes |
| 3 | Polar: archive the two old 2.5 products; limit the 100% code `F53KRI8C` (a few uses or an end date) or delete it after testing | Daniel | 5 minutes |
| 4 | Delete the unused `POLAR_ORG_ID` setting in Vercel | Claude, with a yes | 1 minute |
| 5 | Turn the report-only Content-Security-Policy into an enforced one after about a week without new violations | Claude | Low |
| 6 | Done 28 Nov 2026: Vercel Firewall rate limit, 60 requests a minute per IP on `/api/*` except the Polar webhook | Claude | Done |
| 7 | List an unlocked report whose guide did not save, with a button to finish it | Claude | Low |
| 8 | Supabase custom domain for login (Google then shows getaisitescan.com) | Daniel decides, paid | Low |
| 9 | Move the Resend domain to a US region (now Tokyo); a little faster | Daniel | Low |

Business next steps (traffic, conversion, the 2-week money test) are in the review doc of 28 Nov 2026.

## Next ideas (not started)

| Idea | Value | Effort |
|---|---|---|
| Scan several key pages (from the sitemap) | Site-wide view | Medium |
| "Live AI test": ask an AI model what it knows about the site | Shows real-world effect | Medium, has cost |
| Scheduled rescans with email alerts (a Pro feature) | Catch regressions after deploys; a reason to stay subscribed | Medium |
| GitHub Action that fails a build below a score | For developers | Low |
| Hebrew interface | Local market | Medium |
| Scan a few key pages from the sitemap and compare them | Shows whether the problems are site-wide | Medium |
| Check that links in llms.txt and the sitemap really answer 200 | Finds broken pages AI would be sent to | Low to medium |
| Show which bots the site blocks in robots.txt next to what the server does, as one picture | Clearer story for non-technical owners | Low |
| Optional "deep scan": check internal links for broken ones (engine supports it) | More issues found, more requests to the site | Low |
| PDF export of the report | Share with clients | Low |
| Run a real AI agent on a task ("What does this site do, who is it for, where is it?") and show its notes | The closest test of what AI really understands. is-agentic.com does it | Medium, has cost and needs an API key |
| "Copy prompt" button on each fix | One click to hand a fix to an AI coding tool. isitagentready.com and is-agentic.com have it | Low |
| Location check across the homepage and the contact page | Tells "this page lacks the address" from "the whole site lacks it" | Medium |
| The fix guide writes the agent files (API catalog, Agent Skills index with real digests, MCP server card) when the site has the material | Closes the gaps the Agent readiness panel shows | Medium |
| Show the agent readiness level in History and the trend line | Progress over time for the new panel | Low |
| Fix the Duet121 site (SSR or prerender) using its fix guide | Duet121 scored 45, 0 words visible to AI | Depends on its stack |

## What other checkers do (reviewed 25 Nov 2026)

| Tool | What it checks | Location or local business? |
|---|---|---|
| isitagentready.com (Cloudflare) | Five groups of agent standards: robots.txt, sitemap and Link headers; Markdown negotiation; AI bot rules, Content Signals, Web Bot Auth; MCP server card, Agent Skills, WebMCP, API catalog, OAuth; commerce protocols (x402, ACP, UCP). Levels, red or green cards with Goal, Issue, How to implement, Copy prompt and raw HTTP evidence | No. A small shop page scored 33, "Level 1", mostly for protocols it does not need |
| is-agentic.com (Ora, Vercel) | Guesses the site type (docs, business, app, commerce) and weights checks for it: essential 80 points, recommended 20, bonus 5. Critical access first. Runs a real AI agent on a task and shows its notes. Copy fix prompt per gap | Only in structured data ("Organization schema complete with contactPoint and address") |
| agnt.one site checker | A crawl preview ("the raw text an agent would use") and an agency upsell | In our test the button sent no scan request, so no result |

What we took: apply a check only when the page shows it matters (is-agentic's site types, our local-business gate), an optional Content Signals extra, and (v2.4.0) every check of isitagentready.com as the Agent readiness panel. Ideas not built are in the table above.
