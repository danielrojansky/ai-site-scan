"""Browser test of the paid flow (scan, login, checkout, return, workspace, logout) with fake Supabase and Polar.
Run: add "127.0.0.1 local.aisitescan.test" to /etc/hosts, start `python3 tests/devserver.py . 8765`,
then `python3 tests/ui_flow.py`. Needs Playwright with Chromium."""
import os, tempfile
SHOTS = os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="aiss-shots-")
"""Browser test of the account flow: scan, log in with a code, pay (fake Polar), unlock, report saved, workspace."""
import json, sys
from playwright.sync_api import sync_playwright

BASE = "http://local.aisitescan.test:8765"
SITE = BASE + "/"
STATE = {"paid": False, "unlocks": 0, "saved": [], "sessions": 0}

FAKE_SB = r"""
const KEY = "sb-fake-auth";
let listeners = [];
function load(){ try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; } }
function emit(ev, s){ listeners.forEach(f => f(ev, s)); }
window.supabase = { createClient };
function createClient(){
  return { auth: {
    async getSession(){ return {data: {session: load()}}; },
    onAuthStateChange(f){ listeners.push(f); return {data: {subscription: {unsubscribe(){}}}}; },
    async signInWithOtp({email}){ window.__otpTo = email; return {error: null}; },
    async verifyOtp({email, token}){ if(token !== "123456") return {data: {}, error: {message: "bad"}}; const s = {access_token: "tok-" + email, user: {email}}; localStorage.setItem(KEY, JSON.stringify(s)); emit("SIGNED_IN", s); return {data: {session: s}, error: null}; },
    async signOut(){ localStorage.removeItem(KEY); emit("SIGNED_OUT", null); return {error: null}; },
    async signInWithOAuth(){ return {error: null}; }
  }};
}
"""

def account(route, request):
    url = request.url; act = url.split("do=")[1].split("&")[0]
    body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer tok-"): return route.fulfill(status=401, json={"error": "Please log in again."})
    if act == "me":
        reports = [{"id": r, "host": "local.aisitescan.test", "url": SITE, "kind": "full", "score": 92, "created_at": "2026-11-28T10:00:00Z", "scan_key": "x"} for r in STATE["saved"]]
        sites = [{"host": "local.aisitescan.test", "rescans_left": 1, "rescans_until": "2026-12-28T00:00:00Z", "unlimited": False}] if STATE["unlocks"] else []
        return route.fulfill(json={"ok": True, "email": "buyer@example.com", "credits": {"single": 1 if STATE["paid"] and not STATE["unlocks"] else 0, "pro": 0, "pro_until": None}, "subscribed": False, "sites": sites, "reports": reports})
    if act == "checkout":
        STATE["checkout_plan"] = body.get("plan")
        return route.fulfill(json={"ok": True, "url": BASE + "/__fake_polar"})
    if act == "sync": return route.fulfill(json={"ok": True})
    if act == "unlock":
        if not STATE["paid"]: return route.fulfill(status=402, json={"ok": False, "no_credit": True})
        STATE["unlocks"] += 1
        return route.fulfill(json={"ok": True, "report_id": "33333333-3333-4333-8333-333333333333", "kind": "full", "plan": "single"})
    if act == "save":
        STATE["saved"].append(body["report_id"]); STATE["guide_len"] = len(body.get("guide_md", ""))
        return route.fulfill(json={"ok": True, "emailed": True})
    if act == "report":
        return route.fulfill(json={"ok": True, "report": {"id": "r", "host": "local.aisitescan.test", "score": 92, "created_at": "2026-11-28T10:00:00Z", "guide_md": "# AI fix guide\nsaved"}})
    if act == "portal": return route.fulfill(json={"ok": True, "url": "https://polar.sh/x/portal"})
    route.fulfill(status=404, json={})

def fake_polar(route, request):
    STATE["paid"] = True
    route.fulfill(status=302, headers={"Location": SITE + "?paid=single&checkout_id=44444444-4444-4444-8444-444444444444"})

errors = []
ok = lambda m: print("ok", m)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server", "--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    ctx.route("**/api/config", lambda r, q: r.fulfill(json={"site": SITE, "support": "support@getaisitescan.com", "paywall": True, "pro": True, "prices": {"single": 19, "pro": 29}, "pro_reports": 10, "supabase": {"url": "https://fake.supabase.co", "key": "sb_publishable_x", "google": True}, "email": False, "posthog": None, "ads": None}))
    ctx.add_init_script(FAKE_SB)  # the page skips loading /vendor when window.supabase exists
    ctx.route("**/api/account?**", account)
    ctx.route("**/__fake_polar", fake_polar)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto(SITE, wait_until="networkidle")
    assert page.locator("#tabAcct").is_visible() and page.locator("#tabAcct").inner_text() == "Log in"
    page.fill("#u", SITE); page.press("#u", "Enter")
    page.wait_for_function("() => typeof DATA !== 'undefined' && DATA && ISSUES.length > 0", timeout=90000)
    page.wait_for_timeout(800)
    assert page.locator(".locked").count() >= 1
    ok("scan: fixes after the first 3 are locked")

    page.locator('[data-act="buy"]').first.click(); page.wait_for_selector("#payDlg[open]")
    t = page.locator("#payDlg").inner_text()
    assert "$19" in t and "$29 a month" in t and "1 rescan" in t, t
    page.screenshot(path=SHOTS + "/pay_plans.png")
    page.locator('[data-buy="single"]').click()
    page.wait_for_selector("#payLogin .lmail")
    assert page.locator("#payLogin .lgoogle").is_visible()
    page.fill("#payLogin .lmail", "buyer@example.com"); page.click("#payLogin .lsend")
    page.wait_for_selector("#payLogin .lcodeRow:not(.hide)")
    page.screenshot(path=SHOTS + "/pay_login.png")
    page.fill("#payLogin .lcode", "111111"); page.click("#payLogin .lverify")
    page.wait_for_function("() => document.querySelector('#payLogin .lmsg').textContent.includes('did not work')")
    ok("login: wrong code is refused")
    page.fill("#payLogin .lcode", "123456")
    with page.expect_navigation(timeout=30000):
        page.press("#payLogin .lcode", "Enter")  # native form submit
    assert STATE["checkout_plan"] == "single"
    ok("login then checkout: went to Polar for the single plan")

    page.wait_for_function("() => typeof CUR !== 'undefined' && CUR && CUR.report_id", timeout=60000)
    page.wait_for_timeout(1200)
    assert page.locator(".locked").count() == 0
    assert "paid=" not in page.url
    assert STATE["saved"] and STATE["guide_len"] > 500, STATE
    page.screenshot(path=SHOTS + "/after_pay.png")
    ok("return: credit used, all fixes open, report saved (%d chars)" % STATE["guide_len"])

    page.click("#tabAcct"); page.wait_for_selector("#acctReports .rrow")
    assert page.locator("#tabAcct").inner_text() == "My reports"
    assert "Rescans" in page.locator("#acctPlan").inner_text()
    page.screenshot(path=SHOTS + "/account.png", full_page=True)
    page.click("#acctReports [data-open]"); page.wait_for_selector("#guideDlg[open]")
    assert "saved" in page.locator("#guideText").inner_text()
    ok("workspace: report listed and opens")
    page.keyboard.press("Escape")
    page.click("#logoutBtn"); page.wait_for_function("() => document.querySelector('#tabAcct').textContent === 'Log in'")
    ok("log out")
    b.close()

print("errors:", errors or "none")
sys.exit(1 if errors else 0)
