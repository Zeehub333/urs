"""
ODEX shared CSS editable server — port 8002
Serves C:/urs2/odex/web as root, /static/*, and /api/css (GET/POST) for shared CSS.
"""
import http.server, socketserver, pathlib, json, urllib.parse

ROOT = pathlib.Path(__file__).parent
SHARED = ROOT / "static" / "shared.css"
SHARED_ALT = pathlib.Path("C:/urs2/shared.css")
DEMO_CSS = ROOT / "static" / "demo.css"

# ensure shared exists
if not SHARED.exists() and DEMO_CSS.exists():
    SHARED.write_text(DEMO_CSS.read_text(encoding="utf-8"), encoding="utf-8")
if not SHARED_ALT.exists() and SHARED.exists():
    try: SHARED_ALT.write_text(SHARED.read_text(encoding="utf-8"), encoding="utf-8")
    except: pass

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()
    def do_OPTIONS(self):
        self.send_response(204); self.end_headers()
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/css":
            p = SHARED if SHARED.exists() else SHARED_ALT
            css = p.read_text(encoding="utf-8") if p.exists() else ""
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.end_headers()
            self.wfile.write(css.encode("utf-8"))
            return
        if parsed.path == "/api/components":
            # return component stats
            try:
                import sys
                sys.path.insert(0, str(ROOT.parent.parent))
                sys.path.insert(0, "C:/urs2")
                from odex.web.ui_engine import UIEngine
                e = UIEngine()
                data = json.dumps(e.component_stats())
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data.encode())
                return
            except Exception as ex:
                self.send_error(500, str(ex)); return
        return super().do_GET()
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/css":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            # try json or raw css
            css = ""
            ctype = self.headers.get("Content-Type","")
            if "json" in ctype:
                try: css = json.loads(body.decode())["css"]
                except: css = body.decode()
            else:
                css = body.decode("utf-8")
            # write to shared locations
            for p in [SHARED, SHARED_ALT, pathlib.Path("C:/urs2/odex/shared.css")]:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(css, encoding="utf-8")
                except: pass
            # also update demo.css for backwards compat
            try: (ROOT / "static" / "demo.css").write_text(css, encoding="utf-8")
            except: pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "bytes": len(css)}).encode())
            return
        self.send_error(404, "Not Found")

if __name__ == "__main__":
    PORT = 8002
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"ODEX serving {ROOT} at http://127.0.0.1:{PORT}/demo.html")
        print(f"Shared CSS: {SHARED} (editable via POST /api/css)")
        print(f"Try: curl http://127.0.0.1:{PORT}/api/css")
        httpd.serve_forever()
