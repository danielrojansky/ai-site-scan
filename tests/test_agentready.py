"""Offline tests for the agent readiness checks (api/_agentready.py). Run: python -m unittest discover -s tests"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import _agentready as A  # noqa: E402


def probe(body="", status=200, ctype="application/json", headers=None, url="https://x.test/"):
    h = {"content-type": ctype, **{k.lower(): v for k, v in (headers or {}).items()}}
    if ctype is None:
        h.pop("content-type")
    data = body if isinstance(body, bytes) else (json.dumps(body) if isinstance(body, (dict, list)) else body).encode()
    return {"status": status, "headers": h, "content": data, "error": None, "url": url}


def gone(status=404, url="https://x.test/"):
    return {"status": status, "headers": {"content-type": "text/html"}, "content": b"<html>not found</html>", "error": None, "url": url}


def down(err="timeout"):
    return {"status": None, "headers": {}, "content": b"", "error": err, "url": "https://x.test/"}


def run(cid, **P):
    return A.EVALUATORS[cid](P, "x.test") if cid == "personalAgentProtocol" else A.EVALUATORS[cid](P)


ROBOTS = "User-agent: *\nDisallow: /admin\n\nSitemap: https://x.test/sitemap.xml\n"
SITEMAP = '<?xml version="1.0"?><urlset><url><loc>https://x.test/</loc></url><url><loc>https://x.test/a</loc></url></urlset>'


class Basics(unittest.TestCase):
    def test_robots(self):
        self.assertEqual(run("robotsTxt", robots=probe(ROBOTS, ctype="text/plain; charset=utf-8"))[0], "pass")
        self.assertEqual(run("robotsTxt", robots=gone())[0], "fail")
        self.assertEqual(run("robotsTxt", robots=probe("<html><body>x</body></html>", ctype="text/html"))[0], "fail")
        self.assertEqual(run("robotsTxt", robots=probe("Disallow: /x", ctype="text/plain"))[1], "No User-agent line found")
        self.assertEqual(run("robotsTxt", robots=probe("User-agent: *\n", ctype="text/plain"))[1], "No Allow or Disallow rules found")
        self.assertIn("text/plain", run("robotsTxt", robots=probe(ROBOTS, ctype="application/octet-stream"))[1])
        self.assertEqual(run("robotsTxt", robots=down())[0], "unable")
        self.assertEqual(run("robotsTxt", robots=gone(403))[0], "unable")  # refused is not "missing"

    def test_robots_parser(self):
        r = A.parse_robots("User-agent: GPTBot\nUser-agent: ClaudeBot\nDisallow: /\n# c\nUser-agent: *\nAllow: /\nContent-Signal: ai-train=no, search=yes\nSitemap: /s.xml\nAgentmap: https://x.test/c.json")
        self.assertEqual([g["agents"] for g in r["groups"]], [["gptbot", "claudebot"], ["*"]])  # two UA lines in a row share a group
        self.assertEqual(r["sitemaps"], ["/s.xml"])
        self.assertEqual(r["agentmap"], ["https://x.test/c.json"])
        self.assertEqual(A.parse_signals(r["signals"]), {"ai-train": "no", "search": "yes"})
        self.assertEqual(A.parse_signals(["ai-train=maybe, nonsense=yes, search = YES"]), {"search": "yes"})

    def test_sitemap(self):
        ok = run("sitemap", robots=probe(ROBOTS, ctype="text/plain"), sitemap=probe(SITEMAP, ctype="application/xml"))
        self.assertEqual(ok[0], "pass")
        self.assertIn("2 entries", ok[1])
        self.assertIn("listed in robots.txt", ok[1])
        self.assertIn("does not list it", run("sitemap", robots=probe("User-agent: *\nDisallow:\n", ctype="text/plain"), sitemap=probe(SITEMAP))[1])
        self.assertEqual(run("sitemap", robots=gone(), sitemap=gone())[0], "fail")
        self.assertEqual(run("sitemap", robots=gone(), sitemap=probe("<html></html>", ctype="text/html"))[0], "fail")
        self.assertEqual(run("sitemap", robots=gone(), sitemap=probe('<urlset></urlset>'))[0], "fail")
        declared = run("sitemap", robots=probe(ROBOTS, ctype="text/plain"), sitemap=gone(), sitemap_declared=probe(SITEMAP))
        self.assertEqual(declared[0], "pass")  # a declared sitemap wins over /sitemap.xml
        self.assertEqual(run("sitemap", robots=gone(), sitemap=down())[0], "unable")

    def test_link_headers(self):
        h = {"Link": '</.well-known/api-catalog>; rel="api-catalog", </docs>; rel="service-doc"; type="text/html"'}
        r = run("linkHeaders", home=probe("<html></html>", ctype="text/html", headers=h))
        self.assertEqual(r, ("pass", "Found: api-catalog, service-doc"))
        self.assertEqual(run("linkHeaders", home=probe("", ctype="text/html", headers={"Link": '<https://x.test/a.css>; rel=preload'}))[0], "fail")
        wp = {"Link": '<https://x.test/wp-json/>; rel="https://api.w.org/", <https://x.test/wp-json/wp/v2/pages/7>; rel="alternate"; title="JSON"; type="application/json"'}
        self.assertEqual(run("linkHeaders", home=probe("", ctype="text/html", headers=wp)), ("pass", "Found: alternate"))  # as isitagentready.com counts a WordPress site
        lang = {"Link": '<https://x.test/fr>; rel="alternate"; hreflang="fr"'}
        self.assertEqual(run("linkHeaders", home=probe("", ctype="text/html", headers=lang))[0], "fail")  # a language link is for people
        self.assertIn("no Link header", run("linkHeaders", home=probe("", ctype="text/html"))[1])
        self.assertEqual(run("linkHeaders", home=down())[0], "unable")
        self.assertEqual(run("linkHeaders", home=probe("", ctype="text/html", headers={"Link": '</x>; rel="describedby service-desc"'}))[1], "Found: service-desc, describedby")
        parsed = A.parse_link_header('</a>; rel="x"; title="a, b", </b>; rel=y')
        self.assertEqual([(d["url"], d["rel"]) for d in parsed], [("/a", "x"), ("/b", "y")])

    def test_markdown_negotiation(self):
        md = probe("# Hi", ctype="text/markdown; charset=utf-8", headers={"x-markdown-tokens": "420"})
        self.assertEqual(run("markdownNegotiation", page_md=md), ("pass", "Markdown served, about 420 tokens"))
        self.assertEqual(run("markdownNegotiation", page_md=probe("<html></html>", ctype="text/html"))[0], "fail")
        self.assertIn("text/html", run("markdownNegotiation", page_md=probe("<html></html>", ctype="text/html"))[1])
        self.assertEqual(run("markdownNegotiation", page_md=down())[0], "unable")

    def test_search_for_agents_is_optional(self):
        self.assertEqual(run("searchForAgents", search=probe("# Search results for: ai", ctype="text/markdown"))[0], "pass")
        self.assertEqual(run("searchForAgents", search=probe("x", ctype="text/html", headers={"Accept-Query": "text/plain"}))[0], "pass")
        self.assertEqual(run("searchForAgents", search=gone())[0], "neutral")  # never a failure
        self.assertEqual(run("searchForAgents", search=probe("<html></html>", ctype="text/html"))[0], "neutral")


class Bots(unittest.TestCase):
    def test_ai_rules(self):
        named = probe("User-agent: GPTBot\nDisallow: /\n\nUser-agent: OAI-SearchBot\nAllow: /\n", ctype="text/plain")
        r = run("robotsTxtAiRules", robots=named)
        self.assertEqual(r[0], "pass")
        self.assertIn("2 AI crawlers named", r[1])
        wild = run("robotsTxtAiRules", robots=probe(ROBOTS, ctype="text/plain"))
        self.assertEqual(wild[0], "pass")  # a * group with rules also passes
        self.assertIn("* group", wild[1])
        self.assertEqual(run("robotsTxtAiRules", robots=probe("User-agent: *\n", ctype="text/plain"))[0], "fail")
        self.assertEqual(run("robotsTxtAiRules", robots=gone())[0], "fail")
        self.assertEqual(run("robotsTxtAiRules", robots=down())[0], "unable")

    def test_content_signals(self):
        r = run("contentSignals", robots=probe("User-agent: *\nContent-Signal: ai-train=no, search=yes, ai-input=no\nAllow: /\n", ctype="text/plain"))
        self.assertEqual(r, ("pass", "ai-train=no, search=yes, ai-input=no"))
        self.assertEqual(run("contentSignals", robots=probe(ROBOTS, ctype="text/plain"))[0], "fail")
        self.assertEqual(run("contentSignals", robots=gone())[0], "fail")

    def test_web_bot_auth(self):
        self.assertEqual(run("webBotAuth", webbotauth=probe({"keys": [{"kty": "OKP", "crv": "Ed25519", "x": "abc"}]}))[0], "pass")
        self.assertEqual(run("webBotAuth", webbotauth=probe({"keys": []}))[0], "fail")  # present but empty
        self.assertEqual(run("webBotAuth", webbotauth=gone())[0], "neutral")  # informational: never a failure when absent
        self.assertEqual(run("webBotAuth", webbotauth=probe("<html>app</html>", ctype="text/html"))[1], "It returned a web page, not a data file")  # a single page app answers 200 for every path

    def test_a_file_that_is_too_large_is_not_the_expected_file(self):
        big = {"status": None, "error": "Response too large: >600000 bytes (max: 600000)", "headers": {}, "content": b"", "url": "https://x.test/auth.md"}
        self.assertEqual(run("authMd", authmd=big)[0], "neutral")
        self.assertEqual(run("mpp", openapi=big)[0], "neutral")
        self.assertEqual(run("webBotAuth", webbotauth=big)[0], "neutral")
        self.assertEqual(run("webBotAuth", webbotauth=down("connection reset"))[0], "unable")


class Discovery(unittest.TestCase):
    def test_api_catalog(self):
        cat = {"linkset": [{"anchor": "https://x.test/api", "service-desc": [{"href": "https://x.test/openapi.json"}]}]}
        self.assertEqual(run("apiCatalog", apicatalog=probe(cat, ctype="application/linkset+json; profile=x"))[0], "pass")
        wrong = run("apiCatalog", apicatalog=probe(cat, ctype="application/json"))
        self.assertEqual(wrong[0], "pass")  # the data is right; the Content-Type is only a note
        self.assertIn("application/linkset+json", wrong[1])
        self.assertEqual(run("apiCatalog", apicatalog=probe(cat, ctype=None))[0], "pass")  # isitagentready.com serves its own catalog with no type
        self.assertEqual(run("apiCatalog", apicatalog=probe({"linkset": []}, ctype="application/linkset+json"))[0], "fail")
        self.assertEqual(run("apiCatalog", apicatalog=probe("{broken", ctype="application/json"))[1], "The file is not valid JSON")

    def test_oauth_metadata_is_optional(self):
        full = {"issuer": "https://auth.x.test", "authorization_endpoint": "https://auth.x.test/a", "token_endpoint": "https://auth.x.test/t", "jwks_uri": "https://auth.x.test/j"}
        self.assertEqual(run("oauthDiscovery", oidc=gone(), oauth_as=probe(full))[0], "pass")
        self.assertEqual(run("oauthDiscovery", oidc=probe(full), oauth_as=gone())[0], "pass")
        part = run("oauthDiscovery", oidc=probe({"issuer": "https://auth.x.test"}), oauth_as=gone())
        self.assertEqual(part[0], "neutral")
        self.assertIn("token_endpoint", part[1])
        self.assertEqual(run("oauthDiscovery", oidc=gone(), oauth_as=gone())[0], "neutral")  # never a failure
        self.assertEqual(run("oauthProtectedResource", oauth_prm=probe({"resource": "https://x.test/api", "authorization_servers": ["https://auth.x.test"]}))[0], "pass")
        self.assertEqual(run("oauthProtectedResource", oauth_prm=probe({"resource": "https://x.test/api"}))[0], "neutral")
        self.assertEqual(run("oauthProtectedResource", oauth_prm=gone())[0], "neutral")

    def test_auth_md(self):
        self.assertEqual(run("authMd", authmd=probe("# Example auth.md\n\nRegister here.", ctype="text/markdown"))[0], "pass")
        self.assertEqual(run("authMd", authmd=probe("# Welcome\n", ctype="text/markdown"))[0], "neutral")
        self.assertEqual(run("authMd", authmd=probe("<html></html>", ctype="text/html"))[0], "neutral")
        self.assertEqual(run("authMd", authmd=gone())[0], "neutral")

    def test_mcp_server_card(self):
        card = {"serverInfo": {"name": "Acme", "version": "1.2.0"}, "transport": {"type": "streamable-http", "endpoint": "/mcp"}, "capabilities": {"tools": {}}}
        self.assertEqual(run("mcpServerCard", mcp=probe(card))[0], "pass")
        # the shape of isitagentready.com: serverInfo and a top-level url
        self.assertEqual(run("mcpServerCard", mcp=probe({"serverInfo": {"name": "S", "version": "1"}, "url": "https://x.test/mcp", "transport": {"type": "streamable-http"}}))[0], "pass")
        # the shape of developers.cloudflare.com: name, version and remotes
        cf = {"name": "com.cloudflare/mcp", "version": "0.1.0", "remotes": [{"url": "https://mcp.x.test/mcp", "type": "streamable-http"}]}
        self.assertEqual(run("mcpServerCard", mcp=probe(cf)), ("pass", "com.cloudflare/mcp 0.1.0"))
        for bad, word in (({**card, "serverInfo": {"name": "A"}}, "version"), ({k: v for k, v in card.items() if k != "transport"}, "endpoint"),
                          ({"serverInfo": {"version": "1"}, "url": "https://x.test/mcp"}, "name")):
            r = run("mcpServerCard", mcp=probe(bad))
            self.assertEqual(r[0], "fail")
            self.assertIn(word, r[1])
        self.assertEqual(run("mcpServerCard", mcp=gone())[0], "fail")

    def test_a2a_agent_card(self):
        card = {"name": "Helper", "version": "1.0", "description": "Helps", "supportedInterfaces": [{"url": "https://x.test/a2a", "protocolBinding": "JSONRPC"}],
                "capabilities": {"streaming": True}, "skills": [{"id": "s1", "name": "Search", "description": "Searches"}]}
        self.assertEqual(run("a2aAgentCard", a2a=probe(card))[0], "pass")
        r = run("a2aAgentCard", a2a=probe({**card, "skills": [{"id": "s1"}]}))
        self.assertEqual(r[0], "fail")
        self.assertIn("skills", r[1])
        self.assertEqual(run("a2aAgentCard", a2a=gone())[0], "neutral")  # not scanned by default, so never a failure

    def test_agent_skills(self):
        digest = "sha256:" + "a" * 64
        idx = {"$schema": "https://schemas.agentskills.io/discovery/0.2.0/schema.json",
               "skills": [{"name": "do-thing", "type": "skill-md", "description": "Does a thing", "url": "/.well-known/agent-skills/do-thing/SKILL.md", "digest": digest}]}
        self.assertEqual(run("agentSkills", skills=probe(idx)), ("pass", "1 skill listed"))
        self.assertIn("digest", run("agentSkills", skills=probe({**idx, "skills": [{**idx["skills"][0], "digest": "md5:1"}]}))[1])
        self.assertIn("name", run("agentSkills", skills=probe({**idx, "skills": [{**idx["skills"][0], "name": "Do Thing"}]}))[1])
        self.assertIn("type", run("agentSkills", skills=probe({**idx, "skills": [{**idx["skills"][0], "type": "zip"}]}))[1])
        self.assertIn("$schema", run("agentSkills", skills=probe({"skills": idx["skills"]}))[1])
        self.assertEqual(run("agentSkills", skills=probe({**idx, "skills": []}))[0], "fail")
        self.assertEqual(run("agentSkills", skills=gone())[0], "fail")

    def test_web_mcp_reads_code_only(self):
        form = '<html><form toolname="search" tooldescription="Search the site"><input name="q"></form></html>'
        self.assertEqual(run("webMcp", home=probe(form, ctype="text/html"))[0], "pass")
        code = '<html><script>if (document.modelContext) document.modelContext.registerTool({name:"x"})</script></html>'
        self.assertEqual(run("webMcp", home=probe(code, ctype="text/html"))[0], "pass")
        script = {"url": "https://x.test/app.js", "status": 200, "headers": {}, "content": b"navigator.modelContext.registerTool(t)"}
        r = run("webMcp", home=probe("<html></html>", ctype="text/html"), scripts=[script])
        self.assertEqual(r[0], "pass")
        self.assertIn("/app.js", r[1])
        none = run("webMcp", home=probe("<html><body>hi</body></html>", ctype="text/html"))
        self.assertEqual(none[0], "fail")
        self.assertIn("do not run the page", none[1])
        self.assertEqual(run("webMcp", home=down())[0], "unable")

    def test_ard(self):
        cat = {"specVersion": "1.0", "host": {"displayName": "Acme", "identifier": "did:web:x.test"},
               "entries": [{"identifier": "urn:air:x.test:server:a", "displayName": "A", "type": "application/mcp-server-card+json", "url": "https://x.test/a.json"}]}
        self.assertEqual(run("ard", ard=probe(cat))[0], "pass")
        both = {**cat, "entries": [{**cat["entries"][0], "data": {}}]}
        self.assertIn("exactly one of url or data", run("ard", ard=probe(both))[1])
        none = {**cat, "entries": [{k: v for k, v in cat["entries"][0].items() if k != "url"}]}
        self.assertIn("exactly one of url or data", run("ard", ard=probe(none))[1])
        self.assertIn("host", run("ard", ard=probe({k: v for k, v in cat.items() if k != "host"}))[1])
        self.assertIn("entries", run("ard", ard=probe({**cat, "entries": []}))[1])
        style = {**cat, "entries": [{**cat["entries"][0], "identifier": "my-server"}]}
        r = run("ard", ard=probe(style))
        self.assertEqual(r[0], "pass")  # style problems are reported, not failed
        self.assertIn("urn:air:", r[1])
        alt = run("ard", ard=gone(), ard_alt=probe(cat))
        self.assertEqual(alt[0], "pass")
        self.assertIn("linked", alt[1])
        self.assertEqual(run("ard", ard=gone())[0], "fail")

    def test_poppy(self):
        base = {"protocol_version": "0.1", "organization": {"name": "Acme", "domain": "x.test"}, "web": {}}
        self.assertEqual(run("personalAgentProtocol", poppy=probe(base))[0], "pass")
        self.assertEqual(run("personalAgentProtocol", poppy=probe({**base, "organization": {"name": "Acme", "domain": "other.test"}}))[0], "neutral")
        self.assertEqual(run("personalAgentProtocol", poppy=probe({**base, "organization": {"name": "Acme", "domain": "www.x.test"}}))[0], "pass")  # leading www is ignored
        self.assertEqual(run("personalAgentProtocol", poppy=probe({**base, "protocol_version": "2.0"}))[0], "unable")
        self.assertEqual(run("personalAgentProtocol", poppy=gone())[0], "neutral")
        withapi = {**base, "apis": [{"type": "rest", "url": "https://x.test/api", "description": "API"}], "auth": {"issuer": "https://auth.x.test/tenant"}}
        meta = {"issuer": "https://auth.x.test/tenant", "poppy_domains": ["x.test"], "token_endpoint": "https://auth.x.test/t", "revocation_endpoint": "https://auth.x.test/r"}
        self.assertEqual(run("personalAgentProtocol", poppy=probe(withapi), poppy_issuer=probe(meta))[1], "Discovery found, issuer verified")
        self.assertEqual(run("personalAgentProtocol", poppy=probe(withapi), poppy_issuer=probe({**meta, "poppy_domains": []}))[0], "neutral")
        self.assertEqual(run("personalAgentProtocol", poppy=probe(withapi), poppy_issuer=down())[0], "unable")
        self.assertEqual(A.poppy_issuer_url("https://auth.x.test/tenant"), "https://auth.x.test/.well-known/oauth-authorization-server/tenant")
        self.assertIsNone(A.poppy_issuer_url("http://auth.x.test"))

    def test_dns_aid(self):
        rec = {"name": "_index._agents.x.test", "type": "SVCB", "answers": ['1 agents.x.test. alpn="a2a" port=443'], "ad": True, "error": None}
        r = run("dnsAid", dns={"dns_index": rec})
        self.assertEqual(r[0], "pass")
        self.assertIn("DNSSEC checked", r[1])
        self.assertIn("not confirmed", run("dnsAid", dns={"dns_index": {**rec, "ad": False}})[1])
        alias = {**rec, "answers": ["0 other.x.test."]}  # AliasMode is not a service record
        self.assertEqual(run("dnsAid", dns={"dns_index": alias})[0], "fail")
        self.assertEqual(run("dnsAid", dns={"dns_index": {**rec, "answers": []}, "dns_a2a": {**rec, "answers": []}})[0], "fail")
        self.assertEqual(run("dnsAid", dns={"dns_index": {**rec, "answers": [], "error": "timeout"}})[0], "unable")


class Commerce(unittest.TestCase):
    def test_x402(self):
        self.assertEqual(run("x402", x402=probe({"x402Version": 2, "kind": "manifest"}))[0], "pass")
        paid = probe('{"x402Version":1,"accepts":[{"scheme":"exact"}]}', status=402)
        self.assertEqual(run("x402", x402=gone(), page_md=paid)[0], "pass")
        self.assertEqual(run("x402", x402=gone(), page_md=probe("<html></html>", ctype="text/html"))[0], "fail")
        self.assertEqual(run("x402", x402=gone(), dns={"dns_x402": {"answers": ["v=x402; k=facilitator"], "error": None}})[0], "pass")

    def test_mpp(self):
        spec = {"paths": {"/q": {"get": {"x-payment-info": {"intent": "charge", "method": "stripe", "amount": "0.01"}}}}}
        self.assertEqual(run("mpp", openapi=probe(spec)), ("pass", "1 payable operation described"))
        bad = {"paths": {"/q": {"get": {"x-payment-info": {"intent": "gift", "method": "cash"}}}}}
        self.assertEqual(run("mpp", openapi=probe(bad))[0], "fail")
        self.assertIn("no x-payment-info", run("mpp", openapi=probe({"paths": {"/q": {"get": {}}}}))[1])
        self.assertEqual(run("mpp", openapi=gone())[0], "neutral")

    def test_ucp_and_acp(self):
        self.assertEqual(run("ucp", ucp=probe({"protocol_version": "1.0", "services": [1], "capabilities": {"a": 1}, "endpoints": {"b": 1}}))[0], "pass")
        self.assertIn("endpoints", run("ucp", ucp=probe({"protocol_version": "1.0", "services": [1], "capabilities": {"a": 1}}))[1])
        acp = {"protocol": {"name": "acp", "version": "2025-01"}, "api_base_url": "https://x.test/acp", "transports": ["rest"], "capabilities": {"services": ["checkout"]}}
        self.assertEqual(run("acp", acp=probe(acp))[0], "pass")
        for field in ("api_base_url", "transports"):
            r = run("acp", acp=probe({k: v for k, v in acp.items() if k != field}))
            self.assertEqual(r[0], "fail")
            self.assertIn(field, r[1])
        self.assertEqual(run("acp", acp=probe({**acp, "protocol": {"name": "other", "version": "1"}}))[0], "fail")
        self.assertEqual(run("acp", acp=gone())[0], "neutral")


class Levels(unittest.TestCase):
    def lv(self, *ok):
        return A.level_for({k: "pass" for k in ok})

    def test_scale(self):
        self.assertEqual(self.lv()["level"], 0)
        self.assertEqual(self.lv("robotsTxt")["level"], 0)
        self.assertEqual(self.lv("robotsTxt", "sitemap")["level"], 1)
        self.assertEqual(self.lv("robotsTxt", "linkHeaders")["name"], "Basic Web Presence")
        self.assertEqual(self.lv("robotsTxt", "sitemap", "robotsTxtAiRules")["level"], 1)  # needs both bot checks
        base = ("robotsTxt", "sitemap", "robotsTxtAiRules", "contentSignals")
        self.assertEqual(self.lv(*base)["level"], 2)
        self.assertEqual(self.lv(*base, "markdownNegotiation")["level"], 3)
        self.assertEqual(self.lv(*base, "mcpServerCard")["level"], 2)  # level 3 is skipped
        l4 = (*base, "markdownNegotiation", "agentSkills")
        self.assertEqual(self.lv(*l4)["level"], 4)
        self.assertEqual(self.lv(*l4, "webBotAuth")["level"], 4)  # one of the three extras is not enough
        self.assertEqual(self.lv(*l4, "webBotAuth", "authMd")["level"], 5)
        self.assertEqual(self.lv(*l4, "authMd", "oauthDiscovery")["level"], 4)  # both are "auth metadata": one extra
        self.assertEqual(self.lv(*l4, "mcpServerCard", "a2aAgentCard", "apiCatalog", "webBotAuth")["level"], 5)  # all four integrations count as one
        self.assertIsNone(self.lv(*l4, "webBotAuth", "authMd")["next"])
        # isitagentready.com rates developers.cloudflare.com and its own site Level 5 with these three together and no auth metadata
        self.assertEqual(self.lv(*base, "markdownNegotiation", "mcpServerCard", "agentSkills", "apiCatalog")["level"], 5)
        self.assertEqual(self.lv(*base, "markdownNegotiation", "mcpServerCard", "agentSkills")["level"], 4)

    def test_next_level_text(self):
        n = self.lv("robotsTxt", "sitemap")["next"]
        self.assertEqual((n["level"], n["name"]), (2, "Bot-Aware"))
        self.assertIn("Content Signals", n["needs"])
        self.assertEqual(self.lv()["next"]["level"], 1)


class Whole(unittest.TestCase):
    def test_catalog_is_complete(self):
        self.assertEqual(len(A.ORDER), 23)
        self.assertEqual(set(A.ORDER), set(A.META))
        self.assertEqual(set(A.EVALUATORS), set(A.ORDER))
        self.assertEqual(set(A.EVIDENCE), set(A.ORDER))
        self.assertEqual({m["group"] for m in A.META.values()}, {g for g, _ in A.GROUPS})
        self.assertEqual({m["profile"] for m in A.META.values()}, {"content", "app", "commerce"})
        optional = {c for c in A.ORDER if not A.META[c]["scored"]}
        self.assertEqual(optional, {"searchForAgents", "oauthDiscovery", "oauthProtectedResource", "authMd", "personalAgentProtocol", "webBotAuth", "a2aAgentCard", "x402", "mpp", "ucp", "acp"})
        self.assertEqual(len(A.ORDER) - len(optional), 12)  # the same 12 that count on isitagentready.com for a plain site
        content = {c for c in A.ORDER if A.META[c]["profile"] == "content"}
        self.assertEqual(content, {"robotsTxt", "sitemap", "linkHeaders", "dnsAid", "markdownNegotiation", "searchForAgents", "robotsTxtAiRules", "contentSignals"})
        self.assertEqual(len(A.AI_CRAWLERS), 20)

    def test_every_file_a_check_reads_is_fetched(self):
        known = set(A.PROBES) | set(A.DNS_PROBES) | {"page_md", "sitemap_declared", "ard_alt", "poppy_issuer", "scripts"}
        for cid, names in A.EVIDENCE.items():
            for n in names:
                self.assertIn(n, known | {"dns_index", "dns_a2a", "dns_mcp", "dns_x402"}, (cid, n))
        self.assertTrue(all(path.startswith("/") for path, _ in A.PROBES.values()))

    def test_evaluate_nothing_reachable(self):
        r = A.evaluate({}, "x.test")
        self.assertFalse(r["reachable"])
        self.assertEqual(r["level"], 0)
        self.assertEqual(len(r["checks"]), 23)
        self.assertTrue(all(c["status"] == "unable" for c in r["checks"]))
        self.assertEqual((r["passed"], r["scored"]), (0, 0))

    def test_evaluate_a_small_site(self):
        P = {"robots": probe(ROBOTS + "Content-Signal: search=yes\n", ctype="text/plain"), "sitemap": probe(SITEMAP, ctype="application/xml"), "home": probe("<html></html>", ctype="text/html"),
             "page_md": probe("<html></html>", ctype="text/html"), "dns": {k: {"name": k, "type": "SVCB", "answers": [], "ad": False, "error": None} for k in ("dns_index", "dns_a2a", "dns_mcp")}}
        for k in A.PROBES:
            P.setdefault(k, gone())
        P["home"] = probe("<html></html>", ctype="text/html")
        r = A.evaluate(P, "x.test")
        by = {c["id"]: c for c in r["checks"]}
        self.assertEqual([by[k]["status"] for k in ("robotsTxt", "sitemap", "robotsTxtAiRules", "contentSignals")], ["pass"] * 4)
        self.assertEqual([by[k]["status"] for k in ("linkHeaders", "markdownNegotiation", "mcpServerCard")], ["fail"] * 3)
        self.assertEqual(by["x402"]["status"], "neutral")
        self.assertTrue(by["x402"]["message"].endswith("(not a shop)"))
        self.assertTrue(by["webMcp"]["browser"])  # a real browser may still find tools
        self.assertFalse(r["is_commerce"])
        self.assertEqual(by["oauthDiscovery"]["status"], "neutral")
        self.assertEqual(r["level"], 2)
        self.assertTrue(r["reachable"])
        g = {x["key"]: x for x in r["groups"]}
        self.assertEqual((g["bots"]["passed"], g["bots"]["scored"]), (2, 2))  # Web Bot Auth does not count
        self.assertEqual(r["passed"], sum(1 for c in r["checks"] if c["scored"] and c["status"] == "pass"))
        self.assertEqual(by["robotsTxt"]["evidence"][0]["status"], 200)

    def test_shop_detection_and_percent(self):
        woo = '<html><body class="woocommerce"><a href="/cart">Cart</a> Add to cart</body></html>'
        self.assertEqual(A.commerce_signals(woo)[0], "shop platform")
        self.assertIn("product data", A.commerce_signals('<script type="application/ld+json">{"@type": "Product"}</script>'))
        self.assertEqual(A.commerce_signals('<a href="/cart">x</a> הוסף לסל')[0], "cart and checkout links")
        self.assertEqual(A.commerce_signals("<html><h1>Docs</h1></html>"), [])
        P = {k: gone() for k in A.PROBES}
        P["home"] = probe(woo, ctype="text/html")
        P["robots"] = probe(ROBOTS, ctype="text/plain")
        P["sitemap"] = probe(SITEMAP, ctype="application/xml")
        r = A.evaluate(P, "x.test")
        self.assertTrue(r["is_commerce"])
        by = {c["id"]: c for c in r["checks"]}
        self.assertFalse(by["acp"]["message"].endswith("(not a shop)"))  # it is a shop, so no such note
        self.assertLessEqual(r["scored"], 12)  # the checks that could not be made (DNS, Markdown) are left out
        self.assertEqual(r["percent"], round(100 * r["passed"] / r["scored"]))

    def test_one_broken_answer_does_not_hide_the_rest(self):
        P = {"robots": probe(ROBOTS, ctype="text/plain"), "mcp": {"status": 200, "headers": {}, "content": None, "error": None, "url": "x"}}
        r = A.evaluate(P, "x.test")
        self.assertEqual(len(r["checks"]), 23)

    def test_copy_has_no_long_dashes_and_is_short(self):
        for cid, m in A.META.items():
            for f in ("label", "why", "fix"):
                self.assertNotIn("—", m[f], (cid, f))
            self.assertLess(len(m["why"]), 130, cid)
        P = {k: gone() for k in A.PROBES}
        for c in A.evaluate(P, "x.test")["checks"]:
            self.assertNotIn("—", c["message"])


if __name__ == "__main__":
    unittest.main()
