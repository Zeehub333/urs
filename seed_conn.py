"""Seed the default connection from app.conf into a RUNNING Django instance.

Usage:  python seed_conn.py http://127.0.0.1:8004
POSTs {} to /api/setup/wizard/connection/from-appconf/ (server decrypts the
PBKDF2 envelope, upserts urs_local id=1, tests, ensures schema).

Exit codes: 0 = seeded | 1 = server unreachable (caller should retry) |
            2 = server answered (skip/HTTP error shown, no retry needed).
Short single-line output only (batch-friendly, no tracebacks).
"""
import json
import sys
import urllib.error
import urllib.request


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8004").rstrip("/")
    url = base + "/api/setup/wizard/connection/from-appconf/"
    try:
        req = urllib.request.Request(
            url, data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        body = urllib.request.urlopen(req, timeout=25).read().decode("utf-8")
    except urllib.error.HTTPError as h:
        try:
            detail = h.read().decode("utf-8")[:250]
        except Exception:
            detail = ""
        print("HTTP %s: %s" % (h.code, detail))
        return 2
    except Exception as e:
        print("unreachable: %s" % str(e)[:150])
        return 1
    try:
        j = json.loads(body)
        c, t = j.get("connection") or {}, j.get("tested") or {}
        print("seed ok=%s conn=%s/%s tested=%s schema_ok=%s%s" % (
            j.get("ok"), c.get("host"), c.get("user"), t.get("ok"),
            j.get("schema_ok"),
            "" if j.get("ok") else " err=" + str(j.get("error") or j.get("reason") or "")[:150]))
    except Exception:
        print(body[:250])
    return 0


if __name__ == "__main__":
    sys.exit(main())
