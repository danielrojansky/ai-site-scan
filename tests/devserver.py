"""Local test server: serves the site like Vercel (clean URLs, rewrites) with test APIs.

/api/scan runs the real scanner (public-IP guard off, for this local host only).
/api/config, /api/claim and /api/unlock are fakes, so the paywall and the return
from checkout can be tested without Polar. Run: python3 devserver.py ROOT PORT
"""
import json, sys, threading, time, os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(sys.argv[1]); PORT = int(sys.argv[2])
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("NO_PROXY", "local.aisitescan.test,127.0.0.1,localhost")
import geo_optimizer.utils.validators as V  # noqa: E402
V._check_ip_blocked = lambda ip: (False, None)  # test only: allow 127.0.0.1
import scan as S  # noqa: E402
S.validate_public_url = lambda url: (True, None)

CLAIMS = {}
REWRITE = {"/llms-txt": "/index.html", "/ai-robots-txt": "/index.html", "/": "/index.html"}


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); p = u.path; q = parse_qs(u.query)
        if p == "/api/config":
            return self._json(200, {"site": "http://local.aisitescan.test:%d" % PORT, "support": "support@getaisitescan.com", "paywall": True,
                                    "checkout": {"single": "https://buy.polar.sh/polar_cl_single_test", "pack": "https://buy.polar.sh/polar_cl_pack_test"},
                                    "prices": {"single": 19, "pack": 49}, "portal": "https://polar.sh/aisitescan/portal", "email": True, "posthog": None, "ads": None})
        if p == "/api/scan":
            try:
                return self._json(200, S.scan(q.get("url", [""])[0]))
            except Exception as e:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        if p == "/api/claim":
            cid = q.get("checkout_id", [""])[0]; n = CLAIMS.get(cid, 0) + 1; CLAIMS[cid] = n
            if n < 2: return self._json(200, {"ok": False, "pending": True, "status": "granting"})
            return self._json(200, {"ok": True, "key": "AISS-TEST-1234", "sites_limit": 1, "portal": "https://polar.sh/aisitescan/portal?customer_session_token=x"})
        if p.startswith("/api/"):
            return self._json(503, {"error": "not in the test server"})
        if p in REWRITE: self.path = REWRITE[p]
        elif "." not in p.rsplit("/", 1)[-1] and (ROOT / (p.lstrip("/") + ".html")).exists(): self.path = p + ".html"
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path); n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/api/unlock":
            if body.get("key") != "AISS-TEST-1234": return self._json(404, {"error": "This key was not found. Check it and try again."})
            return self._json(200, {"ok": True, "host": body.get("host"), "activation_id": "act-test", "sites_limit": 1, "expires_at": None})
        if u.path == "/api/email":
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "no route"})


ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
