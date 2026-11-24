"""Offline tests for api/_checks.py. Run: python -m unittest discover -s tests"""

import gzip
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


if __name__ == "__main__":
    unittest.main()
