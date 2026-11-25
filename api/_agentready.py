"""Agent readiness: the standards that let an AI agent find and use a site.

These checks follow the public standards that isitagentready.com (Cloudflare) tests: robots.txt and
sitemap, Link headers, DNS-AID, Markdown negotiation, Search for Agents, AI bot rules and Content
Signals, Web Bot Auth, API catalog, OAuth metadata, Auth.md, MCP server card, A2A agent card, Agent
Skills, WebMCP, ARD, Personal Agent Protocol, and the agent payment protocols (x402, MPP, UCP, ACP).
The rules come from each standard's public requirements. They do not change the score.

Pure functions, no network. agent.py fetches the files, this module judges them.
"""
from __future__ import annotations

import json
import re
import zlib
from urllib.parse import urlparse

LEVELS = {0: "Not Ready", 1: "Basic Web Presence", 2: "Bot-Aware", 3: "Agent-Readable", 4: "Agent-Integrated", 5: "Agent-Native"}

GROUPS = (("discoverability", "Discoverability"), ("content", "Content for agents"), ("bots", "Bot access control"),
          ("discovery", "APIs, auth, MCP and skills"), ("commerce", "Commerce"))

AI_CRAWLERS = ("OAI-SearchBot", "Claude-SearchBot", "PerplexityBot", "ChatGPT-User", "Claude-User", "Perplexity-User",
               "GPTBot", "ClaudeBot", "anthropic-ai", "Google-Extended", "Applebot-Extended", "CCBot", "Bytespider",
               "Amazonbot", "cohere-ai", "Meta-ExternalAgent", "FacebookBot", "Diffbot", "ImagesiftBot", "Omgilibot")
_AI_LOWER = {a.lower() for a in AI_CRAWLERS}
LINK_RELS = {"api-catalog", "service-desc", "service-doc", "describedby", "alternate"}  # alternate: a machine-readable copy of the page (not a language link)

# id: group, label, profile (content: every website, app: APIs and apps, commerce), scored, spec, what it is for, how to fix it
_META = (
    ("robotsTxt", "discoverability", "robots.txt", "content", True, "https://www.rfc-editor.org/rfc/rfc9309",
     "robots.txt tells crawlers what they may read. A missing or broken file leaves AI crawlers guessing.",
     "Publish /robots.txt as plain text with User-agent lines and Allow or Disallow rules, and a Sitemap line."),
    ("sitemap", "discoverability", "Sitemap", "content", True, "https://www.sitemaps.org/protocol.html",
     "A sitemap lists the pages you want found.",
     "Publish /sitemap.xml with your public pages and add the line Sitemap: https://your-site/sitemap.xml to robots.txt."),
    ("linkHeaders", "discoverability", "Link headers", "content", True, "https://www.rfc-editor.org/rfc/rfc8288",
     "Link headers on the homepage point agents to machine-readable files without parsing any HTML.",
     "Send a Link header on the homepage, for example Link: </.well-known/api-catalog>; rel=\"api-catalog\". Registered types: api-catalog, service-desc, service-doc, describedby."),
    ("dnsAid", "discoverability", "DNS for AI Discovery (DNS-AID)", "content", True, "https://datatracker.ietf.org/doc/draft-mozleywilliams-dnsop-dnsaid/",
     "DNS records let agents find your agent endpoints before they open any page.",
     "Publish SVCB records under _index._agents.your-domain (and _a2a._agents.your-domain for A2A agents), and sign the zone with DNSSEC."),
    ("markdownNegotiation", "content", "Markdown negotiation", "content", True, "https://developers.cloudflare.com/fundamentals/reference/markdown-for-agents/",
     "Agents read Markdown with far fewer tokens than HTML.",
     "When a request sends Accept: text/markdown, answer with a Markdown version of the page and Content-Type: text/markdown. Cloudflare's Markdown for Agents does this for you."),
    ("searchForAgents", "content", "Search for Agents", "content", False, "https://developers.cloudflare.com/ai-search/how-to/search-for-agents/",
     "One search request is cheaper for an agent than crawling many pages.",
     "Serve GET /.well-known/search?q={query} returning Markdown (Content-Type: text/markdown) and advertise it with agent:endpoint meta tags."),
    ("robotsTxtAiRules", "bots", "AI bot rules in robots.txt", "content", True, "https://developers.cloudflare.com/ai-crawl-control/",
     "Naming AI crawlers lets you treat search bots (they put you in AI answers) and training bots differently.",
     "Add User-agent groups for OAI-SearchBot, Claude-SearchBot and PerplexityBot (search), and GPTBot, ClaudeBot and Google-Extended (training), with Allow or Disallow rules. A * group with rules also counts."),
    ("contentSignals", "bots", "Content Signals in robots.txt", "content", True, "https://contentsignals.org/",
     "Content Signals say how AI may use your content: train on it, search it, or answer from it.",
     "Add this line under a User-agent group in robots.txt: Content-Signal: ai-train=no, search=yes, ai-input=no"),
    ("webBotAuth", "bots", "Web Bot Auth", "app", False, "https://datatracker.ietf.org/wg/webbotauth/about/",
     "Web Bot Auth lets a bot prove who it is, so sites can trust signed requests.",
     "Publish a key set (JWKS) at /.well-known/http-message-signatures-directory and sign your own bot's requests."),
    ("apiCatalog", "discovery", "API catalog", "app", True, "https://www.rfc-editor.org/rfc/rfc9727",
     "An API catalog lets agents find your APIs on their own.",
     "Serve /.well-known/api-catalog as application/linkset+json with a linkset array. Each entry needs an anchor, service-desc (OpenAPI) and service-doc."),
    ("oauthDiscovery", "discovery", "OAuth / OIDC discovery", "app", False, "https://www.rfc-editor.org/rfc/rfc8414",
     "Agents need to know how to sign in to your APIs.",
     "Serve /.well-known/openid-configuration or /.well-known/oauth-authorization-server with issuer, authorization_endpoint, token_endpoint and jwks_uri."),
    ("oauthProtectedResource", "discovery", "OAuth Protected Resource", "app", False, "https://www.rfc-editor.org/rfc/rfc9728",
     "It tells an agent which authorization server issues tokens for your API.",
     "Serve /.well-known/oauth-protected-resource with resource and authorization_servers (and scopes_supported)."),
    ("authMd", "discovery", "Auth.md agent registration", "app", False, "https://workos.com/auth-md",
     "Auth.md explains how an agent can register and get credentials.",
     "Serve /auth.md as Markdown with a heading that contains auth.md, and describe how agents register."),
    ("mcpServerCard", "discovery", "MCP Server Card", "app", True, "https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2127",
     "A server card tells agents which MCP server you run and how to connect, before they connect.",
     "Serve /.well-known/mcp/server-card.json with serverInfo (name, version), the transport endpoint and capabilities."),
    ("a2aAgentCard", "discovery", "A2A Agent Card", "app", False, "https://a2a-protocol.org/latest/topics/agent-discovery/",
     "An agent card lets other agents find and talk to your agent.",
     "Serve /.well-known/agent-card.json with name, version, description, supportedInterfaces, capabilities and skills."),
    ("agentSkills", "discovery", "Agent Skills index", "app", True, "https://github.com/cloudflare/agent-skills-discovery-rfc",
     "A skills index lets agents load instructions for your site on demand.",
     "Serve /.well-known/agent-skills/index.json with a $schema and a skills array (name, type, description, url, sha256 digest)."),
    ("webMcp", "discovery", "WebMCP", "app", True, "https://webmachinelearning.github.io/webmcp/",
     "WebMCP lets a page offer its actions to a browser agent as tools.",
     "Register tools with document.modelContext.registerTool(), or add toolname and tooldescription to your HTML forms."),
    ("ard", "discovery", "ARD (Agentic Resource Discovery)", "app", True, "https://agenticresourcediscovery.org/",
     "One catalog tells agents which MCP servers, agents, skills and APIs you offer.",
     "Serve /.well-known/ai-catalog.json with specVersion, a host object and an entries array (each entry has an identifier, displayName, type and exactly one of url or data)."),
    ("personalAgentProtocol", "discovery", "Personal Agent Protocol (Poppy)", "app", False, "https://personalagentprotocol.org/docs/spec#3-discovery",
     "Poppy discovery lets a personal agent find how to act for a user on your service.",
     "Serve /.well-known/poppy.json with protocol_version, organization (name, domain) and web."),
    ("x402", "commerce", "x402 payments", "commerce", False, "https://x402.org",
     "x402 lets an agent pay for an HTTP request on its own.",
     "Serve a manifest at /.well-known/x402 (x402Version) and answer paid routes with HTTP 402 and the payment terms."),
    ("mpp", "commerce", "MPP (Machine Payment Protocol)", "commerce", False, "https://mpp.dev",
     "MPP payment details in your OpenAPI file tell agents which calls cost money.",
     "Serve /openapi.json and add x-payment-info (intent, method, amount) to the payable operations."),
    ("ucp", "commerce", "Universal Commerce Protocol", "commerce", False, "https://ucp.dev/specification/overview/",
     "UCP lets an agent discover how to buy from you.",
     "Serve /.well-known/ucp with protocol_version, services, capabilities and endpoints."),
    ("acp", "commerce", "ACP (Agentic Commerce Protocol)", "commerce", False, "https://agenticcommerce.dev",
     "ACP lets an agent find your checkout API.",
     "Serve /.well-known/acp.json with protocol (name acp, version), api_base_url, transports and capabilities.services."),
)
META = {m[0]: {"group": m[1], "label": m[2], "profile": m[3], "scored": m[4], "spec": m[5], "why": m[6], "fix": m[7]} for m in _META}
ORDER = [m[0] for m in _META]

# Files fetched from the site root (name: path and extra request headers). "page" and "page_md" use the scanned page.
JSON_ACCEPT = {"Accept": "application/json, application/linkset+json, text/markdown;q=0.8, */*;q=0.5"}
PROBES: dict[str, tuple[str, dict]] = {
    "robots": ("/robots.txt", {"Accept": "text/plain, */*;q=0.5"}),
    "home": ("/", {}),
    "sitemap": ("/sitemap.xml", {"Accept": "application/xml, text/xml, */*;q=0.5"}),
    "search": ("/.well-known/search?q=ai", {"Accept": "text/markdown, */*;q=0.5"}),
    "webbotauth": ("/.well-known/http-message-signatures-directory", JSON_ACCEPT),
    "apicatalog": ("/.well-known/api-catalog", JSON_ACCEPT),
    "oidc": ("/.well-known/openid-configuration", JSON_ACCEPT),
    "oauth_as": ("/.well-known/oauth-authorization-server", JSON_ACCEPT),
    "oauth_prm": ("/.well-known/oauth-protected-resource", JSON_ACCEPT),
    "authmd": ("/auth.md", {"Accept": "text/markdown, text/plain, */*;q=0.5"}),
    "mcp": ("/.well-known/mcp/server-card.json", JSON_ACCEPT),
    "a2a": ("/.well-known/agent-card.json", JSON_ACCEPT),
    "skills": ("/.well-known/agent-skills/index.json", JSON_ACCEPT),
    "ard": ("/.well-known/ai-catalog.json", JSON_ACCEPT),
    "poppy": ("/.well-known/poppy.json", JSON_ACCEPT),
    "x402": ("/.well-known/x402", JSON_ACCEPT),
    "openapi": ("/openapi.json", JSON_ACCEPT),
    "ucp": ("/.well-known/ucp", JSON_ACCEPT),
    "acp": ("/.well-known/acp.json", JSON_ACCEPT),
}
# DNS names under the site's domain: label, record type
DNS_PROBES = {"dns_index": ("_index._agents", "SVCB"), "dns_a2a": ("_a2a._agents", "SVCB"), "dns_mcp": ("_mcp._agents", "SVCB"),
              "dns_catalog": ("_catalog._agents", "TXT"), "dns_x402": ("_x402", "TXT")}


# ---------------------------------------------------------------------------
# Reading a fetched file
# ---------------------------------------------------------------------------
def _hdr(p: dict, name: str) -> str:
    return str((p.get("headers") or {}).get(name.lower()) or "")


def _ctype(p: dict) -> str:
    return _hdr(p, "content-type").split(";")[0].strip().lower()


def _text(p: dict, limit: int = 600_000) -> str:
    c = p.get("content") or b""
    if c[:2] == b"\x1f\x8b":  # a gzip file, for example sitemap.xml.gz
        try:
            c = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(c, 4_000_000)
        except Exception:
            pass
    return c[:limit].decode("utf-8", "replace")


def _looks_html(t: str) -> bool:
    head = t.lstrip()[:600].lower()
    return head.startswith(("<!doctype html", "<html", "<head", "<body")) or "<html" in head


def _state(p: dict | None) -> tuple[str, str]:
    """ok, missing (not there), blocked (refused), error (no answer or server error)."""
    if not p:
        return "error", "no answer"
    s = p.get("status")
    if s is None:
        err = str(p.get("error") or "no answer")
        if err.lower().startswith("response too large"):
            return "missing", "file too large"  # a huge answer is a web page or the wrong kind of file
        return "error", err[:80]
    if s == 200:
        return "ok", ""
    if s in (404, 410):
        return "missing", f"HTTP {s}"
    if s in (401, 403, 429, 451):
        return "blocked", f"HTTP {s}"
    if s >= 500:
        return "error", f"HTTP {s}"
    return "missing", f"HTTP {s}"


def _json(p: dict | None) -> tuple[str, str, object]:
    """Read a JSON file. Returns (outcome, message, data). outcome: ok, missing, unable or invalid."""
    st, note = _state(p)
    if st == "missing":
        return "missing", f"Not found ({note})", None
    if st == "blocked":
        return "unable", f"The server refused the request ({note})", None
    if st == "error":
        return "unable", f"Could not check ({note})", None
    t = _text(p)
    if not t.strip():
        return "missing", "The file is empty", None
    if _looks_html(t):
        return "missing", "It returned a web page, not a data file", None
    try:
        return "ok", "", json.loads(t)
    except Exception:
        return "invalid", "The file is not valid JSON", None


def _na(cid: str, outcome: str, msg: str) -> tuple[str, str]:
    """A file that is not there: a failure for a scored check, neutral for an optional one."""
    if outcome == "unable":
        return "unable", msg
    return ("fail" if META[cid]["scored"] else "neutral"), msg


def _str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    return f"{n} {word[:-1] + 'ies' if word.endswith('y') and not word.endswith('ay') else word + 's'}"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def parse_robots(text: str) -> dict:
    groups: list[dict] = []
    sitemaps: list[str] = []
    agentmap: list[str] = []
    signals: list[str] = []
    cur = None
    last_was_agent = False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            if cur is None or not last_was_agent:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
            cur["agents"].append(v.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if k == "sitemap":
            sitemaps.append(v)
        elif k == "agentmap":
            agentmap.append(v)
        elif k == "content-signal":
            signals.append(v)
            if cur is not None:
                cur["rules"].append((k, v))
        elif k in ("allow", "disallow", "crawl-delay") and cur is not None:
            cur["rules"].append((k, v))
    return {"groups": groups, "sitemaps": sitemaps, "agentmap": agentmap, "signals": signals}


def parse_signals(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in values:
        for part in v.split(","):
            if "=" in part:
                k, val = (x.strip().lower() for x in part.split("=", 1))
                if k in ("ai-train", "search", "ai-input") and val in ("yes", "no"):
                    out[k] = val
    return out


def parse_link_header(value: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<([^>]*)>\s*((?:;\s*[^,;<]+(?:="[^"]*"|=[^,;<]*)?\s*)*)', value or ""):
        d = {"url": m.group(1)}
        for pm in re.finditer(r';\s*([\w*-]+)\s*(?:=\s*(?:"([^"]*)"|([^;,\s]*)))?', m.group(2)):
            d[pm.group(1).lower()] = pm.group(2) if pm.group(2) is not None else (pm.group(3) or "")
        out.append(d)
    return out


def sitemap_info(p: dict | None) -> tuple[str, str, int]:
    """(outcome, message, number of entries). outcome: ok, missing, unable, invalid."""
    st, note = _state(p)
    if st == "missing":
        return "missing", f"Not found ({note})", 0
    if st == "blocked":
        return "unable", f"The server refused the request ({note})", 0
    if st == "error":
        return "unable", f"Could not check ({note})", 0
    t = _text(p, 3_000_000)
    head = t[:3000].lower()
    if "<urlset" in head or "<sitemapindex" in head:
        n = len(re.findall(r"<loc>", t, re.I))
        if n:
            return "ok", "", n
        return "invalid", "The sitemap lists no pages", 0
    if _looks_html(t):
        return "missing", "It returned a web page, not a sitemap", 0
    return "invalid", "The file is not a sitemap", 0


# ---------------------------------------------------------------------------
# The checks. Each takes the fetched files P and returns (status, message).
# status: pass, fail, neutral (an optional standard that is not used) or unable (could not check)
# ---------------------------------------------------------------------------
def ev_robotsTxt(P):
    p = P.get("robots")
    st, note = _state(p)
    if st == "missing":
        return _na("robotsTxt", "missing", f"Not found ({note})")
    if st != "ok":
        return "unable", f"Could not check ({note})"
    t = _text(p)
    if _looks_html(t) or _ctype(p) == "text/html":
        return "fail", "It returned a web page, not a text file"
    r = parse_robots(t)
    if not r["groups"]:
        return "fail", "No User-agent line found"
    if not sum(len(g["rules"]) for g in r["groups"]):
        return "fail", "No Allow or Disallow rules found"
    if _ctype(p) not in ("text/plain", ""):
        return "fail", f"Content-Type is {_ctype(p)}, it should be text/plain"
    return "pass", f"Found, {_plural(len(r['groups']), 'rule group')}"


def ev_sitemap(P):
    robots = parse_robots(_text(P["robots"])) if _state(P.get("robots"))[0] == "ok" else {"sitemaps": []}
    out, msg, n = sitemap_info(P.get("sitemap_declared") or P.get("sitemap"))
    if out == "ok":
        return "pass", f"{_plural(n, 'entry')}" + (", listed in robots.txt" if robots["sitemaps"] else ", but robots.txt does not list it")
    return _na("sitemap", out if out != "invalid" else "missing", msg)


def ev_linkHeaders(P):
    p = P.get("home")
    st, note = _state(p)
    if st in ("blocked", "error"):
        return "unable", f"Could not check the homepage ({note})"
    links = parse_link_header(_hdr(p, "link"))
    rels: set[str] = set()
    for lk in links:
        r = set((lk.get("rel") or "").lower().split())
        if lk.get("hreflang"):
            r.discard("alternate")  # a language link is for people, not agents
        rels |= r
    hit = sorted(rels & LINK_RELS, key=lambda x: ["api-catalog", "service-desc", "service-doc", "describedby", "alternate"].index(x))
    if hit:
        return "pass", "Found: " + ", ".join(hit)
    if links:
        return "fail", "The homepage sends Link headers, but none points to an API catalog, service description, documentation or alternate version"
    return "fail", "The homepage sends no Link header"


def _svcb_ok(data: str) -> bool:
    m = re.match(r"\s*(\d+)\s+(\S+)", str(data))
    return bool(m and int(m.group(1)) >= 1 and m.group(2) not in ("", "."))  # priority 0 is AliasMode


def ev_dnsAid(P):
    d = P.get("dns") or {}
    looked = [d[k] for k in ("dns_index", "dns_a2a", "dns_mcp") if d.get(k)]
    found, errors, dnssec = [], 0, False
    for r in looked:
        if r.get("error"):
            errors += 1
            continue
        if any(_svcb_ok(a) for a in r.get("answers", [])):
            found.append(r["name"])
            dnssec = dnssec or bool(r.get("ad"))
    if found:
        return "pass", f"Found {found[0]}" + (", DNSSEC checked" if dnssec else ", DNSSEC not confirmed")
    if not looked or errors == len(looked):
        return "unable", "The DNS lookup failed" if looked else "The DNS lookup did not run"
    return "fail", "No SVCB records under _index._agents or _a2a._agents"


def ev_markdownNegotiation(P):
    p = P.get("page_md")
    st, note = _state(p)
    if st in ("blocked", "error"):
        return "unable", f"Could not check ({note})"
    if st == "missing":
        return "fail", f"The page answered {note}"
    ct = _ctype(p)
    if ct in ("text/markdown", "text/x-markdown"):
        tok = _hdr(p, "x-markdown-tokens")
        return "pass", "Markdown served" + (f", about {tok} tokens" if tok else "")
    return "fail", f"The server answered {ct or 'a web page'} to Accept: text/markdown"


def ev_searchForAgents(P):
    p = P.get("search")
    st, note = _state(p)
    if st in ("blocked", "error"):
        return "unable", f"Could not check ({note})"
    if st == "ok" and (_ctype(p) in ("text/markdown", "text/x-markdown") or _hdr(p, "accept-query")):
        return "pass", "A search endpoint answers with Markdown"
    return "neutral", "No search endpoint at /.well-known/search"


def ev_robotsTxtAiRules(P):
    p = P.get("robots")
    st, note = _state(p)
    if st == "missing":
        return "fail", f"No robots.txt ({note})"
    if st != "ok":
        return "unable", f"Could not check ({note})"
    t = _text(p)
    if _looks_html(t):
        return "fail", "No robots.txt (it returned a web page)"
    r = parse_robots(t)
    named = sorted({a for g in r["groups"] for a in g["agents"] if a in _AI_LOWER})
    if named:
        shown = [x for x in AI_CRAWLERS if x.lower() in named]
        return "pass", f"{_plural(len(named), 'AI crawler')} named: " + ", ".join(shown[:4]) + (" and more" if len(shown) > 4 else "")
    if any("*" in g["agents"] and g["rules"] for g in r["groups"]):
        return "pass", "No AI crawler is named, but the * group sets rules for all crawlers"
    return "fail", "No rules for AI crawlers"


def ev_contentSignals(P):
    p = P.get("robots")
    st, note = _state(p)
    if st != "ok":
        return ("unable", f"Could not check ({note})") if st != "missing" else ("fail", "No robots.txt")
    sig = parse_signals(parse_robots(_text(p))["signals"])
    if sig:
        return "pass", ", ".join(f"{k}={v}" for k, v in sig.items())
    return "fail", "No Content-Signal line in robots.txt"


def ev_webBotAuth(P):
    out, msg, obj = _json(P.get("webbotauth"))
    if out != "ok":
        return _na("webBotAuth", out, msg)
    keys = obj.get("keys") if isinstance(obj, dict) else None
    good = [k for k in keys or [] if isinstance(k, dict) and k.get("kty")]
    if good:
        return "pass", _plural(len(good), "public key") + " published"
    return "fail", "The key set has no public keys"


def ev_apiCatalog(P):
    p = P.get("apicatalog")
    out, msg, obj = _json(p)
    if out != "ok":
        return _na("apiCatalog", out, msg)
    ls = obj.get("linkset") if isinstance(obj, dict) else None
    entries = [e for e in ls or [] if isinstance(e, dict) and _str(e.get("anchor"))]
    if not entries:
        return "fail", "No linkset array with an anchor"
    note = "" if "linkset+json" in _ctype(p) else " (the Content-Type should be application/linkset+json)"  # isitagentready.com also accepts a missing type
    return "pass", _plural(len(entries), "API") + " listed" + note


def ev_oauthDiscovery(P):
    seen = None
    for key in ("oidc", "oauth_as"):
        out, msg, obj = _json(P.get(key))
        if out == "unable":
            seen = seen or (out, msg)
        if out == "ok" and isinstance(obj, dict):
            need = [f for f in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri") if not _str(obj.get(f))]
            if not need:
                return "pass", "Found, issuer " + str(obj["issuer"])[:60]
            return "neutral", "Found, but missing " + ", ".join(need)
    if seen:
        return "unable", seen[1]
    return "neutral", "No OpenID or OAuth metadata"


def ev_oauthProtectedResource(P):
    out, msg, obj = _json(P.get("oauth_prm"))
    if out != "ok":
        return _na("oauthProtectedResource", out, "No protected resource metadata" if out == "missing" else msg)
    if isinstance(obj, dict) and _str(obj.get("resource")) and isinstance(obj.get("authorization_servers"), list) and obj["authorization_servers"]:
        return "pass", "Found, resource " + str(obj["resource"])[:60]
    return "neutral", "Found, but resource or authorization_servers is missing"


def ev_authMd(P):
    p = P.get("authmd")
    st, note = _state(p)
    if st in ("blocked", "error"):
        return "unable", f"Could not check ({note})"
    if st != "ok":
        return "neutral", "No /auth.md file"
    t = _text(p)
    if _looks_html(t):
        return "neutral", "No /auth.md file (it returned a web page)"
    h1 = re.search(r"^#\s+(.+)$", t, re.M)
    if h1 and "auth.md" in h1.group(1).lower():
        return "pass", "Found"
    return "neutral", "Found, but no heading contains auth.md"


def _endpoint(o: dict) -> bool:
    for key in ("endpoint", "url"):
        if _str(o.get(key)):
            return True
    t = o.get("transport")
    if isinstance(t, dict) and (_str(t.get("endpoint")) or _str(t.get("url"))):
        return True
    for key in ("transports", "remotes", "endpoints"):
        for x in o.get(key) or []:
            if isinstance(x, dict) and (_str(x.get("endpoint")) or _str(x.get("url"))):
                return True
    return False


def ev_mcpServerCard(P):
    out, msg, obj = _json(P.get("mcp"))
    if out != "ok":
        return _na("mcpServerCard", out, msg)
    if not isinstance(obj, dict):
        return "fail", "The file is not a JSON object"
    si = obj.get("serverInfo") if isinstance(obj.get("serverInfo"), dict) else {}
    name = si.get("name") if _str(si.get("name")) else obj.get("name")
    version = si.get("version") if _str(si.get("version")) else obj.get("version")
    miss = []
    if not _str(name):
        miss.append("a name (serverInfo.name)")
    if not _str(version):
        miss.append("a version (serverInfo.version)")
    if not _endpoint(obj):
        miss.append("an endpoint (url, transport.endpoint or remotes)")
    if miss:
        return "fail", "Missing " + ", ".join(miss)
    return "pass", f"{name} {version}"[:80]


def ev_a2aAgentCard(P):
    out, msg, obj = _json(P.get("a2a"))
    if out != "ok":
        return _na("a2aAgentCard", out, msg)
    if not isinstance(obj, dict):
        return "fail", "The file is not a JSON object"
    miss = [f for f in ("name", "version", "description") if not _str(obj.get(f))]
    ifaces = obj.get("supportedInterfaces")
    if not (isinstance(ifaces, list) and any(isinstance(i, dict) and _str(i.get("url")) for i in ifaces)) and not _str(obj.get("url")):
        miss.append("supportedInterfaces")
    if not isinstance(obj.get("capabilities"), dict):
        miss.append("capabilities")
    skills = obj.get("skills")
    if not (isinstance(skills, list) and skills and all(isinstance(s, dict) and _str(s.get("id")) and _str(s.get("name")) and _str(s.get("description")) for s in skills)):
        miss.append("skills (id, name, description)")
    if miss:
        return "fail", "Missing " + ", ".join(miss)
    return "pass", f"{obj['name']}, {_plural(len(skills), 'skill')}"[:90]


_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def ev_agentSkills(P):
    out, msg, obj = _json(P.get("skills"))
    if out != "ok":
        return _na("agentSkills", out, msg)
    if not isinstance(obj, dict):
        return "fail", "The file is not a JSON object"
    if "agentskills.io/discovery/" not in str(obj.get("$schema") or ""):
        return "fail", "The $schema field is missing or is not the Agent Skills discovery schema"
    skills = obj.get("skills")
    if not (isinstance(skills, list) and skills):
        return "fail", "The skills array is empty"
    for s in skills:
        if not isinstance(s, dict):
            return "fail", "A skill entry is not an object"
        nm = s.get("name")
        if not (isinstance(nm, str) and _SKILL_NAME.match(nm)):
            return "fail", f"A skill name is not lowercase letters, digits and hyphens ({str(nm)[:30]})"
        for f in ("type", "description", "url", "digest"):
            if not _str(s.get(f)):
                return "fail", f"Skill {nm} has no {f}"
        if s["type"] not in ("skill-md", "archive"):
            return "fail", f"Skill {nm} has type {s['type']}, it should be skill-md or archive"
        if not _DIGEST.match(s["digest"]):
            return "fail", f"Skill {nm} has a digest that is not sha256:<64 hex digits>"
    return "pass", _plural(len(skills), "skill") + " listed"


def ev_webMcp(P):
    found = []
    home = P.get("home")
    st, note = _state(home)
    if st in ("blocked", "error"):
        return "unable", f"Could not check the homepage ({note})"
    html = _text(home) if st == "ok" else ""
    if re.search(r"<form\b[^>]*\btoolname\s*=", html, re.I):
        found.append("a form with toolname")
    for label, t in [("the homepage", html)] + [(urlparse(s.get("url") or "").path or "a script", _text(s)) for s in (P.get("scripts") or [])]:
        if re.search(r"modelContext", t) and re.search(r"registerTool|provideContext", t):
            found.append(f"modelContext in {label}")
            break
    if found:
        return "pass", "Found " + found[0] + ". We read the code, we do not run the page"
    return "fail", "No WebMCP tools found in the homepage code. We do not run the page in a browser"


def ard_problems(obj) -> tuple[list[str], list[str]]:
    """Hard problems fail the check. Notes (identifier style) are only reported."""
    if not isinstance(obj, dict):
        return ["The file is not a JSON object"], []
    missing, other, notes = [], [], []
    if not _str(obj.get("specVersion")):
        missing.append("specVersion")
    host = obj.get("host")
    if not (isinstance(host, dict) and _str(host.get("displayName")) and _str(host.get("identifier"))):
        missing.append("host (displayName, identifier)")
    entries = obj.get("entries")
    if not (isinstance(entries, list) and entries):
        missing.append("entries")
    else:
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                other.append(f"Entry {i + 1} is not an object")
                continue
            for f in ("identifier", "displayName", "type"):
                if not _str(e.get(f)):
                    other.append(f"Entry {i + 1} has no {f}")
            if ("url" in e) == ("data" in e):
                other.append(f"Entry {i + 1} needs exactly one of url or data")
            if _str(e.get("identifier")) and not str(e["identifier"]).startswith("urn:air:"):
                notes.append("identifiers do not use urn:air:")
    msg = (["Missing " + ", ".join(missing)] if missing else []) + other[:2]
    return (["; ".join(msg)] if msg else []), sorted(set(notes))


def ev_ard(P):
    out, msg, obj = _json(P.get("ard"))
    via = "/.well-known/ai-catalog.json"
    if out != "ok" and P.get("ard_alt"):
        out, msg, obj = _json(P["ard_alt"])
        via = "a catalog linked from robots.txt, the homepage or DNS"
    if out != "ok":
        return _na("ard", out, msg)
    bad, notes = ard_problems(obj)
    if bad:
        return "fail", bad[0][:140]
    return "pass", f"{_plural(len(obj['entries']), 'entry')} in {via}" + (f". Note: {notes[0]}" if notes else "")


def poppy_issuer_url(issuer: str) -> str | None:
    """RFC 8414: https://host/path becomes https://host/.well-known/oauth-authorization-server/path"""
    u = urlparse(issuer or "")
    if u.scheme != "https" or not u.hostname:
        return None
    path = u.path.rstrip("/")
    return f"https://{u.netloc}/.well-known/oauth-authorization-server{path}"


def _declares_interfaces(o: dict) -> bool:
    ag = o.get("agent") if isinstance(o.get("agent"), dict) else {}
    web = o.get("web") if isinstance(o.get("web"), dict) else {}
    return bool(ag.get("protocols") or o.get("apis") or web.get("browser_session_endpoint"))


def ev_personalAgentProtocol(P, host: str = ""):
    out, msg, obj = _json(P.get("poppy"))
    if out != "ok":
        return _na("personalAgentProtocol", out, "No /.well-known/poppy.json" if out == "missing" else msg)
    if not isinstance(obj, dict):
        return "neutral", "The file is not a JSON object"
    ver = str(obj.get("protocol_version") or "")
    if not ver:
        return "neutral", "No protocol_version"
    if not ver.startswith("0."):
        return "unable", f"Protocol version {ver} is not supported by this check"
    org = obj.get("organization") if isinstance(obj.get("organization"), dict) else {}
    if not _str(org.get("name")) or not _str(org.get("domain")):
        return "neutral", "organization.name or organization.domain is missing"
    h = host.lower().removeprefix("www.")
    if h and str(org["domain"]).lower().removeprefix("www.") != h:
        return "neutral", f"organization.domain ({org['domain']}) does not match {h}"
    if not isinstance(obj.get("web"), dict):
        return "neutral", "The web object is missing"
    if not _declares_interfaces(obj):
        return "pass", "Discovery found, no session interfaces declared"
    iss = (obj.get("auth") or {}).get("issuer") if isinstance(obj.get("auth"), dict) else None
    if not _str(iss):
        return "neutral", "Interfaces are declared but auth.issuer is missing"
    out2, msg2, meta = _json(P.get("poppy_issuer"))
    if out2 == "unable":
        return "unable", "Could not check the issuer: " + msg2
    if out2 != "ok" or not isinstance(meta, dict):
        return "neutral", "The issuer publishes no OAuth metadata"
    bad = []
    if meta.get("issuer") != iss:
        bad.append("issuer does not match auth.issuer")
    if org["domain"] not in (meta.get("poppy_domains") or []):
        bad.append("poppy_domains does not contain the organization domain")
    for f in ("token_endpoint", "revocation_endpoint"):
        if not str(meta.get(f) or "").startswith("https://"):
            bad.append(f"{f} is not an HTTPS URL")
    if bad:
        return "neutral", "Issuer problem: " + "; ".join(bad)
    return "pass", "Discovery found, issuer verified"


def ev_x402(P):
    out, msg, obj = _json(P.get("x402"))
    if out == "ok" and isinstance(obj, dict) and obj.get("x402Version") is not None:
        return "pass", f"Manifest found, x402Version {obj['x402Version']}"
    pg = P.get("page_md") or P.get("home")
    if pg and pg.get("status") == 402:
        t = _text(pg)
        if "x402Version" in t or "accepts" in t or _hdr(pg, "payment-required") or _hdr(pg, "x-payment"):
            return "pass", "The page answers HTTP 402 with payment terms"
    d = (P.get("dns") or {}).get("dns_x402")
    if d and any("x402" in str(a).lower() or "k=" in str(a) for a in d.get("answers", [])) and not d.get("error"):
        return "pass", "A _x402 DNS record points to the payment manifest"
    if out == "unable":
        return "unable", msg
    return "fail", "No /.well-known/x402 manifest and no HTTP 402 payment answer"


_MPP_METHODS = {"tempo", "stripe", "lightning", "card"}


def ev_mpp(P):
    out, msg, obj = _json(P.get("openapi"))
    if out != "ok":
        return _na("mpp", out, "No /openapi.json" if out == "missing" else msg)
    paths = obj.get("paths") if isinstance(obj, dict) else None
    good = bad = 0
    for ops in (paths or {}).values():
        for op in (ops.values() if isinstance(ops, dict) else []):
            info = op.get("x-payment-info") if isinstance(op, dict) else None
            if info is None:
                continue
            methods = info.get("method") if isinstance(info, dict) else None
            methods = methods if isinstance(methods, list) else [methods]
            if isinstance(info, dict) and info.get("intent") in ("charge", "session") and any(m in _MPP_METHODS for m in methods) and info.get("amount") not in (None, ""):
                good += 1
            else:
                bad += 1
    if good:
        return "pass", _plural(good, "payable operation") + " described"
    if bad:
        return "fail", "x-payment-info needs intent (charge or session), method and amount"
    return "fail", "openapi.json has no x-payment-info on any operation"


def ev_ucp(P):
    out, msg, obj = _json(P.get("ucp"))
    if out != "ok":
        return _na("ucp", out, msg)
    if not isinstance(obj, dict):
        return "fail", "The file is not a JSON object"
    miss = [f for f in ("protocol_version", "services", "capabilities", "endpoints") if not obj.get(f)]
    if miss:
        return "fail", "Missing " + ", ".join(miss)
    return "pass", "Found, protocol " + str(obj["protocol_version"])[:20]


def ev_acp(P):
    out, msg, obj = _json(P.get("acp"))
    if out != "ok":
        return _na("acp", out, msg)
    if not isinstance(obj, dict):
        return "fail", "The file is not a JSON object"
    proto = obj.get("protocol") if isinstance(obj.get("protocol"), dict) else {}
    miss = []
    if proto.get("name") != "acp":
        miss.append('protocol.name "acp"')
    if not _str(proto.get("version")):
        miss.append("protocol.version")
    if not re.match(r"^https?://\S+$", str(obj.get("api_base_url") or "")):
        miss.append("api_base_url")
    if not (isinstance(obj.get("transports"), list) and obj["transports"]):
        miss.append("transports")
    caps = obj.get("capabilities") if isinstance(obj.get("capabilities"), dict) else {}
    if not (isinstance(caps.get("services"), list) and caps["services"]):
        miss.append("capabilities.services")
    if miss:
        return "fail", "Missing " + ", ".join(miss)
    return "pass", "Found, protocol version " + str(proto["version"])[:20]


EVALUATORS = {cid: globals()["ev_" + cid] for cid in ORDER}

# Which fetched files each check looked at (shown as the evidence)
EVIDENCE = {
    "robotsTxt": ["robots"], "sitemap": ["sitemap_declared", "sitemap"], "linkHeaders": ["home"], "dnsAid": ["dns_index", "dns_a2a", "dns_mcp"],
    "markdownNegotiation": ["page_md"], "searchForAgents": ["search"], "robotsTxtAiRules": ["robots"], "contentSignals": ["robots"],
    "webBotAuth": ["webbotauth"], "apiCatalog": ["apicatalog"], "oauthDiscovery": ["oidc", "oauth_as"], "oauthProtectedResource": ["oauth_prm"],
    "authMd": ["authmd"], "mcpServerCard": ["mcp"], "a2aAgentCard": ["a2a"], "agentSkills": ["skills"], "webMcp": ["home"],
    "ard": ["ard", "ard_alt"], "personalAgentProtocol": ["poppy", "poppy_issuer"], "x402": ["x402", "dns_x402"], "mpp": ["openapi"],
    "ucp": ["ucp"], "acp": ["acp"],
}


def _evidence(P: dict, names: list[str]) -> list[dict]:
    out = []
    for n in names:
        p = (P.get("dns") or {}).get(n) if n.startswith("dns_") else P.get(n)
        if not p:
            continue
        if n.startswith("dns_"):
            out.append({"path": p.get("name", n) + " " + p.get("type", ""), "status": "found" if p.get("answers") else ("error" if p.get("error") else "none"), "note": ""})
        else:
            u = urlparse(p.get("req") or p.get("url") or "")
            path, q = u.path or n, u.query
            out.append({"path": path + (("?" + q) if q else ""), "status": p.get("status"), "note": (p.get("error") or "")[:60] if p.get("status") is None else _ctype(p)})
    return out


# ---------------------------------------------------------------------------
# Level (0 to 5), as the scale of isitagentready.com: each level needs all the ones before it
# ---------------------------------------------------------------------------
def level_for(st: dict[str, str]) -> dict:
    ok = lambda k: st.get(k) == "pass"  # noqa: E731
    integ = [ok(k) for k in ("mcpServerCard", "a2aAgentCard", "agentSkills", "apiCatalog")]
    auth = ok("oauthDiscovery") or ok("authMd") or ok("oauthProtectedResource")
    steps = [
        (1, sum(ok(k) for k in ("robotsTxt", "sitemap", "linkHeaders")) >= 2, "two of robots.txt, a sitemap and Link headers"),
        (2, ok("robotsTxtAiRules") and ok("contentSignals"), "AI bot rules and Content Signals in robots.txt"),
        (3, ok("markdownNegotiation"), "Markdown negotiation"),
        (4, any(integ), "one of MCP server card, A2A agent card, Agent Skills or API catalog"),
        (5, sum([ok("webBotAuth"), all(ok(k) for k in ("mcpServerCard", "agentSkills", "apiCatalog")), auth]) >= 2 or all(ok(k) for k in ("mcpServerCard", "agentSkills", "apiCatalog")),
         "MCP server card, Agent Skills and API catalog together, or two of Web Bot Auth, all integrations and OAuth or Auth.md metadata"),
    ]
    level = 0
    for n, met, _ in steps:
        if not met:
            break
        level = n
    nxt = steps[level] if level < 5 else None
    return {"level": level, "name": LEVELS[level], "next": {"level": nxt[0], "name": LEVELS[nxt[0]], "needs": nxt[2]} if nxt else None}


_SHOP_PLATFORMS = re.compile(r"woocommerce|cdn\.shopify\.com|shopify\.theme|\bMage\.|magento|bigcommerce|prestashop|wix-?stores|squarespace-commerce|opencart", re.I)
_SHOP_WORDS = re.compile(r"add[- ]to[- ](?:cart|basket|bag)|shopping[- ]cart|view[- ]cart|הוסף(?:ה)? ל(?:סל|עגלה)|לעגלה|סל קניות", re.I)


def commerce_signals(html: str) -> list[str]:
    """Does the page look like an online shop? Used only to word the payment checks."""
    out = []
    if _SHOP_PLATFORMS.search(html or ""):
        out.append("shop platform")
    if re.search(r'"@type"\s*:\s*"(?:Product|Offer|ProductGroup)"|property=["\']og:type["\'][^>]*content=["\']product|product:price', html or "", re.I):
        out.append("product data")
    if _SHOP_WORDS.search(html or "") and re.search(r'href=["\'][^"\']*/(?:cart|checkout|basket)\b', html or "", re.I):
        out.append("cart and checkout links")
    return out


def evaluate(P: dict, host: str = "") -> dict:
    checks, st = [], {}
    home = P.get("home")
    shop = commerce_signals(_text(home)) if _state(home)[0] == "ok" else []
    for cid in ORDER:
        m = META[cid]
        try:
            ev = EVALUATORS[cid]
            status, message = ev(P, host) if cid == "personalAgentProtocol" else ev(P)
        except Exception as e:  # one broken answer must not hide the other checks
            status, message = "unable", f"Could not read the answer ({type(e).__name__})"
        if not m["scored"] and status == "fail":
            status = "neutral"  # an optional standard is never a failure
        if m["profile"] == "commerce" and status == "neutral" and not shop:
            message += " (not a shop)"
        st[cid] = status
        c = {"id": cid, "group": m["group"], "label": m["label"], "profile": m["profile"], "scored": m["scored"],
             "status": status, "message": message, "evidence": _evidence(P, EVIDENCE.get(cid, [])),
             "why": m["why"], "fix": m["fix"], "spec": m["spec"]}
        if cid == "webMcp" and status != "pass":
            c["browser"] = True  # a real browser can find tools that this reading of the code cannot
        checks.append(c)
    scored = [c for c in checks if c["scored"] and c["status"] != "unable"]
    groups = []
    for key, label in GROUPS:
        cs = [c for c in checks if c["group"] == key]
        sc = [c for c in cs if c["scored"] and c["status"] != "unable"]
        groups.append({"key": key, "label": label, "passed": sum(1 for c in sc if c["status"] == "pass"), "scored": len(sc),
                       "optional_passed": sum(1 for c in cs if not c["scored"] and c["status"] == "pass"), "ids": [c["id"] for c in cs]})
    unable = sum(1 for c in checks if c["status"] == "unable")
    lvl = level_for(st)
    passed = sum(1 for c in scored if c["status"] == "pass")
    return {"level": lvl["level"], "level_name": lvl["name"], "next": lvl["next"],
            "passed": passed, "scored": len(scored), "percent": round(100 * passed / len(scored)) if scored else None, "unable": unable,
            "reachable": unable < len(checks) * 0.6, "is_commerce": bool(shop), "shop_signals": shop, "groups": groups, "checks": checks}
