"""Offline tests for api/_checks.py. Run: python -m unittest discover -s tests"""

import gzip
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import _checks as C  # noqa: E402


def probe(status=200, chars=1000, head="", ms=100, error=None):
    return {"status": status, "chars": chars, "bytes": chars, "head": head, "ms": ms, "error": error}


BASE = probe(200, 1000)
CORE = C.BOTS_BY_NAME["GPTBot"]


class BotJudging(unittest.TestCase):
    def test_ok(self):
        r = C.judge_bot(probe(200, 1000), BASE, CORE)
        self.assertEqual((r["verdict"], r["ratio"]), ("ok", 1.0))

    def test_forbidden_is_blocked(self):
        self.assertEqual(C.judge_bot(probe(403, 200), BASE, CORE)["verdict"], "blocked")

    def test_no_answer_is_error(self):
        self.assertEqual(C.judge_bot(probe(None, 0, error="Timeout"), BASE, CORE)["verdict"], "error")

    def test_cloudflare_challenge(self):
        r = C.judge_bot(probe(200, 1000, head="<title>just a moment...</title>"), BASE, CORE)
        self.assertEqual(r["verdict"], "challenge")

    def test_weak_word_ignored_when_page_delivered(self):
        # Wikipedia-style: the word "captcha" appears in a normal page
        r = C.judge_bot(probe(200, 1000, head="... captcha ..."), BASE, CORE)
        self.assertEqual(r["verdict"], "ok")

    def test_weak_word_counts_when_page_is_tiny(self):
        r = C.judge_bot(probe(200, 120, head="access denied"), BASE, CORE)
        self.assertIn(r["verdict"], ("challenge", "blocked"))

    def test_small_page_is_blocked_like_the_engine(self):
        self.assertEqual(C.judge_bot(probe(200, 200), BASE, CORE)["verdict"], "blocked")  # ratio 0.2 < 0.3

    def test_different_size_is_flagged_not_blocked(self):
        self.assertEqual(C.judge_bot(probe(200, 500), BASE, CORE)["verdict"], "different")
        self.assertEqual(C.judge_bot(probe(200, 2500), BASE, CORE)["verdict"], "different")

    def test_404_for_bot_only_counts_as_refused(self):
        self.assertEqual(C.judge_bot(probe(404, 50), BASE, CORE)["verdict"], "blocked")

    def test_public_rows_hide_private_flags(self):
        rows = {"GPTBot": C.judge_bot(probe(), BASE, CORE)}
        self.assertFalse(any(k.startswith("_") for k in C.public_rows(rows)["GPTBot"]))


class EngineCdnResult(unittest.TestCase):
    def rows(self, blocked=()):
        out = {}
        for n in C.ENGINE_CDN_BOTS:
            p = probe(403, 100) if n in blocked else probe(200, 1000)
            out[n] = C.judge_bot(p, BASE, C.BOTS_BY_NAME[n])
        return out

    def test_none(self):
        r = C.build_cdn_result(BASE, {"server": "nginx"}, self.rows())
        self.assertEqual((r.checked, r.any_blocked, r.block_pattern), (True, False, "none"))

    def test_targeted(self):
        r = C.build_cdn_result(BASE, {}, self.rows(blocked=("GPTBot",)))
        self.assertEqual((r.any_blocked, r.block_pattern), (True, "targeted"))

    def test_blanket(self):
        r = C.build_cdn_result(BASE, {}, self.rows(blocked=("GPTBot", "OAI-SearchBot", "PerplexityBot", "Claude-SearchBot")))
        self.assertEqual(r.block_pattern, "blanket")

    def test_cdn_detection(self):
        r = C.build_cdn_result(BASE, {"server": "cloudflare", "cf-ray": "x"}, self.rows())
        self.assertEqual(r.cdn_detected, "cloudflare")

    def test_unreachable_browser_skips_check(self):
        self.assertFalse(C.build_cdn_result(probe(None, 0), {}, self.rows()).checked)

    def test_six_bots_in_engine_order(self):
        r = C.build_cdn_result(BASE, {}, self.rows())
        self.assertEqual([b["bot"] for b in r.bot_results], list(C.ENGINE_CDN_BOTS))


PAGE = """<html lang="en"><head><title>Acme | Widgets</title><meta name="description" content="We make widgets.">
<meta name="robots" content="index, nofollow"><link rel="canonical" href="https://acme.test/">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Acme"},{"@type":["WebSite","Thing"]}]}</script>
<script type="application/ld+json">{ broken </script></head>
<body><h1>Acme</h1><h1>Second</h1><h2>A</h2><h4>Skipped</h4><h3></h3>
<p>%s</p><a href="/a">Learn more</a><a href="/b">Learn more</a><a href="/c">Learn more</a><a href="/d">Read more</a>
<a href="https://other.test/x">Other</a><a href="/e"></a><img src="a.png"><img src="b.png" alt=""><img src="c.png" alt="Logo">
<div style="display:none"><h2>Hidden</h2></div></body></html>""" % ("word " * 200)


class PageAnalysis(unittest.TestCase):
    def setUp(self):
        self.p = C.analyze_html(PAGE, "https://acme.test/")

    def test_basics(self):
        self.assertEqual(self.p["title"], "Acme | Widgets")
        self.assertEqual(self.p["lang"], "en")
        self.assertEqual(self.p["js_risk"], "low")
        self.assertFalse(self.p["thin"])

    def test_jsonld(self):
        self.assertEqual(self.p["jsonld_types"], ["Organization", "Thing", "WebSite"])
        self.assertEqual(self.p["jsonld_invalid"], 1)

    def test_headings(self):
        h = self.p["headings"]
        self.assertEqual(h["h1_count"], 2)
        self.assertGreaterEqual(h["skip_count"], 1)
        self.assertEqual(h["empty"], 1)  # the hidden h2 is ignored
        self.assertIsNotNone(C.check_headings(h))

    def test_links(self):
        lk = self.p["links"]
        self.assertEqual((lk["generic"], lk["empty"]), (4, 1))  # the image link is not "empty"
        self.assertEqual(lk["internal"], 5)
        self.assertIsNotNone(C.check_links(lk))

    def test_images(self):
        self.assertEqual(self.p["images"], {"total": 3, "missing_alt": 1})

    def test_nofollow(self):
        self.assertIsNotNone(C.check_crawl(self.p["robots_tokens"], None))
        self.assertIsNone(C.check_crawl(["noindex"], None))
        self.assertIsNotNone(C.check_crawl([], "nofollow"))


class JsOrThin(unittest.TestCase):
    def test_empty_spa_is_js(self):
        p = C.analyze_html('<html><body><div id="root"></div><script src="/app.js"></script></body></html>', "https://a.test/")
        self.assertEqual((p["js_risk"], p["thin"]), ("high", False))

    def test_enable_js_message_is_js(self):
        p = C.analyze_html("<html><body><noscript>You need to enable JavaScript to run this app.</noscript></body></html>", "https://a.test/")
        self.assertEqual(p["js_risk"], "high")

    def test_small_static_page_is_thin_not_js(self):
        html = "<html><body><h1>Example</h1><p>%s</p><p>%s</p><script src='/a.js'></script></body></html>" % (
            "one two three four five six seven eight nine", "ten eleven twelve thirteen fourteen fifteen sixteen")
        p = C.analyze_html(html, "https://a.test/")
        self.assertEqual((p["js_risk"], p["thin"]), ("low", True))
        self.assertIsNotNone(C.check_thin(True, p["word_count"], 0))

    def test_one_full_paragraph_and_a_script_is_thin(self):  # the real example.com of 2026
        p = C.analyze_html("<html><body><p>%s</p><script src=/s.js></script></body></html>" % ("This domain is for use in docs. " * 4), "https://a.test/")
        self.assertEqual((p["js_risk"], p["thin"]), ("low", True))

    def test_no_script_page_with_few_words_is_thin(self):
        p = C.analyze_html("<html><body><img src='a.jpg'><a href='/x'>Essays</a></body></html>", "https://a.test/")
        self.assertTrue(p["thin"])

    def test_medium(self):
        p = C.analyze_html("<html><body><p>%s</p><script src='/a.js'></script></body></html>" % ("w " * 80), "https://a.test/")
        self.assertEqual(p["js_risk"], "medium")


class Language(unittest.TestCase):
    HEB = "זהו טקסט בעברית על החברה שלנו והשירותים שאנחנו מציעים ללקוחות. " * 6

    def test_hebrew_text_with_english_lang(self):
        self.assertIsNotNone(C.language_mismatch("en-US", self.HEB))

    def test_hebrew_text_with_he_lang_is_fine(self):
        self.assertIsNone(C.language_mismatch("he", self.HEB))

    def test_english_text_with_he_lang(self):
        self.assertIsNotNone(C.language_mismatch("he", "This is an English sentence about our products. " * 10))

    def test_short_or_unknown_is_ignored(self):
        self.assertIsNone(C.language_mismatch("en", "Hi"))
        self.assertIsNone(C.language_mismatch("xx", self.HEB))


class Sitemap(unittest.TestCase):
    URLSET = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.test/1</loc></url><url><loc>https://a.test/2</loc></url></urlset>'

    def test_urlset(self):
        r = C.parse_sitemap("https://a.test/sitemap.xml", 200, self.URLSET)
        self.assertEqual((r["ok"], r["kind"], r["urls"]), (True, "urlset", 2))

    def test_gzip(self):
        r = C.parse_sitemap("https://a.test/s.xml.gz", 200, gzip.compress(self.URLSET))
        self.assertEqual(r["urls"], 2)

    def test_index(self):
        r = C.parse_sitemap("u", 200, b"<sitemapindex><sitemap><loc>x</loc></sitemap></sitemapindex>")
        self.assertEqual((r["kind"], r["children"]), ("index", 1))

    def test_html_is_not_a_sitemap(self):
        self.assertFalse(C.parse_sitemap("u", 200, b"<!DOCTYPE html><html></html>")["ok"])

    def test_404(self):
        self.assertEqual(C.parse_sitemap("u", 404, b"")["error"], "HTTP 404")

    def test_robots_lines(self):
        self.assertEqual(C.sitemap_lines("User-agent: *\nSitemap: https://a.test/s.xml\nsitemap:https://a.test/t.xml"),
                         ["https://a.test/s.xml", "https://a.test/t.xml"])

    def test_findings(self):
        ok = C.parse_sitemap("https://a.test/sitemap.xml", 200, self.URLSET)
        bad = C.parse_sitemap("https://a.test/sitemap.xml", 404, b"")
        self.assertIsNone(C.check_sitemap(ok, ["https://a.test/sitemap.xml"]))
        self.assertEqual(C.check_sitemap(ok, [])["priority"], "low")
        self.assertEqual(C.check_sitemap(bad, ["https://a.test/sitemap.xml"])["priority"], "medium")
        self.assertIsNone(C.check_sitemap(bad, []))  # nothing declared, nothing found: the engine reports it
        refused = C.parse_sitemap("https://a.test/sitemap.xml", 403, b"")
        self.assertIsNone(C.check_sitemap(refused, ["https://a.test/sitemap.xml"]))  # maybe just refused


class Findings(unittest.TestCase):
    def test_status(self):
        self.assertEqual(C.check_status(404, "x", 10)["label"], "Page not found")
        self.assertEqual(C.check_status(502, "x", 10)["category"], "status")
        self.assertEqual(C.check_status(200, "Page not found", 20)["label"], "This looks like an error page")
        self.assertIsNone(C.check_status(200, "Welcome", 500))
        self.assertIsNone(C.check_status(200, "Not found: why a lost page matters", 3000))

    def test_challenge_page_for_the_browser(self):
        self.assertTrue(C.is_challenge_page("<title>just a moment...</title>", 5000))
        self.assertTrue(C.is_challenge_page('window.gokuprops = {"key":', 2000))  # AWS WAF, HTTP 202
        self.assertFalse(C.is_challenge_page("just a moment", 90_000))  # a big real page that mentions it
        self.assertFalse(C.is_challenge_page("hello world", 500))
        self.assertEqual(C.check_blocked(202, True)["label"], "The site blocked the scan")
        self.assertIsNone(C.check_blocked(200, False))

    def test_bot_content_lists_are_short(self):
        base = probe()
        rows = {b["name"]: C.judge_bot(probe(200, 450), base, b) for b in C.BOTS}
        a = C.check_bot_content(rows)
        self.assertTrue(a["items"][0].startswith("All bots got a much smaller page"))
        self.assertEqual(a["priority"], "medium")

    def test_blocked_is_not_404_or_5xx(self):
        self.assertIsNotNone(C.check_blocked(403))
        self.assertIsNone(C.check_blocked(404))
        self.assertIsNone(C.check_blocked(500))
        self.assertIsNone(C.check_blocked(200))

    def test_bot_access_priorities(self):
        base = probe()
        rows = {b["name"]: C.judge_bot(probe(), base, b) for b in C.BOTS}
        self.assertEqual(C.check_bots(rows, 200), [])
        rows["ClaudeBot"] = C.judge_bot(probe(403, 50), base, C.BOTS_BY_NAME["ClaudeBot"])
        rows["Googlebot"] = C.judge_bot(probe(403, 50), base, C.BOTS_BY_NAME["Googlebot"])  # imitation refused: normal
        out = C.check_bots(rows, 200)
        self.assertEqual([(a["category"], a["priority"]) for a in out], [("access", "high")])
        self.assertNotIn("Googlebot", out[0]["items"][0])
        for n in ("GPTBot", "OAI-SearchBot", "ChatGPT-User"):
            rows[n] = C.judge_bot(probe(403, 50), base, C.BOTS_BY_NAME[n])
        self.assertEqual(C.check_bots(rows, 200)[0]["priority"], "critical")  # 4 of 8
        self.assertEqual(C.check_bots(rows, 403), [])  # the whole site refused us: reported once elsewhere

    def test_performance(self):
        self.assertEqual(C.check_performance([7000, 7500], 50_000, "gzip")["priority"], "high")
        self.assertEqual(C.check_performance([3000, 2800], 50_000, "gzip")["priority"], "medium")
        self.assertIsNone(C.check_performance([200, 300], 50_000, None))
        self.assertEqual(C.check_performance([200], 5_000_000, "br")["priority"], "medium")
        self.assertEqual(C.check_performance([200], 2_000_000, "br")["priority"], "low")
        self.assertIsNotNone(C.check_performance([200], 200_000, None))  # not compressed

    def test_title_desc(self):
        self.assertIsNotNone(C.check_title_desc("x" * 90, "fine " * 20))
        self.assertIsNone(C.check_title_desc("A good page title about widgets", "w" * 150))
        self.assertIsNone(C.check_title_desc("React", "w" * 150))  # a brand-only title is normal

    def test_cookie_banner(self):
        self.assertIsNotNone(C.check_cookie_banner("We use cookies. Accept all"))
        self.assertIsNone(C.check_cookie_banner("# Welcome\n\nOur cookie recipes..."[:30] + " zzz" * 200 + " cookie accept"))

    def test_https(self):
        self.assertEqual(C.check_https("http://a.test/")["priority"], "high")
        self.assertIsNone(C.check_https("https://a.test/"))


def audit(**over):
    """A healthy audit; tests break one thing at a time."""
    a = {"checks": {
        "meta_tags": {"details": {"has_title": True, "has_description": True, "has_canonical": True, "has_og_title": True, "has_og_description": True, "x_robots_noindex": False, "has_noai": False}},
        "schema_jsonld": {"details": {"found_types": ["Organization", "WebSite"], "has_website": True, "has_faq": False, "has_visible_faq": False}},
        "content": {"details": {"has_h1": True, "has_heading_hierarchy": True, "has_numbers": True, "has_front_loading": True, "word_count": 900}},
        "signals": {"details": {"has_lang": True, "has_rss": True}},
        "brand_entity": {"details": {"brand_name_consistent": True, "names_found": ["A"], "kg_pillar_count": 3, "has_contact_info": True, "has_about_link": True}},
        "robots_txt": {"details": {"bots_blocked": [], "sitemaps": ["https://a.test/s.xml"], "crawl_delay": None}},
        "llms_txt": {"details": {"found": True}},
        "ai_discovery": {"details": {"has_well_known_ai": True, "has_summary": True}},
    }, "google_ai": {"checks": [{"id": "G-BYLINE", "status": "pass"}, {"id": "G-DATES", "status": "pass"}, {"id": "G-LINKS", "status": "pass"}, {"id": "G-SNIPPET", "status": "pass"}]},
        "multimodal": {"total_images": 10, "images_with_alt": 10, "alt_coverage": 1.0}, "recommendations": []}
    for k, v in over.items():
        a["checks"][k] = {"details": v} if k in a["checks"] else v
    return a


PAGE_OK = {"ok": True, "browser_status": 200, "word_count": 900, "js_risk": "low", "thin": False, "html_bytes": 100_000, "final_url": "https://a.test/", "jsonld_types": ["Organization"], "markdown": "# Hi"}
GOOD_SITEMAP = {"files": {"declared": ["https://a.test/s.xml"], "sitemap": {"ok": True, "urls": 5, "children": 0, "status": 200, "kind": "urlset", "url": "https://a.test/s.xml"}}}


def ids(m):
    return {i["id"] for i in m["items"]}


class Missed(unittest.TestCase):
    def build(self, ai=None, aud=None, rows=None, files=None, score=80, actions=()):
        return C.build_missed({**PAGE_OK, **(ai or {})}, aud or audit(), rows or {}, files if files is not None else GOOD_SITEMAP["files"], score, list(actions))

    def test_healthy_page_misses_nothing(self):
        m = self.build()
        self.assertEqual((m["count"], m["items"], m["usable"]), (0, [], True))
        self.assertTrue(all(s["status"] == "ok" for s in m["stages"]))

    def test_unusable_pages_miss_one_thing(self):
        for ai, want in (({"browser_status": 404}, "not_found"), ({"browser_status": 503}, "server_error"), ({"browser_status": 403}, "site_blocked"),
                         ({"browser_status": 202, "browser_challenge": True}, "site_blocked"), ({"ok": False, "browser_status": 0, "error": "Timeout"}, "no_answer")):
            m = self.build(ai=ai)
            self.assertEqual([i["id"] for i in m["items"]], [want])
            self.assertFalse(m["usable"])
            self.assertEqual(m["stages"][0]["status"], "fail")
            self.assertEqual(m["stages"][1]["status"], "skipped")

    def test_refused_bots(self):
        base = probe()
        rows = {b["name"]: C.judge_bot(probe(), base, b) for b in C.BOTS}
        self.assertEqual(self.build(rows=rows)["count"], 0)
        rows["GPTBot"] = C.judge_bot(probe(403, 50), base, C.BOTS_BY_NAME["GPTBot"])
        rows["Googlebot"] = C.judge_bot(probe(403, 50), base, C.BOTS_BY_NAME["Googlebot"])
        m = self.build(rows=rows)
        item = [i for i in m["items"] if i["id"] == "bots_refused"][0]
        self.assertEqual(item["impact"], "high")
        self.assertEqual(item["title"], "An AI crawler was turned away")
        self.assertIn("1 of 8 AI crawlers was refused (GPTBot). It got HTTP 403", item["detail"])
        self.assertNotIn("Googlebot", item["detail"])  # an imitation refused by IP check is not a miss
        self.assertEqual(m["stages"][0]["status"], "fail")

    def test_javascript_only_and_thin(self):
        self.assertIn("js_only", ids(self.build(ai={"js_risk": "high", "word_count": 3})))
        m = self.build(ai={"thin": True, "word_count": 20})
        self.assertIn("thin", ids(m))
        self.assertNotIn("js_only", ids(m))

    def test_understand_gaps(self):
        aud = audit(meta_tags={"has_title": True, "has_description": False, "has_canonical": False, "has_og_title": False, "has_og_description": False},
                    content={"has_h1": False, "has_numbers": False, "has_front_loading": False, "has_heading_hierarchy": False},
                    signals={"has_lang": False, "has_rss": False})
        got = ids(self.build(aud=aud))
        self.assertTrue({"no_description", "no_canonical", "no_h1", "no_lang", "no_og"} <= got)

    def test_structured_data(self):
        none = audit(schema_jsonld={"found_types": [], "has_website": False})
        self.assertIn("no_schema", ids(self.build(ai={"jsonld_types": []}, aud=none)))
        aud = audit(schema_jsonld={"found_types": ["WebPage"], "has_website": False})
        aud["recommendations"] = ["Add Organization JSON-LD schema with name, url, and logo", "Incomplete Product schema: add required fields name, description (see schema.org/Product for the full spec)"]
        got = ids(self.build(aud=aud))
        self.assertTrue({"no_org", "no_website", "schema_fields"} <= got)
        self.assertIn("broken_schema", ids(self.build(ai={"jsonld_invalid": 2})))

    def test_trust_gaps(self):
        aud = audit(brand_entity={"brand_name_consistent": False, "names_found": ["A", "B"], "kg_pillar_count": 0, "has_contact_info": False, "has_about_link": False})
        aud["google_ai"]["checks"] = [{"id": "G-BYLINE", "status": "warn"}, {"id": "G-DATES", "status": "warn", "evidence": "Date only in structured data: show it on the page too"}]
        m = self.build(aud=aud, ai={"final_url": "http://a.test/", "jsonld_types": ["Article"]})
        self.assertTrue({"no_author", "no_date", "no_profiles", "no_contact", "no_about", "no_https", "brand"} <= ids(m))
        self.assertIn("only in the code", [i for i in m["items"] if i["id"] == "no_date"][0]["detail"])
        home = self.build(aud=aud)  # a company homepage needs no byline, and a missing date matters less
        self.assertNotIn("no_author", ids(home))
        self.assertEqual([i["impact"] for i in home["items"] if i["id"] == "no_date"], ["low"])

    def test_sitemap_and_links(self):
        self.assertIn("no_sitemap", ids(self.build(files={"declared": [], "sitemap": {"ok": False, "status": 404, "error": "HTTP 404", "url": "u"}}, aud=audit(robots_txt={"sitemaps": [], "bots_blocked": []}))))
        self.assertIn("sitemap", ids(self.build(files={"declared": ["u"], "sitemap": {"ok": False, "status": 404, "error": "HTTP 404", "url": "u"}})))
        refused = self.build(files={"declared": [], "sitemap": {"ok": False, "status": 403, "error": "HTTP 403", "url": "u"}}, aud=audit(robots_txt={"sitemaps": [], "bots_blocked": []}))
        self.assertNotIn("no_sitemap", ids(refused))  # we may just be refused
        aud = audit(); aud["google_ai"]["checks"].append({"id": "G-LINKS", "status": "fail"})
        aud["google_ai"]["checks"] = [c for c in aud["google_ai"]["checks"] if not (c["id"] == "G-LINKS" and c["status"] == "pass")]
        self.assertIn("links", ids(self.build(aud=aud)))

    def test_optional_items_are_apart(self):
        aud = audit(llms_txt={"found": False}, signals={"has_lang": True, "has_rss": False}, ai_discovery={"has_well_known_ai": False, "has_summary": False})
        m = self.build(aud=aud)
        self.assertEqual(m["count"], 0)
        self.assertEqual({i["id"] for i in m["optional"]}, {"llms", "feed", "ai_files"})

    def test_images_and_potential(self):
        aud = audit(); aud["multimodal"] = {"total_images": 20, "images_with_alt": 4, "alt_coverage": 0.2}
        m = self.build(aud=aud, score=60, actions=[{"source": "engine", "gain": 8}, {"source": "engine", "gain": 20}, {"source": "extra", "gain": None}])
        item = [i for i in m["items"] if i["id"] == "images"][0]
        self.assertEqual((item["impact"], item["title"]), ("medium", "Could not see 16 of 20 images"))
        self.assertEqual(m["potential"], 88)

    def test_items_are_grouped_by_stage_then_impact(self):
        aud = audit(meta_tags={"has_title": True, "has_description": False, "has_canonical": True, "has_og_title": True, "has_og_description": True})
        m = self.build(ai={"js_risk": "high", "word_count": 2}, aud=aud)
        order = [(i["stage"], i["impact"]) for i in m["items"]]
        self.assertEqual(order, sorted(order, key=lambda t: ([k for k, _ in C.STAGES].index(t[0]), {"critical": 0, "high": 1, "medium": 2, "low": 3}[t[1]])))

    def test_no_long_dashes_in_copy(self):
        base = probe()
        rows = {b["name"]: C.judge_bot(probe(403, 50), base, b) for b in C.BOTS}
        aud = audit(meta_tags={"has_title": False, "has_description": False, "has_canonical": False, "has_og_title": False, "has_og_description": False},
                    content={"has_h1": False, "has_numbers": False, "has_front_loading": False}, signals={"has_lang": False, "has_rss": False},
                    brand_entity={"brand_name_consistent": False, "names_found": ["A", "B"], "kg_pillar_count": 0, "has_contact_info": False, "has_about_link": False},
                    schema_jsonld={"found_types": [], "has_website": False}, llms_txt={"found": False})
        m = self.build(ai={"js_risk": "high", "word_count": 0, "jsonld_invalid": 1, "response_ms": 7000, "html_bytes": 9_000_000, "jsonld_types": []}, aud=aud, rows=rows)
        for i in m["items"] + m["optional"]:
            self.assertNotIn("\u2014", i["title"] + i["detail"])



# ---------------------------------------------------------------------------
# Where is the business? (a local business must say it in the page text)
# ---------------------------------------------------------------------------
import _location as LOC  # noqa: E402

HE_SCHEMA = json.dumps({"@context": "https://schema.org", "@graph": [
    {"@type": "WebSite", "url": "https://shop.test/", "name": "שוקולד של קוביות"},
    {"@type": "Store", "name": "שוקולד של קוביות", "telephone": "+972-52-4305080",
     "address": {"@type": "PostalAddress", "streetAddress": "נחלה 6", "addressLocality": "פרדס חנה כרכור", "postalCode": "3750203", "addressCountry": "IL"},
     "geo": {"@type": "GeoCoordinates", "addressCountry": "IL"},
     "openingHoursSpecification": [{"@type": "OpeningHoursSpecification", "dayOfWeek": ["Sunday"], "opens": "08:30", "closes": "16:30"}],
     "areaServed": {"@type": "Country", "name": "Israel"}}]}, ensure_ascii=False)


def he_page(footer="צרו קשר kubiot@example.com", extra="", schema=HE_SCHEMA, head_extra=""):
    ld = f'<script type="application/ld+json">{schema}</script>' if schema else ""
    return (f'<html lang="he-IL" dir="rtl"><head><title>סדנאות שוקולד ליום הולדת | שוקולד של קוביות</title>'
            f'<meta name="description" content="סדנת שוקולד אקטיבית לימי הולדת">{ld}{head_extra}</head><body>'
            '<header><nav><a href="/contact">צרו קשר</a></nav></header><main><h1>סדנאות שוקולד ליום הולדת</h1>'
            '<p>סדנת שוקולד ליום הולדת הינה חוויה להורים ולילדים יחד. משלוח חינם מעל 300 שח.</p>' + extra +
            f'</main><footer>{footer}<a href="tel:0524305080">052-4305080</a></footer></body></html>')


def loc_of(html):
    return C.analyze_html(html, "https://shop.test/p")["location"]


class Location(unittest.TestCase):
    def missed(self, loc):
        return C.build_missed({**PAGE_OK, "location": loc}, audit(), {}, GOOD_SITEMAP["files"], 80, [])

    def test_address_only_in_hidden_code_is_critical(self):  # the real case: shokolad
        loc = loc_of(he_page())
        self.assertEqual(loc["local"]["level"], "strong")
        self.assertEqual(loc["visible"]["status"], "hidden")
        self.assertEqual((loc["schema"]["street"], loc["schema"]["city"], loc["schema"]["postal"]), ("נחלה 6", "פרדס חנה כרכור", "3750203"))
        self.assertEqual(loc["schema"]["geo"], "empty")
        self.assertTrue(loc["schema"]["area_country_only"])
        self.assertEqual(C.location_impact(loc), "critical")
        a = C.check_location(loc)
        self.assertEqual((a["category"], a["priority"], a["label"]), ("location", "critical", "Say where your business is"))
        self.assertIn("נחלה 6", a["lead"])
        self.assertTrue(any("map pin" in i for i in a["items"]) and any("only a country" in i for i in a["items"]))
        self.assertIn("local business", a["note"])
        self.assertFalse(any("local business because" in i for i in a["items"]))  # the note is not a step
        self.assertTrue(all(len(i) < 200 for i in a["items"]))  # short steps
        m = self.missed(loc)
        it = [i for i in m["items"] if i["id"] == "location"][0]
        self.assertEqual((it["stage"], it["impact"], it["title"]), ("understand", "critical", "Could not tell where you are"))
        self.assertIn("only in hidden code", it["detail"])
        self.assertIn("geo_empty", ids(m))
        self.assertEqual([s["status"] for s in m["stages"] if s["key"] == "understand"], ["fail"])

    def test_address_in_the_footer_is_fine(self):
        for footer in ("כתובת: נחלה 6, פרדס חנה כרכור | 052-4305080", "אנחנו בפרדס חנה", "רחוב הרצל 12", "ת.ד. 123"):
            loc = loc_of(he_page(footer=footer))
            self.assertEqual(loc["visible"]["status"], "ok", footer)
            self.assertIsNone(C.location_impact(loc))
            self.assertIsNone(C.check_location(loc))
            self.assertNotIn("location", ids(self.missed(loc)))
        wins = C.build_wins({**PAGE_OK, "location": loc_of(he_page(footer="כתובת: נחלה 6, פרדס חנה כרכור"))}, audit(), {}, None, [])
        self.assertTrue(any(w.startswith("The page says where the business is") for w in wins))

    def test_local_shop_without_any_address(self):
        loc = loc_of(he_page(schema=None, extra="<button>הוסף לסל</button><p>משלוחים מהירים</p>"))
        self.assertEqual((loc["local"]["level"], loc["visible"]["status"]), ("likely", "none"))
        self.assertEqual(C.location_impact(loc), "high")
        self.assertIn("no address in the structured data", C.check_location(loc)["lead"])

    def test_restaurant_with_hours_and_no_address_is_critical(self):
        html = ('<html lang="en"><head><title>Luigi</title></head><body><h1>Luigi\'s Pizza</h1><p>Wood fired pizza, made fresh every day.</p>'
                '<p>Opening hours: Mon-Fri 11:00-22:00. Reserve a table or call (212) 555-0100.</p></body></html>')
        loc = loc_of(html)
        self.assertEqual((loc["local"]["level"], loc["visible"]["status"]), ("strong", "none"))
        self.assertEqual(C.location_impact(loc), "critical")

    def test_software_and_media_sites_are_never_flagged(self):
        html = ('<html lang="en"><head><title>Acme API</title></head><body><h1>Build faster</h1><p>Our API delivers data to your app. '
                'Contact sales: +1 415 555 0100. Pricing starts at $29. Read the docs, then deploy from your git branch.</p></body></html>')
        loc = loc_of(html)
        self.assertEqual(loc["local"]["level"], "none")
        self.assertIsNone(C.location_impact(loc))
        self.assertIsNone(C.check_location(loc))
        self.assertIsNone(C.location_impact(None))
        self.assertIsNone(C.location_impact({}))
        online = {"@type": "OnlineStore", "name": "x"}
        self.assertFalse(LOC.schema_facts([online])["local_type"])  # online shops are not tied to a place

    def test_english_addresses(self):
        for text in ("Visit us at 12 Main Street, Springfield", "Address: 5 Rothschild Blvd, Tel Aviv", "Herzl Street 5", "Austin, TX 78701",
                     "P.O. Box 1234", "10 Downing Street", "Torstraße 12", "12 rue de la Paix"):
            html = f'<html lang="en"><body><h1>Shop</h1><p>Opening hours daily. Call 212-555-0100. Add to cart.</p><footer>{text}</footer></body></html>'
            self.assertEqual(loc_of(html)["visible"]["status"], "ok", text)
        html = '<html lang="en"><body><h1>Shop</h1><p>Opening hours daily. Call 212-555-0100.</p><address>Unit 4, The Old Mill</address></body></html>'
        self.assertEqual(loc_of(html)["visible"]["status"], "ok")

    def test_place_names_with_hebrew_prefixes_and_lookalikes(self):
        self.assertEqual(LOC.places("הגענו לחיפה ובתל-אביב ובפרדס חנה")[:3], ["חיפה", "תל אביב", "פרדס חנה"])
        self.assertEqual(LOC.places("Open in Tel Aviv and Jerusalem")[:2], ["Tel Aviv", "Jerusalem"])
        for word_text in ("עוגה שחורה וטעימה", "25 מעלות", "אזור התעשייה", "קדימה, תזמינו", "קוראים לי אפרת", "Cork stoppers and Lima beans"):
            self.assertEqual(LOC.places(word_text), [], word_text)

    def test_country_or_delivery_area_only(self):
        loc = loc_of(he_page(schema=None, extra="<p>משלוחים לכל הארץ</p><button>הוסף לסל</button>"))
        self.assertEqual(loc["visible"]["status"], "area")
        self.assertIsNone(C.location_impact(loc))  # a shop with a weak signal set is not nagged
        strong = loc_of(he_page(extra="<p>משלוחים לכל הארץ</p>", schema=HE_SCHEMA))
        self.assertEqual(strong["visible"]["status"], "area")
        self.assertEqual(C.location_impact(strong), "medium")
        self.assertIn("not where the business is", C.check_location(strong)["lead"])

    def test_email_label_and_form_labels_are_not_addresses(self):
        loc = loc_of(he_page(footer="כתובת אימייל: a@b.co", schema=None, extra="<p>הוסף לסל</p>"))
        self.assertEqual(loc["visible"]["status"], "none")
        html = he_page(footer="Address: Phone: Email:", schema=None, extra="<p>הוסף לסל</p>")
        self.assertEqual(loc_of(html)["visible"]["status"], "none")

    def test_a_map_alone_is_not_an_address(self):
        html = he_page(footer='<iframe src="https://www.google.com/maps/embed?pb=1"></iframe>', schema=HE_SCHEMA)
        loc = loc_of(html)
        self.assertEqual(loc["visible"]["status"], "mention")
        self.assertEqual(loc["visible"]["map"], "embed")
        self.assertEqual(C.location_impact(loc), "medium")

    def test_schema_facts(self):
        s = LOC.schema_facts([{"@type": "Person", "name": "Ann", "address": {"streetAddress": "1 Elm St"}}])
        self.assertFalse(s["has_address"])  # a person's address is not the business
        s = LOC.schema_facts([{"@type": ["LocalBusiness", "Bakery"], "name": "B", "address": "5 Oak Road, Leeds", "geo": {"latitude": "53.8", "longitude": "-1.55"},
                               "areaServed": [{"@type": "City", "name": "Leeds"}, "Bradford"], "openingHours": "Mo-Fr 09:00-17:00"}])
        self.assertEqual((s["has_address"], s["geo"], s["hours"], s["local_type"]), (True, "ok", True, True))
        self.assertEqual((s["area_served"], s["area_country_only"]), (["Leeds", "Bradford"], False))
        s = LOC.schema_facts([{"@type": "Organization", "location": {"@type": "Place", "address": {"addressLocality": "Haifa"}}}])
        self.assertEqual((s["has_address"], s["city"]), (True, "Haifa"))
        self.assertEqual(LOC.schema_facts([])["geo"], "none")

    def test_the_check_never_breaks_a_page(self):
        bad = he_page(schema='{"@type": "Store", "address": 7, "geo": "x", "areaServed": [1, {"name": 5}]}')
        self.assertIn(loc_of(bad)["schema"]["found"], (True, False))
        ai = C.analyze_html("<html><body></body></html>", "https://a.test/")
        self.assertIsNotNone(ai["location"])
        self.assertEqual(ai["location"]["visible"]["status"], "none")

    def test_every_missed_item_names_its_fix(self):
        m = self.missed(loc_of(he_page()))
        loc_item = [i for i in m["items"] if i["id"] == "location"][0]
        self.assertEqual(loc_item["fix"], "location")
        self.assertEqual([i["fix"] for i in m["items"] if i["id"] == "geo_empty"], ["location"])
        self.assertTrue(all("fix" in i for i in m["items"] + m["optional"]))
        self.assertTrue(all(i["fix"] is None for i in m["optional"]))  # optional extras have no fix row
        # every id the report can raise has a known fix category (a missing one would make a lone row)
        for id_, cat in C.FIX_FOR.items():
            self.assertRegex(cat, r"^[a-z_]+$", id_)
        self.assertEqual(C.FIX_FOR["bots_refused"], "access")

    def test_hints_exist_for_items_that_may_stand_alone(self):
        for id_ in ("no_sitemap", "robots_block", "images"):
            self.assertIn(id_, C.HINTS)
            self.assertIn(id_, C.FIX_FOR)
        m = C.build_missed(PAGE_OK, audit(robots_txt={"bots_blocked": [], "sitemaps": [], "crawl_delay": None}), {}, {"declared": [], "sitemap": None}, 80, [])
        item = [i for i in m["items"] if i["id"] == "no_sitemap"]
        self.assertTrue(item and item[0]["hint"].startswith("Create a sitemap.xml"))
        for h in C.HINTS.values():
            self.assertNotIn("\u2014", h)

    def test_content_signal_is_an_optional_extra(self):
        self.assertTrue(C.has_content_signal("User-agent: *\nContent-Signal: ai-train=no, search=yes"))
        self.assertFalse(C.has_content_signal("User-agent: *\nDisallow: /x"))
        files = {**GOOD_SITEMAP["files"], "content_signal": False}
        m = C.build_missed(PAGE_OK, audit(), {}, files, 80, [])
        self.assertEqual(m["count"], 0)  # optional items are never counted
        self.assertIn("content_signal", [i["id"] for i in m["optional"]])
        files["content_signal"] = None  # no robots.txt: nothing to say
        self.assertNotIn("content_signal", [i["id"] for i in C.build_missed(PAGE_OK, audit(), {}, files, 80, [])["optional"]])

    def test_copy_has_no_long_dashes(self):
        for loc in (loc_of(he_page()), loc_of(he_page(schema=None, extra="<button>הוסף לסל</button>")),
                    loc_of(he_page(extra="<p>משלוחים לכל הארץ</p>")), loc_of(he_page(footer='<iframe src="https://www.google.com/maps/embed"></iframe>'))):
            a = C.check_location(loc)
            self.assertIsNotNone(a)
            for t in a["items"] + [a["lead"], a.get("note", "")] + [i["title"] + i["detail"] for i in self.missed(loc)["items"]]:
                self.assertNotIn("—", t)

if __name__ == "__main__":
    unittest.main()
