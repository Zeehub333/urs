import json
import pathlib
import threading as _th
import time as _time
import uuid as _uuid
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .models import App


def _needs_onboarding() -> bool:
    """بوابة الشركة الأولى: True إذا لا يوجد app.config أو لا توجد شركة أو لا يوجد فرع."""
    try:
        from config.dbconf import base_dir as _bd
        if not (_bd() / "app.config").exists():
            return True
    except Exception:
        pass
    try:
        from .models import Company, Branch
        if Company.objects.count() == 0:
            return True
        if Branch.objects.count() == 0:
            return True
        return False
    except Exception:
        return False


def _gate_redirect(request):
    """إعادة توجيه لصفحة الإعداد الأولى عند الحاجة (حجب كامل)."""
    try:
        p = request.path or ""
        if p.startswith("/settings/setup") or p.startswith("/api/setup/") or p.startswith("/admin"):
            return None
        if _needs_onboarding():
            return redirect("/settings/setup/")
    except Exception:
        pass
    return None

BASE_DIR = pathlib.Path(settings.BASE_DIR)

# ── Postgres Engine for 172.16.10.101/urs (fixes Oracle vs Postgres binds) ─────
import re as _re_pg
# Business timezone for all Postgres report sessions (Yemen). The ZKBioTime
# mirror DB runs Asia/Shanghai (+08); without this, timestamptz values display
# 5 hours ahead (e.g. a 16:48 Aden punch shows as 21:48) and date filters use
# the wrong day boundary.
PG_SESSION_TZ = "Asia/Aden"
# Bind placeholder `:name` outside quoted literals ("..." / '...') — quoted
# text (e.g. an Arabic column alias containing "IP:Port") is left untouched,
# and Postgres casts (`::date`) are never treated as binds.
_BIND_OR_QUOTED = _re_pg.compile(r"\"(?:\"\"|[^\"])*\"|'(?:''|[^'])*'|(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
def _sub_bind(m) -> str:
    if m.group(1) is None:
        return m.group(0)
    return "%(" + m.group(1) + ")s"
class PostgresEngine:
    """Minimal Postgres wrapper mimicking OracleEngine interface for RML/FML engines"""
    def __init__(self, host="172.16.10.101", dbname="urs", user="postgres", password="postgres", port=5432):
        self.host, self.dbname, self.user, self.password = host, dbname, user, password
        try:
            self.port = int(port or 5432)
        except (TypeError, ValueError):
            self.port = 5432
        self.conn = None
    def connect(self):
        import psycopg2
        if self.conn and not self.conn.closed:
            return self.conn
        self.conn = psycopg2.connect(dbname=self.dbname, user=self.user, password=self.password, host=self.host, port=self.port, connect_timeout=5)
        try:
            cur = self.conn.cursor()
            cur.execute("SET TIME ZONE '%s'" % PG_SESSION_TZ)
            cur.close()
        except Exception:
            # Never leave a poisoned transaction behind: a failed SET would make
            # every later statement fail with "current transaction is aborted".
            try:
                self.conn.rollback()
            except Exception:
                pass
        return self.conn
    def disconnect(self):
        if self.conn:
            try: self.conn.close()
            except: pass
            self.conn = None
    def _exec(self, sql: str, params=None, commit=False):
        # Convert Oracle binds :name -> psycopg2 %(name)s
        # — skipping quoted literals (e.g. aliases like "أجهزة (IP:Port)")
        # so a colon inside "..." or '...' is never treated as a bind.
        def _conv(s: str) -> str:
            return _BIND_OR_QUOTED.sub(_sub_bind, s)
        sql_pg = _conv(sql) if params else sql
        cur = self.conn.cursor()
        try:
            cur.execute(sql_pg, params or {})
            if commit:
                self.conn.commit()
            return cur
        except Exception:
            # do not close cursor here, caller handles
            raise

def _load_system_apps():
    """Scan odex/system/*/metadata.json — 20 ERP apps (image) + fallback to system/"""
    candidates = [BASE_DIR / "odex" / "system", BASE_DIR / "system"]
    apps = []
    seen = set()
    for system in candidates:
        if not system.exists():
            continue
        for meta_path in sorted(system.glob("*/metadata.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                if data.get("name") not in seen:
                    apps.append(data)
                    seen.add(data["name"])
            except Exception:
                continue
    # Sort by sort_order
    apps.sort(key=lambda x: x.get("sort_order", 999))
    return apps

def _load_db_apps():
    """Load from DB (synced via db_init)"""
    try:
        return [a.to_dict() for a in App.objects.filter(is_active=True).order_by("sort_order", "name")]
    except Exception:
        return []

def _load_apps_folder_driven():
    """Pure folder-driven catalog: ONLY odex/system folders (by sort_order).

    Empty folders → empty list (no DB backfill). DB rows without folders are hidden.
    """
    return _load_system_apps()

def _load_app_context(app_name):
    """Helper: load app_meta + fml_files + rml_files for any view"""
    candidates = [BASE_DIR / "odex" / "system" / app_name, BASE_DIR / "system" / app_name]
    app_meta = None
    rml_files = []
    fml_files = []
    for cand in candidates:
        if cand.exists():
            meta_path = cand / "metadata.json"
            if meta_path.exists():
                try:
                    app_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except:
                    app_meta = {"name": app_name, "ar": app_name}
            for p in sorted(cand.glob("*.rml")):
                try:
                    from rml_python.compiler import RMLReportCompiler
                    comp = RMLReportCompiler(path=p)
                    rml_files.append({"file": p.name, "path": str(p), "metadata": comp.rpt_metadata(), "columns": [c.to_dict() for c in comp.columns()], "fields": [f.to_dict() for f in comp.fields()], "connections": [c.to_dict() for c in comp.connections()], "charts": [c.to_dict() for c in comp.charts()], "rules": [r.to_dict() for r in comp.rules()]})
                except Exception as e:
                    rml_files.append({"file": p.name, "error": str(e), "metadata": {"displayName": p.name}, "columns": [], "fields": [], "connections": [], "charts": []})
            for p in sorted(cand.glob("*.fmlk")) + sorted(cand.glob("*.fml")):
                try:
                    from fmlk_engine.compiler import FMLKFormCompiler
                    comp = FMLKFormCompiler(path=p)
                    fml_files.append({"file": p.name, "path": str(p), "metadata": comp.fml_metadata(), "tabs": [t.to_dict() for t in comp.tabs()], "fields": [f.to_dict() for f in comp.fields()]})
                except Exception as e:
                    fml_files.append({"file": p.name, "error": str(e), "metadata": {"displayName": p.name}, "tabs": [], "fields": []})
            break
    if not app_meta:
        app_meta = {"name": app_name, "ar": app_name, "icon": "fa-cube", "icon_bg": "bg-gray-900", "icon_color": "text-white", "version": "1.0.0", "description": ""}
    return app_meta, fml_files, rml_files

def _group_files_by_category(files):
    """Group fml/rml file dicts by metadata.category (order-preserving) → [{name, files}].

    Single source for collapsible sidebar categories in all screens.
    """
    groups, idx = [], {}
    for _f in files or []:
        _cat = (((_f.get("metadata") or {}).get("category")) or "عام").strip() or "عام"
        if _cat not in idx:
            idx[_cat] = len(groups)
            groups.append({"name": _cat, "files": []})
        groups[idx[_cat]]["files"].append(_f)
    return groups

def _unified_side_groups(fml_files, rml_files):
    """One group per shared category name holding BOTH its forms and reports.

    [{name, fml: [...], rml: [...]}] — fml categories first-seen, then rml-only ones.
    """
    groups, idx = [], {}
    def _cat(m):
        return ((m or {}).get("category") or "عام").strip() or "عام"
    for _f in fml_files or []:
        _c = _cat(_f.get("metadata"))
        if _c not in idx:
            idx[_c] = len(groups)
            groups.append({"name": _c, "fml": [], "rml": []})
        groups[idx[_c]]["fml"].append(_f)
    for _f in rml_files or []:
        _c = _cat(_f.get("metadata"))
        if _c not in idx:
            idx[_c] = len(groups)
            groups.append({"name": _c, "fml": [], "rml": []})
        groups[idx[_c]]["rml"].append(_f)
    return groups

def _find_fml_path(fml_name: str, app_name: str | None = None):
    """Locate FML file by name (with or without extension) optionally scoped to app"""
    from fmlk_engine.compiler import FMLKFormCompiler
    # If app_name given, search inside that app first
    if app_name:
        for cand in [BASE_DIR / "odex" / "system" / app_name / fml_name,
                     BASE_DIR / "odex" / "system" / app_name / f"{fml_name}.fmlk",
                     BASE_DIR / "odex" / "system" / app_name / f"{fml_name}.fml",
                     BASE_DIR / "system" / app_name / fml_name,
                     BASE_DIR / "system" / app_name / f"{fml_name}.fmlk"]:
            if cand.exists():
                return cand
        # also glob search inside app folder for partial match
        for base in [BASE_DIR / "odex" / "system" / app_name, BASE_DIR / "system" / app_name]:
            if base.exists():
                for p in list(base.glob("*.fmlk")) + list(base.glob("*.fml")):
                    if p.name == fml_name or p.stem == fml_name:
                        return p
    # Search globally
    for fml_dir in [BASE_DIR / "fmlk_engine" / "examples", BASE_DIR / "odex" / "web"]:
        for cand in [fml_dir / fml_name, fml_dir / f"{fml_name}.fmlk", fml_dir / f"{fml_name}.fml"]:
            if cand.exists():
                return cand
    # Search all system apps
    for system in [BASE_DIR / "odex" / "system", BASE_DIR / "system"]:
        if system.exists():
            for p in system.glob("*/*.fmlk"):
                if p.name == fml_name or p.stem == fml_name:
                    return p
            for p in system.glob("*/*.fml"):
                if p.name == fml_name or p.stem == fml_name:
                    return p
    # Fallback to default example
    default = BASE_DIR / "fmlk_engine" / "examples" / "hr_form.fmlk"
    if default.exists():
        return default
    return None

def _find_rml_path(rml_name: str, app_name: str | None = None):
    from rml_python.compiler import RMLReportCompiler
    if app_name:
        for cand in [BASE_DIR / "odex" / "system" / app_name / rml_name,
                     BASE_DIR / "odex" / "system" / app_name / f"{rml_name}.rml",
                     BASE_DIR / "system" / app_name / rml_name,
                     BASE_DIR / "system" / app_name / f"{rml_name}.rml"]:
            if cand.exists():
                return cand
        for base in [BASE_DIR / "odex" / "system" / app_name, BASE_DIR / "system" / app_name]:
            if base.exists():
                for p in base.glob("*.rml"):
                    if p.name == rml_name or p.stem == rml_name:
                        return p
    for rml_dir in [BASE_DIR / "rml_python" / "examples", BASE_DIR / "odex" / "web"]:
        for cand in [rml_dir / rml_name, rml_dir / f"{rml_name}.rml"]:
            if cand.exists():
                return cand
    for system in [BASE_DIR / "odex" / "system", BASE_DIR / "system"]:
        if system.exists():
            for p in system.glob("*/*.rml"):
                if p.name == rml_name or p.stem == rml_name:
                    return p
    default = BASE_DIR / "rml_python" / "examples" / "emp_report.rml"
    if default.exists():
        return default
    return None

def home(request):
    """
    Home UI — style from provided RAW-EXS template, without company name,
    apps list dynamic from DB + system folders.
    """
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    # Dynamic apps: FOLDER-DRIVEN (odex/system folders first, DB-only leftovers last)
    apps = _load_apps_folder_driven()

    # Real stats for home cards (no static numbers)
    total_reports = 0
    total_forms = 0
    reports_with_charts = 0
    for system in [BASE_DIR / "odex" / "system", BASE_DIR / "system"]:
        if not system.exists():
            continue
        rml_paths = list(system.glob("*/*.rml"))
        total_reports += len(rml_paths)
        total_forms += len(list(system.glob("*/*.fmlk"))) + len(list(system.glob("*/*.fml")))
        for rp in rml_paths:
            try:
                txt = rp.read_text(encoding="utf-8", errors="ignore")
                low = txt.lower()
                if "<chart" in low and ("<rml_chart" in low or "<rml_charts" in low or "<charts" in low):
                    reports_with_charts += 1
            except Exception:
                pass
    try:
        from .models import FilePermission, Connection
        shared_count = FilePermission.objects.count()
        connections_count = Connection.objects.count()
    except Exception:
        shared_count = 0
        connections_count = 0

    # Also pass system apps for debugging
    try:
        deploy_version = (BASE_DIR / "VERSION.txt").read_text(encoding="utf-8").strip()[:16] or "dev"
    except Exception:
        deploy_version = "dev"
    return render(request, "home.html", {
        "apps": apps,
        "apps_count": len(apps),
        "system_apps": apps,
        "total_reports": total_reports,
        "total_forms": total_forms,
        "reports_with_charts": reports_with_charts,
        "shared_count": shared_count,
        "connections_count": connections_count,
        "deploy_version": deploy_version,
    })

def my_reports(request):
    """صفحة تقاريري المستقلة — التقارير المفضلة (localStorage) بجريد ومخططات مصغرة."""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    return render(request, "my_reports.html", {})

def dashboards(request):
    """صفحة لوحات المعلومات — مخططات التقارير المحفوظة (localStorage) بمربعات."""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    return render(request, "dashboards.html", {})

MY_DOCS_DIR = BASE_DIR / "my_docs"
MY_DOCS_INDEX = MY_DOCS_DIR / "index.json"
MY_DOCS_MAX_HTML = 10 * 1024 * 1024
MY_DOCS_PBKDF2_ROUNDS = 200_000
# صيغة الملف المشفر (ثنائية، موثقة — يقرأها open_doc.py المستقل):
# MAGIC(4)=b'MDE1' | salt(16) | iv(16) | ciphertext(n, AES-256-CBC/PKCS7) | hmac(32, SHA256)
MY_DOCS_MAGIC = b"MDE1"


def _my_docs_keys(salt: bytes) -> tuple:
    """اشتقاق مفتاحي التشفير والتحقق: PBKDF2-HMAC-SHA256 (SECRET_KEY, salt) → 64B."""
    from django.conf import settings as _djset
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _PBKDF2
    _kdf = _PBKDF2(algorithm=_hashes.SHA256(), length=64, salt=salt,
                   iterations=MY_DOCS_PBKDF2_ROUNDS)
    _raw = _kdf.derive(str(_djset.SECRET_KEY).encode("utf-8"))
    return _raw[:32], _raw[32:]


def _my_docs_key(salt: bytes) -> bytes:
    """توافق خلفي: مفتاح Fernet القديم (32B)."""
    import base64 as _b64
    from django.conf import settings as _djset
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _PBKDF2
    _kdf = _PBKDF2(algorithm=_hashes.SHA256(), length=32, salt=salt,
                   iterations=MY_DOCS_PBKDF2_ROUNDS)
    return _b64.urlsafe_b64encode(_kdf.derive(str(_djset.SECRET_KEY).encode("utf-8")))


def _my_docs_encrypt(raw: bytes, salt_hex: str) -> bytes:
    """تشفير AES-256-CBC + HMAC مع ترويسة (salt/IV) — فكها في open_doc.py."""
    import hmac as _hmac, hashlib as _hashlib, os as _os
    from cryptography.hazmat.primitives.ciphers import Cipher as _Cipher, algorithms as _algos, modes as _modes
    from cryptography.hazmat.primitives import padding as _pad
    salt = bytes.fromhex(salt_hex)
    enc_key, mac_key = _my_docs_keys(salt)
    iv = _os.urandom(16)
    _padder = _pad.PKCS7(128).padder()
    cipher = _padder.update(raw) + _padder.finalize()
    _enc = _Cipher(_algos.AES(enc_key), _modes.CBC(iv))
    _encryptor = _enc.encryptor()
    _ct = _encryptor.update(cipher) + _encryptor.finalize()
    tag = _hmac.new(mac_key, MY_DOCS_MAGIC + salt + iv + _ct, _hashlib.sha256).digest()
    return MY_DOCS_MAGIC + salt + iv + _ct + tag


def _my_docs_decrypt(token: bytes, salt_hex: str) -> bytes:
    """فك الجديد (MAGIC) مع fallback لصيغة Fernet القديمة."""
    import hmac as _hmac, hashlib as _hashlib
    if token[:4] == MY_DOCS_MAGIC and len(token) > 4 + 16 + 16 + 32:
        from cryptography.hazmat.primitives.ciphers import Cipher as _Cipher, algorithms as _algos, modes as _modes
        from cryptography.hazmat.primitives import padding as _pad
        salt, iv, rest = token[4:20], token[20:36], token[36:]
        _ct, tag = rest[:-32], rest[-32:]
        enc_key, mac_key = _my_docs_keys(salt)
        _good = _hmac.new(mac_key, MY_DOCS_MAGIC + salt + iv + _ct, _hashlib.sha256).digest()
        if not _hmac.compare_digest(_good, tag):
            raise ValueError("HMAC غير صالح — الملف معدل أو كلمة المرور خاطئة")
        _enc = _Cipher(_algos.AES(enc_key), _modes.CBC(iv))
        _dec = _enc.decryptor()
        _padded = _dec.update(_ct) + _dec.finalize()
        _unpadder = _pad.PKCS7(128).unpadder()
        return _unpadder.update(_padded) + _unpadder.finalize()
    from cryptography.fernet import Fernet as _Fernet
    return _Fernet(_my_docs_key(bytes.fromhex(salt_hex))).decrypt(token)


def _my_docs_load():
    """الفهرس + ترحيل كسول لأي مستند قديم غير مشفر."""
    items = _my_docs_load_raw()
    for d in items:
        if isinstance(d, dict) and not d.get("enc"):
            _my_docs_migrate_legacy(d)
    return _my_docs_load_raw()


def _my_docs_save_index(items):
    import json as _j
    MY_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    MY_DOCS_INDEX.write_text(_j.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _my_docs_migrate_legacy(entry):
    """ترحيل مستند قديم غير مشفر (html/png) إلى مشفر (.html.enc/.png.enc) — لمرة واحدة."""
    try:
        import os as _os
        if entry.get("enc"):
            return entry
        _html = MY_DOCS_DIR / f"{entry.get('id')}.html"
        if not _html.exists():
            return entry
        salt_hex = _os.urandom(16).hex()
        _html.write_bytes(_my_docs_encrypt(_html.read_bytes(), salt_hex))
        _html.replace(MY_DOCS_DIR / f"{entry.get('id')}.html.enc")
        _png = MY_DOCS_DIR / f"{entry.get('id')}.png"
        if _png.exists():
            _png.write_bytes(_my_docs_encrypt(_png.read_bytes(), salt_hex))
            _png.replace(MY_DOCS_DIR / f"{entry.get('id')}.png.enc")
        entry["salt"] = salt_hex
        entry["enc"] = True
        entry["thumb"] = (MY_DOCS_DIR / f"{entry.get('id')}.png.enc").exists()
        items = _my_docs_load_raw()
        for i, d in enumerate(items):
            if d.get("id") == entry.get("id"):
                items[i] = entry
                break
        _my_docs_save_index(items)
    except Exception:
        pass
    return entry


def _my_docs_load_raw():
    try:
        MY_DOCS_DIR.mkdir(parents=True, exist_ok=True)
        if MY_DOCS_INDEX.exists():
            import json as _j
            return _j.loads(MY_DOCS_INDEX.read_text(encoding="utf-8") or "[]")
    except Exception:
        pass
    return []


def _my_docs_id(name, taken):
    import re as _re, datetime as _dt
    base = _re.sub(r"[^\w\-]+", "_", str(name or "doc").strip(), flags=_re.UNICODE).strip("_") or "doc"
    base = base[:60]
    stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    cand = f"{base}_{stamp}"
    i = 2
    while cand in taken:
        cand = f"{base}_{stamp}_{i}"
        i += 1
    return cand


def my_docs(request):
    """صفحة مستنداتي — استعراض المستندات المحفوظة بحالتها (my_docs/)."""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    return render(request, "my_docs.html", {})


def api_my_docs_list(request):
    """GET /api/my-docs/list/ → {docs:[{id,name,app,rml,title,created,size}]}"""
    try:
        return JsonResponse({"docs": _my_docs_load()}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_my_docs_save(request):
    """POST /api/my-docs/save/ {name, app?, rml?, title?, html} → يحفظ HTML بحالته في my_docs/."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        name = str(data.get("name") or "").strip()
        html = data.get("html") or ""
        if not name:
            return JsonResponse({"error": "اسم المستند مطلوب"}, status=400)
        if not html or not str(html).strip():
            return JsonResponse({"error": "لا يوجد محتوى للحفظ — ولّد المعاينة أولاً"}, status=400)
        if len(html.encode("utf-8", "ignore")) > MY_DOCS_MAX_HTML:
            return JsonResponse({"error": "المستند أكبر من 10MB"}, status=400)
        import datetime as _dt
        import os as _os
        items = _my_docs_load()
        doc_id = _my_docs_id(name, {d.get("id") for d in items})
        salt_hex = _os.urandom(16).hex()
        html_bytes = str(html).encode("utf-8")
        # المصغرة من نسخة مؤقتة (لا تُكتب كنص صريح في my_docs أبداً)
        _thumb = False
        try:
            import tempfile as _tf
            with _tf.TemporaryDirectory(prefix="mydoctmp_") as _td:
                _tmp = _os.path.join(_td, "doc.html")
                with open(_tmp, "w", encoding="utf-8") as _fh:
                    _fh.write(str(html))
                _png = _os.path.join(_td, "thumb.png")
                _chrome = _os.environ.get("CHROME_BIN") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                if _os.path.exists(_chrome):
                    import subprocess as _sp
                    _sp.run([_chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                             "--hide-scrollbars", "--window-size=420,594",
                             f"--screenshot={_png}", "--virtual-time-budget=6000",
                             pathlib.Path(_tmp).as_uri()],
                            timeout=60, capture_output=True)
                    if _os.path.exists(_png):
                        with open(_png, "rb") as _fh:
                            _png_bytes = _fh.read()
                        if _png_bytes:
                            (MY_DOCS_DIR / f"{doc_id}.png.enc").write_bytes(
                                _my_docs_encrypt(_png_bytes, salt_hex))
                            _thumb = True
        except Exception:
            _thumb = False
        (MY_DOCS_DIR / f"{doc_id}.html.enc").write_bytes(_my_docs_encrypt(html_bytes, salt_hex))
        entry = {"id": doc_id, "name": name[:120],
                 "app": str(data.get("app") or ""), "rml": str(data.get("rml") or ""),
                 "title": str(data.get("title") or "")[:200],
                 "created": _dt.datetime.now().isoformat(timespec="seconds"),
                 "size": len(html_bytes),
                 "salt": salt_hex, "enc": True, "thumb": _thumb}
        items.insert(0, entry)
        _my_docs_save_index(items)
        return JsonResponse({"ok": True, "doc": entry}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_my_docs_view(request, doc_id):
    """GET /api/my-docs/<id>/ → محتوى HTML المحفوظ (للعرض في iframe).

    يُعفى من X-Frame-Options (الافتراضي DENY يمنع كل iframes) — والعرض
    في iframe محمي بـ sandbox بلا سكربتات.
    """
    from django.views.decorators.clickjacking import xframe_options_exempt as _exempt

    @_exempt
    def _inner(_request, _doc_id):
        try:
            import re as _re
            if not _re.fullmatch(r"[\w\-]+", str(_doc_id or "")):
                return JsonResponse({"error": "invalid id"}, status=400)
            _enc = MY_DOCS_DIR / f"{_doc_id}.html.enc"
            if _enc.exists():
                _salt = ""
                for _d in _my_docs_load_raw():
                    if _d.get("id") == str(_doc_id):
                        _salt = _d.get("salt") or ""
                        break
                if not _salt:
                    return JsonResponse({"error": "مفتاح المستند مفقود"}, status=500)
                try:
                    _raw = _my_docs_decrypt(_enc.read_bytes(), _salt)
                except Exception:
                    return JsonResponse({"error": "تعذر فك تشفير المستند"}, status=500)
                try:
                    # ترقية شفافة: صيغة Fernet القديمة ← MDE1 (salt/IV صريح)
                    if _enc.read_bytes()[:4] != MY_DOCS_MAGIC:
                        _enc.write_bytes(_my_docs_encrypt(_raw, _salt))
                except Exception:
                    pass
                return HttpResponse(_raw.decode("utf-8", "replace"),
                                    content_type="text/html; charset=utf-8")
            target = MY_DOCS_DIR / f"{_doc_id}.html"
            if not target.exists():
                return JsonResponse({"error": "المستند غير موجود"}, status=404)
            return HttpResponse(target.read_text(encoding="utf-8"),
                                content_type="text/html; charset=utf-8")
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return _inner(request, doc_id)


def api_my_docs_thumb(request, doc_id):
    """GET /api/my-docs/<id>/thumb/ → صورة المصغرة (PNG) أو 404."""
    try:
        import re as _re
        if not _re.fullmatch(r"[\w\-]+", str(doc_id or "")):
            return JsonResponse({"error": "invalid id"}, status=400)
        _enc = MY_DOCS_DIR / f"{doc_id}.png.enc"
        if _enc.exists():
            _salt = ""
            for _d in _my_docs_load_raw():
                if _d.get("id") == str(doc_id):
                    _salt = _d.get("salt") or ""
                    break
            if not _salt:
                return JsonResponse({"error": "مفتاح المستند مفقود"}, status=500)
            try:
                _raw = _my_docs_decrypt(_enc.read_bytes(), _salt)
            except Exception:
                return JsonResponse({"error": "تعذر فك تشفير المصغرة"}, status=500)
            return HttpResponse(_raw, content_type="image/png")
        target = MY_DOCS_DIR / f"{doc_id}.png"
        if not target.exists():
            return JsonResponse({"error": "no thumbnail"}, status=404)
        return HttpResponse(target.read_bytes(), content_type="image/png")
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_my_docs_delete(request):
    """POST /api/my-docs/delete/ {id} → حذف المستند."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        import re as _re
        data = json.loads(request.body.decode() or "{}")
        doc_id = str(data.get("id") or "")
        if not _re.fullmatch(r"[\w\-]+", doc_id):
            return JsonResponse({"error": "invalid id"}, status=400)
        items = [d for d in _my_docs_load() if d.get("id") != doc_id]
        _my_docs_save_index(items)
        try:
            for _suf in (".html.enc", ".png.enc", ".html", ".png"):
                (MY_DOCS_DIR / f"{doc_id}{_suf}").unlink(missing_ok=True)
        except Exception:
            pass
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def api_apps(request):
    """
    API: GET /api/apps/ → {apps: [...], count: 15, sources: {db, system}}
    Query: ?q=search to filter by ar/name
    """
    q = request.GET.get("q", "").strip()
    merged = _load_apps_folder_driven()
    db_names = {a["name"] for a in merged}

    if q:
        ql = q.lower()
        merged = [a for a in merged if ql in a.get("ar","").lower() or ql in a.get("name","").lower() or ql in a.get("description","").lower()]

    return JsonResponse({
        "apps": merged,
        "count": len(merged),
        "sources": {"folder": len(merged)},
    })


APP_CREATE_RESERVED = {"admin", "api", "static", "settings", "app", "apps", "my-reports",
                       "dashboards", "my-docs"}
APP_CREATE_ICONS = ["fa-cube", "fa-folder-open", "fa-gear", "fa-boxes-stacked",
                    "fa-chart-line", "fa-coins", "fa-calculator", "fa-wallet",
                    "fa-industry", "fa-truck", "fa-warehouse", "fa-file-contract",
                    "fa-server", "fa-database", "fa-bolt", "fa-clipboard-list",
                    "fa-users", "fa-heart-pulse", "fa-shield-halved", "fa-store",
                    "fa-screwdriver-wrench", "fa-wrench", "fa-car", "fa-helmet-safety"]
APP_CREATE_BADGES = [("bg-emerald-50", "text-emerald-600"), ("bg-blue-50", "text-blue-600"),
                     ("bg-violet-50", "text-violet-600"), ("bg-amber-50", "text-amber-600"),
                     ("bg-rose-50", "text-rose-600"), ("bg-cyan-50", "text-cyan-600"),
                     ("bg-fuchsia-50", "text-fuchsia-600"), ("bg-lime-50", "text-lime-600"),
                     ("bg-orange-50", "text-orange-600"), ("bg-teal-50", "text-teal-600"),
                     ("bg-indigo-50", "text-indigo-600"), ("bg-pink-50", "text-pink-600")]


APP_ICON_UPLOAD_EXTS = {"svg", "png", "jpg", "jpeg", "webp"}
APP_ICON_UPLOAD_MAX = 512 * 1024


def _app_icon_pending_dir():
    d = BASE_DIR / "odex" / "system" / ".pending_icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


@csrf_exempt
def api_apps_icon_upload(request):
    """POST /api/apps/icon/upload/ (multipart file=) — stage a custom app icon.

    Validates svg/png/jpg/webp ≤512KB (+ magic sniff), stores under
    odex/system/.pending_icons/<uuid>.<ext>, prunes entries older than 24h.
    Returns {ok, token: <uuid>.<ext>, kind} — pass token as icon="upload:<token>".
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        import re as _re_io, uuid as _uuid, time as _time
        f = request.FILES.get("file")
        if not f:
            return JsonResponse({"error": "file required"}, status=400)
        ext = (f.name.rsplit(".", 1)[-1] if "." in (f.name or "") else "").lower()
        if ext not in APP_ICON_UPLOAD_EXTS:
            return JsonResponse({"error": "svg/png/jpg/webp only"}, status=400)
        if (f.size or 0) > APP_ICON_UPLOAD_MAX or (f.size or 0) <= 0:
            return JsonResponse({"error": "empty or >512KB"}, status=400)
        raw = f.read()
        if len(raw) > APP_ICON_UPLOAD_MAX:
            return JsonResponse({"error": ">512KB"}, status=400)
        ok_magic = (ext == "svg" and b"<svg" in raw[:2048].lower()) \
            or (ext == "png" and raw[:8] == b"\x89PNG\r\n\x1a\n") \
            or (ext in ("jpg", "jpeg") and raw[:2] == b"\xff\xd8") \
            or (ext == "webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
        if not ok_magic:
            return JsonResponse({"error": "file content mismatch"}, status=400)
        d = _app_icon_pending_dir()
        now = _time.time()
        for p in d.iterdir():
            try:
                if now - p.stat().st_mtime > 24 * 3600:
                    p.unlink()
            except Exception:
                pass
        token = f"{_uuid.uuid4().hex}.{ext}"
        if not _re_io.fullmatch(r"[A-Za-z0-9]{32}\.(svg|png|jpg|jpeg|webp)", token):
            return JsonResponse({"error": "internal"}, status=500)
        (d / token).write_bytes(raw)
        return JsonResponse({"ok": True, "token": token, "kind": ext},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_apps_icon_file(request, app_name):
    """GET /api/apps/<app>/icon-file — serve the custom icon (file:icon.*)."""
    try:
        import re as _re_in
        import mimetypes as _mt
        if not _re_in.fullmatch(r"[a-z][a-z0-9_]*", (app_name or "").lower()):
            return JsonResponse({"error": "invalid app"}, status=400)
        app_dir = BASE_DIR / "odex" / "system" / app_name.lower()
        found = None
        if app_dir.exists():
            for ext in ("svg", "png", "jpg", "jpeg", "webp"):
                p = app_dir / f"icon.{ext}"
                if p.is_file():
                    found, ext = p, ext
                    break
        if not found:
            return JsonResponse({"error": "no custom icon"}, status=404)
        ctype = {"svg": "image/svg+xml", "png": "image/png",
                 "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}[ext]
        return HttpResponse(found.read_bytes(), content_type=ctype)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_apps_create(request):
    """POST /api/apps/create/ — create an app (folder odex/system/<name>/ + metadata.json + App row).

    Body: {name (latin id), ar*, en?, icon?, category?, description?, version?}
    sort_order = max+1 so the app lands at the END of home/API lists.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        import re as _re_app
        data = json.loads(request.body.decode() or "{}")
        name = (data.get("name") or "").strip().lower()
        ar = (data.get("ar") or "").strip()
        if not _re_app.fullmatch(r"[a-z][a-z0-9_]*", name or ""):
            return JsonResponse({"error": "name: أحرف لاتينية صغيرة تبدأ بحرف (a-z, 0-9, _)"}, status=400)
        if name in APP_CREATE_RESERVED:
            return JsonResponse({"error": f"name محجوز للنظام: {name}"}, status=400)
        if not ar:
            return JsonResponse({"error": "ar (الاسم العربي) مطلوب"}, status=400)
        from .models import App
        from django.db.models import Max
        app_dir = BASE_DIR / "odex" / "system" / name
        if app_dir.exists() or App.objects.filter(name=name).exists():
            return JsonResponse({"error": f"التطبيق موجود بالفعل: {name}"}, status=400)
        icon = (data.get("icon") or "fa-cube").strip()
        icon_file = None
        if icon.startswith("upload:"):
            import re as _re_tok
            token = icon[len("upload:"):]
            if not _re_tok.fullmatch(r"[A-Za-z0-9]{32}\.(svg|png|jpg|jpeg|webp)", token):
                return JsonResponse({"error": "bad upload token"}, status=400)
            src = _app_icon_pending_dir() / token
            if not src.is_file():
                return JsonResponse({"error": "uploaded icon expired — re-upload"}, status=400)
            icon_file = f"icon.{token.rsplit('.', 1)[-1]}"
        elif icon not in APP_CREATE_ICONS:
            icon = "fa-cube"
        badge = ((data.get("icon_bg") or "").strip(), (data.get("icon_color") or "").strip())
        if badge not in APP_CREATE_BADGES:
            badge = ("bg-indigo-50", "text-indigo-600")
        try:
            max_order = App.objects.aggregate(_m=Max("sort_order"))["_m"] or 0
        except Exception:
            max_order = 0
        meta = {
            "name": name,
            "ar": ar,
            "en": (data.get("en") or name).strip(),
            "icon": icon,
            "icon_bg": badge[0],
            "icon_color": badge[1],
            "version": (data.get("version") or "1.0.0").strip(),
            "category": (data.get("category") or "عام").strip(),
            "description": (data.get("description") or "").strip(),
            "sort_order": int(max_order) + 1,
            "is_new": True,
        }
        app_dir.mkdir(parents=True, exist_ok=False)
        if icon_file:
            import shutil as _sh
            _sh.move(str(_app_icon_pending_dir() / token), str(app_dir / icon_file))
            icon = f"file:{icon_file}"
            meta["icon"] = icon
        (app_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
        (app_dir / "documents").mkdir(exist_ok=True)
        app, _ = App.objects.update_or_create(
            name=name,
            defaults={"name_ar": meta["ar"], "name_en": meta["en"], "icon": meta["icon"],
                      "icon_bg": meta["icon_bg"], "icon_color": meta["icon_color"],
                      "version": meta["version"], "description": meta["description"],
                      "description_ar": meta["description"], "category": meta["category"],
                      "is_new": True, "sort_order": meta["sort_order"]},
        )
        return JsonResponse({"ok": True, "app": app.to_dict()},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def _wizard_flags():
    """Designer modal files may be absent (restructuring) — pages degrade gracefully.

    Also exposed as a template context processor (see config/settings.py).
    """
    try:
        d = BASE_DIR / "odex" / "system" / "settings" / "modals"
        rml = (d / "rml_wizard_modal.html").is_file() and (d / "rml_wizard_script.html").is_file()
        fml = (d / "forms_wizard_modal.html").is_file() and (d / "forms_wizard_script.html").is_file()
    except Exception:
        rml, fml = False, False
    return {"has_rml_wizard": bool(rml), "has_fml_wizard": bool(fml)}


def wizard_flags_cp(request):
    """Context processor — has_rml_wizard / has_fml_wizard in every template."""
    try:
        return _wizard_flags()
    except Exception:
        return {"has_rml_wizard": False, "has_fml_wizard": False}


def app_detail(request, app_name):
    """App detail — shows sidebar with الادخالات (FML) and التقارير (RML) for one app"""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    app_meta, fml_files, rml_files = _load_app_context(app_name)
    app_rules = []
    for _f in rml_files:
        for _r in (_f.get("rules") or []):
            app_rules.append({"file": _f.get("file"), "name": _r.get("name"),
                              "display": _r.get("display"), "icon": _r.get("icon"),
                              "description": _r.get("description"),
                              "policy_count": len(_r.get("policies") or [])})
    return render(request, "app_detail.html", {
        "app": app_meta,
        "rml_files": rml_files,
        "fml_files": fml_files,
        "app_name": app_name,
        "app_rules": app_rules,
        "fml_by_category": _group_files_by_category(fml_files),
        "rml_by_category": _group_files_by_category(rml_files),
        "side_groups": _unified_side_groups(fml_files, rml_files),
    })

# ── Player Views (moved into urs/templates) ──────────────────────────────────
def app_form_player(request, app_name, fml_file):
    """Render Forms Player inside urs app keeping sidebar navigation"""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    app_meta, fml_files, rml_files = _load_app_context(app_name)
    # Load specific FML metadata for header
    fml_path = _find_fml_path(fml_file, app_name)
    fml_metadata = {"displayName": fml_file, "name": fml_file}
    fml_tabs = []
    fml_fields = []
    if fml_path and fml_path.exists():
        try:
            from fmlk_engine.compiler import FMLKFormCompiler
            comp = FMLKFormCompiler(path=fml_path)
            fml_metadata = comp.fml_metadata()
            fml_tabs = [t.to_dict() for t in comp.tabs()]
            fml_fields = [f.to_dict() for f in comp.fields()]
        except Exception:
            pass
    # تجميع نماذج الشريط الجانبي حسب الفئة (للطي والتوسيع)
    fml_by_category = _group_files_by_category(fml_files)
    # خريطة الجدول → ملف النموذج (لزرّي إنشاء/تحرير المرجع في تبويب جديد)
    import json as _json2
    _tbl_map = {}
    for _f in fml_files:
        _t = (((_f.get("metadata") or {}).get("table")) or "").strip()
        if _t:
            _tbl_map.setdefault(_t.split(".")[-1], _f.get("file"))
    return render(request, "forms_player.html", {
        "app": app_meta,
        "app_name": app_name,
        "fml_file": fml_file,
        "fml_metadata": fml_metadata,
        "fml_tabs": fml_tabs,
        "fml_fields": fml_fields,
        "fml_files": fml_files,
        "fml_by_category": fml_by_category,
        "rml_files": rml_files,
        "rml_by_category": _group_files_by_category(rml_files),
        "side_groups": _unified_side_groups(fml_files, rml_files),
        "fml_table_map_json": _json2.dumps(_tbl_map, ensure_ascii=False),
    })

def app_report_player(request, app_name, rml_file):
    """Render Report Player inside urs app keeping sidebar navigation"""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    app_meta, fml_files, rml_files = _load_app_context(app_name)
    rml_path = _find_rml_path(rml_file, app_name)
    rml_metadata = {"displayName": rml_file, "name": rml_file}
    rml_columns = []
    rml_fields = []
    rml_connections = []
    rml_charts = []
    rml_rules = []
    rml_groups = []
    rml_table_opts = []
    if rml_path and rml_path.exists():
        try:
            from rml_python.compiler import RMLReportCompiler
            comp = RMLReportCompiler(path=rml_path)
            rml_metadata = comp.rpt_metadata()
            rml_columns = [c.to_dict() for c in comp.columns()]
            rml_fields = [f.to_dict() for f in comp.fields()]
            rml_connections = [c.to_dict() for c in comp.connections()]
            rml_charts = [c.to_dict() for c in comp.charts()]
            rml_rules = [r.to_dict() for r in comp.rules()]
            rml_groups = [g.to_dict() for g in comp.groups()]
            try:
                rml_table_opts = comp.table_opts() or []
            except Exception:
                rml_table_opts = []
        except Exception:
            pass
    # Ensure json-serializable for JS
    import json as _json
    return render(request, "report_player.html", {
        "app": app_meta,
        "app_name": app_name,
        "rml_file": rml_file,
        "rml_metadata": rml_metadata,
        "rml_metadata_json": _json.dumps(rml_metadata, ensure_ascii=False),
        "rml_columns": rml_columns,
        "rml_columns_json": _json.dumps(rml_columns, ensure_ascii=False),
        "rml_fields": rml_fields,
        "rml_fields_json": _json.dumps(rml_fields, ensure_ascii=False),
        "rml_connections": rml_connections,
        "rml_connections_json": _json.dumps(rml_connections, ensure_ascii=False),
        "rml_charts": rml_charts,
        "rml_charts_json": _json.dumps(rml_charts, ensure_ascii=False),
        "rml_rules": rml_rules,
        "rml_rules_json": _json.dumps(rml_rules, ensure_ascii=False),
        "rml_groups": rml_groups,
        "rml_groups_json": _json.dumps(rml_groups, ensure_ascii=False),
        "rml_table_opts": rml_table_opts,
        "rml_table_opts_json": _json.dumps(rml_table_opts, ensure_ascii=False),
        "fml_files": fml_files,
        "rml_files": rml_files,
        "fml_by_category": _group_files_by_category(fml_files),
        "rml_by_category": _group_files_by_category(rml_files),
        "side_groups": _unified_side_groups(fml_files, rml_files),
    })

def app_report_designer(request, app_name):
    """Standalone fullscreen report designer (create/edit RML wizard as a page)."""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    app_meta, fml_files, rml_files = _load_app_context(app_name)
    return render(request, "report_designer.html", {
        "app": app_meta,
        "app_name": app_name,
        "fml_files": fml_files,
        "rml_files": rml_files,
    })

def app_forms_designer(request, app_name):
    """Standalone fullscreen forms designer — same RML wizard, local connections only."""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    app_meta, fml_files, rml_files = _load_app_context(app_name)
    return render(request, "forms_designer.html", {
        "app": app_meta,
        "app_name": app_name,
        "fml_files": fml_files,
        "rml_files": rml_files,
    })

def app_data_diagram(request, app_name):
    """Standalone UML-like data diagram (tables, links, drag & drop)."""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    app_meta, fml_files, rml_files = _load_app_context(app_name)
    return render(request, "data_diagram.html", {
        "app": app_meta,
        "app_name": app_name,
        "fml_files": fml_files,
        "rml_files": rml_files,
    })

def api_app_files(request, app_name):
    """API: GET /api/apps/<app_name>/files/ → {rml: [...], fml: [...]}"""
    candidates = [BASE_DIR / "odex" / "system" / app_name, BASE_DIR / "system" / app_name]
    rml_files = []
    fml_files = []
    for cand in candidates:
        if cand.exists():
            for p in sorted(cand.glob("*.rml")):
                _dn = p.stem.replace("_", " ")
                try:
                    from rml_python.compiler import RMLReportCompiler
                    _dn = RMLReportCompiler(path=p).rpt_metadata().get("displayName") or _dn
                except Exception:
                    pass
                rml_files.append({"file": p.name, "path": str(p), "displayName": _dn})
            for p in sorted(cand.glob("*.fmlk")) + sorted(cand.glob("*.fml")):
                fml_files.append({"file": p.name, "path": str(p)})
            break
    return JsonResponse({"app": app_name, "rml": rml_files, "fml": fml_files, "counts": {"rml": len(rml_files), "fml": len(fml_files)}})

def api_apps_sync(request):
    """POST /api/apps/sync/ — re-sync system folders → DB (for UI editable)"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    # Re-run sync logic (same as db_init) — now from odex/system
    from django.db import transaction
    synced = 0
    for cand in [BASE_DIR / "odex" / "system", BASE_DIR / "system"]:
        if not cand.exists():
            continue
        for meta_path in sorted(cand.glob("*/metadata.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                App.objects.update_or_create(
                    name=data["name"],
                    defaults={
                        "name_ar": data.get("ar", data["name"]),
                        "name_en": data.get("en", ""),
                        "icon": data.get("icon", "fa-cube"),
                        "icon_bg": data.get("icon_bg", "bg-gray-900"),
                        "icon_color": data.get("icon_color", "text-white"),
                        "version": data.get("version", "1.0.0"),
                        "description": data.get("description", ""),
                        "description_ar": data.get("description", ""),
                        "category": data.get("category", "عام"),
                        "is_new": data.get("is_new", False),
                        "sort_order": data.get("sort_order", 0),
                    },
                )
                synced += 1
            except Exception as e:
                continue
    return JsonResponse({"synced": synced, "total": App.objects.count()})

# ── FMLK API proxied into Django (so players work on same host) ─────────────
def _wizard_effective_conn():
    """Effective default connection: row #1 overlaid with app.conf DB_* (wizard/hand-edit).

    app.conf is the fresher source (wizard writes it on every save); the DB row is
    fallback. Password: app.conf PBKDF2 envelope first, else row plaintext.
    Returns SimpleNamespace compatible with _build_db_engine/_conn_is_sql_queryable,
    or None when neither source has anything usable.
    """
    from types import SimpleNamespace
    from .models import Connection
    try:
        dj = Connection.objects.filter(id=1).first()
    except Exception:
        dj = None
    ac = _appconf_read()
    if dj is None and not any((ac.get(k) or "").strip() for k in ("DB_HOST", "DB_ENGINE", "DB_NAME")):
        return None
    pw = _dbpass_resolve(ac.get("DB_PASS") or "") or (getattr(dj, "password", "") if dj else "")
    return SimpleNamespace(
        name="urs_local",
        engine=((ac.get("DB_ENGINE") or "").strip() or (getattr(dj, "engine", "") if dj else "") or "postgres"),
        host=((ac.get("DB_HOST") or "").strip() or (getattr(dj, "host", "") if dj else "") or ""),
        port=((ac.get("DB_PORT") or "").strip() or (getattr(dj, "port", "") if dj else "") or ""),
        user=((ac.get("DB_USER") or "").strip() or (getattr(dj, "user", "") if dj else "") or ""),
        password=pw,
        instance=((ac.get("DB_NAME") or "").strip() or (getattr(dj, "instance", "") if dj else "") or ""),
        schema=((ac.get("DB_SCHEMA") or "").strip() or (getattr(dj, "schema", "") if dj else "") or ""),
        is_queryable=(getattr(dj, "is_queryable", True) if dj else True),
    )


def _fmlk_resolve_db(comp):
    """Routing: the file's declared <fml_metadata connection> wins when it
    resolves to a SQL-queryable row; else connection #1 (urs_local); else legacy.

    Built lazy (no connect here) so a down DB surfaces the row's real params via
    MockDB in _fmlk_get_engine — never the legacy hardcoded host.
    """
    try:
        from .models import Connection
        cname = (comp.fml_metadata().get("connection") or "").strip()
    except Exception:
        cname = ""
    if cname and cname != "ORCL_PROD":
        try:
            dj = None
            if cname.isdigit():
                dj = Connection.objects.filter(id=int(cname)).first()
            if dj is None:
                dj = Connection.objects.filter(name=cname).first()
            if dj is not None and _conn_is_sql_queryable(dj):
                return _build_db_engine(dj, connect=False)
        except Exception:
            pass
    try:
        eff = _wizard_effective_conn()
        if eff is not None and _conn_is_sql_queryable(eff):
            return _build_db_engine(eff, connect=False)
    except Exception:
        pass
    return PostgresEngine(host="172.16.10.101", dbname="urs", user="postgres", password="postgres")


def _fmlk_get_engine(fml_name, app_name=None):
    from fmlk_engine.compiler import FMLKFormCompiler
    path = _find_fml_path(fml_name, app_name)
    if path and path.exists():
        comp = FMLKFormCompiler(path=path)
    else:
        # fallback default
        comp = FMLKFormCompiler(xml_text=f'<fml><fml_metadata name="{fml_name}" displayName="{fml_name}" table="employees"/><fields><field name="first_name" alias="الاسم" inputType="text"/></fields></fml>')
    # Use PostgresEngine on 172.16.10.101/urs
    try:
        db = _fmlk_resolve_db(comp)
        db.connect()
    except Exception as e:
        # mock db for preview if postgres not reachable
        # NOTE: capture str(e) in a plain local first — the except-variable `e`
        # is deleted when the block exits, so nested methods must NOT close over it
        # (else: "cannot access free variable 'e'").
        _mock_msg = f"mock db: {e}"
        class MockDB:
            conn = None
            def connect(self): raise Exception(_mock_msg)
            def _exec(self, *a, **kw): raise Exception(_mock_msg)
        db = MockDB()
    from fmlk_engine.engine import FMLKFormEngine
    engine = FMLKFormEngine(comp, db)
    # Monkey-patch Postgres bind handling for FML engine if needed (already in _exec)
    return engine

def api_fmlk_metadata(request):
    fml = request.GET.get("fml", "hr_form")
    app = request.GET.get("app", None)
    try:
        from fmlk_engine.compiler import FMLKFormCompiler
        path = _find_fml_path(fml, app)
        if path and path.exists():
            comp = FMLKFormCompiler(path=path)
        else:
            # try direct string
            comp = FMLKFormCompiler(xml_text=f'<fml><fml_metadata name="{fml}" displayName="{fml}"/><fields></fields></fml>')
            # fallback to _fmlk_get_engine logic
            eng = _fmlk_get_engine(fml, app)
            comp = eng.compiler
            return JsonResponse({"metadata": comp.fml_metadata(), "tabs": [t.to_dict() for t in comp.tabs()], "fields": [f.to_dict() for f in comp.fields()], "details": [d.to_dict() for d in comp.details()], "actions": [a.to_dict() for a in comp.actions()]})
        return JsonResponse({"metadata": comp.fml_metadata(), "tabs": [t.to_dict() for t in comp.tabs()], "fields": [f.to_dict() for f in comp.fields()], "details": [d.to_dict() for d in comp.details()], "actions": [a.to_dict() for a in comp.actions()]})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

def api_app_modal(request, app_name, file):
    """GET /api/apps/<app>/modals/<file> — تقديم ملف مودال HTML منفصل لأزرار custom_actions (render)."""
    f = (file or "").strip()
    if not f or not f.endswith(".html") or ".." in f or f.startswith("/") or ":" in f:
        return HttpResponse("invalid modal file", status=400, content_type="text/plain; charset=utf-8")
    for base in [BASE_DIR / "odex" / "system" / app_name, BASE_DIR / "system" / app_name]:
        cand = base / f
        try:
            if cand.exists() and cand.is_file() and str(cand.resolve()).startswith(str(base.resolve())):
                return HttpResponse(cand.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
        except Exception:
            continue
    return HttpResponse("modal not found", status=404, content_type="text/plain; charset=utf-8")


def api_fmlk_options(request):
    """GET /api/fmlk/options?table=&column=&schema=&search=&limit=&display=&conn= — قيم مرجع [table.column]."""
    table = (request.GET.get("table") or "").strip()
    column = (request.GET.get("column") or "").strip()
    schema = (request.GET.get("schema") or "").strip()
    display = (request.GET.get("display") or "").strip() or None
    search = request.GET.get("search") or None
    conn_ref = (request.GET.get("conn") or "").strip()
    if "." in table and not schema:
        schema, table = table.split(".", 1)
    try:
        limit = int(request.GET.get("limit") or 500)
    except (TypeError, ValueError):
        limit = 500
    if not table or not column:
        return JsonResponse({"error": "table and column required"}, status=400)
    try:
        from fmlk_engine.engine import get_options_source
        conn_params = None
        if conn_ref:
            try:
                from .models import Connection
                _c = Connection.objects.filter(name=conn_ref).first()
                if _c is None and conn_ref.isdigit():
                    _c = Connection.objects.filter(id=int(conn_ref)).first()
                if _c is not None:
                    _c = _effective_or_row(_c.id)
                if _c is not None and str(getattr(_c, "engine", "") or "").lower() == "postgres":
                    conn_params = dict(dbname=_c.instance or "urs", user=_c.user or "",
                                       password=_c.password or "", host=_c.host or "",
                                       port=int(_c.port or 5432))
            except Exception:
                conn_params = None
        opts = get_options_source(table, column, schema, limit, search, display, conn_params)
        return JsonResponse({"table": table, "column": column, "schema": schema or "public",
                             "display": display or column, "options": opts, "total": len(opts)})
    except ValueError as ve:
        return JsonResponse({"error": str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_fmlk_lookup(request):
    fml = request.GET.get("fml", "hr_form")
    field = request.GET.get("field", "")
    table = request.GET.get("table", "")
    app = request.GET.get("app", None)
    limit = int(request.GET.get("limit", "50") or 50)
    search = request.GET.get("search")
    if not field and not table:
        return JsonResponse({"error": "field or table required"}, status=400)
    try:
        eng = _fmlk_get_engine(fml, app)
        if field:
            try:
                data = eng.get_field_lookup(field_name=field, limit=limit, search=search)
                return JsonResponse(data)
            except ValueError as ve:
                if table:
                    pass
                else:
                    return JsonResponse({"error": str(ve)}, status=404)
        # generic table fallback handled inside engine lookup? simple mock
        return JsonResponse({"field": field or table, "refTable": table, "options": [{"value": 1, "label": "الإدارة العامة"}, {"value": 2, "label": "الفرع الرئيسي"}], "source": "mock"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

def api_fmlk_lookups(request):
    fml = request.GET.get("fml", "hr_form")
    app = request.GET.get("app")
    try:
        eng = _fmlk_get_engine(fml, app)
        return JsonResponse({"fml": fml, "lookups": eng.list_lookups()})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

def api_fmlk_field_types(request):
    """GET /api/fmlk/field-types/ → registry of supported field types."""
    try:
        from fmlk_engine.field_types import list_types
        types = list_types()
        return JsonResponse({"types": types, "total": len(types)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


_META_CATS_CACHE = {"at": 0.0, "cats": []}

def api_meta_categories(request):
    """GET /api/meta/categories/ → sorted unique categories from all .rml/.fmlk metadata.

    Feeds designer category datalists (type new or pick existing). 60s cache.
    """
    import time
    try:
        if time.time() - _META_CATS_CACHE["at"] < 60 and _META_CATS_CACHE["cats"]:
            return JsonResponse({"categories": _META_CATS_CACHE["cats"]})
    except Exception:
        pass
    cats = set()
    try:
        from rml_python.compiler import RMLReportCompiler
        from fmlk_engine.compiler import FMLKFormCompiler
        for _root in [BASE_DIR / "odex" / "system", BASE_DIR / "system"]:
            if not _root.exists():
                continue
            for _app in sorted([d for d in _root.iterdir() if d.is_dir()]):
                for _p in sorted(_app.glob("*.rml")):
                    try:
                        _c = (RMLReportCompiler(path=_p).rpt_metadata().get("category") or "").strip()
                        if _c:
                            cats.add(_c)
                    except Exception:
                        pass
                for _p in sorted(_app.glob("*.fmlk")) + sorted(_app.glob("*.fml")):
                    try:
                        _c = (FMLKFormCompiler(path=_p).fml_metadata().get("category") or "").strip()
                        if _c:
                            cats.add(_c)
                    except Exception:
                        pass
            break
    except Exception:
        pass
    out = sorted(cats)
    try:
        _META_CATS_CACHE.update({"at": time.time(), "cats": out})
    except Exception:
        pass
    return JsonResponse({"categories": out})


def api_fmlk_sql_functions(request):
    """GET /api/fmlk/sql-functions → دوال SQL المقبولة في الصيغ مع [field]."""
    try:
        from fmlk_engine.engine import SQL_FUNCTIONS
        return JsonResponse({"functions": SQL_FUNCTIONS, "total": len(SQL_FUNCTIONS),
                             "hint": "استخدم [field] للإشارة لحقل آخر، مثال: [qty] * [price] أو COALESCE([name], '')"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


DATA_TYPE_CHOICES = ["VARCHAR", "TEXT", "INTEGER", "BIGINT", "NUMERIC", "BOOLEAN", "DATE", "TIME", "TIMESTAMP", "IMAGE", "MEDIA"]


def api_fmlk_data_types(request):
    """GET /api/fmlk/data-types → أنواع البيانات المدعومة للترحيل."""
    return JsonResponse({"types": DATA_TYPE_CHOICES, "total": len(DATA_TYPE_CHOICES)})


def _resolve_app_dir(app_name):
    """مجلد التطبيق (odex/system أولاً) مع إنشائه عند الحاجة."""
    app_dir = BASE_DIR / "odex" / "system" / app_name
    if not app_dir.exists():
        alt = BASE_DIR / "system" / app_name
        if alt.exists():
            return alt
        app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


@csrf_exempt
def api_models_design_save(request, app_name):
    """POST /api/apps/<app>/models/design/ — حفظ مصمم الموديل كـ .fmlk.

    Body: {file?, table_ar, table_en, table?, model_type(form|rule), schema?, category?, description?,
           connection?, icon?, tabs?[{id,name,alias,sort_order,visibleIf}],
           fields: [{name, alias, data_type, inputType?, primary_key?, required?, nullable?, editable?,
                      default_mode(fixed|formula|empty)?, default_value?, formula?, validation?,
                      tab?, category?, icon?, destination?, options?([str|{value,label,color}]),
                      options_source?[{table,schema,column}], refTable?...,
                      config?{parent_field,sync_source,...}}],
           details?[{table,alias,master,detail,rel_type,columns:[{name,alias,data_type,input_type}]}],
           overwrite?}

    table_en = اسم النموذج البرمجي (للملف)؛ table = جدول القاعدة الفعلي
    (مجرّد من السكيما/القاعدة) ويُحفظ في <fml_metadata table>.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        table_en = (data.get("table_en") or "").strip()
        real_table = (data.get("table") or "").strip()
        if real_table and "." in real_table:
            real_table = real_table.split(".")[-1].strip()
        if not table_en:
            table_en = real_table
        table_ar = (data.get("table_ar") or data.get("displayName") or table_en).strip()
        model_type = (data.get("model_type") or data.get("modelType") or "form").strip().lower()
        if model_type not in ("form", "rule"):
            model_type = "form"
        if not table_en:
            return JsonResponse({"error": "table_en required (الاسم البرمجي)"}, status=400)
        if not table_en.replace("_", "").isalnum():
            return JsonResponse({"error": "table_en: أحرف وأرقام و _ فقط"}, status=400)
        schema = (data.get("schema") or "").strip() or None
        if not schema:
            try:
                from .models import Branch
                b = Branch.objects.filter(is_active=True).order_by("id").first()
                schema = b.schema_name if b and b.schema_name else "public"
            except Exception:
                schema = "public"
        fields = data.get("fields") or []
        if not fields:
            return JsonResponse({"error": "field واحد على الأقل مطلوب"}, status=400)
        seen, pks = set(), 0
        for i, f in enumerate(fields, start=1):
            nm = (f.get("name") or "").strip()
            if not nm or not nm.replace("_", "").isalnum():
                return JsonResponse({"error": f"الحقل {i}: اسم برمجي غير صالح"}, status=400)
            if nm in seen:
                return JsonResponse({"error": f"اسم الحقل مكرر: {nm}"}, status=400)
            seen.add(nm)
            if not (f.get("alias") or "").strip():
                return JsonResponse({"error": f"الحقل {nm}: الاسم العربي مطلوب"}, status=400)
            dt = (f.get("data_type") or f.get("dataType") or "VARCHAR").upper()
            if dt not in DATA_TYPE_CHOICES:
                return JsonResponse({"error": f"الحقل {nm}: نوع غير مدعوم {dt}"}, status=400)
            if f.get("primary_key"):
                pks += 1
            mode = (f.get("default_mode") or "empty").lower()
            if mode == "fixed" and (f.get("default_value") is None or str(f.get("default_value")) == "") and (f.get("default") is None or str(f.get("default")) == ""):
                return JsonResponse({"error": f"الحقل {nm}: القيمة الثابتة فارغة"}, status=400)
            if mode == "formula" and not ((f.get("formula") or f.get("calc_expr") or "").strip()):
                return JsonResponse({"error": f"الحقل {nm}: الصيغة فارغة — اكتب صيغة بـ [ ] أو اتركه فارغاً لليدوي"}, status=400)
        if pks > 1:
            pass  # composite PK مسموح
        fname = (data.get("file") or table_en).strip()
        if not fname.endswith((".fmlk", ".fml")):
            fname += ".fmlk"
        if "/" in fname or "\\" in fname or ".." in fname:
            return JsonResponse({"error": "invalid file name"}, status=400)
        app_dir = _resolve_app_dir(app_name)
        target = app_dir / fname
        if target.exists() and not data.get("overwrite"):
            return JsonResponse({"error": "file already exists (أرسل overwrite=true للتحديث)"}, status=400)
        import xml.etree.ElementTree as ET, xml.dom.minidom
        fml = ET.Element("fml")
        md = ET.SubElement(fml, "fml_metadata")
        md.set("name", table_en)
        md.set("displayName", table_ar or table_en)
        md.set("table", real_table or table_en)
        md.set("model_type", model_type)
        md.set("schema", schema)
        md.set("connection", (data.get("connection") or "urs_local").strip() or "urs_local")
        md.set("category", (data.get("category") or "عام").strip() or "عام")
        if data.get("icon"):
            md.set("icon", str(data.get("icon")).strip())
        if data.get("description"):
            md.set("description", str(data.get("description")))
        tabs_el = ET.SubElement(fml, "tabs")
        _tabs = data.get("tabs") or [{"id": "main", "name": table_ar or table_en,
                                      "alias": table_ar or table_en, "sort_order": 1}]
        for _ti, _t in enumerate(_tabs, start=1):
            tab = ET.SubElement(tabs_el, "tab")
            _tid = (str(_t.get("id") or _t.get("name") or "main")).strip() or "main"
            tab.set("id", _tid)
            tab.set("name", str(_t.get("name") or _tid))
            tab.set("alias", str(_t.get("alias") or _t.get("name") or _tid))
            try:
                tab.set("sort_order", str(int(_t.get("sort_order", _t.get("sortOrder", _ti)) or _ti)))
            except Exception:
                tab.set("sort_order", str(_ti))
            if _t.get("visibleIf") or _t.get("visible_if"):
                tab.set("visibleIf", str(_t.get("visibleIf") or _t.get("visible_if")))
        _first_tab = ((_tabs[0].get("id") or _tabs[0].get("name") or "main") if _tabs else "main")
        fields_el = ET.SubElement(fml, "fields")
        for i, f in enumerate(fields, start=1):
            el = ET.SubElement(fields_el, "field")
            el.set("id", str(f.get("id") or i))
            el.set("name", f["name"].strip())
            el.set("alias", (f.get("alias") or f["name"]).strip())
            el.set("dataType", (f.get("data_type") or f.get("dataType") or "VARCHAR").upper())
            el.set("inputType", (f.get("inputType") or f.get("input_type") or "text"))
            el.set("required", "true" if f.get("required") else "false")
            el.set("nullable", "true" if f.get("nullable", True) else "false")
            el.set("editable", "false" if f.get("editable") is False else "true")
            el.set("primary_key", "true" if f.get("primary_key") else "false")
            el.set("tab", str(f.get("tab") or _first_tab))
            _vis = (f.get("visibleIf") or f.get("visible_if") or "").strip()
            if _vis:
                if len(_vis) > 500:
                    return JsonResponse({"error": f"الحقل {nm}: شرط الإظهار أطول من 500 حرف"}, status=400)
                el.set("visibleIf", _vis)
            if f.get("icon"):
                el.set("icon", str(f.get("icon")).strip())
            if f.get("destination"):
                el.set("destination", str(f.get("destination")).strip())
            if f.get("category"):
                el.set("category", str(f.get("category")))
            if f.get("position"):
                el.set("position", str(f.get("position")))
            try:
                _cs = int(f.get("colSpan", f.get("col_span", 1)) or 1)
                if _cs > 1:
                    el.set("colSpan", str(max(1, min(12, _cs))))
            except Exception:
                pass
            mode = (f.get("default_mode") or "empty").lower()
            if mode == "fixed":
                dv = f.get("default_value", f.get("default", ""))
                el.set("default", str(dv))
            elif mode == "formula":
                fx = (f.get("formula") or f.get("calc_expr") or "").strip()
                fx_el = ET.SubElement(el, "formula")
                fx_el.text = fx
            for k in ("refTable", "refFk", "refDisplay", "placeholder",
                        "displayTable", "displayKey", "displayFk", "displayShow", "displayExpr"):
                if f.get(k):
                    el.set(k, str(f.get(k)))
            if f.get("displayOnly") in (True, 1, "1", "true", "yes"):
                el.set("displayOnly", "true")
            _cfg = f.get("config") or {}
            if isinstance(_cfg, dict):
                for _ck in ("parent_field", "sync_source", "auto_condition", "calc_expr", "poly_types",
                            "tree_parent", "sub_fields", "grid_columns", "grid_rows",
                            "matrix_rows", "matrix_cols", "separator", "placeholder_add", "allow_new"):
                    if _cfg.get(_ck) not in (None, ""):
                        el.set(_ck, str(_cfg.get(_ck)))
            _opts = f.get("options") or []
            if isinstance(_opts, str):
                _opts = [o.strip() for o in _opts.split(",") if o.strip()]
            _srcs = f.get("options_source") or f.get("optionsSource") or []
            _ref_opts = []
            if isinstance(_srcs, list):
                for _s in _srcs:
                    if not isinstance(_s, dict):
                        continue
                    if _s.get("ref"):
                        _ref_opts.append(str(_s["ref"]).strip())
                        continue
                    _tt, _cc, _ss = (_s.get("table") or "").strip(), (_s.get("column") or "").strip(), (_s.get("schema") or "").strip()
                    if _tt and _cc:
                        _ref_opts.append(f"[{_ss + '.' if _ss else ''}{_tt}.{_cc}]")
            if _opts or _ref_opts:
                o_el = ET.SubElement(el, "options")
                for o in list(_opts) + _ref_opts:
                    oo = ET.SubElement(o_el, "option")
                    if isinstance(o, dict):
                        if o.get("value") not in (None, ""):
                            oo.set("value", str(o.get("value")))
                        if o.get("color"):
                            oo.set("color", str(o.get("color")))
                        oo.text = str(o.get("label", o.get("value", "")))
                    else:
                        oo.text = str(o)
            vr = f.get("validation") or {}
            if isinstance(vr, dict) and vr:
                v_el = ET.SubElement(el, "validation")
                for vk, vv in vr.items():
                    v_el.set(vk, str(vv))
        _dets = data.get("details") or []
        if isinstance(_dets, list) and _dets:
            d_el = ET.SubElement(fml, "details")
            for _d in _dets:
                if not isinstance(_d, dict):
                    continue
                _dt = (_d.get("table") or "").strip()
                _dm, _dd = (_d.get("master") or "").strip(), (_d.get("detail") or "").strip()
                if not _dt or not _dm or not _dd:
                    continue
                de = ET.SubElement(d_el, "detail")
                de.set("table", _dt)
                de.set("alias", str(_d.get("alias") or _dt))
                de.set("master", _dm)
                de.set("detail", _dd)
                de.set("rel_type", str(_d.get("rel_type") or "one_to_many"))
                _dcols = _d.get("columns") or []
                if _dcols:
                    cs_el = ET.SubElement(de, "columns")
                    for _c in _dcols:
                        if not isinstance(_c, dict) or not (_c.get("name") or "").strip():
                            continue
                        ce = ET.SubElement(cs_el, "column")
                        ce.set("name", str(_c.get("name")).strip())
                        ce.set("alias", str(_c.get("alias") or _c.get("name")).strip())
                        ce.set("data_type", str(_c.get("data_type") or _c.get("dataType") or "VARCHAR").upper())
                        ce.set("input_type", str(_c.get("input_type") or _c.get("inputType") or "text"))
        acts = data.get("actions") or []
        if acts:
            ca_el = ET.SubElement(fml, "custom_actions")
            for a in acts:
                nm = (a.get("name") or "").strip()
                ep = (a.get("endpoint") or "").strip()
                if not nm or not ep or not ep.startswith("/"):
                    continue
                a_el = ET.SubElement(ca_el, "action")
                a_el.set("name", nm)
                a_el.set("label", (a.get("label") or nm).strip())
                a_el.set("endpoint", ep)
                a_el.set("icon", (a.get("icon") or "fa-bolt").strip() or "fa-bolt")
                a_el.set("badge_color", (a.get("badge_color") or a.get("badgeColor") or "#4f46e5").strip() or "#4f46e5")
                rn = (a.get("render") or "").strip()
                if rn and rn.endswith(".html") and ".." not in rn and not rn.startswith("/") and ":" not in rn:
                    a_el.set("render", rn)
                lv = (a.get("level") or "record").strip().lower()
                a_el.set("level", lv if lv in ("record", "form") else "record")
                rp = (a.get("replace") or "new").strip().lower()
                a_el.set("replace", rp if rp in ("new", "add", "edit", "delete", "save") else "new")
        raw = ET.tostring(fml, encoding="utf-8")
        pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        pretty = "\n".join([l for l in pretty.split("\n") if l.strip()])
        target.write_text(pretty, encoding="utf-8")
        return JsonResponse({"ok": True, "file": fname, "path": str(target), "table_en": table_en,
                             "table_ar": table_ar, "model_type": model_type, "schema": schema,
                             "fields": len(fields), "primary_keys": pks})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_models_migrate(request, app_name):
    """POST /api/apps/<app>/models/migrate/ — ترحيل موديل .fmlk إلى جدول DB.

    Body: {file} → يبني DDL من الحقول وينفذه على اتصال النموذج (PG)، ثم يضيف
    الأعمدة الناقصة (ADD COLUMN IF NOT EXISTS) — CREATE وحده لا يكفي للجداول القائمة.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        fname = (data.get("file") or "").strip()
        if not fname:
            return JsonResponse({"error": "file required"}, status=400)
        from fmlk_engine.compiler import FMLKFormCompiler
        from fmlk_engine.engine import FMLKFormEngine
        path = _find_fml_path(fname, app_name)
        if not path or not path.exists():
            return JsonResponse({"error": "model file not found"}, status=404)
        comp = FMLKFormCompiler(path=path)
        eng = FMLKFormEngine(comp, None)
        meta = comp.fml_metadata()
        sch = meta.get("schema") or "public"
        tbl = meta.get("table") or meta.get("name")
        conn_name = (meta.get("connection") or "urs_local").strip()
        # اتصال النموذج نفسه (PG) بدل المضمّن — يبقى القديم احتياطاً
        _pg = dict(host="172.16.10.101", dbname="urs", user="postgres", password="postgres", port=5432)
        try:
            from .models import Connection
            _dj = Connection.objects.filter(name=conn_name).first()
            if _dj is None and conn_name.isdigit():
                _dj = Connection.objects.filter(id=int(conn_name)).first()
            if _dj is not None and str(getattr(_dj, "engine", "") or "").lower() == "postgres":
                _pg = dict(host=getattr(_dj, "host", "") or _pg["host"],
                           dbname=getattr(_dj, "instance", "") or _pg["dbname"],
                           user=getattr(_dj, "user", "") or _pg["user"],
                           password=getattr(_dj, "password", "") or "",
                           port=int(getattr(_dj, "port", 0) or 0) or _pg["port"])
        except Exception:
            pass
        import psycopg2
        conn = psycopg2.connect(dbname=_pg["dbname"], user=_pg["user"], password=_pg["password"],
                                host=_pg["host"], port=_pg["port"])
        conn.autocommit = True
        cur = conn.cursor()
        try:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                        (sch, tbl))
            have = {r[0].lower() for r in cur.fetchall()}
        except Exception:
            have = set()
        # إعادة تسمية برمجية قبل الترحيل: {old: new} → اسم الحقل + مراجع [old]
        # في الصيغ + مفاتيح التفاصيل، ثم يُحفظ الملف ويُعاد تحميله.
        renames = data.get("renames") or {}
        renamed = {}
        if isinstance(renames, dict) and renames:
            import xml.etree.ElementTree as ET, xml.dom.minidom, re as _re_rn
            tree = ET.parse(str(path))
            root = tree.getroot()
            _fels = [el for el in root.iter("field") if (el.get("name") or "").strip()]
            _cur = { (el.get("name") or ""): el for el in _fels }
            _low = { (el.get("name") or "").strip().lower(): (el.get("name") or "") for el in _fels }
            for _o, _n in renames.items():
                o, n = str(_o or "").strip(), str(_n or "").strip()
                if not o or not n or o == n:
                    continue
                if o not in _cur and o.lower() not in _low:
                    return JsonResponse({"error": f"الحقل '{o}' غير موجود في النموذج"}, status=400)
                if not _re_rn.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n):
                    return JsonResponse({"error": f"الاسم البرمجي '{n}' غير صالح (لاتيني + _ فقط)"}, status=400)
                if n.lower() in _low and _low[n.lower()] != (o if o in _cur else _low[o.lower()]):
                    return JsonResponse({"error": f"الاسم '{n}' مستخدم لحقل آخر"}, status=400)
                if n.lower() in have:
                    return JsonResponse({"error": f"العمود '{n}' موجود أصلاً في الجدول"}, status=400)
                real_old = o if o in _cur else _low[o.lower()]
                _cur[real_old].set("name", n)
                # مراجع الصيغ [old] في كل الحقول
                for el in _fels:
                    for _ak in ("formula", "calc_expr"):
                        _fv = el.get(_ak)
                        if _fv and f"[{real_old}]" in _fv:
                            el.set(_ak, _fv.replace(f"[{real_old}]", f"[{n}]"))
                # مفاتيح التفاصيل
                for d in root.iter("detail"):
                    if (d.get("master") or "") == real_old:
                        d.set("master", n)
                    if (d.get("detail") or "") == real_old:
                        d.set("detail", n)
                    for c in d.iter("column"):
                        if (c.get("name") or "") == real_old:
                            c.set("name", n)
                renamed[real_old] = n
                del _cur[real_old]
                _low = { (el.get("name") or "").strip().lower(): (el.get("name") or "") for el in _fels }
            raw = ET.tostring(root, encoding="utf-8")
            pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
            path.write_text("\n".join([l for l in pretty.split("\n") if l.strip()]), encoding="utf-8")
            comp = FMLKFormCompiler(path=path)
            eng = FMLKFormEngine(comp, None)
            meta = comp.fml_metadata()
        ddl = eng.build_create_table_ddl()
        cur.execute(ddl)
        # أعمدة ناقصة في جدول قائم → إضافة (IF NOT EXISTS آمنة للتكرار)
        added = []
        for f in (comp.fields() or []):
            nm = (getattr(f, "name", "") or "").strip()
            if not nm or nm.lower() in have:
                continue
            if getattr(f, "display_only", False):
                continue  # عرض فقط — بلا عمود
            dt = str(getattr(f, "data_type", "") or "VARCHAR").upper().split("(")[0].strip()
            pg_t = eng.DATA_TYPE_MAP.get(dt, "TEXT")
            try:
                cur.execute(f'ALTER TABLE "{sch}"."{tbl}" ADD COLUMN IF NOT EXISTS "{nm}" {pg_t}')
                added.append({"name": nm, "type": pg_t})
            except Exception:
                pass
        # تحقق من الأعمدة الفعلية
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", (sch, tbl))
        cols = [{"name": r[0], "db_type": r[1]} for r in cur.fetchall()]
        conn.close()
        return JsonResponse({"ok": True, "file": path.name, "schema": sch, "table": tbl,
                             "ddl": ddl, "columns": cols, "added": added,
                             "renamed": renamed,
                             "primary_keys": eng.primary_key_fields()})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_models_migrate_preview(request, app_name):
    """POST /api/apps/<app>/models/migrate/preview/ — معاينة الترحيل بلا تنفيذ.

    Body: {file} → {table, schema, connection, db_columns, new_inputs:[{name,alias,type}],
    ddl_add:[statements], form_fields}.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        fname = (data.get("file") or "").strip()
        if not fname:
            return JsonResponse({"error": "file required"}, status=400)
        from fmlk_engine.compiler import FMLKFormCompiler
        from fmlk_engine.engine import FMLKFormEngine
        path = _find_fml_path(fname, app_name)
        if not path or not path.exists():
            return JsonResponse({"error": "model file not found"}, status=404)
        comp = FMLKFormCompiler(path=path)
        eng = FMLKFormEngine(comp, None)
        meta = comp.fml_metadata()
        sch = meta.get("schema") or "public"
        tbl = meta.get("table") or meta.get("name")
        conn_name = (meta.get("connection") or "urs_local").strip()
        fields = [{"name": (getattr(f, "name", "") or ""),
                   "alias": (getattr(f, "alias", "") or getattr(f, "name", "")),
                   "type": str(getattr(f, "data_type", "") or "VARCHAR").upper()}
                  for f in (comp.fields() or [])
                  if (getattr(f, "name", "") or "").strip()
                  and not getattr(f, "display_only", False)]
        db_cols = []
        note = ""
        try:
            from .models import Connection
            _dj = Connection.objects.filter(name=conn_name).first()
            if _dj is None and conn_name.isdigit():
                _dj = Connection.objects.filter(id=int(conn_name)).first()
            if _dj is not None and str(getattr(_dj, "engine", "") or "").lower() == "postgres":
                for _r in _table_columns_obj(_dj, sch, tbl):
                    db_cols.append({"name": _r.get("name"), "db_type": _r.get("db_type")})
            else:
                note = "الاتصال ليس Postgres — المقارنة غير متاحة"
        except Exception as e:
            note = f"تعذر قراءة أعمدة الجدول: {str(e)[:120]}"
        have = {c["name"].lower() for c in db_cols if c.get("name")}
        new_inputs, ddl_add = [], []
        for f in fields:
            if f["name"].lower() in have:
                continue
            pg_t = eng.DATA_TYPE_MAP.get(f["type"].split("(")[0].strip(), "TEXT")
            new_inputs.append({**f, "db_type": pg_t})
            ddl_add.append(f'ALTER TABLE "{sch}"."{tbl}" ADD COLUMN IF NOT EXISTS "{f["name"]}" {pg_t};')
        return JsonResponse({"ok": True, "file": path.name, "table": tbl, "schema": sch,
                             "connection": conn_name, "db_columns": db_cols, "new_inputs": new_inputs,
                             "ddl_add": ddl_add, "form_fields": len(fields), "note": note},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _convert_cml_to_fmlk(cml_path, out_path):
    """تحويل CML (controls) إلى FMLK بالبنية الجديدة: model_type + PK + nullable/editable + formula فارغة.

    - أول حقل يطابق قاعدة unique يصبح primary_key.
    - nullable = not required, editable = True.
    - الصيغة تُترك فارغة لليدوي (default_mode=empty).
    """
    from cml_engine.compiler import CMLCompiler
    comp = CMLCompiler(path=cml_path)
    md = comp.cml_metadata()
    ctrls = comp.controls()
    rules = comp.rules()
    unique_targets = {r.get("target") for r in [x.to_dict() for x in rules] if r.get("type") == "unique" and r.get("target")}
    import xml.etree.ElementTree as ET, xml.dom.minidom
    fml = ET.Element("fml")
    m = ET.SubElement(fml, "fml_metadata")
    m.set("name", md.get("name") or cml_path.stem)
    m.set("displayName", md.get("displayName") or md.get("name") or cml_path.stem)
    m.set("table", md.get("table") or md.get("name") or cml_path.stem)
    m.set("model_type", "form")
    m.set("schema", "public")
    m.set("connection", "urs_local")
    m.set("category", md.get("category") or "النظام")
    if md.get("description"):
        m.set("description", md.get("description"))
    if md.get("icon"):
        m.set("icon", md.get("icon"))
    tabs_el = ET.SubElement(fml, "tabs")
    tab = ET.SubElement(tabs_el, "tab")
    tab.set("id", "main")
    tab.set("name", md.get("displayName") or cml_path.stem)
    tab.set("alias", md.get("displayName") or cml_path.stem)
    tab.set("sort_order", "1")
    fields_el = ET.SubElement(fml, "fields")
    for i, c in enumerate([x.to_dict() for x in ctrls], start=1):
        el = ET.SubElement(fields_el, "field")
        el.set("id", str(c.get("id") or i))
        el.set("name", c.get("name"))
        el.set("alias", c.get("alias") or c.get("name"))
        el.set("dataType", (c.get("dataType") or c.get("data_type") or "VARCHAR"))
        it = (c.get("inputType") or c.get("input_type") or "text")
        el.set("inputType", it)
        req = bool(c.get("required"))
        el.set("required", "true" if req else "false")
        el.set("nullable", "false" if req else "true")
        el.set("editable", "true")
        el.set("primary_key", "true" if c.get("name") in unique_targets and len(unique_targets) == 1 else "false")
        el.set("tab", "main")
        if c.get("category"):
            el.set("category", c.get("category"))
        if c.get("default"):
            el.set("default", str(c.get("default")))
        if c.get("placeholder"):
            el.set("placeholder", str(c.get("placeholder")))
        if c.get("refTable") or c.get("ref_table"):
            el.set("refTable", str(c.get("refTable") or c.get("ref_table")))
        if c.get("refFk") or c.get("ref_fk"):
            el.set("refFk", str(c.get("refFk") or c.get("ref_fk")))
        if c.get("refDisplay") or c.get("ref_display"):
            el.set("refDisplay", str(c.get("refDisplay") or c.get("ref_display")))
        opts = c.get("options") or []
        if opts:
            o_el = ET.SubElement(el, "options")
            for o in opts:
                oo = ET.SubElement(o_el, "option")
                oo.text = str(o)
        vr = c.get("validation") or {}
        if isinstance(vr, dict) and vr:
            v_el = ET.SubElement(el, "validation")
            for vk, vv in vr.items():
                v_el.set(vk, str(vv))
    raw = ET.tostring(fml, encoding="utf-8")
    pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    pretty = "\n".join([l for l in pretty.split("\n") if l.strip()])
    out_path.write_text(pretty, encoding="utf-8")
    return {"controls": len(ctrls), "unique_targets": sorted(unique_targets)}


@csrf_exempt
def api_models_convert_cml(request, app_name):
    """POST /api/apps/<app>/models/convert-cml/ — تحويل ملف .cml إلى .fmlk بالبنية الجديدة.

    Body: {cml_file, out_file?} — out_file افتراضياً <stem>.fmlk بنفس المجلد.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        cml_file = (data.get("cml_file") or data.get("file") or "").strip()
        if not cml_file:
            return JsonResponse({"error": "cml_file required"}, status=400)
        if not cml_file.endswith(".cml"):
            cml_file += ".cml"
        if "/" in cml_file or "\\" in cml_file or ".." in cml_file:
            return JsonResponse({"error": "invalid file name"}, status=400)
        app_dir = _resolve_app_dir(app_name)
        src = app_dir / cml_file
        if not src.exists():
            return JsonResponse({"error": "cml file not found"}, status=404)
        out_name = (data.get("out_file") or src.stem).strip()
        if not out_name.endswith(".fmlk"):
            out_name += ".fmlk"
        if "/" in out_name or "\\" in out_name or ".." in out_name:
            return JsonResponse({"error": "invalid out_file"}, status=400)
        dst = app_dir / out_name
        if dst.exists() and not data.get("overwrite"):
            return JsonResponse({"error": "fmlk already exists (أرسل overwrite=true)"}, status=400)
        info = _convert_cml_to_fmlk(src, dst)
        from fmlk_engine.compiler import FMLKFormCompiler
        comp = FMLKFormCompiler(path=dst)
        return JsonResponse({"ok": True, "src": src.name, "file": dst.name,
                             "table_en": comp.fml_metadata().get("table"),
                             "fields": len(comp.fields()),
                             "converted": info})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _table_columns_obj(obj, schema: str, table: str):
    """أعمدة جدول في أي اتصال → [{name, type, db_type}]."""
    if obj.engine == "postgres":
        conn = _pg_conn_for(obj)
        try:
            cur = conn.cursor()
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", (schema, table))
            cols = [{"name": r[0], "db_type": r[1], "type": _pg_type_to_field(r[1])} for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
        return cols
    if obj.engine == "oracle":
        return _oracle_columns(obj, schema, table)
    if obj.engine == "sqlserver":
        return _mssql_columns(obj, schema, table)
    if obj.engine == "mysql":
        return _mysql_columns(obj, schema, table)
    if obj.engine == "zk":
        t = (table or "").lower()
        if t in _ZK_TABLES:
            return [{"name": n, "type": ty, "db_type": ty} for n, ty in _ZK_TABLES[t]]
        return []
    raise ValueError(f"محرك غير مدعوم: {obj.engine}")


@csrf_exempt
def api_models_sync(request, app_name):
    """POST /api/apps/<app>/models/sync/ — مزامنة سجل الموديلات من جداول الاتصالات المحلية.

    لكل اتصال محلي (يشمل البعيدة): يسرد الجداول + تعريف أعمدتها ويحفظها في public.sys_models.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from .models import Connection
        only = (json.loads(request.body.decode() or "{}") or {}).get("connection")
        synced, skipped, errors = 0, 0, []
        import psycopg2
        local = psycopg2.connect(dbname="urs", user="postgres", password="postgres", host="172.16.10.101", port=5432)
        local.autocommit = True
        for obj in Connection.objects.filter(is_local=True).order_by("name"):
            if only and obj.name != only and str(obj.id) != str(only):
                continue
            try:
                tables = _list_tables_obj(obj)
            except ValueError as ve:
                skipped += 1
                errors.append(f"{obj.name}: {ve}")
                continue
            except Exception as e:
                errors.append(f"{obj.name}: {e}")
                continue
            cur = local.cursor()
            for t in tables:
                try:
                    cols = _table_columns_obj(obj, t["schema"], t["name"])
                except Exception:
                    cols = []
                definition = json.dumps({"columns": cols}, ensure_ascii=False)
                cur.execute("SELECT id FROM public.sys_models WHERE connection=%s AND schema_name=%s AND table_name=%s",
                            (obj.name, t["schema"], t["name"]))
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE public.sys_models SET table_ar=%s, fields_count=%s, definition=%s, is_active=true WHERE id=%s",
                                (t["name"], len(cols), definition, row[0]))
                else:
                    cur.execute("INSERT INTO public.sys_models (connection, schema_name, table_name, table_ar, app, file, model_type, fields_count, definition, is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true)",
                                (obj.name, t["schema"], t["name"], t["name"], "", "", "form", len(cols), definition))
                synced += 1
            cur.close()
        local.close()
        return JsonResponse({"ok": True, "synced": synced, "skipped": skipped, "errors": errors,
                             "message": f"تمت مزامنة {synced} جدولاً من الاتصالات المحلية"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
def api_fmlk_preview_insert(request):
    try:
        data = json.loads(request.body.decode() or "{}")
        fml = data.get("fml", request.GET.get("fml", "hr_form"))
        app = data.get("app", request.GET.get("app"))
        payload = data.get("data", {})
        eng = _fmlk_get_engine(fml, app)
        sql = eng.preview_insert_sql(payload)
        return JsonResponse({"sql": sql, "metadata": eng.compiler.fml_metadata()})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

@csrf_exempt
def api_fmlk_import_xlsx(request, app_name):
    """POST /api/apps/<app>/models/import-xlsx/ (multipart: file, fml, preview?, mapping?, match_column?).

    preview=1 → {headers, sample_rows(5), total_rows} بلا كتابة.
    Иначе → يستورد الصفوف: مطابقة عمود المطابقة تحدّث الموجود (upsert) وإلا ينشئ.
    mapping: {excel_header: field_name}. حد أقصى 2000 صف.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        import datetime as _dtm
        import decimal as _dec
        fml = (request.POST.get("fml") or "").strip()
        if not fml:
            return JsonResponse({"error": "fml required"}, status=400)
        up = request.FILES.get("file")
        if up is None:
            return JsonResponse({"error": "file required (xlsx)"}, status=400)
        if not str(up.name or "").lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
            return JsonResponse({"error": "ملف Excel (.xlsx) فقط"}, status=400)
        try:
            import openpyxl
        except ImportError:
            return JsonResponse({"error": "openpyxl غير مثبت على الخادم"}, status=500)

        def _norm(v):
            if v is None:
                return ""
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (_dtm.datetime,)):
                return v.isoformat(sep=" ", timespec="seconds")
            if isinstance(v, (_dtm.date, _dtm.time)):
                return v.isoformat()
            if isinstance(v, _dec.Decimal):
                return int(v) if v == int(v) else float(v)
            return v

        wb = openpyxl.load_workbook(up, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
        finally:
            try:
                wb.close()
            except Exception:
                pass
        rows = [r for r in (rows or []) if any(c is not None and str(c).strip() != "" for c in (r or []))]
        if not rows:
            return JsonResponse({"error": "الملف فارغ"}, status=400)
        headers = [str(c or "").strip() for c in (rows[0] or [])]
        if not any(headers):
            return JsonResponse({"error": "الصف الأول يجب أن يحتوي الترويسات"}, status=400)
        data_rows = rows[1:]
        if request.POST.get("preview"):
            return JsonResponse({"ok": True, "headers": headers,
                                 "sample_rows": [[_norm(c) for c in (r or [])] for r in data_rows[:5]],
                                 "total_rows": len(data_rows)},
                                json_dumps_params={"ensure_ascii": False})
        try:
            mapping = json.loads(request.POST.get("mapping") or "{}")
        except Exception:
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}
        match_column = (request.POST.get("match_column") or "").strip()
        eng = _fmlk_get_engine(fml, app_name)
        fields = {getattr(f, "name", ""): f for f in (eng.compiler.fields() or []) if getattr(f, "name", "")}
        MAX_ROWS = 2000
        truncated = len(data_rows) > MAX_ROWS
        created = updated = skipped = 0
        errors = []
        for _ri, _r in enumerate(data_rows[:MAX_ROWS], start=2):
            try:
                vals = list(_r or []) + [""] * max(0, len(headers) - len(_r or []))
                data = {}
                for _hi, _h in enumerate(headers):
                    if not _h:
                        continue
                    _fn = mapping.get(_h)
                    if not _fn or _fn not in fields:
                        continue
                    _v = _norm(vals[_hi] if _hi < len(vals) else "")
                    if _v == "" or _v is None:
                        continue
                    data[_fn] = _v
                if not data:
                    skipped += 1
                    continue
                _rid = None
                if match_column and match_column in data and str(data[match_column]).strip() != "":
                    try:
                        _lr = eng.list_records(
                            filters=[{"field": match_column, "op": "=", "value": data[match_column]}],
                            page=1, page_size=1)
                        _rows = _lr.get("rows") or []
                        if _rows:
                            _rid = _rows[0].get("__pk_id", _rows[0].get("id"))
                    except Exception:
                        _rid = None
                if _rid is not None and str(_rid).strip() != "":
                    eng.update_record({"id": _rid}, data)
                    updated += 1
                else:
                    eng.create_record(data)
                    created += 1
            except Exception as e:
                if len(errors) < 10:
                    errors.append(f"صف {_ri}: {str(e)[:120]}")
                else:
                    skipped += 1
        return JsonResponse({"ok": True, "created": created, "updated": updated, "skipped": skipped,
                             "total": min(len(data_rows), MAX_ROWS), "truncated": truncated, "errors": errors},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_fmlk_display_map(request):
    """POST /api/fmlk/display-map/ {fml, app, table, key, show, keys[]} — دفعات قيم العرض.

    يحل key→show لمدخلات العرض فقط (قراءة واحدة بدل N استعلامات).
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        import re as _re_id
        data = json.loads(request.body.decode() or "{}")
        fml = data.get("fml", "hr_form")
        app = data.get("app")
        table = str(data.get("table") or "").strip().strip('"')
        key = str(data.get("key") or "").strip().strip('"')
        show = str(data.get("show") or "").strip().strip('"') or key
        keys = data.get("keys") or []
        if not table or not key:
            return JsonResponse({"error": "table and key required"}, status=400)
        if not _re_id.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", table):
            return JsonResponse({"error": "invalid table name"}, status=400)
        for _c in (key, show):
            if not _re_id.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", _c):
                return JsonResponse({"error": "invalid column name"}, status=400)
        keys = [k for k in (keys or []) if k is not None and str(k).strip() != ""][:500]
        if not keys:
            return JsonResponse({"ok": True, "map": {}})
        parts = table.split(".")
        if len(parts) == 2:
            sch, tbl = parts
        else:
            sch, tbl = "", parts[-1]
        eng = _fmlk_get_engine(fml, app)
        db = getattr(eng, "db", None)
        if db is None or getattr(db, "conn", None) is None and hasattr(db, "connect"):
            try:
                db.connect()
            except Exception as e:
                return JsonResponse({"error": f"تعذر الاتصال: {str(e)[:150]}"}, status=400)
        if not sch:
            try:
                sch = (eng.compiler.fml_metadata().get("schema") or "").strip() or "public"
            except Exception:
                sch = "public"
        _q2 = lambda s: '"' + str(s).replace('"', '""') + '"'
        binds = ", ".join(f":k{i}" for i in range(len(keys)))
        sql = f"SELECT DISTINCT {_q2(key)}, {_q2(show)} FROM {_q2(sch)}.{_q2(tbl)} WHERE {_q2(key)} IN ({binds})"
        params = {f"k{i}": v for i, v in enumerate(keys)}
        try:
            cur = db._exec(sql, params)
            rows = cur.fetchall() if hasattr(cur, "fetchall") else []
            try:
                cur.close()
            except Exception:
                pass
        except Exception as e:
            return JsonResponse({"error": str(e)[:300]}, status=400)
        out = {}
        for r in rows or []:
            try:
                kv, sv = (list(r.values())[0], list(r.values())[1]) if isinstance(r, dict) else (r[0], r[1])
            except Exception:
                continue
            if kv is None:
                continue
            out[str(kv)] = "" if sv is None else str(sv)
        return JsonResponse({"ok": True, "map": out}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_fmlk_create(request):
    try:
        data = json.loads(request.body.decode() or "{}")
        fml = data.get("fml", "hr_form")
        app = data.get("app")
        payload = data.get("data", {})
        eng = _fmlk_get_engine(fml, app)
        res = eng.create_record(payload)
        return JsonResponse(res)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
def api_fmlk_update(request):
    try:
        data = json.loads(request.body.decode() or "{}")
        fml = data.get("fml", "hr_form")
        app = data.get("app")
        rid = data.get("id")
        payload = data.get("data", {})
        eng = _fmlk_get_engine(fml, app)
        pk = {"id": rid}
        res = eng.update_record(pk, payload)
        return JsonResponse(res)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
def api_fmlk_delete(request):
    try:
        data = json.loads(request.body.decode() or "{}")
        fml = data.get("fml", "hr_form")
        app = data.get("app")
        rid = data.get("id")
        eng = _fmlk_get_engine(fml, app)
        pk = {"id": rid}
        res = eng.delete_record(pk)
        return JsonResponse(res)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def api_fmlk_records(request):
    fml = request.GET.get("fml", "hr_form")
    app = request.GET.get("app")
    page = int(request.GET.get("page", "1") or 1)
    pageSize = int(request.GET.get("pageSize", "50") or 50)
    try:
        eng = _fmlk_get_engine(fml, app)
        res = eng.list_records(page=page, page_size=pageSize)
        return JsonResponse(res)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

def _fmlk_find_detail(comp, ident):
    """Branch table def by index or table name → (detail_dict, schema)."""
    dets = [d.to_dict() for d in comp.details()]
    det = None
    try:
        det = dets[int(ident)]
    except (TypeError, ValueError):
        for d in dets:
            if d.get("table") == ident or d.get("alias") == ident:
                det = d
                break
    if not det:
        return None, ""
    return det, ""


@csrf_exempt
def api_fmlk_action_test_connection(request):
    """POST /api/fmlk/action/test-connection {app, fml, action, record} — test one grid row.

    Custom action for connections.fmlk: resolves field values via the FMLK's own
    alias map (robust to Arabic aliases), tests with _test_connection_obj, and
    best-effort stamps the Django row matched by name. Never writes row data.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from types import SimpleNamespace
        data = json.loads(request.body.decode() or "{}")
        rec = data.get("record") or {}
        if not isinstance(rec, dict) or not rec:
            return JsonResponse({"error": "record required"}, status=400)
        fml, app = data.get("fml", ""), data.get("app")
        alias_of = {}
        try:
            from fmlk_engine.compiler import FMLKFormCompiler
            path = _find_fml_path(fml, app)
            if path and path.exists():
                for f in FMLKFormCompiler(path=path).fields():
                    alias_of[f.name] = (f.alias or f.name).lower()
        except Exception:
            pass

        def val(*names):
            for n in names:
                a = (alias_of.get(n, n) or "").lower()
                if a in rec and rec[a] not in (None, ""):
                    return rec[a]
                if n in rec and rec[n] not in (None, ""):
                    return rec[n]
            return ""

        nm = str(val("name") or "")
        if not nm:
            return JsonResponse({"error": "تعذر تحديد الاتصال من السجل"}, status=400)
        # كلمة المرور الحقيقية أولاً: صريحة بالطلب > app.conf (للاتصال الافتراضي فقط)
        # > صف Django المطابق بالاسم > قيمة صف الشبكة المعروض
        from .models import Connection
        from config.dbconf import read_appconf, dbpass_resolve
        dj = Connection.objects.filter(name=nm).first()
        ac = read_appconf()
        pw_req = (data.get("password") or "").strip()
        pw_appconf = dbpass_resolve(ac.get("DB_PASS") or "") if nm == "urs_local" else ""
        pw_stored = (dj.password or "") if dj is not None else ""
        pw_row = val("password")
        resolved_pw, pw_source = "", "row"
        for _cand, _src in ((pw_req, "request"), (pw_appconf, "appconf"),
                            (pw_stored, "stored"), (pw_row, "row")):
            if _cand:
                resolved_pw, pw_source = _cand, _src
                break

        try:
            port = int(val("port") or 5432)
        except (TypeError, ValueError):
            port = 5432
        obj = SimpleNamespace(
            name=nm,
            engine=str(val("engine") or "postgres").lower() or "postgres",
            host=str(val("host") or ""),
            port=port,
            user=str(val("user") or ""),
            password=resolved_pw,
            instance=str(val("instance") or ""),
            instance_name=str(val("instance_name") or ""),
        )
        ok, payload, _status = _test_connection_obj(obj)
        _row_err = ""
        try:
            _row_err = (payload or {}).get("error") if isinstance(payload, dict) else ""
        except Exception:
            _row_err = ""
        try:
            from django.utils import timezone
            if dj is not None:
                dj.last_check_ok = bool(ok)
                dj.last_check_at = timezone.now()
                _uf = ["last_check_ok", "last_check_at"]
                try:
                    if any(f.name == "last_check_error" for f in dj._meta.get_fields()):
                        dj.last_check_error = ("" if ok else str(_row_err or ""))[:500]
                        _uf.append("last_check_error")
                except Exception:
                    pass
                dj.save(update_fields=_uf)
        except Exception:
            pass
        if ok:
            out = {"ok": True, "password_source": pw_source,
                   "message": f"الاتصال يعمل ✓ ({obj.name})" + (f" [بكلمة {pw_source}]" if pw_source != "row" else "")}
            if isinstance(payload, dict):
                for k in ("driver", "note", "user", "engine", "server"):
                    if payload.get(k) not in (None, ""):
                        out[k] = payload.get(k)
            return JsonResponse(out, json_dumps_params={"ensure_ascii": False})
        err = (payload or {}).get("error") if isinstance(payload, dict) else ""
        return JsonResponse({"ok": False, "error": err or "فشل الاتصال"},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_fmlk_branch(request):
    """GET /api/fmlk/branch?fml=&app=&detail=<idx|table>&key= → branch rows for master key."""
    fml, app = request.GET.get("fml", ""), request.GET.get("app")
    ident, key = request.GET.get("detail", "0"), request.GET.get("key", "")
    try:
        from fmlk_engine.compiler import FMLKFormCompiler
        path = _find_fml_path(fml, app)
        if not path or not path.exists():
            return JsonResponse({"error": "form not found"}, status=404)
        comp = FMLKFormCompiler(path=path)
        det, _ = _fmlk_find_detail(comp, ident)
        if not det:
            return JsonResponse({"error": "detail not found"}, status=404)
        if key is None or str(key) == "":
            return JsonResponse({"detail": det, "columns": det.get("columns") or [], "rows": []})
        import re as _re
        tbl = det["table"]
        sch = comp.fml_metadata().get("schema") or "public"
        if "." in tbl:
            sch, tbl = tbl.split(".", 1)
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tbl):
            return JsonResponse({"error": "invalid table"}, status=400)
        fk = det["detail"]
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", fk):
            return JsonResponse({"error": "invalid link column"}, status=400)
        eng = _fmlk_get_engine(fml, app)
        db = eng.db
        if not getattr(db, "conn", None):
            db.connect()
        cols = [c["name"] for c in (det.get("columns") or []) if _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c.get("name", ""))]
        sel = ", ".join(f'"{c}"' for c in cols) if cols else "*"
        cur = db._exec(f'SELECT {sel} FROM "{sch}"."{tbl}" WHERE "{fk}" = :key', {"key": key})
        names = [d[0] for d in (cur.description or [])]
        rows = [dict(zip(names, r)) for r in (cur.fetchall() or [])]
        try:
            cur.close()
        except Exception:
            pass
        return JsonResponse({"detail": det, "columns": det.get("columns") or [{"name": n, "alias": n} for n in names], "rows": rows})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_fmlk_branch_save(request):
    """POST /api/fmlk/branch/save {fml,app,detail,key,rows[]} — replace branch rows for master key."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        fml, app = data.get("fml", ""), data.get("app")
        ident, key = data.get("detail", 0), data.get("key", "")
        rows = data.get("rows") or []
        from fmlk_engine.compiler import FMLKFormCompiler
        path = _find_fml_path(fml, app)
        if not path or not path.exists():
            return JsonResponse({"error": "form not found"}, status=404)
        comp = FMLKFormCompiler(path=path)
        det, _ = _fmlk_find_detail(comp, ident)
        if not det:
            return JsonResponse({"error": "detail not found"}, status=404)
        if key is None or str(key) == "":
            return JsonResponse({"error": "master key required"}, status=400)
        import re as _re
        tbl = det["table"]
        sch = comp.fml_metadata().get("schema") or "public"
        if "." in tbl:
            sch, tbl = tbl.split(".", 1)
        fk = det["detail"]
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tbl) or not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", fk):
            return JsonResponse({"error": "invalid table/link"}, status=400)
        eng = _fmlk_get_engine(fml, app)
        db = eng.db
        if not getattr(db, "conn", None):
            db.connect()
        n = 0
        db._exec(f'DELETE FROM "{sch}"."{tbl}" WHERE "{fk}" = :key', {"key": key}, commit=True)
        for r in rows:
            if not isinstance(r, dict):
                continue
            cols, binds, params = [fk], [":key"], {"key": key}
            for k, v in r.items():
                if k == fk or not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)):
                    continue
                if v is None or (isinstance(v, str) and v.strip() == ""):
                    continue
                cols.append(f'"{k}"')
                binds.append(f":p_{k}")
                params[f"p_{k}"] = v
            if len(cols) > 1:
                db._exec(f'INSERT INTO "{sch}"."{tbl}" ({", ".join(cols)}) VALUES ({", ".join(binds)})', params, commit=True)
                n += 1
        return JsonResponse({"ok": True, "saved": n})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_fmlk_record(request):
    fml = request.GET.get("fml", "hr_form")
    app = request.GET.get("app")
    rid = request.GET.get("id", "1")
    try:
        eng = _fmlk_get_engine(fml, app)
        row = eng.get_record({"id": rid})
        return JsonResponse({"row": row})
    except Exception as e:
        return JsonResponse({"error": f"id={str(rid)[:60]}: {str(e)[:240]}"}, status=400)

def _rml_is_live_sql(dj):
    """RML-only: connection can be queried live (pg/oracle always; sqlserver
    when flagged queryable — direct mode, no staging). FML paths intentionally
    keep the stricter _conn_is_sql_queryable."""
    try:
        if _conn_is_sql_queryable(dj):
            return True
        try:
            _fl = getattr(dj, "is_queryable", True)
            _ok = False if _fl is False or str(_fl).strip().lower() in ("0", "false", "no", "none") else True
        except Exception:
            _ok = True
        return bool(_ok) and str(getattr(dj, "engine", "") or "").lower() == "sqlserver"
    except Exception:
        return False


def _rml_build_db_engine(dj, connect=True):
    """RML-only engine builder: pg/oracle via _build_db_engine, sqlserver direct."""
    if str(getattr(dj, "engine", "") or "").lower() == "sqlserver":
        from rml_python.engine import SqlServerDirect
        db = SqlServerDirect(dj)
        if connect:
            db.connect()
        return db
    return _build_db_engine(dj, connect=connect)


# ── RML API proxied into Django ──────────────────────────────────────────────
def _build_db_engine(dj, connect=True):
    """Build a live DB engine (postgres/oracle) from a Connection row."""
    eng = (getattr(dj, "engine", "") or "postgres").lower()
    eng = (getattr(dj, "engine", "") or "postgres").lower()
    if eng not in ("postgres", "oracle"):
        try:
            _nm = getattr(dj, "name", "") or ""
        except Exception:
            _nm = ""
        raise ValueError(
            f"الاتصال '{_nm}' من نوع '{eng}' غير قابل للاستعلام SQL — "
            f"علّمه كغير قابل للاستعلام (قابل للاستعلام = لا) ليُجلب عبر API الخاص به.")
    host = getattr(dj, "host", "") or "172.16.10.101"
    try:
        port = int(getattr(dj, "port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    user = getattr(dj, "user", "") or "postgres"
    pwd = getattr(dj, "password", "") or ""
    inst = getattr(dj, "instance", "") or ""
    if eng == "oracle":
        from rml_python.oracle_engine import OracleEngine
        db = OracleEngine(dsn=f"{host}:{port or 1521}/{inst}" if inst else f"{host}:{port or 1521}",
                          user=user, password=pwd)
        if connect:
            try:
                db.conn = _oracle_connect_obj(type("O", (), {"host": host, "port": port or 1521,
                                                             "instance": inst, "user": user,
                                                             "password": pwd})())
            except Exception:
                db.connect()
        return db
    db = PostgresEngine(host=host, dbname=inst or "urs", user=user, password=pwd, port=port or 5432)
    if connect:
        db.connect()
    return db


def _conn_is_sql_queryable(dj):
    """True when a Connection row can be queried via SQL.

    Honors the explicit Connection.is_queryable flag (قابل للاستعلام):
    SQL-family engines are queryable by default; anything else (e.g. zk
    fingerprint devices) is an API source even if flagged True, and a
    SQL engine flagged False is treated as API (then fails loudly with
    the supported-API list when no API exists for it).
    """
    try:
        _fl = getattr(dj, "is_queryable", True)
        flag = False if _fl is False or str(_fl).strip().lower() in ("0", "false", "no", "none") else True
    except Exception:
        flag = True
    if not flag:
        return False
    return (getattr(dj, "engine", "") or "").lower() in ("postgres", "oracle")


def _rml_get_pipeline(rml_name, app_name=None):
    from rml_python.pipeline import OdexPipeline
    path = _find_rml_path(rml_name, app_name)
    if path and path.exists():
        pipe = OdexPipeline(rml_path=path)
    else:
        pipe = OdexPipeline(rml_path=path) if path and path.exists() else OdexPipeline(rml_path=BASE_DIR / "rml_python" / "examples" / "emp_report.rml")
    # Inject DB engine — prefer connection chosen in the report (or fallback to urs)
    conn_id, conn_host, conn_db, conn_port, conn_user, conn_pwd, conn_engine = (
        None, "172.16.10.101", "urs", 5432, "postgres", "postgres", "postgres"
    )
    try:
        meta = pipe.rpt_metadata() if hasattr(pipe, "rpt_metadata") else pipe.compiler.rpt_metadata()
        conn_id = meta.get("connection") or meta.get("connectionId") or meta.get("conn_id")
        # conn_id may be a name (e.g. "ORCL_PROD") — resolve to numeric ID from connections list
        if conn_id and not str(conn_id).isdigit():
            try:
                rml_conns = pipe.compiler.connections()
                for rc in rml_conns:
                    rc_id = getattr(rc, "connection_id", None) or rc.to_dict().get("connection_id")
                    if rc_id and str(rc_id).isdigit():
                        conn_id = str(rc_id)
                        break
                else:
                    conn_id = None
            except Exception:
                conn_id = None
    except Exception:
        pass
    try:
        from urs.models import Connection as DjangoConn
        # Linked connections (report-level + every field's connection)
        linked_ids = []
        try:
            for rc in pipe.compiler.connections():
                _cid = getattr(rc, "connection_id", None) or rc.to_dict().get("connection_id")
                if _cid and str(_cid).isdigit() and str(_cid) not in linked_ids:
                    linked_ids.append(str(_cid))
        except Exception:
            pass
        try:
            for _ff in pipe.compiler.fields():
                _cid = getattr(_ff, "connection_id", None)
                if _cid and str(_cid).isdigit() and str(_cid) not in linked_ids:
                    linked_ids.append(str(_cid))
        except Exception:
            pass
        conns = {}
        for _cid in linked_ids:
            try:
                _dj = DjangoConn.objects.filter(id=int(_cid)).first()
            except Exception:
                _dj = None
            if _dj is not None and _rml_is_live_sql(_dj):
                conns[str(_cid)] = _dj
        if not conns and conn_id:
            try:
                _dj0 = DjangoConn.objects.filter(id=int(conn_id)).first()
            except Exception:
                _dj0 = None
            if _dj0 is not None and _rml_is_live_sql(_dj0):
                conns[str(conn_id)] = _dj0
        # Primary = base table's connection (else first linked, else urs default)
        primary_id = None
        try:
            for _ff in pipe.compiler.fields():
                _ts = getattr(_ff, "table_source", None)
                if _ts and "." not in str(_ts):
                    _cid = getattr(_ff, "connection_id", None)
                    if _cid and str(_cid).isdigit() and str(_cid) in conns:
                        primary_id = str(_cid)
                        break
        except Exception:
            pass
        if primary_id is None and conns:
            primary_id = next(iter(conns))
        if primary_id is None:
            # No linked connection: prefer the effective default PG (staging target
            # for API/sqlserver tables), else legacy urs default.
            db = None
            try:
                _effc = _wizard_effective_conn()
                if _effc is not None and (_effc.engine or "postgres") == "postgres" \
                        and (_effc.host or ""):
                    db = PostgresEngine(host=_effc.host, dbname=(_effc.instance or "urs"),
                                        user=_effc.user, password=_effc.password or "",
                                        port=int(_effc.port or 5432))
                    db.connect()
            except Exception:
                db = None
            if db is None:
                db = PostgresEngine(host=conn_host, dbname=conn_db, user=conn_user,
                                    password=conn_pwd, port=conn_port)
                db.connect()
            pipe.oracle = db
            from rml_python.engine import RMLReportEngine as _RML
            pipe.rml_engine = _RML(pipe.compiler, db)
            return pipe
        dj = conns[primary_id]
        conn_host = dj.host or conn_host
        conn_db = dj.instance or conn_db
        conn_port = int(dj.port or conn_port)
        conn_user = dj.user or conn_user
        conn_pwd = dj.password or conn_pwd
        conn_engine = (dj.engine or "postgres").lower()
        # Use schema from Connection model if set
        if dj.schema and dj.schema.strip():
            try:
                meta_obj = pipe.compiler._metadata
                if meta_obj is not None:
                    meta_obj.schema = dj.schema.strip()
            except Exception:
                pass
        db = _rml_build_db_engine(dj)
        pipe.oracle = db
        # Detect actual schema (for Postgres: query current_schema; for Oracle: use connection schema)
        actual_schema = None
        if conn_engine == "sqlserver":
            actual_schema = (dj.schema or "").strip() or "dbo"
        elif conn_engine != "oracle":
            try:
                cur = db._exec("SELECT current_schema()")
                actual_schema = cur.fetchone()[0]
                try: cur.close()
                except Exception: pass
            except Exception:
                actual_schema = "public"
        try:
            meta_obj = pipe.compiler._metadata
            if meta_obj is not None:
                cur_schema = (meta_obj.schema or "").strip()
                if conn_engine == "oracle":
                    # For Oracle, schema from Connection model is authoritative
                    if not cur_schema:
                        meta_obj.schema = conn_db.upper() if conn_db else None
                else:
                    # For Postgres, verify schema exists in database
                    if cur_schema:
                        try:
                            cur = db._exec(
                                "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s",
                                [cur_schema],
                            )
                            exists = cur.fetchone() is not None
                            try: cur.close()
                            except Exception: pass
                            if not exists:
                                meta_obj.schema = actual_schema
                        except Exception:
                            meta_obj.schema = actual_schema
                    else:
                        meta_obj.schema = actual_schema
        except Exception:
            pass
        from rml_python.engine import RMLReportEngine
        extras = {}
        for _cid, _dj in conns.items():
            if _cid == primary_id:
                continue
            try:
                extras[str(_cid)] = _rml_build_db_engine(_dj)
            except Exception:
                pass
        pipe.rml_engine = RMLReportEngine(pipe.compiler, db, databases=extras)
        pipe.rml_engine.primary_conn = primary_id
        # Get final schema after overrides
        try:
            final_schema = (pipe.compiler._metadata.schema or actual_schema or "").strip()
        except Exception:
            final_schema = actual_schema or ""
        # If engine has no default table, detect first available table from the schema
        if not pipe.rml_engine._default_table and final_schema and conn_engine == "postgres":
            try:
                cur = db._exec(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema=%s AND table_type IN ('BASE TABLE','VIEW') "
                    "ORDER BY table_name LIMIT 1",
                    [final_schema],
                )
                row = cur.fetchone()
                if row:
                    pipe.rml_engine._default_table = row[0]
                try: cur.close()
                except Exception: pass
            except Exception:
                pass
    except Exception as e:
        # keep original oracle fallback (will use mock in execute)
        pass
    return pipe

def api_rml_metadata(request):
    rml = request.GET.get("rml", "emp_report")
    app = request.GET.get("app")
    try:
        from rml_python.compiler import RMLReportCompiler
        path = _find_rml_path(rml, app)
        comp = RMLReportCompiler(path=path) if path and path.exists() else RMLReportCompiler(xml_text='<rml><rpt_metadata name="emp_report" displayName="تقرير"/><columns><column name="id" alias="المعرف"/></columns></rml>')
        _det = comp.detail()
        _doct = comp.doc_template() if hasattr(comp, "doc_template") else ""
        _gw = comp.general_where() if hasattr(comp, "general_where") else ""
        return JsonResponse({"metadata": comp.rpt_metadata(), "columns": [c.to_dict() for c in comp.columns()], "fields": [f.to_dict() for f in comp.fields()], "connections": [c.to_dict() for c in comp.connections()], "charts": [c.to_dict() for c in comp.charts()], "rules": [r.to_dict() for r in comp.rules()], "groups": [g.to_dict() for g in comp.groups()], "detail": (_det.to_dict() if _det else None), "links": comp.links(), "table_opts": comp.table_opts(), "doc_template": _doct, "docTemplate": _doct, "general_where": _gw, "generalWhere": _gw})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

@csrf_exempt
@csrf_exempt
def api_rml_parse_sql(request):
    """POST /api/rml/parse-sql/ — import a SELECT into wizard structures.

    Body: {sql*, connection_id?, schema?} → {ok, tables, fields, columns, links,
    general_where, distinct, warnings}. Star expansion uses the given connection
    (effective params for the default connection); without it stars are skipped.
    Read-only: never touches files or the DB data (introspection only).
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from rml_python.sql_import import parse_sql
        data = json.loads(request.body.decode() or "{}")
        sql = (data.get("sql") or "").strip()
        if not sql:
            return JsonResponse({"error": "sql required"}, status=400)
        cid = str(data.get("connection_id") or "").strip()
        dj = None
        if cid:
            try:
                dj = _effective_or_row(int(cid)) if cid.isdigit() else None
                if dj is None:
                    from .models import Connection
                    dj = Connection.objects.filter(name=cid).first()
            except Exception:
                dj = None
        schema = (data.get("schema") or "").strip()

        # fetch-query output references STAGE tables (rml_api_<gid>_<table>_<hash>)
        # which don't exist in the source DB — map them back to source tables so
        # the query imports directly. Unanimous gid ⇒ introspect that connection.
        import re as _re_stage
        _stage_re = _re_stage.compile(r"\brml_api_(\d+)_([A-Za-z_][\w$#]*)_[0-9a-f]{8}\b",
                                      _re_stage.IGNORECASE)
        _union_re = _re_stage.compile(r"\brml_union_([A-Za-z_][\w$#]*)_[0-9a-f]{8}\b",
                                      _re_stage.IGNORECASE)
        try:
            _stage_hits = _stage_re.findall(sql)
        except Exception:
            _stage_hits = []
        _stage_gids = {g for g, _t in (_stage_hits or [])}
        if _stage_hits and len(_stage_gids) == 1:
            _g0 = next(iter(_stage_gids))
            try:
                _dj0 = _effective_or_row(int(_g0)) if _g0.isdigit() else None
            except Exception:
                _dj0 = None
            if _dj0 is not None:
                dj = _dj0
                try:
                    cid = str(_dj0.id)
                except Exception:
                    pass
        if _stage_hits or _union_re.search(sql):
            sql = _stage_re.sub(lambda _m: _m.group(2), sql)
            sql = _union_re.sub(lambda _m: _m.group(1), sql)

        def _expand(sch, tbl):
            if dj is None:
                return []
            try:
                rows = _table_columns_obj(dj, sch or _default_schema_for(dj), tbl)
                return [{"name": r["name"], "type": r.get("type") or "VARCHAR"} for r in rows]
            except Exception:
                return []

        def _describe(sch, tbl):
            try:
                return [r["name"] for r in _expand(sch, tbl)]
            except Exception:
                return []

        def _fk_of(sch, tbl):
            if dj is None:
                return []
            try:
                return _table_fks_obj(dj, sch or _default_schema_for(dj), tbl)
            except Exception:
                return []

        res = parse_sql(sql, conn_id=(str(dj.id) if dj is not None else cid),
                        default_schema=schema or (_default_schema_for(dj) if dj is not None else ""),
                        expand_star=_expand, describe=_describe, fk_of=_fk_of)
        if _stage_hits:
            try:
                res.setdefault("warnings", []).insert(
                    0, "حُلت جداول الترحيل (rml_api_*) إلى جداول المصدر — الاستعلام المجلوب يُستورد مباشرة")
            except Exception:
                pass
        # FK verification: mark links matching a declared DB foreign key (either direction)
        if dj is not None:
            try:
                _tsch = {t.get("table"): (t.get("schema") or "") for t in (res.get("tables") or []) if t.get("table")}
                _defsch = schema or _default_schema_for(dj)
                _fkc: dict = {}

                def _fks(tbl):
                    if tbl not in _fkc:
                        try:
                            _fkc[tbl] = _table_fks_obj(dj, _tsch.get(tbl) or _defsch, tbl)
                        except Exception:
                            _fkc[tbl] = []
                    return _fkc[tbl]

                def _match(ft, fc, tt, tc):
                    for f in _fks(ft):
                        if (str(f.get("column") or "").lower() == str(fc or "").lower()
                                and str(f.get("ref_table") or "").lower().endswith(str(tt or "").lower())
                                and str(f.get("ref_column") or "").lower() == str(tc or "").lower()):
                            return True
                    return False

                for l in (res.get("links") or []):
                    l["verified_fk"] = bool(
                        _match(l.get("from_table"), l.get("from_col"), l.get("to_table"), l.get("to_col"))
                        or _match(l.get("to_table"), l.get("to_col"), l.get("from_table"), l.get("from_col")))
            except Exception:
                pass
        return JsonResponse({"ok": True, **res}, json_dumps_params={"ensure_ascii": False})
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_rml_preview(request):
    try:
        data = json.loads(request.body.decode() or "{}")
        rml = data.get("rml", "emp_report")
        app = data.get("app")
        payload = data.get("payload", {})
        pipe = _rml_get_pipeline(rml, app)
        sql = pipe.preview_sql(payload)
        _pdet = pipe.compiler.detail() if hasattr(pipe.compiler, "detail") else None
        return JsonResponse({"sql": sql, "metadata": pipe.rpt_metadata(), "columns": [c.to_dict() for c in pipe.columns], "fields": [f.to_dict() for f in pipe.compiler.fields()], "connections": [c.to_dict() for c in pipe.compiler.connections()], "charts": [c.to_dict() for c in pipe.compiler.charts()], "detail": (_pdet.to_dict() if _pdet else None)}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

@csrf_exempt
def _suggest_columns(schema: str, missing: str, db_engine=None):
    """أعمدة السكيما الفعلية + أقرب الأسماء للعمود المفقود."""
    import difflib
    try:
        # Detect engine type
        is_oracle = "oracle" in type(db_engine).__name__.lower() if db_engine else False
        if is_oracle:
            # Oracle: use ALL_TABLES / ALL_TAB_COLUMNS
            try:
                conn = db_engine.conn
                if not conn or getattr(conn, 'closed', True):
                    db_engine.connect()
                    conn = db_engine.conn
                cur = conn.cursor()
                schema_upper = (schema or "").upper()
                if schema_upper:
                    cur.execute("SELECT table_name FROM all_tables WHERE owner=:s ORDER BY table_name", {"s": schema_upper})
                else:
                    cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
                tables = [r[0] for r in cur.fetchall()]
                if schema_upper:
                    cur.execute("SELECT table_name, column_name FROM all_tab_columns WHERE owner=:s ORDER BY table_name, column_id", {"s": schema_upper})
                else:
                    cur.execute("SELECT table_name, column_name FROM user_tab_columns ORDER BY table_name, column_id")
                by_table = {}
                for t, c in cur.fetchall():
                    by_table.setdefault(t, []).append(c)
                cur.close()
            except Exception:
                return [], {}, [], schema
        else:
            # PostgreSQL
            import psycopg2
            host, dbname, port, user, password = "172.16.10.101", "urs", 5432, "postgres", "postgres"
            if db_engine:
                host = getattr(db_engine, "host", host) or host
                dbname = getattr(db_engine, "dbname", dbname) or dbname
                port = int(getattr(db_engine, "port", port) or port)
                user = getattr(db_engine, "user", user) or user
                password = getattr(db_engine, "password", password) or password
            conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port, connect_timeout=5)
            try:
                cur = conn.cursor()
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type IN ('BASE TABLE','VIEW') ORDER BY table_name", (schema,))
                tables = [r[0] for r in cur.fetchall()]
                if not tables:
                    cur.execute("SELECT current_schema()")
                    actual_schema = cur.fetchone()[0]
                    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type IN ('BASE TABLE','VIEW') ORDER BY table_name", (actual_schema,))
                    tables = [r[0] for r in cur.fetchall()]
                    schema = actual_schema
                cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name, ordinal_position", (schema,))
                by_table = {}
                for t, c in cur.fetchall():
                    by_table.setdefault(t, []).append(c)
                cur.close()
            finally:
                conn.close()
        all_cols = sorted({c for cols in by_table.values() for c in cols})
        similar = difflib.get_close_matches(missing, all_cols, n=5, cutoff=0.4) if missing else []
        return tables, by_table, similar, schema
    except Exception:
        return [], {}, [], schema


# ── Presets (saved filters, table `presets`) ───────────────────────────────
def api_presets_list(request):
    """GET /api/presets/?app=&type=filter — list saved presets."""
    try:
        from .models import Preset
        app = (request.GET.get("app") or "").strip()
        ptype = (request.GET.get("type") or "filter").strip()
        qs = Preset.objects.all()
        if app:
            qs = qs.filter(app=app)
        if ptype:
            qs = qs.filter(type=ptype)
        return JsonResponse({"presets": [p.to_dict() for p in qs.order_by("name")]})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_presets_create(request):
    """POST /api/presets/create/ {app, type, name, filter} — save a preset."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from .models import Preset
        data = json.loads(request.body.decode() or "{}")
        app = (data.get("app") or "").strip()
        name = (data.get("name") or "").strip()
        if not app or not name:
            return JsonResponse({"error": "app and name required"}, status=400)
        ptype = (data.get("type") or "filter").strip() or "filter"
        flt = data.get("filter", data.get("filters", []))
        if not isinstance(flt, list):
            return JsonResponse({"error": "filter must be a list"}, status=400)
        p, created = Preset.objects.update_or_create(
            app=app, type=ptype, name=name, defaults={"filter": flt})
        return JsonResponse({"preset": p.to_dict(), "created": created})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_presets_delete(request):
    """POST /api/presets/delete/ {id} — delete a preset."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from .models import Preset
        data = json.loads(request.body.decode() or "{}")
        pid = data.get("id")
        n, _ = Preset.objects.filter(id=pid).delete()
        return JsonResponse({"ok": True, "deleted": n})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_rml_joins(request):
    """GET /api/rml/joins?rml=&app= — effective JOINs (explicit + inferred)."""
    rml = request.GET.get("rml", "emp_report")
    app = request.GET.get("app")
    try:
        pipe = _rml_get_pipeline(rml, app)
        return JsonResponse({"joins": pipe.inferred_joins()}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_rml_detail(request):
    """Fetch <detail> sub-records for one master key value.

    POST {rml, app, master_value, page, pageSize}
    """
    try:
        data = json.loads(request.body.decode() or "{}")
        rml = data.get("rml", "emp_report")
        app = data.get("app")
        master_value = data.get("master_value", data.get("masterValue"))
        page = data.get("page", 1)
        page_size = data.get("pageSize", data.get("page_size", 50))
        pipe = _rml_get_pipeline(rml, app)
        result = pipe.detail_rows(master_value, page=page, page_size=page_size)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
def api_rml_detail_search(request):
    """Search INSIDE detail columns: POST {rml, app, q} → {master_values, count}."""
    try:
        data = json.loads(request.body.decode() or "{}")
        rml = data.get("rml", "emp_report")
        app = data.get("app")
        q = data.get("q", data.get("query", ""))
        pipe = _rml_get_pipeline(rml, app)
        result = pipe.detail_search(q)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_rml_distinct(request):
    """Distinct raw values of one report column (status-map designer helper).

    POST {rml, app, column, limit, search} → {values, column, truncated, sql}
    Queries ALL records (not the page); search filters server-side.
    """
    try:
        data = json.loads(request.body.decode() or "{}")
        rml = data.get("rml", "emp_report")
        app = data.get("app")
        column = data.get("column", data.get("alias", data.get("field", "")))
        _lim_raw = data.get("limit", 100)
        if isinstance(_lim_raw, str) and _lim_raw.strip().lower() in ("all", "unlimited"):
            limit = "all"  # بلا سقف (للتحليل في المصمم)
        else:
            try:
                limit = int(_lim_raw or 100)
            except (TypeError, ValueError):
                limit = 100
        search = data.get("search", None)
        pipe = _rml_get_pipeline(rml, app)
        result = pipe.distinct_values(column, limit=limit, search=search)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
def api_rml_groups(request):
    """Server-side grouping over ALL records (sidebar, not the page).

    POST {rml, app, column, filters, columnFilters, bucket, limit}
      bucket: null | 'day' | 'month' | 'year' | number (numeric range size)
    → {groups:[{value,count}], column, bucket, truncated}
    """
    try:
        data = json.loads(request.body.decode() or "{}")
        rml = data.get("rml", "emp_report")
        app = data.get("app")
        column = data.get("column", data.get("alias", data.get("field", "")))
        filters = data.get("filters") or []
        for col, vals in (data.get("columnFilters") or {}).items():
            if vals:
                filters.append({"field": col, "op": "in", "value": vals})
        bucket = data.get("bucket", None)
        try:
            limit = int(data.get("limit", 500) or 500)
        except (TypeError, ValueError):
            limit = 500
        pipe = _rml_get_pipeline(rml, app)
        result = pipe.group_values(column, filters=filters, bucket=bucket, limit=limit)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ── Business Rules: policies per rule ($rule.var$) ──────────────────────────
def _scan_app_rules(app_name):
    """قواعد الأعمال في كل تقارير التطبيق: [{file, rule{...}}]."""
    out = []
    for cand in [BASE_DIR / "odex" / "system" / app_name, BASE_DIR / "system" / app_name]:
        if not cand.exists():
            continue
        for p in sorted(cand.glob("*.rml")):
            try:
                from rml_python.compiler import RMLReportCompiler
                comp = RMLReportCompiler(path=p)
                for r in comp.rules():
                    d = r.to_dict()
                    d["file"] = p.name
                    out.append(d)
            except Exception:
                continue
        break
    return out


def _save_rule_policies_xml(rml_path, rule_name, policies, variables=None):
    """استبدال سياسات قاعدة واحدة داخل ملف RML مع الحفاظ على باقي الملف.
    variables: قائمة متغيرات (name/display/type/options) — تُستبدل عند تمريرها
    (تُستخدم لتثبيت الأنواع المكتشفة تلقائياً من قيم السياسات)."""
    import xml.etree.ElementTree as ET
    raw = rml_path.read_text(encoding="utf-8").lstrip("\ufeff")
    root = ET.fromstring(raw)
    target = None
    for el in root.iter():
        if el.tag.lower() == "rule":
            for k, v in el.attrib.items():
                if k.lower() == "name" and str(v or "").strip().lower() == str(rule_name).strip().lower():
                    target = el
                    break
        if target is not None:
            break
    if target is None:
        raise ValueError(f"القاعدة '{rule_name}' غير موجودة في {rml_path.name}")
    for ch in [c for c in list(target) if c.tag.lower() == "policies"]:
        target.remove(ch)
    if policies:
        tmp = ET.Element("rule")
        _write_rule_element(tmp, {"id": target.attrib.get("id", "1"), "name": rule_name,
                                  "policies": policies}, 1)
        for ch in [c for c in list(tmp) if c.tag.lower() == "policies"]:
            target.append(ch)
    if variables is not None:
        for ch in [c for c in list(target) if c.tag.lower() == "variables"]:
            target.remove(ch)
        if variables:
            tmp = ET.Element("rule")
            _write_rule_element(tmp, {"id": target.attrib.get("id", "1"), "name": rule_name,
                                      "variables": variables}, 1)
            for ch in [c for c in list(tmp) if c.tag.lower() == "variables"]:
                target.append(ch)
    rml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode"),
        encoding="utf-8")


def api_app_rules(request, app_name):
    """GET /api/apps/<app>/rules/ → {rules:[{file,name,display,icon,description,sources,variables,policies,warnings}]}"""
    try:
        from rml_python.rulevars import detect_policy_overlaps
        rules = _scan_app_rules(app_name)
        lite = []
        for r in rules:
            try:
                from rml_python.compiler import RMLRulePolicy as _RP
                _pols = [_RP(id=str(p.get("id") or ""), name=str(p.get("name") or ""),
                             match=dict(p.get("match") or {}), values=dict(p.get("values") or {}),
                             priority=int(p.get("priority", 100) or 100),
                             is_default=bool(p.get("is_default")))
                         for p in (r.get("policies") or [])]
                _warns = detect_policy_overlaps(_pols)
            except Exception:
                _warns = []
            lite.append({"file": r.get("file"), "name": r.get("name"), "display": r.get("display"),
                         "icon": r.get("icon"), "description": r.get("description"),
                         "sources": r.get("sources"), "variables": r.get("variables"),
                         "policies": r.get("policies"),
                         "policy_count": len(r.get("policies") or []),
                         "warnings": _warns})
        return JsonResponse({"app": app_name, "rules": lite}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_app_rule_policy_save(request, app_name):
    """POST /api/apps/<app>/rules/policy/save/ {file, rule, policy{id?,name,priority?,is_default?,match,values}} → upsert.

    - name: اسم السياسة (مطلوب، يُعرض في القائمة والتحذيرات).
    - priority: الأصغر = أعلى أولوية = يُقيّم أولاً ويفوز عند التداخل (افتراضي 100).
    - is_default: سياسة افتراضية واحدة لكل قاعدة — تُنزع عن الباقي تلقائياً.
    - يُرجع warnings عند وجود تداخل (تحذير فقط — الحفظ يتم لأن الأولوية تحسم الفوز).
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        file = str(data.get("file", "")).strip()
        rule = str(data.get("rule", "")).strip()
        policy = data.get("policy") or {}
        if not file or not rule:
            return JsonResponse({"error": "file + rule required"}, status=400)
        if "/" in file or "\\" in file or ".." in file:
            return JsonResponse({"error": "invalid file name"}, status=400)
        rml_path = _find_rml_path(file, app_name)
        if not rml_path or not rml_path.exists():
            return JsonResponse({"error": f"التقرير {file} غير موجود"}, status=404)
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(path=rml_path)
        cur = None
        for r in comp.rules():
            if str(r.name or "").strip().lower() == rule.lower():
                cur = r
                break
        if cur is None:
            return JsonResponse({"error": f"القاعدة '{rule}' غير موجودة"}, status=404)
        policies = [p.to_dict() for p in (cur.policies or [])]
        pid = str(policy.get("id") or "").strip()
        pname = str(policy.get("name") or "").strip()
        if not pname:
            return JsonResponse({"error": "اسم السياسة مطلوب"}, status=400)
        try:
            prio = int(policy.get("priority", 100))
        except (TypeError, ValueError):
            prio = 100
        is_def = bool(policy.get("is_default"))
        new_pol = {"id": pid or f"p{len(policies)+1}",
                   "name": pname,
                   "priority": prio,
                   "is_default": is_def,
                   "match": policy.get("match") or {},
                   "values": policy.get("values") or {}}
        if is_def:
            # افتراضية واحدة فقط — تُنزع عن الباقي، وبلا شروط (تنطبق على الكل)
            new_pol["match"] = {}
            for p in policies:
                p["is_default"] = False
        # تحقق: الحقول المطابقة يجب أن تكون من مصادر القاعدة
        srcs = {str(s).strip().lower() for s in (cur.sources or []) if str(s or "").strip()}
        if srcs:
            for fld in (new_pol["match"] or {}):
                if str(fld).strip().lower() not in srcs:
                    return JsonResponse(
                        {"error": f"الحقل '{fld}' ليس من مصادر القاعدة ({'، '.join(cur.sources)})"},
                        status=400)
        # تحقق: المتغيرات يجب أن تكون معرفة في القاعدة
        vnames = {str(getattr(v, "name", "") or "").strip().lower() for v in (cur.variables or [])}
        for vn in (new_pol["values"] or {}):
            if vnames and str(vn).strip().lower() not in vnames:
                return JsonResponse(
                    {"error": f"المتغير '{vn}' غير معرف في القاعدة '{rule}'"}, status=400)
        done = False
        for i, p in enumerate(policies):
            if str(p.get("id")) == new_pol["id"]:
                # الحفاظ على الأولوية المخزنة عند عدم إرسالها
                policies[i] = new_pol
                done = True
                break
        if not done:
            policies.append(new_pol)
        _save_rule_policies_xml(rml_path, cur.name, policies)
        # تحذير التداخل بعد الحفظ (لا يمنع — الأولوية تحسم الفائز)
        warnings = []
        try:
            from rml_python.compiler import RMLRulePolicy as _RP2
            from rml_python.rulevars import detect_policy_overlaps as _det2
            _pols = [_RP2(id=str(p.get("id") or ""), name=str(p.get("name") or ""),
                          match=dict(p.get("match") or {}), values=dict(p.get("values") or {}),
                          priority=int(p.get("priority", 100) or 100),
                          is_default=bool(p.get("is_default")))
                     for p in policies]
            warnings = _det2(_pols)
        except Exception:
            warnings = []
        return JsonResponse({"ok": True, "policy": new_pol, "policy_count": len(policies),
                             "warnings": warnings},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_app_rule_policy_delete(request, app_name):
    """POST /api/apps/<app>/rules/policy/delete/ {file, rule, policy_id}."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        file = str(data.get("file", "")).strip()
        rule = str(data.get("rule", "")).strip()
        pid = str(data.get("policy_id") or data.get("id") or "").strip()
        if not file or not rule or not pid:
            return JsonResponse({"error": "file + rule + policy_id required"}, status=400)
        rml_path = _find_rml_path(file, app_name)
        if not rml_path or not rml_path.exists():
            return JsonResponse({"error": f"التقرير {file} غير موجود"}, status=404)
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(path=rml_path)
        cur = None
        for r in comp.rules():
            if str(r.name or "").strip().lower() == rule.lower():
                cur = r
                break
        if cur is None:
            return JsonResponse({"error": f"القاعدة '{rule}' غير موجودة"}, status=404)
        policies = [p.to_dict() for p in (cur.policies or []) if str(p.id) != pid]
        _save_rule_policies_xml(rml_path, cur.name, policies)
        return JsonResponse({"ok": True, "policy_count": len(policies)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ── RML async execute (background jobs for huge stagings) ───────────────────
_RML_JOBS = {}
_RML_JOBS_LOCK = _th.Lock()
_RML_JOB_TTL = 1800  # keep finished results 30 min


def _log_rml_failure(rml, app, payload, err_text):
    """Write the FULL failure (SQL + report state) to rml_last_error.txt.

    The UI truncates long errors — this file keeps everything for diagnosis:
    report, payload keys, draft connections/tables (draft file still exists
    at job-failure time), and the complete SQL. Overwritten on each failure.
    """
    try:
        import datetime as _dtm
        _lines = ["TIME: %s" % _dtm.datetime.now().isoformat(timespec="seconds"),
                  "RML: %s | APP: %s" % (rml, app)]
        try:
            _pl = payload or {}
            _lines.append("PAYLOAD keys: %s" % sorted(_pl.keys()))
            for _k in ("activeTable", "table", "page", "pageSize", "groupBy", "group_by"):
                if _k in _pl:
                    _lines.append("PAYLOAD %s: %s" % (_k, str(_pl[_k])[:120]))
            _fl = _pl.get("filters") or _pl.get("activeFilters") or []
            _lines.append("FILTERS: %d" % len(_fl))
            try:
                _sort = _pl.get("sort")
                if _sort:
                    _lines.append("SORT: %s" % str(_sort)[:120])
            except Exception:
                pass
        except Exception:
            pass
        if str(rml or "").startswith("__draft_"):
            try:
                _dp = _find_rml_path(rml, app)
                if _dp and _dp.exists():
                    import xml.etree.ElementTree as _ET
                    _root = _ET.parse(str(_dp)).getroot()
                    _conns = [(c.get("id"), c.get("connection_id")) for c in _root.iter("connection")]
                    _lines.append("DRAFT connections: %s" % (_conns,))
                    _tbls = [(t.get("name"), t.get("conn")) for t in _root.iter("table")]
                    _lines.append("DRAFT table_opts: %s" % (_tbls,))
                    _flds = [(f.get("name"), f.get("table_source"), f.get("conn_id")) for f in _root.iter("field")]
                    _lines.append("DRAFT fields: %d e.g. %s" % (len(_flds), _flds[:8]))
            except Exception as _e2:
                _lines.append("DRAFT parse: %s" % str(_e2)[:120])
        _lines.append("ERROR FULL:")
        _lines.append(str(err_text or ""))
        (BASE_DIR / "rml_last_error.txt").write_text("\n".join(_lines), encoding="utf-8")
    except Exception:
        pass


def _rml_job_progress(job_id, info):
    try:
        with _RML_JOBS_LOCK:
            rec = _RML_JOBS.get(job_id)
            if rec is None:
                return
            now = _time.time()
            if now - float(rec.get("_last_prog", 0)) < 1.0 and (info or {}).get("stage") == "rows":
                return
            rec["_last_prog"] = now
            rec["progress"] = dict(info or {})
    except Exception:
        pass


def _rml_job_run(job_id, rml, app, payload):
    try:
        try:
            from django.db import close_old_connections as _close
            _close()
        except Exception:
            pass
        with _RML_JOBS_LOCK:
            rec = _RML_JOBS.get(job_id)
            if rec is None:
                return
            rec["status"] = "running"
            rec["progress"] = {"stage": "prepare", "text": "تجهيز التقرير…"}
        pipe = _rml_get_pipeline(rml, app)
        try:
            eng = getattr(pipe, "rml_engine", None)
            if eng is not None:
                eng._progress_cb = lambda info: _rml_job_progress(job_id, info)
        except Exception:
            pass
        with _RML_JOBS_LOCK:
            rec = _RML_JOBS.get(job_id)
            if rec is not None:
                rec["progress"] = {"stage": "execute", "text": "جلب البيانات…"}
        result = pipe.execute(payload)
        with _RML_JOBS_LOCK:
            rec = _RML_JOBS.get(job_id)
            if rec is not None:
                rec["status"] = "done"
                rec["result"] = result
                rec["progress"] = {"stage": "done", "text": "اكتمل"}
    except Exception as e:
        try:
            with _RML_JOBS_LOCK:
                rec = _RML_JOBS.get(job_id)
                if rec is not None:
                    rec["status"] = "error"
                    rec["error"] = str(e)[:2000]
        except Exception:
            pass
        try:
            _log_rml_failure(rml, app, payload, str(e))
        except Exception:
            pass
    finally:
        try:
            with _RML_JOBS_LOCK:
                rec = _RML_JOBS.get(job_id)
                if rec is not None:
                    rec["done_at"] = _time.time()
            from django.db import close_old_connections as _close2
            _close2()
        except Exception:
            pass


@csrf_exempt
def api_rml_execute_async(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
    except Exception:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    job = _uuid.uuid4().hex[:16]
    with _RML_JOBS_LOCK:
        now = _time.time()
        for _jid in [k for k, v in _RML_JOBS.items()
                     if now - float(v.get("done_at") or v.get("created", now)) > _RML_JOB_TTL]:
            _RML_JOBS.pop(_jid, None)
        _RML_JOBS[job] = {"status": "queued", "progress": {"stage": "queued", "text": "في الانتظار…"},
                          "created": now, "done_at": None, "_last_prog": 0.0,
                          "rml": data.get("rml"), "app": data.get("app")}
    t = _th.Thread(target=_rml_job_run,
                   args=(job, data.get("rml", "emp_report"), data.get("app"), data.get("payload", {})),
                   daemon=True)
    t.start()
    return JsonResponse({"job": job})


def api_rml_job(request, job_id):
    with _RML_JOBS_LOCK:
        rec = _RML_JOBS.get(job_id)
        if rec is None:
            return JsonResponse({"error": "job not found"}, status=404)
        out = {"job": job_id, "status": rec.get("status"),
               "progress": rec.get("progress") or {}}
        if rec.get("status") == "done":
            out["result"] = rec.get("result")
        if rec.get("status") == "error":
            out["error"] = rec.get("error", "")
    return JsonResponse(out, json_dumps_params={"ensure_ascii": False})


# ---------------------------------------------------------------------------
# XSQL endpoints: compile + execute an extended SQL string against one or
# more queryable connections (see rml_python/xsql.py).
# ---------------------------------------------------------------------------

def _xsql_resolve_conn(conn_key):
    """Return (live_engine_obj, db_engine_str) for a Django Connection id.

    Builds a real, connected engine wrapper (SqlServerDirect for SQL Server,
    psycopg2-backed wrapper for Postgres) so the XSQL executor can run
    SQL on it directly.
    """
    try:
        from .models import Connection as _DC
        row = _DC.objects.filter(id=int(conn_key)).first()
    except Exception:
        row = None
    if row is None:
        return None, ""
    eng = str(getattr(row, "engine", "") or "").lower()
    is_queryable = bool(getattr(row, "is_queryable", True))
    if not is_queryable or eng not in ("postgres", "postgresql", "sqlserver", "oracle"):
        return None, eng
    # Build a live engine that has `conn.cursor()`.
    if eng in ("sqlserver",):
        try:
            from rml_python.engine import SqlServerDirect as _SD
            w = _SD(row)
            try:
                w.connect()
            except Exception:
                return None, eng
            return w, eng
        except Exception:
            return None, eng
    # postgres (and oracle via same psycopg2 path used elsewhere)
    try:
        import psycopg2 as _pg
        host = str(getattr(row, "host", "") or "127.0.0.1")
        port = int(getattr(row, "port", 0) or 5432)
        user = str(getattr(row, "user", "") or "")
        name = str(getattr(row, "name", "") or "urs")
        # Password may be PBKDF2-encrypted; decrypt via config.dbconf.
        pwd = str(getattr(row, "password", "") or "")
        try:
            import config.dbconf as _dbconf
            _dec = _dbconf.dbpass_resolve(pwd) if pwd else ""
            if _dec:
                pwd = _dec
        except Exception:
            pass
        cn = _pg.connect(
            host=host, port=port, dbname=name, user=user,
            password=pwd, application_name="xsql_runner",
        )
    except Exception:
        return None, eng
    # Wrap with a tiny shim that mirrors OracleEngine's interface.
    class _PgShim:
        def __init__(self, conn):
            self.conn = conn
        def _exec(self, sql, params):
            cur = self.conn.cursor()
            try:
                cur.execute(sql, params or {})
                return cur
            except Exception:
                try: cur.close()
                except Exception: pass
                raise
    return _PgShim(cn), eng


def _xsql_exec_on_db(db_obj, sql, params):
    """Execute sql on a live engine and return rows as list[dict]."""
    cur = db_obj.conn.cursor()
    try:
        # Postgres wants %s placeholders; SQL Server uses {}. Transpile if needed.
        from rml_python.engine import _mssql_transpile_sql, _mssql_bind_values
        try:
            transpiled_sql = _mssql_transpile_sql(sql, convert_binds=True)
            binds = _mssql_bind_values(sql, params)
            cur.execute(transpiled_sql, binds)
        except Exception:
            cur.execute(sql, params or {})
        names = [d[0] for d in (cur.description or [])] if cur.description else []
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        return rows, names
    finally:
        try: cur.close()
        except Exception: pass


@csrf_exempt
def api_xsql_compile(request):
    """POST {sql, conn_keys:[...], namespaces:{alias: connection_id}} → plan."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        body = json.loads(request.body.decode() or "{}")
        sql_text = (body.get("sql") or "").strip()
        if not sql_text:
            return JsonResponse({"error": "sql required"}, status=400)
        # Build conn_map keyed by the namespace used in the SQL
        # (`conn_a.tbl`, `conn_b.lookup`, ...). Two options:
        #   1) Body provides `namespaces`: {"conn_a": "2", "conn_b": "6"}
        #   2) Fallback: body provides `conn_keys` (list of connection ids);
        #      we map each to `ns_<id>`.
        namespaces = body.get("namespaces") or {}
        conn_map: Dict[str, Dict[str, Any]] = {}
        if namespaces:
            for alias, ck in namespaces.items():
                db_obj, _eng = _xsql_resolve_conn(ck)
                conn_map[str(alias)] = {"engine": _eng, "db": db_obj, "conn_id": str(ck)}
        else:
            conn_keys = body.get("conn_keys") or ["1"]
            for ck in conn_keys:
                db_obj, _eng = _xsql_resolve_conn(ck)
                alias = f"ns_{ck}"
                conn_map[alias] = {"engine": _eng, "db": db_obj, "conn_id": str(ck)}
        from rml_python.xsql import compile_xsql, parse as _xparse
        try:
            parsed = _xparse(sql_text)
        except Exception as pe:
            return JsonResponse({"error": f"parse error: {pe}"}, status=400)
        try:
            compiled = compile_xsql(sql_text, conn_map)
        except Exception as ce:
            return JsonResponse({"error": f"compile error: {ce}"}, status=400)
        return JsonResponse({
            "ok": True,
            "primary_sql": compiled.primary.sql,
            "primary_conn": compiled.primary.conn_key,
            "primary_columns": compiled.primary.columns,
            "secondaries": [
                {"conn_key": s.conn_key, "schema": s.schema, "table": s.table, "sql": s.sql}
                for s in compiled.secondaries
            ],
            "merges": compiled.merges,
            "final_columns": compiled.final_columns,
        }, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_xsql_execute(request):
    """POST {sql, namespaces:{alias: connection_id}} → {rows, columns}."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        body = json.loads(request.body.decode() or "{}")
        sql_text = (body.get("sql") or "").strip()
        if not sql_text:
            return JsonResponse({"error": "sql required"}, status=400)
        namespaces = body.get("namespaces") or {}
        conn_map: Dict[str, Dict[str, Any]] = {}
        if namespaces:
            for alias, ck in namespaces.items():
                db_obj, _eng = _xsql_resolve_conn(ck)
                conn_map[str(alias)] = {"engine": _eng, "db": db_obj, "conn_id": str(ck)}
        else:
            conn_keys = body.get("conn_keys") or ["1"]
            for ck in conn_keys:
                db_obj, _eng = _xsql_resolve_conn(ck)
                alias = f"ns_{ck}"
                conn_map[alias] = {"engine": _eng, "db": db_obj, "conn_id": str(ck)}
        from rml_python.xsql import compile_xsql
        try:
            compiled = compile_xsql(sql_text, conn_map)
        except Exception as ce:
            return JsonResponse({"error": f"compile error: {ce}"}, status=400)

        sql_per_conn = {}
        # Run primary SQL.
        pkey = compiled.primary.conn_key or ""
        pdb = (conn_map.get(pkey) or {}).get("db")
        if pdb is None:
            return JsonResponse({"error": f"primary conn {pkey!r} not queryable"}, status=400)
        sql_per_conn[pkey or "_"] = compiled.primary.sql
        try:
            primary_rows, primary_names = _xsql_exec_on_db(pdb, compiled.primary.sql, {})
        except Exception as pe:
            return JsonResponse({
                "error": f"primary query failed: {pe}",
                "primary_sql": compiled.primary.sql,
            }, status=500)

        # For each JOIN, fetch the secondary table once and merge.
        if compiled.merges:
            for jn in compiled.merges:
                sk = str(jn.get("secondary_conn") or "")
                sec_db = (conn_map.get(sk) or {}).get("db")
                if sec_db is None:
                    continue
                try:
                    sec_db.connect()
                except Exception:
                    pass
                sec_sql = f"SELECT * FROM {jn['secondary_table']}"
                sql_per_conn[sk or "_"] = sec_sql
                try:
                    sec_rows, _ = _xsql_exec_on_db(sec_db, sec_sql, {})
                except Exception:
                    sec_rows = []
                key_col = jn.get("secondary_col") or ""
                idx: Dict[Any, Dict[str, Any]] = {}
                for r in sec_rows:
                    idx[r.get(key_col)] = r
                pc = jn.get("primary_col") or ""
                for r in primary_rows:
                    r[f"{jn['secondary_table']}.{key_col}"] = idx.get(r.get(pc), {}).get(key_col)

        return JsonResponse({
            "ok": True,
            "rows": primary_rows,
            "columns": primary_names,
            "sql_per_conn": sql_per_conn,
        }, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@csrf_exempt
def api_xsql_run(request):
    """One-shot XSQL endpoint for the designer/player UI.

    POST {sql, namespaces:{alias: connection_id}, page, pageSize} →
        {ok, columns, rows, total, sql_per_conn, primary_conn, errors}
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        body = json.loads(request.body.decode() or "{}")
        sql_text = (body.get("sql") or "").strip()
        if not sql_text:
            return JsonResponse({"error": "sql required"}, status=400)
        namespaces = body.get("namespaces") or {}
        conn_keys = body.get("conn_keys") or []
        if not namespaces and conn_keys:
            namespaces = {f"ns_{ck}": str(ck) for ck in conn_keys}
        try:
            page = max(1, int(body.get("page") or 1))
        except Exception:
            page = 1
        try:
            page_size = body.get("pageSize") or body.get("page_size") or 50
            page_size = int(page_size) if page_size != "all" else None
        except Exception:
            page_size = 50
        conn_map: Dict[str, Dict[str, Any]] = {}
        for alias, ck in namespaces.items():
            db_obj, _eng = _xsql_resolve_conn(ck)
            conn_map[str(alias)] = {"engine": _eng, "db": db_obj, "conn_id": str(ck)}
        from rml_python.xsql import compile_xsql, parse as _xparse
        try:
            parsed = _xparse(sql_text)
        except Exception as pe:
            return JsonResponse({"ok": False, "error": f"parse error: {pe}"}, status=400)
        try:
            compiled = compile_xsql(sql_text, conn_map)
        except Exception as ce:
            return JsonResponse({"ok": False, "error": f"compile error: {ce}"}, status=400)

        sql_per_conn: Dict[str, str] = {}
        pkey = compiled.primary.conn_key or ""
        pdb = (conn_map.get(pkey) or {}).get("db")
        if pdb is None:
            return JsonResponse({"ok": False, "error": f"primary conn {pkey!r} not queryable", "primary_sql": compiled.primary.sql}, status=400)
        sql_per_conn[pkey or "_"] = compiled.primary.sql
        try:
            primary_rows, primary_names = _xsql_exec_on_db(pdb, compiled.primary.sql, {})
        except Exception as pe:
            return JsonResponse({"ok": False, "error": f"primary query failed: {pe}",
                                "primary_sql": compiled.primary.sql}, status=500)

        # Merge JOINs: index secondary by key col, attach to primary row.
        for jn in (compiled.merges or []):
            sk = str(jn.get("secondary_conn") or "")
            sec_db = (conn_map.get(sk) or {}).get("db")
            if sec_db is None:
                continue
            sec_sql = f"SELECT * FROM {jn['secondary_table']}"
            sql_per_conn[sk or "_"] = sec_sql
            try:
                sec_rows, _ = _xsql_exec_on_db(sec_db, sec_sql, {})
            except Exception:
                sec_rows = []
            key_col = jn.get("secondary_col") or ""
            idx: Dict[Any, Dict[str, Any]] = {}
            for r in sec_rows:
                idx[r.get(key_col)] = r
            pc = jn.get("primary_col") or ""
            for r in primary_rows:
                r[f"{jn['secondary_table']}.{key_col}"] = idx.get(r.get(pc), {}).get(key_col)

        total = len(primary_rows)
        if page_size:
            start = (page - 1) * page_size
            page_rows = primary_rows[start:start + page_size]
        else:
            page_rows = primary_rows
        return JsonResponse({
            "ok": True,
            "columns": primary_names,
            "rows": page_rows,
            "total": total,
            "page": page,
            "pageSize": page_size or "all",
            "sql_per_conn": sql_per_conn,
            "primary_conn": pkey,
            "plan": {
                "primary_sql": compiled.primary.sql,
                "primary_columns": compiled.primary.columns,
                "secondaries": [
                    {"conn_key": s.conn_key, "schema": s.schema, "table": s.table, "sql": s.sql}
                    for s in compiled.secondaries
                ],
                "merges": compiled.merges,
                "final_columns": compiled.final_columns,
            },
        }, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


def api_rml_execute(request):
    pipe = None
    try:
        data = json.loads(request.body.decode() or "{}")
        rml = data.get("rml", "emp_report")
        app = data.get("app")
        payload = data.get("payload", {})
        pipe = _rml_get_pipeline(rml, app)
        result = pipe.execute(payload)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        msg = str(e)
        try:
            _d = data if "data" in locals() else {}
            _log_rml_failure(_d.get("rml"), _d.get("app"), _d.get("payload", {}), msg)
        except Exception:
            pass
        import re as _re_missing
        # Handle column does not exist
        m = _re_missing.search(r'column "([A-Za-z_][A-Za-z0-9_]*)" does not exist', msg)
        # Handle relation (table) does not exist
        m_table = _re_missing.search(r'relation "([A-Za-z_][A-Za-z0-9_]*)" does not exist', msg)
        # Handle schema does not exist
        m_schema = _re_missing.search(r'schema "([A-Za-z_][A-Za-z0-9_]*)" does not exist', msg)
        if (m or m_table or m_schema) and pipe is not None:
            missing = (m.group(1) if m else None) or (m_table.group(1) if m_table else None) or (m_schema.group(1) if m_schema else None)
            try:
                schema = (pipe.compiler.rpt_metadata().get("schema") or "")
            except Exception:
                schema = ""
            db_engine = getattr(pipe, "rml_engine", None) and getattr(pipe.rml_engine, "db", None)
            tables, by_table, similar, schema = _suggest_columns(schema, missing, db_engine)
            if m:
                hint = f'العمود "{missing}" غير موجود في السكيما "{schema}".'
            elif m_schema:
                hint = f'المخطط "{missing}" غير موجود في قاعدة البيانات.'
            else:
                hint = f'الجدول "{missing}" غير موجود في السكيما "{schema}".'
            if similar:
                hint += f' هل تقصد: {"، ".join(similar)}؟'
            if tables:
                sample = ", ".join(f"{t} ({len(by_table.get(t, []))} عمود)" for t in tables[:15])
                hint += f' الجداول المتاحة: {sample}.'
                if len(tables) > 15:
                    hint += f' (+{len(tables) - 15} أخرى).'
            hint += ' عدّل أعمدة التقرير من مودال التخصيص.'
            return JsonResponse({"error": hint, "missing_column": missing, "available_tables": tables[:30]}, status=500)
        return JsonResponse({"error": msg}, status=500)

def _server_host_name():
    """اسم جهاز الخادم من بيئة التشغيل (os env)."""
    try:
        import os as _os
        for _k in ("COMPUTERNAME", "HOSTNAME"):
            _v = (_os.environ.get(_k) or "").strip()
            if _v:
                return _v[:200]
    except Exception:
        pass
    try:
        import socket as _sock
        return (_sock.gethostname() or "")[:200]
    except Exception:
        return ""


def _monitor_machine(request, explicit=""):
    """هوية الجهاز: صريح من العميل، وإلا اسم جهاز الخادم (os env).

    - عميل محلي (127.0.0.1) ← اسم الجهاز يكفي (تشغيل فرعي/محلي).
    - عميل شبكة ← اسم الجهاز + IP العميل للتمييز بين الأجهزة.
    """
    if (explicit or "").strip():
        return explicit.strip()[:200]
    host = _server_host_name()
    try:
        fwd = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        ip = fwd or request.META.get("REMOTE_ADDR") or ""
    except Exception:
        ip = ""
    try:
        u = getattr(request, "user", None)
        who = str(getattr(u, "username", "") or "") if u is not None and getattr(u, "is_authenticated", False) else ""
    except Exception:
        who = ""
    if ip in ("127.0.0.1", "::1", "localhost", ""):
        base = host or "local"
    else:
        base = f"{host}/{ip}" if host else ip
    if who:
        base = f"{who}@{base}"
    return (base or "جهاز غير معروف")[:200]


@csrf_exempt
def api_monitor_touch(request):
    """POST /api/monitor/touch/ {record_id*, table?, event: add|edit|print, machine?}."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        rid = str(data.get("record_id") or "").strip()
        if not rid:
            return JsonResponse({"error": "record_id required"}, status=400)
        event = str(data.get("event") or "print").strip().lower()
        if event not in ("add", "edit", "print"):
            event = "print"
        from django.utils import timezone
        from django.db.models import F as _F
        from .models import AdMonitorLog
        machine = _monitor_machine(request, data.get("machine"))
        now = timezone.now()
        row, created = AdMonitorLog.objects.get_or_create(
            record_id=rid[:300],
            defaults={"table_name": str(data.get("table") or "")[:150]})
        if data.get("table") and not row.table_name:
            row.table_name = str(data.get("table"))[:150]
        if event == "add":
            if not row.ad_machine:
                row.ad_machine = machine
            if not row.ad_date:
                row.ad_date = now
            row.save()
        elif event == "edit":
            row.edit_machine = machine
            row.edit_date = now
            row.save()
        else:
            AdMonitorLog.objects.filter(pk=row.pk).update(print_count=_F("print_count") + 1)
            row.refresh_from_db()
            if data.get("table") and not row.table_name:
                row.table_name = str(data.get("table"))[:150]
                row.save(update_fields=["table_name"])
        return JsonResponse({"ok": True, "log": row.to_dict(), "created": created})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_monitor_get(request):
    """GET /api/monitor/get/?record_id= → سجل المراقبة أو {exists:false}."""
    try:
        rid = str(request.GET.get("record_id") or "").strip()
        if not rid:
            return JsonResponse({"error": "record_id required"}, status=400)
        from .models import AdMonitorLog
        row = AdMonitorLog.objects.filter(record_id=rid[:300]).first()
        if row is None:
            return JsonResponse({"exists": False})
        return JsonResponse({"exists": True, "log": row.to_dict()})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ── Create FML/RML files from sidebar (Add Form / Add Report) ─────────────────
@csrf_exempt
def api_create_fml(request, app_name):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        file = data.get("file", "").strip()
        displayName = data.get("displayName", "").strip() or file.replace(".fmlk","").replace(".fml","")
        table = data.get("table", "").strip() or "hr_custom"
        category = data.get("category", "").strip() or "عام"
        description = data.get("description", "").strip()
        if not file:
            return JsonResponse({"error": "file required"}, status=400)
        if not file.endswith((".fmlk", ".fml")):
            file += ".fmlk"
        if "/" in file or "\\" in file or ".." in file:
            return JsonResponse({"error": "invalid file name"}, status=400)
        # Resolve app dir (prefer odex/system)
        app_dir = BASE_DIR / "odex" / "system" / app_name
        if not app_dir.exists():
            # try system fallback
            alt = BASE_DIR / "system" / app_name
            if alt.exists():
                app_dir = alt
            else:
                app_dir.mkdir(parents=True, exist_ok=True)
        target = app_dir / file
        if target.exists():
            return JsonResponse({"error": "file already exists"}, status=400)
        import xml.etree.ElementTree as ET, xml.dom.minidom
        fml = ET.Element("fml")
        md = ET.SubElement(fml, "fml_metadata")
        md.set("name", file.replace(".fmlk","").replace(".fml",""))
        md.set("displayName", displayName)
        md.set("table", table)
        md.set("category", category)
        if description:
            md.set("description", description)
        md.set("connection", "ORCL_PROD")
        md.set("schema", "HR_SYS")
        tabs_el = ET.SubElement(fml, "tabs")
        tab = ET.SubElement(tabs_el, "tab")
        tab.set("id", "main")
        tab.set("name", "البيانات الأساسية")
        tab.set("alias", "البيانات الأساسية")
        tab.set("sort_order", "1")
        fields_el = ET.SubElement(fml, "fields")
        field = ET.SubElement(fields_el, "field")
        field.set("id", "1")
        field.set("name", "field1")
        field.set("alias", "حقل 1")
        field.set("inputType", "text")
        field.set("tab", "main")
        field.set("category", "عام")
        raw = ET.tostring(fml, encoding="utf-8")
        pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        pretty = "\n".join([l for l in pretty.split("\n") if l.strip()])
        target.write_text(pretty, encoding="utf-8")
        # Create table in DB (HR_SYS.<table>) for immediate use
        try:
            import psycopg2
            conn = psycopg2.connect(dbname="urs", user="postgres", password="postgres", host="172.16.10.101", port=5432)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute('CREATE SCHEMA IF NOT EXISTS "HR_SYS";')
            # sanitize table
            tbl = "".join(c for c in table if c.isalnum() or c=="_")
            if tbl:
                cur.execute(f'CREATE TABLE IF NOT EXISTS "HR_SYS"."{tbl}" (id SERIAL PRIMARY KEY, field1 TEXT);')
            conn.close()
        except Exception:
            pass
        return JsonResponse({"ok": True, "file": file, "path": str(target)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

def _norm_join_type(v) -> str:
    """Normalize one_one/one_many variants -> one_to_one / one_to_many (RML columns/detail)."""
    s = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in ("one_to_many", "one_many", "1_n", "1m", "many", "o2m"):
        return "one_to_many"
    return "one_to_one"


def _write_table_opts_el(rml, ET, table_opts):
    """Write <table_opts><table name conn is_default is_sub/> (designer table roles)."""
    if not table_opts:
        return
    try:
        opts_el = ET.SubElement(rml, "table_opts")
        for t in (table_opts or []):
            if not isinstance(t, dict):
                continue
            nm = str(t.get("name") or t.get("table") or "").strip()
            if not nm:
                continue
            t_el = ET.SubElement(opts_el, "table")
            t_el.set("name", nm)
            conn = str(t.get("conn") or t.get("conn_id") or t.get("connection_id") or "").strip()
            if conn:
                t_el.set("conn", conn)
            if t.get("is_default", t.get("isDefault", t.get("default", False))):
                t_el.set("is_default", "1")
            if t.get("is_sub", t.get("isSub", t.get("sub", False))):
                t_el.set("is_sub", "1")
    except Exception:
        pass


def _write_links_el(rml, ET, links):
    """Write <links><link from_table from_col to_table to_col rel_type/> (data diagram)."""
    if not links:
        return
    links_el = ET.SubElement(rml, "links")
    for idx, l in enumerate(links, start=1):
        ft = (l.get("from_table") or l.get("fromTable") or "").strip()
        tt = (l.get("to_table") or l.get("toTable") or "").strip()
        if not ft or not tt:
            continue
        el = ET.SubElement(links_el, "link")
        el.set("id", str(l.get("id", idx)))
        el.set("from_table", ft)
        el.set("from_col", str(l.get("from_col") or l.get("fromCol") or ""))
        el.set("to_table", tt)
        el.set("to_col", str(l.get("to_col") or l.get("toCol") or ""))
        if l.get("from_conn") or l.get("fromConn"):
            el.set("from_conn", str(l.get("from_conn") or l.get("fromConn")))
        if l.get("to_conn") or l.get("toConn"):
            el.set("to_conn", str(l.get("to_conn") or l.get("toConn")))
        rel = (l.get("rel_type") or l.get("relType") or l.get("rel") or "").strip().lower().replace("-", "_")
        if rel in ("one_to_one", "one_one", "one_to_many", "one_many"):
            rel = "one_to_one" if rel in ("one_to_one", "one_one") else "one_to_many"
            el.set("rel_type", rel)


def _write_groups_el(rml, ET, groups):
    """Write <groups><group id name order><column alias/>...</group></groups> (header spanning)."""
    if not groups:
        return
    try:
        ordered = sorted(list(groups), key=lambda g: (int(g.get("order", 0) or 0), str(g.get("id", ""))))
    except Exception:
        ordered = list(groups)
    groups_el = ET.SubElement(rml, "groups")
    for idx, g in enumerate(ordered, start=1):
        name = str(g.get("name", "") or "").strip()
        members = [str(a).strip() for a in (g.get("columns") or []) if str(a).strip()]
        if not name or not members:
            continue
        try:
            order = int(g.get("order", idx) or idx)
        except (TypeError, ValueError):
            order = idx
        el = ET.SubElement(groups_el, "group")
        el.set("id", str(g.get("id", idx)))
        el.set("name", name)
        el.set("order", str(order))
        try:
            _lv = max(1, min(int(g.get("level", 1) or 1), 5))
        except (TypeError, ValueError):
            _lv = 1
        if _lv > 1:
            el.set("level", str(_lv))
        for alias in members:
            c = ET.SubElement(el, "column")
            c.set("alias", alias)


def _write_distinct_attr(col_el, col):
    """Persist per-column dedup flag (منع التكرار — DISTINCT ON)."""
    try:
        if col.get("is_distinct", col.get("isDistinct", col.get("distinct", False))):
            col_el.set("distinct", "1")
    except Exception:
        pass


def _write_col_format(col_el, col):
    """Persist display formatting from the designer type modal (text/number/date)."""
    try:
        mc = col.get("max_chars", col.get("maxChars", None))
        try:
            mc = int(str(mc).strip()) if str(mc or "").strip() != "" else None
        except Exception:
            mc = None
        if mc is not None and mc > 0:
            col_el.set("max_chars", str(mc))
        if col.get("wrap", False):
            col_el.set("wrap", "1")
        dc = col.get("decimals", None)
        try:
            dc = int(str(dc).strip()) if str(dc or "").strip() != "" else None
        except Exception:
            dc = None
        if dc is not None and dc >= 0:
            col_el.set("decimals", str(max(0, min(dc, 6))))
        df = str(col.get("date_format") or col.get("dateFormat") or "").strip()
        if df:
            col_el.set("date_format", df)
    except Exception:
        pass


def _write_status_children(col_el, ET, col):
    """Write status flag + <status value label color/> children for a status column."""
    try:
        sm = col.get("status_map", col.get("statusMap", [])) or []
        is_st = col.get("is_status", col.get("isStatus", False))
        if sm and not is_st:
            is_st = True
        if is_st:
            col_el.set("is_status", "1")
        for st in sm:
            if not isinstance(st, dict):
                continue
            val = str(st.get("value", "") or "")
            lab = str(st.get("label", "") or val)
            clr = str(st.get("color", "") or "")
            if val == "" and lab == "":
                continue
            s_el = ET.SubElement(col_el, "status")
            s_el.set("value", val)
            s_el.set("label", lab)
            if clr:
                s_el.set("color", clr)
    except Exception:
        pass


def _write_detail_el(rml, ET, detail):
    """Write <detail table master detail><column/>...</detail> from wizard payload."""
    if not detail:
        return
    table = (detail.get("table") or "").strip()
    master = (detail.get("master") or detail.get("masterKey") or "").strip()
    dkey = (detail.get("detail") or detail.get("detailKey") or "").strip()
    if not table or not master or not dkey:
        return
    det_el = ET.SubElement(rml, "detail")
    det_el.set("table", table)
    det_el.set("master", master)
    det_el.set("detail", dkey)
    _drel = (detail.get("rel_type") or detail.get("relType") or detail.get("rel") or "one_to_many").strip().lower().replace("-", "_")
    det_el.set("rel_type", "one_to_one" if _drel in ("one_to_one", "one_one") else "one_to_many")
    for idx, col in enumerate(detail.get("columns", []) or [], start=1):
        col_el = ET.SubElement(det_el, "column")
        col_el.set("id", str(col.get("id", str(idx))))
        col_el.set("name", col.get("name", f"col{idx}"))
        col_el.set("alias", col.get("alias", col.get("name", f"col{idx}")))
        col_el.set("expr", col.get("expr", col.get("name", f"col{idx}")))
        ctype = col.get("col_type", col.get("type", "direct"))
        if ctype and ctype != "direct":
            col_el.set("col_type", ctype)
        if col.get("refTable") or col.get("ref_table"):
            col_el.set("refTable", col.get("refTable") or col.get("ref_table"))
        if col.get("refFk") or col.get("ref_fk"):
            col_el.set("refFk", col.get("refFk") or col.get("ref_fk"))
        if col.get("refDisplay") or col.get("ref_display"):
            col_el.set("refDisplay", col.get("refDisplay") or col.get("ref_display"))
        _cjt = (col.get("join_type") or col.get("joinType") or col.get("rel_type") or col.get("relType") or "")
        if _cjt:
            _cjt = str(_cjt).strip().lower().replace("-", "_")
            col_el.set("join_type", "one_to_many" if _cjt in ("one_to_many", "one_many") else "one_to_one")
        if col.get("data_type") or col.get("dataType"):
            col_el.set("dataType", col.get("data_type") or col.get("dataType"))
        _write_col_format(col_el, col)
        if col.get("is_amount") or col.get("isAmount"):
            col_el.set("is_amount", "1")
        if col.get("currency_field") or col.get("currencyField"):
            col_el.set("currency_field", str(col.get("currency_field") or col.get("currencyField")))
        if col.get("icon"):
            col_el.set("icon", str(col.get("icon")))


def _write_rule_element(r_el, r, idx=1):
    """كتابة <rule> كاملة: تعريفية + مصادر + متغيرات + سياسات (تُستخدم في الإنشاء والتحديث)."""
    r_el.set("id", str(r.get("id", idx)))
    rname = str(r.get("name", f"rule{idx}")).strip()
    if not rname:
        return False
    r_el.set("name", rname)
    if r.get("display") or r.get("displayName"):
        r_el.set("display", str(r.get("display") or r.get("displayName")))
    if r.get("icon"):
        r_el.set("icon", str(r.get("icon")))
    if r.get("description"):
        r_el.set("description", str(r.get("description")))
    if r.get("expr") or r.get("expression"):
        r_el.set("expr", str(r.get("expr") or r.get("expression")))
    rconn = r.get("connection_id") or r.get("conn_id") or r.get("connectionId")
    if rconn:
        r_el.set("connection_id", str(rconn))
    if r.get("type"):
        r_el.set("type", str(r.get("type")))
    if r.get("message"):
        r_el.set("message", str(r.get("message")))
    if r.get("value") is not None:
        r_el.set("value", str(r.get("value")))
    srcs = r.get("sources") or []
    if srcs:
        import xml.etree.ElementTree as _ET
        s_el = _ET.SubElement(r_el, "sources")
        for s in srcs:
            s = str(s or "").strip()
            if s:
                _ET.SubElement(s_el, "source").set("field", s)
    for v in (r.get("variables") or []):
        vname = str(v.get("name", "")).strip()
        if not vname:
            continue
        import xml.etree.ElementTree as _ET2
        _vparent = r_el.find("variables")
        if _vparent is None:
            _vparent = _ET2.SubElement(r_el, "variables")
        v_el = _ET2.SubElement(_vparent, "variable")
        v_el.set("name", vname)
        v_el.set("display", str(v.get("display") or vname))
        v_el.set("type", str(v.get("type") or "text"))
        for o in (v.get("options") or []):
            o = str(o or "").strip()
            if o:
                _opt = _ET2.SubElement(v_el, "opt")
                _opt.text = o
    for p in (r.get("policies") or []):
        pid = str(p.get("id") or p.get("name") or "").strip()
        if not pid:
            continue
        import xml.etree.ElementTree as _ET3
        _pparent = r_el.find("policies")
        if _pparent is None:
            _pparent = _ET3.SubElement(r_el, "policies")
        p_el = _ET3.SubElement(_pparent, "policy")
        p_el.set("id", pid)
        if p.get("name"):
            p_el.set("name", str(p.get("name")))
        # الأولوية: الأصغر = أعلى أولوية = يُقيّم أولاً ويفوز عند التداخل
        try:
            _pr = int(p.get("priority", 100))
        except (TypeError, ValueError):
            _pr = 100
        p_el.set("priority", str(_pr))
        # الافتراضية: واحدة لكل قاعدة — قيمها هي ELSE عند عدم مطابقة أي سياسة
        if p.get("is_default"):
            p_el.set("is_default", "1")
        if p.get("match"):
            m_el = _ET3.SubElement(p_el, "match")
            for fld, vals in (p.get("match") or {}).items():
                vals = [str(v) for v in (vals or []) if str(v or "").strip() != ""]
                if not str(fld or "").strip() or not vals:
                    continue
                on_el = _ET3.SubElement(m_el, "on")
                on_el.set("field", str(fld).strip())
                for vv in vals:
                    _v = _ET3.SubElement(on_el, "v")
                    _v.text = vv
        if p.get("values"):
            vv_el = _ET3.SubElement(p_el, "values")
            for vn, val in (p.get("values") or {}).items():
                if not str(vn or "").strip():
                    continue
                _x = _ET3.SubElement(vv_el, "v")
                _x.set("name", str(vn).strip())
                _x.text = "" if val is None else str(val)
    return True


def _render_rml_xml(prog_name, displayName, icon, category, schema, description, namespace,
                    connections, fields, columns, charts, rules, report_type="master", detail=None, links=None, doc_template=None, groups=None, distinct=False, table_opts=None, group_levels=1, general_where=None, extra_meta=None):
    """Build pretty RML XML from wizard payload (shared by create/update)."""
    import xml.etree.ElementTree as ET, xml.dom.minidom
    rml = ET.Element("rml")
    md = ET.SubElement(rml, "rpt_metadata")
    md.set("name", prog_name)
    md.set("displayName", displayName)
    md.set("category", category)
    md.set("schema", schema)
    md.set("icon", icon)
    if (report_type or "master").strip().lower() in ("detail", "doc"):
        md.set("type", (report_type or "master").strip().lower())
    if description:
        md.set("description", description)
    md.set("connection", "ORCL_PROD")
    if distinct:
        md.set("distinct", "1")
    try:
        _gl = max(1, min(int(group_levels or 1), 5))
    except (TypeError, ValueError):
        _gl = 1
    if _gl > 1:
        md.set("group_levels", str(_gl))
    if namespace:
        md.set("namespace", namespace)
    # Preserve unknown metadata attrs across designer saves (e.g. stage_max_rows)
    try:
        for _k, _v in (extra_meta or {}).items():
            if _k and _v is not None:
                md.set(str(_k), str(_v))
    except Exception:
        pass
    # rml_connections — auto-numbered id, single canonical connection_id attr
    if connections:
        conn_el = ET.SubElement(rml, "rml_connections")
        for idx, conn_id in enumerate(connections, start=1):
            c = ET.SubElement(conn_el, "connection")
            c.set("id", str(idx))
            c.set("connection_id", str(conn_id))
    # rml_chart — multiple charts
    if charts:
        chart_container = ET.SubElement(rml, "rml_chart")
        for idx, ch in enumerate(charts, start=1):
            c = ET.SubElement(chart_container, "chart")
            c.set("id", str(ch.get("id", idx)))
            c.set("type", ch.get("type", "bar"))
            if ch.get("x"): c.set("x", ch.get("x"))
            if ch.get("y"): c.set("y", ch.get("y"))
            if ch.get("y2"): c.set("y2", ch.get("y2"))
            if ch.get("title"): c.set("title", ch.get("title"))
    # rules — computed business rules, addressable cross-file as ns.name
    # + rule variables/policies ($rule.var$ per-record values)
    if rules:
        rules_el = ET.SubElement(rml, "rules")
        for idx, r in enumerate(rules, start=1):
            r_el = ET.SubElement(rules_el, "rule")
            _write_rule_element(r_el, r, idx)
    # fields — base definitions, name MUST equal DB column
    if fields:
        fields_el = ET.SubElement(rml, "fields")
        for idx, f in enumerate(fields, start=1):
            f_el = ET.SubElement(fields_el, "field")
            f_el.set("id", str(f.get("id", idx)))
            fname = str(f.get("name", "")).strip()
            if not fname:
                continue
            f_el.set("name", fname)
            if f.get("type") or f.get("dataType") or f.get("data_type"):
                f_el.set("type", str(f.get("type") or f.get("dataType") or f.get("data_type")))
            conn_val = f.get("conn_id") or f.get("connection_id") or f.get("connectionId") or f.get("connection")
            if conn_val:
                f_el.set("conn_id", str(conn_val))
            ts = f.get("table_source") or f.get("tableSource") or f.get("table")
            if ts:
                f_el.set("table_source", str(ts))
    # columns — expression + display only; col_type written only when non-direct (compiler infers)
    if not columns:
        columns = [{"id": "1", "name": "id", "alias": "المعرف", "expr": "id"}]
    cols_el = ET.SubElement(rml, "columns")
    for idx, col in enumerate(columns, start=1):
        col_el = ET.SubElement(cols_el, "column")
        cid = col.get("id", str(idx))
        name = col.get("name", f"col{idx}")
        alias = col.get("alias", name)
        expr = col.get("expr", name)
        ctype = col.get("col_type", col.get("type", "direct"))
        col_el.set("id", str(cid))
        col_el.set("name", name)
        col_el.set("alias", alias)
        col_el.set("expr", expr)
        if ctype and ctype != "direct":
            col_el.set("col_type", ctype)
        if col.get("data_type") or col.get("dataType"):
            col_el.set("dataType", col.get("data_type") or col.get("dataType"))
        _write_col_format(col_el, col)
        if col.get("refTable") or col.get("ref_table"):
            col_el.set("refTable", col.get("refTable") or col.get("ref_table"))
        if col.get("refFk") or col.get("ref_fk"):
            col_el.set("refFk", col.get("refFk") or col.get("ref_fk"))
        if col.get("refDisplay") or col.get("ref_display"):
            col_el.set("refDisplay", col.get("refDisplay") or col.get("ref_display"))
        if col.get("refTables") or col.get("ref_tables"):
            import json as _json
            try:
                col_el.set("refTables", _json.dumps(col.get("refTables") or col.get("ref_tables"), ensure_ascii=False))
            except Exception:
                pass
        _jt = col.get("join_type") or col.get("joinType") or col.get("rel_type") or col.get("relType")
        if _jt:
            col_el.set("join_type", _norm_join_type(_jt))
        if col.get("connection_id") or col.get("connectionId"):
            cid_val = col.get("connection_id") or col.get("connectionId")
            col_el.set("connection_id", str(cid_val))
        if col.get("where_clause") or col.get("whereClause") or col.get("where"):
            col_el.set("where_clause", str(col.get("where_clause") or col.get("whereClause") or col.get("where")))
        if col.get("icon"):
            col_el.set("icon", str(col.get("icon")))
        if col.get("is_amount") or col.get("isAmount"):
            col_el.set("is_amount", "1")
        if col.get("currency_field") or col.get("currencyField"):
            col_el.set("currency_field", str(col.get("currency_field") or col.get("currencyField")))
        _write_distinct_attr(col_el, col)
        _write_status_children(col_el, ET, col)
    _write_detail_el(rml, ET, detail)
    _write_links_el(rml, ET, links)
    _write_table_opts_el(rml, ET, table_opts)
    # groups — column header spanning: <groups><group name order><column alias/>
    _write_groups_el(rml, ET, groups)
    # doc_template — print layout for doc-type reports ({{column_alias}} vars)
    if doc_template and str(doc_template).strip():
        dt_el = ET.SubElement(rml, "doc_template")
        dt_el.text = str(doc_template)
    # general_where — report-level filter ANDed into master WHERE
    if general_where and str(general_where).strip():
        gw_el = ET.SubElement(rml, "general_where")
        gw_el.text = str(general_where)
    raw = ET.tostring(rml, encoding="utf-8")
    pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    return "\n".join([l for l in pretty.split("\n") if l.strip()])


_RML_REF3_RE = _re_pg.compile(r"\[([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\]|\{([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\}")
_RML_REF2_RE = _re_pg.compile(r"\[([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\]|\{([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\}")


def _sync_rml_table_refs(fields, columns, detail=None, general_where=""):
    """وحّد مراجع الأعمدة مع table_source الحالي للحقول.

    عند تغيير جدول حقل (مثلاً att ← iot_zk_att) تُعاد كتابة [conn.table.field]
    و[table.field] تلقائياً — الملف يتحدث مع الحقول عند كل حفظ.
    يُعاد الكتابة فقط عندما يكون للحقل جدول واحد لا لبس فيه.
    Returns (columns, general_where)."""
    table_of, multi = {}, set()
    for f in (fields or []):
        if not isinstance(f, dict):
            continue
        nm = str(f.get("name") or "").strip().lower()
        ts = str(f.get("table_source") or f.get("tableSource") or f.get("table") or "").strip()
        short = ts.split(".")[-1].strip() if ts else ""
        if not nm or not short:
            continue
        if nm in table_of and table_of[nm].lower() != short.lower():
            multi.add(nm)
        table_of.setdefault(nm, short)

    def fix(expr):
        if not isinstance(expr, str) or ("[" not in expr and "{" not in expr):
            return expr

        def r3(m):
            g = m.groups()
            if g[0] is not None:
                conn, tbl, fld, _o, _c = g[0], g[1], g[2], "[", "]"
            else:
                conn, tbl, fld, _o, _c = g[3], g[4], g[5], "{", "}"
            want = table_of.get(fld.lower())
            if want and fld.lower() not in multi and want.lower() != tbl.lower():
                return f"{_o}{conn}.{want}.{fld}{_c}"
            return m.group(0)

        def r2(m):
            g = m.groups()
            if g[0] is not None:
                tbl, fld, _o, _c = g[0], g[1], "[", "]"
            else:
                tbl, fld, _o, _c = g[2], g[3], "{", "}"
            want = table_of.get(fld.lower())
            if want and fld.lower() not in multi and want.lower() != tbl.lower():
                return f"{_o}{want}.{fld}{_c}"
            return m.group(0)

        expr = _RML_REF3_RE.sub(r3, expr)
        return _RML_REF2_RE.sub(r2, expr)

    for col in (columns or []):
        if not isinstance(col, dict):
            continue
        if col.get("expr"):
            col["expr"] = fix(col["expr"])
        wc = col.get("where_clause") or col.get("whereClause")
        if wc:
            key = "where_clause" if col.get("where_clause") else "whereClause"
            col[key] = fix(wc)
    try:
        dcols = (detail or {}).get("columns") if isinstance(detail, dict) else None
        for col in (dcols or []):
            if isinstance(col, dict) and col.get("expr"):
                col["expr"] = fix(col["expr"])
    except Exception:
        pass
    try:
        general_where = fix(general_where) if isinstance(general_where, str) and general_where else general_where
    except Exception:
        pass
    return columns, general_where


def _infer_rml_schema(fields, connections, provided=""):
    """السكيما من الجداول الفعلية — لا HR_SYS ثابتة.

    1) بادئة schema.table صريحة في أي table_source
    2) قيمة محفوظة/ممررة غير فارغة (استقرار التقارير القائمة عند التعديل)
    3) اتصال IoT ← سكيما المرآة المحلية (public غالباً)
    4) اتصال قاعدة بيانات ← حقل schema للاتصال
    5) جداول عارية على postgres ← السكيما الفعلية من information_schema
    """
    bare_tables = []
    for f in (fields or []):
        if not isinstance(f, dict):
            continue
        ts = str(f.get("table_source") or f.get("tableSource") or f.get("table") or "").strip()
        if "." in ts:
            head = ts.split(".")[0].strip().strip('"')
            if head:
                return head
        elif ts:
            bare_tables.append(ts.split(".")[-1])
    if (provided or "").strip():
        return provided.strip()
    objs = []
    try:
        from .models import Connection
        for cid in (connections or []):
            try:
                obj = Connection.objects.filter(id=int(str(cid))).first()
            except (TypeError, ValueError):
                obj = None
            if obj is not None:
                objs.append(obj)
    except Exception:
        pass
    for obj in objs:
        try:
            if _iot_is_conn(obj):
                try:
                    from .models import IoTMirror
                    from .iot_sync import mirror_schema
                    m = IoTMirror.objects.select_related("local_connection").filter(
                        connection=obj).first()
                    if m is not None and m.local_connection_id:
                        return (mirror_schema(m.local_connection) or "").strip() or "public"
                except Exception:
                    pass
                return "public"
            sch = (getattr(obj, "schema", "") or "").strip()
            if sch:
                return sch
        except Exception:
            continue
    # جداول عارية + postgres بلا سكيما: اسأل قاعدة البيانات نفسها
    if bare_tables:
        for obj in objs:
            try:
                if str(getattr(obj, "engine", "") or "").lower() != "postgres":
                    continue
                import psycopg2
                pg = psycopg2.connect(dbname=obj.instance or "urs", user=obj.user,
                                      password=obj.password, host=obj.host,
                                      port=int(obj.port or 5432), connect_timeout=5)
                try:
                    cur = pg.cursor()
                    cur.execute(
                        "SELECT table_schema FROM information_schema.tables "
                        "WHERE table_name = ANY(%s) AND table_schema NOT IN "
                        "('pg_catalog','information_schema') "
                        "ORDER BY CASE WHEN table_schema='public' THEN 0 ELSE 1 END "
                        "LIMIT 1", (list(dict.fromkeys(bare_tables)),))
                    row = cur.fetchone()
                    cur.close()
                finally:
                    try:
                        pg.close()
                    except Exception:
                        pass
                if row and row[0]:
                    return str(row[0])
            except Exception:
                continue
    return (provided or "").strip()


def _resolve_rml_target(app_name, file):
    """Resolve app dir + target path for an .rml file (guarded). Returns (app_dir, target, error)."""
    file = (file or "").strip()
    if not file:
        return None, None, "file required"
    if not file.endswith(".rml"):
        file += ".rml"
    if "/" in file or "\\" in file or ".." in file:
        return None, None, "invalid file name"
    app_dir = BASE_DIR / "odex" / "system" / app_name
    if not app_dir.exists():
        alt = BASE_DIR / "system" / app_name
        if alt.exists():
            app_dir = alt
        else:
            app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir, app_dir / file, None


@csrf_exempt
def api_create_rml(request, app_name):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        file = data.get("file", "").strip()
        # Support both old (displayName) and new (name + icon) basic info
        prog_name = data.get("name", "").strip() or file.replace(".rml","")
        displayName = data.get("displayName", "").strip() or prog_name
        icon = data.get("icon", "").strip() or "fa-chart-bar"
        category = data.get("category", "").strip() or "HR"
        schema = data.get("schema", "").strip()
        description = data.get("description", "").strip()
        # New: connections (auto-numbered ids), fields + columns with full objects, charts, rules
        connections = data.get("connections", [])  # list of connection_id (int/str) from connections table
        fields = data.get("fields", [])  # <field name(type db col) type conn_id table_source>
        columns = data.get("columns", [])
        charts = data.get("charts", [])
        rules = data.get("rules", [])  # <rule id name expr connection_id> — addressable as ns.name
        namespace = data.get("namespace", "").strip() or None
        # Backward compat: columns may be list of strings
        if columns and isinstance(columns[0], str):
            # Convert to objects
            clean = []
            for idx, c in enumerate(columns, start=1):
                cc = "".join(ch for ch in c.strip() if ch.isalnum() or ch == "_")
                if cc:
                    clean.append({"id": str(idx), "name": cc, "alias": cc, "expr": cc, "col_type": "direct"})
            columns = clean
        # مراجع الأعمدة تتبع table_source الحالي للحقول (att ← iot_zk_att...)
        _, _gw_sync = _sync_rml_table_refs(fields, columns, data.get("detail"), data.get("general_where", data.get("generalWhere", "")))
        if _gw_sync:
            data["general_where"] = _gw_sync
        # السكيما من الجداول/الاتصال — لا HR_SYS ثابتة (المرايا في public)
        schema = _infer_rml_schema(fields, connections, schema) or "HR_SYS"
        if not file:
            return JsonResponse({"error": "file required"}, status=400)
        if not file.endswith(".rml"):
            file += ".rml"
        if "/" in file or "\\" in file or ".." in file:
            return JsonResponse({"error": "invalid file name"}, status=400)
        app_dir = BASE_DIR / "odex" / "system" / app_name
        if not app_dir.exists():
            alt = BASE_DIR / "system" / app_name
            if alt.exists():
                app_dir = alt
            else:
                app_dir.mkdir(parents=True, exist_ok=True)
        target = app_dir / file
        if target.exists():
            return JsonResponse({"error": "file already exists"}, status=400)
        import xml.etree.ElementTree as ET, xml.dom.minidom
        rml = ET.Element("rml")
        md = ET.SubElement(rml, "rpt_metadata")
        md.set("name", prog_name)
        md.set("displayName", displayName)
        md.set("category", category)
        md.set("schema", schema)
        md.set("icon", icon)
        if description:
            md.set("description", description)
        md.set("connection", "ORCL_PROD")
        if data.get("distinct"):
            md.set("distinct", "1")
        try:
            _gl = max(1, min(int(data.get("group_levels", data.get("groupLevels", 1)) or 1), 5))
        except (TypeError, ValueError):
            _gl = 1
        if _gl > 1:
            md.set("group_levels", str(_gl))
        _crt_type = (data.get("report_type", data.get("type", "master")) or "master")
        if str(_crt_type).strip().lower() in ("detail", "doc"):
            md.set("type", str(_crt_type).strip().lower())
        if namespace:
            md.set("namespace", namespace)
        # rml_connections — auto-numbered id, single canonical connection_id attr
        if connections:
            conn_el = ET.SubElement(rml, "rml_connections")
            for idx, conn_id in enumerate(connections, start=1):
                c = ET.SubElement(conn_el, "connection")
                c.set("id", str(idx))
                c.set("connection_id", str(conn_id))
        # rml_chart — multiple charts
        if charts:
            chart_container = ET.SubElement(rml, "rml_chart")
            for idx, ch in enumerate(charts, start=1):
                c = ET.SubElement(chart_container, "chart")
                c.set("id", str(ch.get("id", idx)))
                c.set("type", ch.get("type", "bar"))
                if ch.get("x"): c.set("x", ch.get("x"))
                if ch.get("y"): c.set("y", ch.get("y"))
                if ch.get("title"): c.set("title", ch.get("title"))
        # rules — computed business rules, addressable cross-file as ns.name
        # + rule variables/policies ($rule.var$ per-record values)
        if rules:
            rules_el = ET.SubElement(rml, "rules")
            for idx, r in enumerate(rules, start=1):
                r_el = ET.SubElement(rules_el, "rule")
                _write_rule_element(r_el, r, idx)
        # fields — base definitions, name MUST equal DB column
        if fields:
            fields_el = ET.SubElement(rml, "fields")
            for idx, f in enumerate(fields, start=1):
                f_el = ET.SubElement(fields_el, "field")
                f_el.set("id", str(f.get("id", idx)))
                fname = str(f.get("name", "")).strip()
                if not fname:
                    continue
                f_el.set("name", fname)
                if f.get("type") or f.get("dataType") or f.get("data_type"):
                    f_el.set("type", str(f.get("type") or f.get("dataType") or f.get("data_type")))
                conn_val = f.get("conn_id") or f.get("connection_id") or f.get("connectionId") or f.get("connection")
                if conn_val:
                    f_el.set("conn_id", str(conn_val))
                ts = f.get("table_source") or f.get("tableSource") or f.get("table")
                if ts:
                    f_el.set("table_source", str(ts))
        # columns with connection_id + where_clause + icon
        if not columns:
            columns = [{"id": "1", "name": "id", "alias": "المعرف", "expr": "id", "col_type": "direct"}]
        cols_el = ET.SubElement(rml, "columns")
        for idx, col in enumerate(columns, start=1):
            col_el = ET.SubElement(cols_el, "column")
            # col may be dict with various keys
            cid = col.get("id", str(idx))
            name = col.get("name", f"col{idx}")
            alias = col.get("alias", name)
            expr = col.get("expr", name)
            ctype = col.get("col_type", col.get("type", "direct"))
            col_el.set("id", str(cid))
            col_el.set("name", name)
            col_el.set("alias", alias)
            col_el.set("expr", expr)
            col_el.set("col_type", ctype)
            if col.get("data_type") or col.get("dataType"):
                col_el.set("dataType", col.get("data_type") or col.get("dataType"))
            _write_col_format(col_el, col)
            if col.get("refTable") or col.get("ref_table"):
                col_el.set("refTable", col.get("refTable") or col.get("ref_table"))
            if col.get("refFk") or col.get("ref_fk"):
                col_el.set("refFk", col.get("refFk") or col.get("ref_fk"))
            if col.get("refDisplay") or col.get("ref_display"):
                col_el.set("refDisplay", col.get("refDisplay") or col.get("ref_display"))
            _cjt = col.get("join_type") or col.get("joinType") or col.get("rel_type") or col.get("relType")
            if _cjt or col.get("refTable") or col.get("ref_table"):
                col_el.set("join_type", _norm_join_type(_cjt or "one_to_one"))
            if col.get("connection_id") or col.get("connectionId"):
                cid_val = col.get("connection_id") or col.get("connectionId")
                col_el.set("connection_id", str(cid_val))
            if col.get("where_clause") or col.get("whereClause") or col.get("where"):
                col_el.set("where_clause", str(col.get("where_clause") or col.get("whereClause") or col.get("where")))
            if col.get("icon"):
                col_el.set("icon", str(col.get("icon")))
            if col.get("is_amount") or col.get("isAmount"):
                col_el.set("is_amount", "1")
            if col.get("currency_field") or col.get("currencyField"):
                col_el.set("currency_field", str(col.get("currency_field") or col.get("currencyField")))
            _write_distinct_attr(col_el, col)
            _write_status_children(col_el, ET, col)
        _write_detail_el(rml, ET, data.get("detail"))
        _write_links_el(rml, ET, data.get("links", []))
        _write_table_opts_el(rml, ET, data.get("table_opts", data.get("tableOpts", [])))
        _write_groups_el(rml, ET, data.get("groups", []))
        _dt = data.get("doc_template", data.get("docTemplate", ""))
        if _dt and str(_dt).strip():
            _dt_el = ET.SubElement(rml, "doc_template")
            _dt_el.text = str(_dt)
        _gw = data.get("general_where", data.get("generalWhere", ""))
        if _gw and str(_gw).strip():
            _gw_el = ET.SubElement(rml, "general_where")
            _gw_el.text = str(_gw)
        raw = ET.tostring(rml, encoding="utf-8")
        pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        pretty = "\n".join([l for l in pretty.split("\n") if l.strip()])
        target.write_text(pretty, encoding="utf-8")
        return JsonResponse({"ok": True, "file": target.name, "path": str(target), "updated": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _rml_extra_meta_attrs(path):
    """Unknown rpt_metadata attrs on an existing file (preserved across saves)."""
    try:
        import xml.etree.ElementTree as ET
        md = ET.parse(str(path)).getroot().find("rpt_metadata")
        if md is None:
            return {}
        known = {"name", "displayname", "category", "schema", "icon", "description",
                 "connection", "distinct", "group_levels", "grouplevels", "namespace", "ns",
                 "type", "report_type", "reporttype", "doc_layout", "doclayout", "layout"}
        return {k: v for k, v in md.attrib.items() if k.lower() not in known}
    except Exception:
        return {}


def _rml_draft_name(file):
    base = (file or "").strip()
    if base.endswith(".rml"):
        base = base[:-4]
    return f"__draft_{base}.rml"


def _rml_normalize_payload(data, target_stem=None):
    """Shared create/update normalization: dup check, ref sync, schema infer. Mutates data."""
    fields = data.get("fields", [])
    columns = data.get("columns", [])
    try:
        _seen = set()
        for _f in (fields or []):
            if not isinstance(_f, dict):
                continue
            _nm = str(_f.get("name", "") or "").strip().lower()
            if not _nm:
                continue
            _tb = str(_f.get("table_source") or _f.get("tableSource") or "").strip().upper()
            _cx = str(_f.get("conn_id") or _f.get("connection_id") or "").strip()
            _k = (_nm, _tb, _cx)
            if _k in _seen:
                return None, (f"الحقل '{_f.get('name', '')}' مكرر في الجدول "
                              f"'{_f.get('table_source') or _f.get('tableSource') or ''}' على نفس الاتصال "
                              f"({_cx or '—'}) — تكرار نفس الجدول بنفس الاتصال غير مسموح")
            _seen.add(_k)
    except Exception:
        pass
    if columns and isinstance(columns[0], str):
        clean = []
        for idx, c in enumerate(columns, start=1):
            cc = "".join(ch for ch in c.strip() if ch.isalnum() or ch == "_")
            if cc:
                clean.append({"id": str(idx), "name": cc, "alias": cc, "expr": cc})
        data["columns"] = clean
    _, _gw_sync = _sync_rml_table_refs(data.get("fields", []), data.get("columns", []),
                                       data.get("detail"),
                                       data.get("general_where", data.get("generalWhere", "")))
    if _gw_sync:
        data["general_where"] = _gw_sync
    data["_schema"] = _infer_rml_schema(data.get("fields", []), data.get("connections", []),
                                        data.get("schema", "").strip()) or "HR_SYS"
    return data, None


@csrf_exempt
def api_rml_draft(request, app_name):
    """POST /api/apps/<app>/rml/draft/ — create/update body → writes __draft_*.rml + launches test job.

    Returns {draft, job}. The draft is finalized or discarded explicitly; never used directly.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        file = (data.get("file") or "").strip()
        _app_dir, _t, err = _resolve_rml_target(app_name, file)
        if err:
            return JsonResponse({"error": err}, status=400)
        data, derr = _rml_normalize_payload(data)
        if derr:
            return JsonResponse({"error": derr}, status=400)
        draft = _rml_draft_name(file)
        app_dir, target, err2 = _resolve_rml_target(app_name, draft)
        if err2:
            return JsonResponse({"error": err2}, status=400)
        prog_name = data.get("name", "").strip() or file.replace(".rml", "")
        _final_name = file if file.endswith(".rml") else file + ".rml"
        _keep = {}
        try:
            _ad0, _ft0, _fe0 = _resolve_rml_target(app_name, _final_name)
            if not _fe0 and _ft0.exists():
                _keep = _rml_extra_meta_attrs(_ft0)
        except Exception:
            _keep = {}
        pretty = _render_rml_xml(
            prog_name, data.get("displayName", "").strip() or prog_name,
            data.get("icon", "").strip() or "fa-chart-bar",
            data.get("category", "").strip() or "HR", data.get("_schema") or "HR_SYS",
            data.get("description", "").strip(), data.get("namespace", "").strip() or None,
            data.get("connections", []), data.get("fields", []), data.get("columns", []),
            data.get("charts", []), data.get("rules", []),
            (data.get("report_type", data.get("type", "master")) or "master"),
            data.get("detail"), data.get("links", []),
            data.get("doc_template", data.get("docTemplate", "")),
            data.get("groups", []), bool(data.get("distinct")),
            data.get("table_opts", data.get("tableOpts", [])),
            data.get("group_levels", data.get("groupLevels", 1)),
            data.get("general_where", data.get("generalWhere", "")), _keep)
        target.write_text(pretty, encoding="utf-8")
        job = _uuid.uuid4().hex[:16]
        with _RML_JOBS_LOCK:
            _RML_JOBS[job] = {"status": "queued", "progress": {"stage": "queued", "text": "في الانتظار…"},
                              "created": _time.time(), "done_at": None, "_last_prog": 0.0,
                              "rml": draft, "app": app_name}
        t = _th.Thread(target=_rml_job_run, args=(job, draft, app_name, {"page": 1, "pageSize": 5}),
                       daemon=True)
        t.start()
        return JsonResponse({"draft": draft, "job": job})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_rml_draft_finalize(request, app_name):
    """POST /api/apps/<app>/rml/draft/finalize/ {draft, mode: create|update} — test passed → publish."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        draft = (data.get("draft") or "").strip()
        mode = (data.get("mode") or "create").strip().lower()
        if not draft.startswith("__draft_") or ".." in draft or "/" in draft or "\\" in draft:
            return JsonResponse({"error": "invalid draft"}, status=400)
        app_dir, dt, err = _resolve_rml_target(app_name, draft)
        if err or not dt.exists():
            return JsonResponse({"error": "draft not found — أعد الحفظ"}, status=404)
        final_name = draft[len("__draft_"):]
        _ad, target, err2 = _resolve_rml_target(app_name, final_name)
        if err2:
            return JsonResponse({"error": err2}, status=400)
        if mode == "create" and target.exists():
            return JsonResponse({"error": "file already exists"}, status=400)
        if mode == "update" and not target.exists():
            return JsonResponse({"error": "file not found, use create"}, status=404)
        target.write_text(dt.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            dt.unlink()
        except Exception:
            pass
        return JsonResponse({"ok": True, "file": target.name, "path": str(target),
                             "updated": mode == "update"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_rml_draft_discard(request, app_name):
    """POST /api/apps/<app>/rml/draft/discard/ {draft} — test failed → delete draft."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        draft = (data.get("draft") or "").strip()
        if not draft.startswith("__draft_") or ".." in draft or "/" in draft or "\\" in draft:
            return JsonResponse({"error": "invalid draft"}, status=400)
        _ad, dt, err = _resolve_rml_target(app_name, draft)
        if not err and dt.exists():
            try:
                dt.unlink()
            except Exception:
                pass
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_rml_fetch_query(request, app_name):
    """POST /api/apps/<app>/rml/fetch-query/ — wizard state → compiled executable SQL.

    Body: same shape as draft (connections, fields, columns, links, table_opts,
    general_where, detail, rules, groups, ...). Renders to a temp __fetchq_*.rml,
    compiles through the normal pipeline (same DB routing as execute, no staging
    and no execution — safe for huge tables), returns {sql}. Temp file deleted.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    target = None
    try:
        data = json.loads(request.body.decode() or "{}")
        data, derr = _rml_normalize_payload(data)
        if derr:
            return JsonResponse({"error": derr}, status=400)
        if not data.get("connections"):
            return JsonResponse({"error": "اختر اتصالاً واحداً على الأقل"}, status=400)
        if not data.get("fields"):
            return JsonResponse({"error": "أضف حقلاً واحداً على الأقل"}, status=400)
        if not data.get("columns"):
            return JsonResponse({"error": "أضف عموداً واحداً على الأقل"}, status=400)
        prog_name = (data.get("name", "") or "").strip() or ("fetchq_" + _uuid.uuid4().hex[:8])
        pretty = _render_rml_xml(
            prog_name, data.get("displayName", "").strip() or prog_name,
            data.get("icon", "").strip() or "fa-chart-bar",
            data.get("category", "").strip() or "HR", data.get("_schema") or "HR_SYS",
            data.get("description", "").strip(), data.get("namespace", "").strip() or None,
            data.get("connections", []), data.get("fields", []), data.get("columns", []),
            data.get("charts", []), data.get("rules", []),
            (data.get("report_type", data.get("type", "master")) or "master"),
            data.get("detail"), data.get("links", []),
            data.get("doc_template", data.get("docTemplate", "")),
            data.get("groups", []), bool(data.get("distinct")),
            data.get("table_opts", data.get("tableOpts", [])),
            data.get("group_levels", data.get("groupLevels", 1)),
            data.get("general_where", data.get("generalWhere", "")), {})
        fname = "__fetchq_%s.rml" % _uuid.uuid4().hex[:12]
        _ad, target, err = _resolve_rml_target(app_name, fname)
        if err or target is None:
            return JsonResponse({"error": err or "bad app"}, status=400)
        target.write_text(pretty, encoding="utf-8")
        pipe = _rml_get_pipeline(fname, app_name)
        sql = pipe.rml_engine.preview_real_sql({"page": 1, "pageSize": 50})
        return JsonResponse({"ok": True, "sql": sql}, json_dumps_params={"ensure_ascii": False})
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    finally:
        try:
            if target is not None and target.exists():
                target.unlink()
        except Exception:
            pass


@csrf_exempt
def api_update_rml(request, app_name):
    """POST /api/apps/<app>/rml/update/ — overwrite an existing .rml file (report edit flow)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        file = data.get("file", "").strip()
        _app_dir, target, err = _resolve_rml_target(app_name, file)
        if err:
            return JsonResponse({"error": err}, status=400)
        if not target.exists():
            return JsonResponse({"error": "file not found, use create"}, status=404)
        prog_name = data.get("name", "").strip() or target.stem
        displayName = data.get("displayName", "").strip() or prog_name
        icon = data.get("icon", "").strip() or "fa-chart-bar"
        category = data.get("category", "").strip() or "HR"
        schema = data.get("schema", "").strip()
        description = data.get("description", "").strip()
        namespace = data.get("namespace", "").strip() or None
        connections = data.get("connections", [])
        fields = data.get("fields", [])
        columns = data.get("columns", [])
        # تكرار نفس الحقل + نفس الجدول + نفس الاتصال ممنوع (أما من اتصال آخر
        # فمقبول ويُدمج المحرك سجلات الاتصالين UNION)
        try:
            _seen = set()
            for _f in (fields or []):
                if not isinstance(_f, dict):
                    continue
                _nm = str(_f.get("name", "") or "").strip().lower()
                if not _nm:
                    continue
                _tb = str(_f.get("table_source") or _f.get("tableSource") or "").strip().upper()
                _cx = str(_f.get("conn_id") or _f.get("connection_id") or "").strip()
                _k = (_nm, _tb, _cx)
                if _k in _seen:
                    return JsonResponse({"error": (
                        f"الحقل '{_f.get('name', '')}' مكرر في الجدول "
                        f"'{_f.get('table_source') or _f.get('tableSource') or ''}' على نفس الاتصال "
                        f"({_cx or '—'}) — تكرار نفس الجدول بنفس الاتصال غير مسموح"
                    )}, status=400)
                _seen.add(_k)
        except Exception:
            pass
        if columns and isinstance(columns[0], str):
            clean = []
            for idx, c in enumerate(columns, start=1):
                cc = "".join(ch for ch in c.strip() if ch.isalnum() or ch == "_")
                if cc:
                    clean.append({"id": str(idx), "name": cc, "alias": cc, "expr": cc})
            columns = clean
        # مراجع الأعمدة تتبع table_source الحالي للحقول (att ← iot_zk_att...)
        _, _gw_sync = _sync_rml_table_refs(fields, columns, data.get("detail"), data.get("general_where", data.get("generalWhere", "")))
        if _gw_sync:
            data["general_where"] = _gw_sync
        # السكيما من الجداول/الاتصال — لا HR_SYS ثابتة (المرايا في public)
        schema = _infer_rml_schema(fields, connections, schema) or "HR_SYS"
        charts = data.get("charts", [])
        rules = data.get("rules", [])
        report_type = (data.get("report_type", data.get("type", "master")) or "master")
        detail = data.get("detail")
        links = data.get("links", [])
        groups = data.get("groups", [])
        doc_template = data.get("doc_template", data.get("docTemplate", ""))
        general_where = data.get("general_where", data.get("generalWhere", ""))
        pretty = _render_rml_xml(prog_name, displayName, icon, category, schema, description,
                                 namespace, connections, fields, columns, charts, rules,
                                 report_type, detail, links, doc_template, groups,
                                 bool(data.get("distinct")),
                                 data.get("table_opts", data.get("tableOpts", [])),
                                 data.get("group_levels", data.get("groupLevels", 1)),
                                 general_where, _rml_extra_meta_attrs(target))
        target.write_text(pretty, encoding="utf-8")
        return JsonResponse({"ok": True, "file": target.name, "path": str(target), "updated": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _sync_connections_both_ways():
    """Unify Django rows <-> PG urs_connection table (matched by name, fill gaps ONLY).

    Forms write PG; admin/API write Django — without this each side hides the other's
    rows (e.g. a form-added sqlserver connection invisible in the report designer).
    Never updates existing rows, never deletes. Returns (pg_to_dj, dj_to_pg).
    All failures swallowed (returns 0,0) — listing must never break.
    """
    import json as _js
    from .models import Connection
    try:
        dj = {c.name: c for c in Connection.objects.all()}
    except Exception:
        return 0, 0
    try:
        eff = _wizard_effective_conn()
        if eff is None or (eff.engine or "postgres") != "postgres" or not (eff.host or ""):
            return 0, 0
        sch = ((eff.schema if hasattr(eff, "schema") else "") or "").strip() or "main_hq_2026"
        import psycopg2
        pg = psycopg2.connect(dbname=(eff.instance or "urs"), user=eff.user,
                              password=eff.password or "", host=eff.host,
                              port=int(eff.port or 5432), connect_timeout=5)
        pg.autocommit = True
        cur = pg.cursor()
        try:
            cur.execute(f'SELECT * FROM "{sch}"."urs_connection"')
            cols = [d[0] for d in (cur.description or [])]
            pgrows = [dict(zip(cols, r)) for r in (cur.fetchall() or [])]
        except Exception:
            pg.rollback()
            return 0, 0
        pg_by_name = {r.get("name"): r for r in pgrows if r.get("name")}
        a = b = 0
        # PG -> Django (form-added rows appear in designer/admin)
        for nm, pr in pg_by_name.items():
            if nm in dj:
                continue
            try:
                vals = {"name": nm}
                for f in ("engine", "host", "user", "password", "instance", "instance_name",
                          "schema", "devices", "endpoint", "description", "conn_type"):
                    if f in pr and pr[f] is not None:
                        vals[f] = pr[f]
                try:
                    vals["port"] = int(pr.get("port") or 5432)
                except (TypeError, ValueError):
                    vals["port"] = 5432
                if pr.get("is_local") is not None:
                    vals["is_local"] = bool(pr.get("is_local"))
                if pr.get("is_queryable") is not None:
                    vals["is_queryable"] = bool(pr.get("is_queryable"))
                if isinstance(vals.get("devices"), (list, dict)):
                    vals["devices"] = _js.dumps(vals.get("devices"), ensure_ascii=False)
                Connection.objects.create(**vals)
                dj[nm] = True
                a += 1
            except Exception:
                continue
        # Django -> PG (admin/API-added rows appear in forms players)
        try:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name=%s", (sch, "urs_connection"))
            tcols = {r[0] for r in cur.fetchall()}
        except Exception:
            tcols = set()
        if tcols:
            for c in Connection.objects.all():
                if c.name in pg_by_name:
                    continue
                try:
                    payload = {"id": c.id, "name": c.name, "engine": c.engine, "host": c.host,
                               "port": c.port, "user": c.user, "password": c.password or "",
                               "instance": c.instance or "", "schema": c.schema or "",
                               "devices": c.devices if isinstance(c.devices, str) else _js.dumps(c.devices or []),
                               "endpoint": c.endpoint or "att", "description": c.description or "",
                               "conn_type": c.conn_type, "is_local": c.is_local, "is_queryable": c.is_queryable}
                    try:
                        payload["instance_name"] = c.instance_name or ""
                    except Exception:
                        pass
                    use = {k: v for k, v in payload.items() if k in tcols}
                    if "name" not in use:
                        continue
                    col_sql = ",".join('"%s"' % k for k in use)
                    cur.execute(f'INSERT INTO "{sch}"."urs_connection" ({col_sql}) '
                                f'VALUES ({",".join(["%s"] * len(use))}) ON CONFLICT (id) DO NOTHING',
                                list(use.values()))
                    if (cur.rowcount or 0) == 0:
                        use2 = {k: v for k, v in use.items() if k != "id"}
                        if use2:
                            col2 = ",".join('"%s"' % k for k in use2)
                            cur.execute(f'INSERT INTO "{sch}"."urs_connection" ({col2}) '
                                        f'VALUES ({",".join(["%s"] * len(use2))})', list(use2.values()))
                    pg_by_name[c.name] = True
                    b += 1
                except Exception:
                    try:
                        pg.rollback()
                    except Exception:
                        pass
                    continue
        try:
            pg.close()
        except Exception:
            pass
        return a, b
    except Exception:
        return 0, 0


def api_connections_list(request):
    """GET /api/connections/ — list all connections for report wizard"""
    try:
        from .models import Connection
        try:
            _sa, _sb = _sync_connections_both_ways()
        except Exception:
            _sa = _sb = 0
        conns = Connection.objects.all().order_by("name")
        return JsonResponse({"connections": [c.to_dict() for c in conns],
                             "synced": {"pg_to_dj": _sa, "dj_to_pg": _sb}})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_connection_create(request):
    """POST /api/connections/create/ — create new connection (for wizard)"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        from .models import Connection
        for r in ["name"]:
            v = data.get(r)
            if v is None or (isinstance(v, str) and not v.strip()):
                return JsonResponse({"error": f"{r} required"}, status=400)
        # Validate engine + connection type (نوع الاتصال: قاعدة بيانات | IoT)
        engine = data.get("engine", "postgres")
        if engine not in [c[0] for c in Connection.ENGINE_CHOICES]:
            engine = "postgres"
        conn_type = str(data.get("conn_type", data.get("connType", "")) or "").strip().lower()
        if conn_type not in ("database", "iot"):
            conn_type = "iot" if engine in Connection.IOT_ENGINES else "database"
        # المضيف إلزامي لقواعد البيانات فقط — مخفي لاتصالات IoT (الأجهزة بقائمة devices)
        _hv = data.get("host")
        if conn_type == "database" and (_hv is None or (isinstance(_hv, str) and not _hv.strip())):
            return JsonResponse({"error": "host required"}, status=400)
        if "is_queryable" in data:
            is_queryable = bool(data.get("is_queryable"))
        else:
            is_queryable = (conn_type == "database")
        try:
            port = int(data.get("port") or 5432)
        except (TypeError, ValueError):
            port = 5432
        endpoint = str(data.get("endpoint", "att") or "att").strip().lower()
        if endpoint not in ("att", "users", "attendance"):
            endpoint = "att"
        import json as _json_c
        try:
            _dv = data.get("devices", "[]")
            devices_json = _dv if isinstance(_dv, str) else _json_c.dumps(_dv or [], ensure_ascii=False)
            _json_c.loads(devices_json or "[]")
        except Exception:
            devices_json = "[]"
        obj = Connection.objects.create(
            name=data["name"],
            host=(data.get("host") or ("172.16.10.101" if conn_type == "database" else "")),
            port=port,
            user=data.get("user") or "postgres",
            password=data.get("password") or "postgres",
            instance=data.get("instance") or "urs",
            engine=engine,
            conn_type=conn_type,
            is_local=bool(data.get("is_local", True)),
            is_queryable=is_queryable,
            schema=data.get("schema", ""),
            devices=devices_json,
            endpoint=endpoint,
            description=data.get("description", ""),
        )
        return JsonResponse({"ok": True, "connection": obj.to_dict()})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_connection_update(request, conn_id):
    """POST /api/connections/<id>/update/ — update connection fields"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        from .models import Connection
        try:
            obj = Connection.objects.get(id=conn_id)
        except Connection.DoesNotExist:
            return JsonResponse({"error": "not found"}, status=404)
        for f in ["name", "host", "port", "user", "password", "instance", "engine", "conn_type", "devices", "endpoint", "is_local", "is_queryable", "schema", "description"]:
            if f in data:
                if f == "port":
                    try:
                        setattr(obj, f, int(data[f]))
                    except Exception:
                        pass
                elif f in ("is_local", "is_queryable"):
                    setattr(obj, f, bool(data[f]))
                elif f == "engine":
                    if data[f] in [c[0] for c in Connection.ENGINE_CHOICES]:
                        setattr(obj, f, data[f])
                        if "conn_type" not in data and "connType" not in data:
                            setattr(obj, "conn_type", "iot" if data[f] in Connection.IOT_ENGINES else "database")
                elif f == "conn_type":
                    v = str(data.get("conn_type", data.get("connType", "")) or "").strip().lower()
                    if v in ("database", "iot"):
                        setattr(obj, f, v)
                elif f == "endpoint":
                    v = str(data.get("endpoint") or "").strip().lower()
                    if v in ("att", "users", "attendance"):
                        setattr(obj, f, v)
                elif f == "devices":
                    import json as _json
                    dv = data.get("devices")
                    try:
                        if isinstance(dv, str):
                            _json.loads(dv or "[]")
                            setattr(obj, f, dv or "[]")
                        else:
                            setattr(obj, f, _json.dumps(dv or [], ensure_ascii=False))
                    except Exception:
                        pass
                else:
                    setattr(obj, f, data[f])
        obj.save()
        return JsonResponse({"ok": True, "connection": obj.to_dict()})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_connection_delete(request, conn_id):
    """POST /api/connections/<id>/delete/ — delete connection if not referenced"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from .models import Connection
        try:
            obj = Connection.objects.get(id=conn_id)
        except Connection.DoesNotExist:
            return JsonResponse({"error": "not found"}, status=404)
        obj.delete()
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


_oracle_thick_done = False


def _oracle_connect_obj(obj):
    """اتصال أوراكل: خفيف أولاً، ثم سميك تلقائياً عند DPY-3015 (مدقق قديم) — بعزل عن ERP."""
    import oracledb
    global _oracle_thick_done
    dsn = f"{obj.host}:{int(obj.port or 1521)}/{obj.instance or 'XEPDB1'}"
    try:
        return oracledb.connect(user=obj.user, password=obj.password or "", dsn=dsn)
    except Exception as e:
        if "3015" not in str(e):
            raise
        if not _oracle_thick_done:
            cfg = BASE_DIR / "oracle_config"
            cfg.mkdir(exist_ok=True)
            sqlnet = cfg / "sqlnet.ora"
            if not sqlnet.exists():
                sqlnet.write_text("SQLNET.AUTHENTICATION_SERVICES=(NONE)\n", encoding="utf-8")
            try:
                oracledb.init_oracle_client(config_dir=str(cfg))
            except Exception:
                try:
                    oracledb.init_oracle_client()
                except Exception:
                    pass
            _oracle_thick_done = True
        return oracledb.connect(user=obj.user, password=obj.password or "", dsn=dsn)


def _sqlserver_error_hint(msg):
    """Arabic actionable hint for common SQL Server ODBC errors (shown before raw text)."""
    m = str(msg or "")
    if "IM002" in m or "Data source name not found" in m:
        return "لا يوجد تعريف ODBC على جهاز التطبيق"
    if "08001" in m or "does not exist or access denied" in m:
        return ("تعذر الوصول للسيرفر — تحقق: 1) المضيف والمنفذ، 2) تفعيل TCP/IP في SQL Configuration، "
                "3) إن كان مثيلًا مسمى أدخل اسم المثيل (SQLEXPRESS) بدل المنفذ، 4) الجدار الناري")
    if "28000" in m or "18456" in m or "Login failed" in m:
        return "فشل الدخول — تحقق من المستخدم وكلمة المرور، وأن المصادقة SQL مفعلة (لا Windows فقط)"
    if "08004" in m or "911" in m or "database" in m.lower() and "does not exist" in m.lower():
        return "القاعدة غير موجودة — تحقق من اسم القاعدة/المثيل"
    if "HYT00" in m or "HYT01" in m or "timeout" in m.lower():
        return "انتهت المهلة — السيرفر لا يرد خلال 5 ثوانٍ (شبكة/جدار ناري/اسم مضيف)"
    return ""


def _split_sqlserver_host_instance(host, instance_name):
    """Split SQL Server target into (host, instance).

    Accepts `SQLEXPRESS` (host from host field) and full `SERVER\\INSTANCE`
    (instance taken after the last backslash; server part used only when
    the host field itself is empty).
    """
    host = (host or "").strip()
    raw = (instance_name or "").strip()
    if "\\" in raw:
        srv, _, inst = raw.rpartition("\\")
        inst = inst.strip()
        if not host and srv.strip():
            host = srv.strip()
        return host, inst
    return host, raw


def _test_connection_obj(obj):
    """اختبار مصادقة فعلي لاتصال (قراءة فقط: SELECT 1) → (ok, payload, status)."""
    try:
        if obj.engine in ("postgres", "oracle", "sqlserver", "mysql") and not (obj.user or "").strip():
            return False, {"ok": False, "error": "اسم المستخدم مطلوب للاختبار"}, 400
        if obj.engine == "postgres":
            import psycopg2
            try:
                conn = psycopg2.connect(dbname=obj.instance or "urs", user=obj.user, password=obj.password, host=obj.host, port=obj.port, connect_timeout=5)
                cur = conn.cursor()
                cur.execute("SELECT 1;")
                cur.fetchone()
                try:
                    cur.execute("SELECT current_user;")
                    who = cur.fetchone()
                except Exception:
                    who = None
                conn.close()
                return True, {"ok": True, "engine": obj.engine, "user": (who[0] if who else obj.user)}, 200
            except Exception as e:
                return False, {"ok": False, "error": str(e)}, 400
        if obj.engine == "oracle":
            try:
                import oracledb
            except ImportError:
                return False, {"ok": False, "error": "تعذر التحقق: oracledb غير مثبت"}, 400
            try:
                conn = _oracle_connect_obj(obj)
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM DUAL")
                cur.fetchone()
                conn.close()
                note = "تم عبر Oracle Client (thick mode)" if _oracle_thick_done else ""
                out = {"ok": True, "engine": obj.engine, "user": obj.user}
                if note:
                    out["note"] = note
                return True, out, 200
            except Exception as e:
                msg = str(e)
                if "ORA-12638" in msg:
                    return False, {"ok": False, "error": (
                        "فشل جلب بيانات اعتماد Windows (NTS). "
                        "الحلول: 1) أدخل مستخدم وكلمة مرور قاعدة البيانات صراحةً في الاتصال (وليس مصادقة Windows)، "
                        "2) اضبط ملف sqlnet.ora بوضع SQLNET.AUTHENTICATION_SERVICES=(NONE)، "
                        "3) راجع DBA للتأكد من السماح بالمصادقة بكلمة المرور")}, 400
                if "3015" in msg:
                    return False, {"ok": False, "error": (
                        "نوع مدقق كلمة المرور (0x939) غير مدعوم حتى بالوضع السميك هنا. "
                        "الحل الجذري (مرة واحدة من DBA): ALTER USER " + (obj.user or "?") + " IDENTIFIED BY <password>; "
                        "ثم أعد الاختبار")}, 400
                return False, {"ok": False, "error": msg}, 400
        if obj.engine == "sqlserver":
            try:
                import pyodbc
            except ImportError:
                return False, {"ok": False, "error": "تعذر التحقق: pyodbc غير مثبت"}, 400
            try:
                conn, _drv_ok, _srv_ok = _mssql_connect_obj(obj, timeout=5)
                cur = conn.cursor()
                cur.execute("SELECT 1;")
                cur.fetchone()
                conn.close()
                _note = "" if _drv_ok.startswith("ODBC Driver 18") else f" عبر {_drv_ok}"
                return True, {"ok": True, "engine": obj.engine, "user": obj.user,
                              "driver": _drv_ok, "server": _srv_ok, "note": _note}, 200
            except Exception as e:
                _msg = str(e)
                _hint = _sqlserver_error_hint(_msg)
                return False, {"ok": False,
                               "error": ((_hint + " — ") if _hint else "") + _msg[:220]}, 400
        if obj.engine == "mysql":
            try:
                import MySQLdb
            except ImportError:
                return False, {"ok": False, "error": "تعذر التحقق: mysqlclient غير مثبت"}, 400
            try:
                conn = MySQLdb.connect(host=obj.host, user=obj.user, passwd=obj.password or "",
                                       db=obj.instance or None, port=int(obj.port or 3306), connect_timeout=5)
                cur = conn.cursor()
                cur.execute("SELECT 1;")
                cur.fetchone()
                conn.close()
                return True, {"ok": True, "engine": obj.engine, "user": obj.user}, 200
            except Exception as e:
                return False, {"ok": False, "error": str(e)}, 400
        if obj.engine == "zk":
            # أجهزة البصمة مستثناة من التحقق من كلمة المرور — فحص استجابة
            # المنفذ لكل أجهزة الاتصال (قائمة devices بصيغة IP:Port)
            import socket
            devs = obj.device_list() if hasattr(obj, "device_list") else []
            if not devs:
                return False, {"ok": False, "error": "لا أجهزة في هذا الاتصال — أضف أجهزة بصيغة IP:Port"}, 400
            ok_hosts, bad = [], []
            for h, p in devs:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3)
                    s.connect((h, int(p or 4370)))
                    s.close()
                    ok_hosts.append(f"{h}:{p}")
                except Exception as e:
                    bad.append(f"{h}:{p} ({e})")
            if ok_hosts:
                return True, {"ok": True, "engine": obj.engine,
                              "note": f"مستجيب {len(ok_hosts)}/{len(devs)}: " + ", ".join(ok_hosts[:5]) + (" — أجهزة البصمة مستثناة من التحقق من كلمة المرور" if not bad else " — متعذر: " + "; ".join(bad[:3]))}, 200
            return False, {"ok": False, "error": "لا جهاز مستجيب: " + "; ".join(bad[:5])}, 400
        return False, {"ok": False, "error": f"محرك غير معروف: {obj.engine}"}, 400
    except Exception as e:
        return False, {"error": str(e)}, 500


def _effective_or_row(conn_id):
    """Connection row overlaid with app.conf DB_* when it IS the default connection.

    Unifies the designers with the single source: id==1 or name==urs_local reads
    app.conf (engine/host/port/user/decrypted password/instance/schema/instance_name)
    over the row; any other connection returns its row untouched. Never saves.
    Returns None when the row doesn't exist.
    """
    from .models import Connection
    try:
        obj = Connection.objects.get(id=conn_id)
    except Exception:
        return None
    try:
        if not (obj.id == 1 or (obj.name or "") == "urs_local"):
            return obj
        from config.dbconf import read_appconf, dbpass_resolve
        ac = read_appconf()
        if not (ac.get("DB_HOST") or "").strip():
            return obj
        if (ac.get("DB_ENGINE") or "").strip():
            obj.engine = ac["DB_ENGINE"].strip().lower()
        obj.host = ac["DB_HOST"].strip()
        if (ac.get("DB_PORT") or "").strip():
            try:
                obj.port = int(ac["DB_PORT"])
            except (TypeError, ValueError):
                pass
        if (ac.get("DB_USER") or "").strip():
            obj.user = ac["DB_USER"].strip()
        _pw = dbpass_resolve(ac.get("DB_PASS") or "")
        if _pw:
            obj.password = _pw
        if (ac.get("DB_NAME") or "").strip():
            obj.instance = ac["DB_NAME"].strip()
        if (ac.get("DB_SCHEMA") or "").strip():
            obj.schema = ac["DB_SCHEMA"].strip()
        if (ac.get("DB_INSTANCENAME") or "").strip():
            try:
                obj.instance_name = ac["DB_INSTANCENAME"].strip()
            except Exception:
                pass
    except Exception:
        pass
    return obj


def api_connection_test(request, conn_id):
    """GET /api/connections/<id>/test/ — اختبار اتصال محفوظ."""
    try:
        from .models import Connection
        obj = _effective_or_row(conn_id)
        if obj is None:
            return JsonResponse({"error": "not found"}, status=404)
        ok, payload, status = _test_connection_obj(obj)
        _err = ""
        try:
            _err = (payload or {}).get("error") if isinstance(payload, dict) else ""
        except Exception:
            _err = ""
        _record_check(obj, ok, _check_actor(request), _err)
        if ok and "message" not in payload:
            payload["message"] = f"اتصال ومصادقة سليمة ✓ ({obj.engine})"
        return JsonResponse(payload, status=status)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _check_actor(request) -> str:
    """اسم منفذ الفحص للسجل."""
    try:
        u = getattr(request, "user", None)
        if u is not None and getattr(u, "is_authenticated", False):
            return u.username or "مستخدم"
    except Exception:
        pass
    return "النظام"


def _record_check(obj, ok: bool, by: str, error: str = ""):
    """حفظ نتيجة آخر اتصال (وقت/مستخدم/نتيجة/نص الخطأ)."""
    try:
        from django.utils import timezone
        obj.last_check_at = timezone.now()
        obj.last_check_by = (by or "")[:100]
        obj.last_check_ok = bool(ok)
        try:
            obj.last_check_error = ("" if ok else str(error or ""))[:500]
        except Exception:
            pass
        _fields = ["last_check_at", "last_check_by", "last_check_ok", "updated_at"]
        try:
            from .models import Connection as _C
            if any(f.name == "last_check_error" for f in _C._meta.get_fields()):
                _fields.insert(3, "last_check_error")
        except Exception:
            pass
        obj.save(update_fields=_fields)
    except Exception:
        pass


# ── IoT: endpoints + mirror tables (UNION ALL + background sync) ─────────────
IOT_ENDPOINTS = {
    "zk": [
        {"endpoint": "att", "label": "الحضور (att) — اسم/رقم/تاريخ/وقت/حالة",
         "columns": ["device_ip", "emp_name", "emp_no", "punch_date", "punch_time", "punch_ts", "status"]},
        {"endpoint": "users", "label": "المستخدمون (users)",
         "columns": ["uid", "user_id", "name", "privilege", "card", "group_id"]},
        {"endpoint": "attendance", "label": "السجلات الخام (attendance)",
         "columns": ["uid", "user_id", "timestamp", "status", "punch"]},
    ],
}


@csrf_exempt
def api_iot_endpoints(request):
    """POST /api/iot/endpoints/ {connection_id} → endpoints المتاحة للمحرك."""
    try:
        data = json.loads(request.body.decode() or "{}")
        from .models import Connection
        try:
            obj = Connection.objects.get(id=int(data.get("connection_id", 0)))
        except (Connection.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "not found"}, status=404)
        eps = IOT_ENDPOINTS.get(str(obj.engine or "").lower(), [])
        return JsonResponse({"connection": obj.to_dict(), "endpoints": eps,
                             "devices": obj.device_list() if hasattr(obj, "device_list") else []})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_iot_fetch(request):
    """POST /api/iot/fetch/ {connection_id, endpoint, limit} → UNION ALL JSON من كل الأجهزة."""
    try:
        data = json.loads(request.body.decode() or "{}")
        from .models import Connection
        try:
            obj = Connection.objects.get(id=int(data.get("connection_id", 0)))
        except (Connection.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "not found"}, status=404)
        if str(obj.conn_type or "") != "iot":
            return JsonResponse({"error": "الاتصال ليس من نوع IoT"}, status=400)
        try:
            limit = max(1, min(int(data.get("limit", 500) or 500), 5000))
        except (TypeError, ValueError):
            limit = 500
        from .iot_sync import fetch_union
        out = fetch_union(obj, str(data.get("endpoint", "att") or "att"), limit=1000000)
        rows = out.get("rows", [])[:limit]
        return JsonResponse({"connection_id": obj.id, "endpoint": out.get("endpoint"),
                             "devices": out.get("devices"), "total": out.get("total"),
                             "rows": rows}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_iot_mirror(request):
    """POST /api/iot/mirror/ {connection_id, endpoint, local_connection_id, auto_sync, interval_min, clear_device}
    → ينشئ جدول iot_<engine>_<endpoint> على الاتصال المحلي + يبدأ المزامنة الخلفية."""
    try:
        data = json.loads(request.body.decode() or "{}")
        from .models import Connection, IoTMirror
        try:
            obj = Connection.objects.get(id=int(data.get("connection_id", 0)))
        except (Connection.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "IoT connection not found"}, status=404)
        try:
            local = Connection.objects.get(id=int(data.get("local_connection_id", 0)))
        except (Connection.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "local connection not found"}, status=404)
        if str(obj.conn_type or "") != "iot":
            return JsonResponse({"error": "الاتصال المصدر يجب أن يكون IoT"}, status=400)
        if str(local.engine or "").lower() != "postgres":
            return JsonResponse({"error": "الاتصال المحلي يجب أن يكون postgres"}, status=400)
        endpoint = str(data.get("endpoint") or getattr(obj, "endpoint", "") or "att").strip().lower()
        if endpoint not in ("att", "users", "attendance"):
            endpoint = "att"
        from .iot_sync import mirror_table_name, local_pg, mirror_schema, ensure_mirror_table, ensure_sweeper, start_sync_async
        table = mirror_table_name(obj.engine, endpoint)
        try:
            pg = local_pg(local)
            try:
                ensure_mirror_table(pg, mirror_schema(local), table)
            finally:
                try:
                    pg.close()
                except Exception:
                    pass
        except Exception as e:
            return JsonResponse({"error": "تعذر إنشاء الجدول: %s" % e}, status=500)
        try:
            interval = max(1, min(int(data.get("interval_min", 15) or 15), 1440))
        except (TypeError, ValueError):
            interval = 15
        mirror, _ = IoTMirror.objects.update_or_create(
            connection=obj, endpoint=endpoint, local_connection=local,
            defaults={"table_name": table,
                      "auto_sync": bool(data.get("auto_sync", True)),
                      "interval_min": interval,
                      "clear_device": bool(data.get("clear_device", True))})
        ensure_sweeper()
        started = start_sync_async(mirror.id)
        return JsonResponse({"ok": True, "mirror": mirror.to_dict(),
                             "table": table, "sync_started": started,
                             "designer": {"connection_id": local.id,
                                          "connection": local.name,
                                          "table": table}},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_iot_mirrors(request):
    """GET /api/iot/mirrors/ — كل المرايا وحالتها."""
    try:
        from .models import IoTMirror
        from .iot_sync import ensure_sweeper
        ensure_sweeper()
        return JsonResponse({"mirrors": [m.to_dict() for m in
                                         IoTMirror.objects.select_related(
                                             "connection", "local_connection").all()]},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_iot_mirror_status(request):
    """GET /api/iot/mirror/status/?mirror_id= — تقدم المزامنة (للشريط)."""
    try:
        from .models import IoTMirror
        try:
            m = IoTMirror.objects.select_related("connection", "local_connection").get(
                id=int(request.GET.get("mirror_id", 0)))
        except (IoTMirror.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "not found"}, status=404)
        return JsonResponse({"mirror": m.to_dict()}, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_iot_mirror_sync(request):
    """POST /api/iot/mirror/sync/ {mirror_id} — مزامنة فورية في الخلفية."""
    try:
        data = json.loads(request.body.decode() or "{}")
        from .models import IoTMirror
        from .iot_sync import start_sync_async
        try:
            m = IoTMirror.objects.get(id=int(data.get("mirror_id", 0)))
        except (IoTMirror.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "not found"}, status=404)
        started = start_sync_async(m.id)
        return JsonResponse({"ok": True, "started": started, "mirror": m.to_dict()},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _resolve_connection_record(rec):
    """مطابقة سجل اتصال من صف شبكة (id/__pk_id/الاسم)."""
    from .models import Connection
    rid = (rec or {}).get("id") or (rec or {}).get("__pk_id") or (rec or {}).get("ID")
    if rid and str(rid).isdigit():
        return Connection.objects.get(id=int(rid))
    return Connection.objects.get(name=((rec or {}).get("name") or (rec or {}).get("الاسم") or ""))


def _list_tables_obj(obj):
    """قائمة جداول أي اتصال (postgres/oracle/sqlserver/mysql) — وجداول zk المنطقية."""
    if obj.engine == "postgres":
        conn = _pg_conn_for(obj)
        try:
            cur = conn.cursor()
            schema = (obj.schema or "").strip()
            if schema:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type IN ('BASE TABLE','VIEW') ORDER BY table_name", (schema,))
                tables = [{"name": r[0], "schema": schema, "full": f"{schema}.{r[0]}"} for r in cur.fetchall()]
            else:
                cur.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type IN ('BASE TABLE','VIEW') ORDER BY table_schema, table_name")
                tables = [{"name": r[1], "schema": r[0], "full": f"{r[0]}.{r[1]}"} for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
        return tables
    if obj.engine == "oracle":
        return _oracle_tables(obj)
    if obj.engine == "sqlserver":
        return _mssql_tables(obj)
    if obj.engine == "mysql":
        return _mysql_tables(obj)
    if obj.engine == "zk":
        return [{"name": t, "schema": "", "full": t} for t in _ZK_TABLES]
    raise ValueError(f"محرك غير مدعوم: {obj.engine}")


@csrf_exempt
def api_connection_stats(request):
    """POST /api/connections/stats/ — إحصائيات الاتصال: الحالة + آخر اتصال + عدد الجداول."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        rec = data.get("record") or {}
        from .models import Connection
        try:
            obj = _resolve_connection_record(rec)
        except (Connection.DoesNotExist, ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "سجل الاتصال غير موجود — احفظ السجل أولاً"}, status=404)
        by = _check_actor(request)
        ok, test_payload, _ = _test_connection_obj(obj)
        _record_check(obj, ok, by)
        tables, tables_error = [], None
        try:
            tables = _list_tables_obj(obj)
        except ValueError as ve:
            tables_error = str(ve)
        except Exception as e:
            tables_error = str(e)
        obj.refresh_from_db()
        stats = {
            "name": obj.name, "engine": obj.engine, "host": obj.host, "port": obj.port,
            "status": "سليم ✓" if ok else "فاشل ✗", "ok": ok,
            "detail": test_payload.get("note") or test_payload.get("user") or test_payload.get("error") or "",
            "last_check_at": obj.last_check_at.isoformat() if obj.last_check_at else "—",
            "last_check_by": obj.last_check_by or by,
            "last_check_ok": "✓" if obj.last_check_ok else ("✗" if obj.last_check_ok is False else "—"),
            "tables_count": len(tables), "tables_error": tables_error,
            "tables": [t["full"] for t in tables[:50]],
        }
        return JsonResponse({"ok": True, "stats": stats, "message": f"إحصائيات {obj.name}"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_connection_test_record(request):
    """POST /api/connections/test/ — زر مخصص في FMLK: {record: {id}} → اختبار ذلك السجل."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        rec = data.get("record") or {}
        from .models import Connection
        try:
            obj = _resolve_connection_record(rec)
        except (Connection.DoesNotExist, ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "سجل الاتصال غير موجود — احفظ السجل أولاً"}, status=404)
        ok, payload, status = _test_connection_obj(obj)
        _err2 = ""
        try:
            _err2 = (payload or {}).get("error") if isinstance(payload, dict) else ""
        except Exception:
            _err2 = ""
        _record_check(obj, ok, _check_actor(request), _err2)
        if ok and "message" not in payload:
            payload["message"] = f"اتصال ومصادقة سليمة ✓ ({obj.engine})"
        return JsonResponse(payload, status=status)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


import re as _re_ident

def _pg_conn_for(obj):
    """Connect via a stored Connection (postgres introspection)."""
    import psycopg2
    if obj.engine != "postgres":
        raise ValueError(f"استكشاف الجداول متاح لاتصالات postgres فقط (المحرك: {obj.engine})")
    conn = psycopg2.connect(dbname=obj.instance or "urs", user=obj.user, password=obj.password,
                            host=obj.host, port=int(obj.port or 5432), connect_timeout=5)
    conn.autocommit = True
    return conn


def _valid_ident(name):
    return bool(_re_ident.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name or ""))


_PG_TYPE_MAP = [
    (("integer", "bigint", "smallint", "serial", "bigserial"), "INTEGER"),
    (("numeric", "decimal", "real", "double precision", "money"), "NUMERIC"),
    (("character varying", "varchar", "character", "char", "name"), "VARCHAR"),
    (("text",), "TEXT"),
    (("date",), "DATE"),
    (("time",), "TIME"),
    (("timestamp",), "TIMESTAMP"),
    (("boolean",), "BOOLEAN"),
]

def _pg_type_to_field(pg_type):
    t = (pg_type or "").lower().split("(")[0].strip()
    for keys, mapped in _PG_TYPE_MAP:
        if t in keys:
            return mapped
    # information_schema variants: 'timestamp with time zone', 'time without time zone', ...
    if t.startswith("timestamp"):
        return "TIMESTAMP"
    if t.startswith("time"):
        return "TIME"
    if t.startswith("date"):
        return "DATE"
    return "TEXT"


def _ora_type_to_field(ora_type, scale=None):
    t = (ora_type or "").upper().split("(")[0].strip()
    if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        return "VARCHAR"
    if t == "NUMBER":
        try:
            return "INTEGER" if scale is not None and int(scale) == 0 else "NUMERIC"
        except (TypeError, ValueError):
            return "NUMERIC"
    if t in ("FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"):
        return "NUMERIC"
    if t == "DATE":
        return "DATE"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if t in ("CLOB", "NCLOB", "LONG"):
        return "TEXT"
    return "TEXT"


def _mssql_type_to_field(ms_type):
    t = (ms_type or "").lower().split("(")[0].strip()
    if t in ("int", "bigint", "smallint", "tinyint"):
        return "INTEGER"
    if t in ("decimal", "numeric", "money", "smallmoney", "float", "real"):
        return "NUMERIC"
    if t in ("varchar", "nvarchar", "char", "nchar", "sysname", "uniqueidentifier"):
        return "VARCHAR"
    if t in ("text", "ntext"):
        return "TEXT"
    if t == "date":
        return "DATE"
    if t == "time":
        return "TIME"
    if t in ("datetime", "datetime2", "smalldatetime", "datetimeoffset"):
        return "TIMESTAMP"
    if t == "bit":
        return "BOOLEAN"
    return "TEXT"


def _mysql_type_to_field(my_type):
    t = (my_type or "").lower().split("(")[0].strip()
    if t in ("int", "bigint", "smallint", "mediumint", "tinyint", "year"):
        return "INTEGER"
    if t in ("decimal", "numeric", "float", "double"):
        return "NUMERIC"
    if t in ("varchar", "char"):
        return "VARCHAR"
    if t in ("text", "mediumtext", "longtext", "tinytext", "enum", "set", "json"):
        return "TEXT"
    if t == "date":
        return "DATE"
    if t == "time":
        return "TIME"
    if t in ("datetime", "timestamp"):
        return "TIMESTAMP"
    if t == "bit":
        return "BOOLEAN"
    return "TEXT"


_ZK_TABLES = {
    "users": [("uid", "INTEGER"), ("user_id", "VARCHAR"), ("name", "VARCHAR"),
              ("privilege", "INTEGER"), ("card", "VARCHAR")],
    "attendance": [("uid", "INTEGER"), ("user_id", "VARCHAR"), ("timestamp", "TIMESTAMP"),
                   ("status", "INTEGER"), ("punch", "INTEGER")],
}
try:
    # Single source of truth lives in the ZK engine module (superset: +group_id)
    from odex.engines.zk import ZK_TABLE_COLUMNS as _ZK_TABLES
except Exception:
    pass


def _oracle_tables(obj):
    conn = _oracle_connect_obj(obj)
    try:
        owner = ((obj.schema or "").strip() or obj.user or "").upper()
        cur = conn.cursor()
        try:
            cur.execute("SELECT table_name FROM all_tables WHERE owner = :o ORDER BY table_name", {"o": owner})
            names = [r[0] for r in cur.fetchall()]
        except Exception:
            if owner == (obj.user or "").upper():
                cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
                names = [r[0] for r in cur.fetchall()]
            else:
                raise
        cur.close()
    finally:
        conn.close()
    return [{"name": n, "schema": owner, "full": f"{owner}.{n}"} for n in names]


def _oracle_columns(obj, schema, table):
    conn = _oracle_connect_obj(obj)
    try:
        owner = (schema or obj.user or "").upper()
        tname = (table or "").upper()
        cur = conn.cursor()
        try:
            cur.execute("SELECT column_name, data_type, data_scale FROM all_tab_columns WHERE owner = :o AND table_name = :t ORDER BY column_id",
                        {"o": owner, "t": tname})
            rows = cur.fetchall()
        except Exception:
            if owner == (obj.user or "").upper():
                cur.execute("SELECT column_name, data_type, data_scale FROM user_tab_columns WHERE table_name = :t ORDER BY column_id", {"t": tname})
                rows = cur.fetchall()
            else:
                raise
        cols = [{"name": r[0], "type": _ora_type_to_field(r[1], r[2]), "db_type": r[1]} for r in rows]
        cur.close()
    finally:
        conn.close()
    return cols


_MSSQL_DRIVERS = (("ODBC Driver 18 for SQL Server", True),
                  ("ODBC Driver 17 for SQL Server", True),
                  ("SQL Server", False))


def _mssql_servers(obj):
    """Candidate SERVER values: explicit custom port first, then host\\instance, then default."""
    _h, _in = _split_sqlserver_host_instance(obj.host, getattr(obj, "instance_name", ""))
    try:
        _p = int(obj.port or 1433)
    except (TypeError, ValueError):
        _p = 1433
    srvs = []
    if _in and _p != 1433:
        srvs.append(f"{_h},{_p}")
    if _in:
        srvs.append(f"{_h}\\{_in}")
    if not srvs:
        srvs.append(f"{_h},{_p or 1433}")
    return srvs


def _mssql_connect_obj(obj, timeout=10):
    """Connect to SQL Server trying server routes × drivers → (conn, driver, server).

    Raises RuntimeError (Arabic) when no ODBC SQL Server driver exists at all,
    else raises the last connection error.
    """
    import pyodbc
    dbn = obj.instance or 'master'
    saw_driver, last = False, None
    for _srv in _mssql_servers(obj):
        for _drv, _modern in _MSSQL_DRIVERS:
            try:
                _parts = [f"DRIVER={{{_drv}}}", f"SERVER={_srv}", f"DATABASE={dbn}",
                          f"UID={obj.user}", f"PWD={obj.password or ''}"]
                if _modern:
                    _parts += ["TrustServerCertificate=yes", "Connect Timeout=5"]
                conn = pyodbc.connect(";".join(_parts) + ";", timeout=timeout)
                return conn, _drv, _srv
            except Exception as e:
                _m = str(e)
                if "IM002" in _m or "Data source name not found" in _m:
                    last = e
                    continue
                saw_driver = True
                last = e
    if not saw_driver:
        import platform as _plat2
        _lin = (" — لينكس (Ubuntu): curl https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc && "
                "curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list && "
                "sudo apt-get update && sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev && pip install pyodbc"
                ) if _plat2.system() == "Linux" else ""
        raise RuntimeError("لا يوجد تعريف ODBC لـ SQL Server على جهاز التطبيق — ثبّت Microsoft ODBC Driver 18 for SQL Server%s." % _lin)
    raise last


def _mssql_tables(obj):
    conn, _, _ = _mssql_connect_obj(obj, timeout=5)
    try:
        cur = conn.cursor()
        schema = (obj.schema or "").strip()
        if schema:
            cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_TYPE IN ('BASE TABLE','VIEW') ORDER BY TABLE_NAME", schema)
            tables = [{"name": r[0], "schema": schema, "full": f"{schema}.{r[0]}"} for r in cur.fetchall()]
        else:
            cur.execute("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE IN ('BASE TABLE','VIEW') ORDER BY TABLE_SCHEMA, TABLE_NAME")
            tables = [{"name": r[1], "schema": r[0], "full": f"{r[0]}.{r[1]}"} for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return tables


def _mssql_columns(obj, schema, table):
    conn, _, _ = _mssql_connect_obj(obj, timeout=5)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION", (schema, table))
        cols = [{"name": r[0], "type": _mssql_type_to_field(r[1]), "db_type": r[1]} for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return cols


def _mysql_tables(obj):
    import MySQLdb
    conn = MySQLdb.connect(host=obj.host, user=obj.user, passwd=obj.password or "",
                           db=obj.instance or None, port=int(obj.port or 3306), connect_timeout=5)
    try:
        cur = conn.cursor()
        db = (obj.schema or "").strip() or obj.instance or ""
        if db:
            cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE','VIEW') ORDER BY TABLE_NAME", (db,))
            tables = [{"name": r[0], "schema": db, "full": f"{db}.{r[0]}"} for r in cur.fetchall()]
        else:
            cur.execute("SHOW FULL TABLES WHERE TABLE_TYPE LIKE '%TABLE'")
            tables = [{"name": r[0], "schema": "", "full": r[0]} for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return tables


def _mysql_columns(obj, schema, table):
    import MySQLdb
    conn = MySQLdb.connect(host=obj.host, user=obj.user, passwd=obj.password or "",
                           db=obj.instance or None, port=int(obj.port or 3306), connect_timeout=5)
    try:
        cur = conn.cursor()
        db = schema or obj.instance or ""
        cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", (db, table))
        cols = [{"name": r[0], "type": _mysql_type_to_field(r[1]), "db_type": r[1]} for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return cols


def _default_schema_for(obj) -> str:
    if (obj.schema or "").strip():
        return obj.schema.strip()
    if obj.engine == "oracle":
        return (obj.user or "").upper() or "HR"
    if obj.engine == "sqlserver":
        return "dbo"
    if obj.engine == "mysql":
        return obj.instance or ""
    return "public"


def _valid_table_ref(name):
    # حروف/أرقام/_ مع دعم $ و # في أوراكل
    return bool(_re_ident.match(r'^[A-Za-z_][A-Za-z0-9_$#]*$', name or ""))


def _iot_is_conn(obj) -> bool:
    """اتصال IoT (أجهزة)؟ — جداوله تُؤخذ من المرايا المحلية لا من API الجهاز."""
    try:
        if str(getattr(obj, "conn_type", "") or "") == "iot":
            return True
    except Exception:
        pass
    return str(getattr(obj, "engine", "") or "").lower() == "zk"


def _iot_mirror_tables(obj):
    """جداول المرآة المحلية لاتصال IoT — تُعرض في المصمم بدل جداول API الجهاز."""
    from .models import IoTMirror
    from .iot_sync import mirror_schema
    out = []
    for m in IoTMirror.objects.select_related("local_connection").filter(connection=obj).order_by("endpoint"):
        try:
            sch = (mirror_schema(m.local_connection) or "").strip() or "public"
        except Exception:
            sch = "public"
        out.append({
            "name": m.table_name, "schema": sch, "full": f"{sch}.{m.table_name}",
            "endpoint": m.endpoint,
            "local_connection_id": m.local_connection_id,
            "local_connection": m.local_connection.name if m.local_connection_id else "",
            "mirror": True,
        })
    return out


def api_connection_tables(request, conn_id):
    """GET /api/connections/<id>/tables/ — جداول أي اتصال (تشمل البعيدة).

    اتصال IoT يُرجع جداول المرايا المحلية (iot_<engine>_<endpoint>) على
    الاتصال المحلي — المصمم يعمل SQL على المرآة لا على API الجهاز.
    """
    try:
        from .models import Connection
        obj = _effective_or_row(conn_id)
        if obj is None:
            return JsonResponse({"error": "not found"}, status=404)
        if _iot_is_conn(obj):
            tables = _iot_mirror_tables(obj)
            payload = {"tables": tables, "total": len(tables), "engine": obj.engine,
                       "mirror": True, "connection_id": obj.id, "connection": obj.name}
            if not tables:
                payload["mirror_missing"] = True
                payload["note"] = "لا مرايا لهذا الاتصال — أنشئ جدول المرآة أولاً من سجل الاتصال"
            return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})
        tables = _list_tables_obj(obj)
        return JsonResponse({"tables": tables, "total": len(tables), "engine": obj.engine})
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _table_fks_obj(obj, schema: str, table: str):
    """قيود المفاتيح الخارجية لجدول → [{column, ref_table, ref_column}] (postgres/oracle)."""
    if obj.engine == "postgres":
        conn = _pg_conn_for(obj)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT kcu.column_name, ccu.table_name, ccu.column_name"
                " FROM information_schema.table_constraints tc"
                " JOIN information_schema.key_column_usage kcu"
                "   ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema"
                " JOIN information_schema.constraint_column_usage ccu"
                "   ON ccu.constraint_name = tc.constraint_name"
                " JOIN information_schema.referential_constraints rc"
                "   ON rc.constraint_name = tc.constraint_name AND rc.constraint_schema = tc.table_schema"
                " WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %s AND tc.table_name = %s"
                " ORDER BY kcu.ordinal_position",
                (schema, table))
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
        return [{"column": r[0], "ref_table": r[1], "ref_column": r[2]} for r in rows]
    if obj.engine == "oracle":
        conn = _oracle_connect_obj(obj)
        try:
            owner = (schema or obj.user or "").upper()
            tname = (table or "").upper()
            cur = conn.cursor()
            cur.execute(
                "SELECT a.column_name, c.table_name, c.column_name"
                " FROM all_cons_columns a"
                " JOIN all_constraints b ON b.owner = a.owner AND b.constraint_name = a.constraint_name"
                " JOIN all_cons_columns c ON c.owner = b.r_owner AND c.constraint_name = b.r_constraint_name AND c.position = a.position"
                " WHERE b.constraint_type = 'R' AND b.owner = :o AND b.table_name = :t"
                " ORDER BY a.position",
                {"o": owner, "t": tname})
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
        return [{"column": r[0], "ref_table": r[1], "ref_column": r[2]} for r in rows]
    if obj.engine == "sqlserver":
        conn, _drv, _srv = _mssql_connect_obj(obj)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT pc.name, OBJECT_SCHEMA_NAME(fk.referenced_object_id) + '.' + OBJECT_NAME(fk.referenced_object_id), rc.name"
                " FROM sys.foreign_keys fk"
                " JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id"
                " JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id"
                " JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id"
                " WHERE OBJECT_SCHEMA_NAME(fk.parent_object_id) = ? AND OBJECT_NAME(fk.parent_object_id) = ?"
                " ORDER BY fkc.constraint_column_id",
                ((schema or "dbo"), table))
            rows = cur.fetchall()
            cur.close()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return [{"column": r[0], "ref_table": r[1], "ref_column": r[2]} for r in rows]
    return []


def api_connection_table_fks(request, conn_id, table):
    """GET /api/connections/<id>/tables/<table>/fks/ — قيود FK (لاقتراح الروابط تلقائياً)."""
    try:
        from .models import Connection
        obj = _effective_or_row(conn_id)
        if obj is None:
            return JsonResponse({"error": "not found"}, status=404)
        from .models import IoTMirror
        from .iot_sync import mirror_schema
        if _iot_is_conn(obj):
            try:
                m = IoTMirror.objects.select_related("local_connection").filter(
                    connection=obj, table_name=table.split(".")[-1]).first()
            except Exception:
                m = None
            if m is None:
                return JsonResponse({"fks": [], "total": 0, "mirror": True})
            try:
                sch = (mirror_schema(m.local_connection) or "").strip() or "public"
            except Exception:
                sch = "public"
            fks = _table_fks_obj(m.local_connection, sch, m.table_name)
            return JsonResponse({"fks": fks, "total": len(fks), "mirror": True,
                                 "local_connection_id": m.local_connection_id},
                                json_dumps_params={"ensure_ascii": False})
        if "." in table:
            schema, tname = table.split(".", 1)
        else:
            schema, tname = _default_schema_for(obj), table
        if not _valid_table_ref(schema) or not _valid_table_ref(tname):
            return JsonResponse({"error": "invalid table name"}, status=400)
        fks = _table_fks_obj(obj, schema, tname)
        return JsonResponse({"table": f"{schema}.{tname}" if schema else tname,
                             "fks": fks, "total": len(fks)},
                            json_dumps_params={"ensure_ascii": False})
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _preview_cell(v):
    """JSON-safe cell for table preview (dates→iso, Decimal→float, bytes→hex)."""
    try:
        import datetime as _d
        if isinstance(v, (_d.datetime, _d.date, _d.time)):
            return v.isoformat()
    except Exception:
        pass
    try:
        import decimal as _dec
        if isinstance(v, _dec.Decimal):
            return float(v)
    except Exception:
        pass
    if isinstance(v, (bytes, bytearray)):
        try:
            return {"$hex": bytes(v[:64]).hex(), "len": len(v)}
        except Exception:
            return None
    return v


def _table_preview_obj(obj, schema: str, table: str, limit: int, q=None, filters=None):
    """أول N صفاً من جدول في أي اتصال → {columns, rows} (للاستعلام التجريبي).

    q: بحث عام (يحتوي) عبر كل الأعمدة. filters: [{col, op, val}] تُجمع بـ AND —
    op ∈ contains/equals/gt/lt. أسماء الأعمدة تُطابق ضد أعمدة الجدول الفعلية.
    """
    n = max(1, min(int(limit or 50), 200))
    cols_info = _table_columns_obj(obj, schema, table)
    names = [c["name"] for c in cols_info]
    lookup = {c["name"]: c for c in cols_info}
    lookup_u = {c["name"].upper(): c for c in cols_info}

    def _col(name):
        if name in lookup:
            return lookup[name]
        c = lookup_u.get(str(name or "").upper())
        return c

    def _kind(c):
        t = str((c or {}).get("db_type") or "").upper()
        if "DATE" in t or "TIME" in t:
            return "date"
        if any(w in t for w in ("INT", "NUM", "NUMBER", "DECIMAL", "FLOAT", "DOUBLE", "MONEY")):
            return "number"
        return "text"

    def _norm_date(v):
        import re as _re
        s = str(v or "").strip()
        m = _re.fullmatch(r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", s)
        if m:
            return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        m = _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)
        if m:
            return s
        return None

    eng = obj.engine
    if eng == "postgres":
        Q = lambda c: f'"{c}"'
    elif eng == "oracle":
        Q = lambda c: f'"{c}"'
    elif eng == "sqlserver":
        Q = lambda c: f'[{c}]'
    elif eng == "mysql":
        Q = lambda c: f'`{c}`'
    else:
        raise ValueError(f"محرك غير مدعوم للمعاينة: {eng}")

    def _contains(col, style):
        # style: 'qmark' | 'format' | 'named'
        if eng == "oracle":
            return f"UPPER(TO_CHAR({Q(col)})) LIKE ", "named"
        if eng == "postgres":
            return f'CAST({Q(col)} AS TEXT) ILIKE ', "format"
        if eng == "sqlserver":
            return f"CAST({Q(col)} AS NVARCHAR(MAX)) LIKE ", "qmark"
        return f"CAST({Q(col)} AS CHAR) LIKE ", "format"

    def _date_eq(col, style):
        if eng == "oracle":
            return f"TRUNC({Q(col)}) = TO_DATE(", ", 'YYYY-MM-DD')"
        if eng == "postgres":
            return f"CAST({Q(col)} AS DATE) = TO_DATE(", ", 'YYYY-MM-DD')"
        if eng == "sqlserver":
            return f"CAST({Q(col)} AS DATE) = ", ""
        return f"DATE({Q(col)}) = ", ""

    ands, ors = [], []

    def _one(col_name, op, val, container, prefix):
        c = _col(col_name)
        if not c:
            return
        cn, kind = c["name"], _kind(c)
        op = str(op or "contains").lower()
        if op not in ("contains", "equals", "gt", "lt"):
            op = "contains"
        if op == "contains":
            expr, style = _contains(cn, None)
            pat = f"%{val}%"
            if eng == "oracle":
                container.append((expr + f":{prefix}", {prefix: pat.upper()}))
            elif eng == "postgres":
                container.append((expr + "%s", pat))
            elif eng == "sqlserver":
                container.append((expr + "?", pat))
            else:
                container.append((expr + "%s", pat))
        elif op == "equals" and kind == "date" and _norm_date(val):
            expr, tail = _date_eq(cn, None)
            dv = _norm_date(val)
            if eng == "oracle":
                container.append((expr + f":{prefix}{tail}", {prefix: dv}))
            elif eng == "postgres":
                container.append((expr + f"%s{tail}", dv))
            elif eng == "sqlserver":
                container.append((expr + "?", dv))
            else:
                container.append((expr + "%s", dv))
        else:
            sym = {"equals": "=", "gt": ">", "lt": "<"}[op]
            if eng == "oracle":
                container.append((f"{Q(cn)} {sym} :{prefix}", {prefix: val}))
            elif eng == "postgres":
                container.append((f"{Q(cn)} {sym} %s", val))
            elif eng == "sqlserver":
                container.append((f"{Q(cn)} {sym} ?", val))
            else:
                container.append((f"{Q(cn)} {sym} %s", val))

    if q is not None and str(q).strip() != "":
        for c in names[:60]:
            _one(c, "contains", str(q).strip(), ors, f"q{len(ors)}")
    for i, flt in enumerate((filters or [])[:20]):
        if not isinstance(flt, dict):
            continue
        if flt.get("col") and str(flt.get("val", "")).strip() != "":
            _one(flt["col"], flt.get("op"), str(flt["val"]).strip(), ands, f"f{i}")

    def _merge_oracle(parts):
        where, params = [], {}
        for sql, pd in parts:
            where.append(sql)
            params.update(pd)
        return where, params

    if eng == "oracle":
        conn = _oracle_connect_obj(obj)
        try:
            cur = conn.cursor()
            clauses, params = [], {}
            if ors:
                ow, op_ = _merge_oracle(ors)
                clauses.append("(" + " OR ".join(ow) + ")")
                params.update(op_)
            for sql, pd in ands:
                clauses.append("(" + sql + ")")
                params.update(pd)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            cur.execute(f'SELECT * FROM "{schema}"."{table}"{where} FETCH FIRST {n} ROWS ONLY', params)
            cols = [d[0] for d in (cur.description or [])]
            rows = [[_preview_cell(v) for v in r] for r in cur.fetchall()]
            cur.close()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return cols, rows

    # postgres / sqlserver / mysql — positional params
    if eng == "postgres":
        conn = _pg_conn_for(obj)
        closer = conn.close
        qmark = "%s"
    elif eng == "sqlserver":
        conn, _, _ = _mssql_connect_obj(obj, timeout=10)
        closer = conn.close
        qmark = "?"
    else:
        import MySQLdb
        conn = MySQLdb.connect(host=obj.host, user=obj.user, passwd=obj.password or "",
                               db=obj.instance or None, port=int(obj.port or 3306), connect_timeout=10)
        closer = conn.close
        qmark = "%s"
    try:
        cur = conn.cursor()
        clauses, params = [], []
        if ors:
            ow = []
            for sql, v in ors:
                ow.append(sql)
                params.append(v)
            clauses.append("(" + " OR ".join(ow) + ")")
        for sql, v in ands:
            clauses.append("(" + sql + ")")
            params.append(v)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        if eng == "postgres":
            cur.execute(f'SELECT * FROM "{schema}"."{table}"{where} LIMIT {n}', params)
        elif eng == "sqlserver":
            cur.execute(f"SELECT TOP {n} * FROM [{schema}].[{table}]{where}", params)
        else:
            cur.execute(f"SELECT * FROM `{schema}`.`{table}`{where} LIMIT {n}", params)
        cols = [d[0] for d in (cur.description or [])]
        rows = [[_preview_cell(v) for v in r] for r in cur.fetchall()]
        cur.close()
    finally:
        closer()
    return cols, rows


def api_connection_table_preview(request, conn_id, table):
    """GET /api/connections/<id>/tables/<table>/preview/?limit=50 — استعلام تجريبي (أول N صفاً).

    اتصال IoT يُحوَّل تلقائياً لجدول المرآة على الاتصال المحلي.
    """
    try:
        from .models import Connection
        obj = _effective_or_row(conn_id)
        if obj is None:
            return JsonResponse({"error": "not found"}, status=404)
        try:
            limit = int(request.GET.get("limit", "50"))
        except Exception:
            limit = 50
        q = (request.GET.get("q") or "").strip() or None
        filters = []
        try:
            _fj = json.loads(request.GET.get("filters") or "[]")
            if isinstance(_fj, list):
                filters = [f for f in _fj if isinstance(f, dict) and f.get("col")][:20]
        except Exception:
            filters = []
        if "." in table:
            _, tname = table.split(".", 1)
        else:
            tname = table
        if _iot_is_conn(obj):
            from .models import IoTMirror
            from .iot_sync import mirror_schema
            try:
                m = IoTMirror.objects.select_related("local_connection").filter(
                    connection=obj, table_name=tname).first()
            except Exception:
                m = None
            if m is None:
                return JsonResponse({"error": "الجداول المتاحة لاتصال IoT هي جداول المرايا فقط — أنشئ المرآة أولاً"}, status=400)
            try:
                sch = (mirror_schema(m.local_connection) or "").strip() or "public"
            except Exception:
                sch = "public"
            cols, rows = _table_preview_obj(m.local_connection, sch, m.table_name, limit, q, filters)
            return JsonResponse({"table": f"{sch}.{m.table_name}", "columns": cols, "rows": rows,
                                 "total": len(rows), "limit": limit, "mirror": True,
                                 "local_connection_id": m.local_connection_id},
                                json_dumps_params={"ensure_ascii": False})
        if "." in table:
            schema, tname = table.split(".", 1)
        else:
            schema, tname = _default_schema_for(obj), table
        if not _valid_table_ref(schema) or not _valid_table_ref(tname):
            return JsonResponse({"error": "invalid table name"}, status=400)
        cols, rows = _table_preview_obj(obj, schema, tname, limit, q, filters)
        return JsonResponse({"table": f"{schema}.{tname}" if schema else tname,
                             "columns": cols, "rows": rows, "total": len(rows), "limit": limit},
                            json_dumps_params={"ensure_ascii": False})
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)[:300]}, status=500)


def api_connection_table_columns(request, conn_id, table):
    """GET /api/connections/<id>/tables/<table>/columns/ — أعمدة أي جدول (تشمل البعيدة).

    اتصال IoT يُحوَّل تلقائياً لأعمدة جدول المرآة على الاتصال المحلي.
    """
    try:
        from .models import Connection
        obj = _effective_or_row(conn_id)
        if obj is None:
            return JsonResponse({"error": "not found"}, status=404)
        if "." in table:
            _, tname = table.split(".", 1)
        else:
            tname = table
        if _iot_is_conn(obj):
            from .models import IoTMirror
            from .iot_sync import mirror_schema
            try:
                m = IoTMirror.objects.select_related("local_connection").filter(
                    connection=obj, table_name=tname).first()
            except Exception:
                m = None
            if m is None:
                return JsonResponse({"error": "الجداول المتاحة لاتصال IoT هي جداول المرايا فقط — أنشئ المرآة أولاً"}, status=400)
            try:
                sch = (mirror_schema(m.local_connection) or "").strip() or "public"
            except Exception:
                sch = "public"
            cols = _table_columns_obj(m.local_connection, sch, m.table_name)
            return JsonResponse({"table": f"{sch}.{m.table_name}", "columns": cols, "total": len(cols),
                                 "mirror": True, "local_connection_id": m.local_connection_id},
                                json_dumps_params={"ensure_ascii": False})
        if "." in table:
            schema, tname = table.split(".", 1)
        else:
            schema, tname = _default_schema_for(obj), table
        if not _valid_table_ref(schema) or not _valid_table_ref(tname):
            return JsonResponse({"error": "invalid table name"}, status=400)
        cols = _table_columns_obj(obj, schema, tname)
        return JsonResponse({"table": f"{schema}.{tname}" if schema else tname, "columns": cols, "total": len(cols)})
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ── CML Settings Engine (controls + business rules) ──────────────────────────
# ملفات CML تُدار عبر مشغل النماذج (FMLK) وليس قواعد الأعمال — مخفية من الإعدادات
FORMS_PLAYER_CML = {"companies.cml", "branches.cml", "users.cml"}


def _cml_scan():
    """Scan system/settings/*.cml (scope=system) + */*.cml in app folders (scope=app)."""
    from cml_engine.compiler import CMLCompiler
    items = []
    # System settings
    for cand in [BASE_DIR / "odex" / "system" / "settings", BASE_DIR / "system" / "settings"]:
        if cand.exists():
            for p in sorted(cand.glob("*.cml")):
                if p.name in FORMS_PLAYER_CML:
                    continue  # شركات/فروع/مستخدمون → مشغل النماذج فقط
                try:
                    comp = CMLCompiler(path=p)
                    md = comp.cml_metadata()
                    items.append({
                        "scope": "system", "app": "settings", "file": p.name,
                        "metadata": md,
                        "controls": len(comp.controls()), "rules": len(comp.rules()),
                        "rules_list": [r.to_dict() for r in comp.rules()],
                    })
                except Exception as e:
                    items.append({"scope": "system", "app": "settings", "file": p.name,
                                  "metadata": {"displayName": p.stem}, "controls": 0, "rules": 0, "rules_list": [], "error": str(e)})
            break
    # App-level cml files
    for system in [BASE_DIR / "odex" / "system", BASE_DIR / "system"]:
        if not system.exists():
            continue
        for app_dir in sorted([d for d in system.iterdir() if d.is_dir()]):
            if app_dir.name == "settings":
                continue
            for p in sorted(app_dir.glob("*.cml")):
                try:
                    comp = CMLCompiler(path=p)
                    md = comp.cml_metadata()
                    if not md.get("app"):
                        md["app"] = app_dir.name
                    if md.get("scope", "system") not in ("system", "app"):
                        md["scope"] = "app"
                    items.append({
                        "scope": md.get("scope") or "app", "app": app_dir.name, "file": p.name,
                        "metadata": md,
                        "controls": len(comp.controls()), "rules": len(comp.rules()),
                        "rules_list": [r.to_dict() for r in comp.rules()],
                    })
                except Exception as e:
                    items.append({"scope": "app", "app": app_dir.name, "file": p.name,
                                  "metadata": {"displayName": p.stem}, "controls": 0, "rules": 0, "rules_list": [], "error": str(e)})
        break
    return items


def _cml_resolve(scope, app, file):
    """Locate a .cml file; guard against path traversal."""
    if not file or "/" in file or "\\" in file or ".." in file:
        return None
    if not file.endswith(".cml"):
        file += ".cml"
    if scope == "system":
        for cand in [BASE_DIR / "odex" / "system" / "settings" / file, BASE_DIR / "system" / "settings" / file]:
            if cand.exists():
                return cand
        return None
    for base in [BASE_DIR / "odex" / "system" / app, BASE_DIR / "system" / app]:
        cand = base / file
        if cand.exists():
            return cand
    return None


def _cml_values_path(cml_path):
    return cml_path.with_suffix("") .with_name(cml_path.stem + ".values.json")


def _cml_all_rules(items):
    """Flatten business rules across scanned CML files for the sidebar."""
    out = []
    for it in items:
        for r in it.get("rules_list") or []:
            out.append({
                "name": r.get("name"), "type": r.get("type"),
                "target": r.get("target"), "message": r.get("message"),
                "scope": it.get("scope"), "app": it.get("app"), "file": it.get("file"),
                "parent": (it.get("metadata") or {}).get("displayName") or it.get("file"),
            })
    return out


def _settings_fmlk_scan():
    """نماذج الإعدادات: كل .fmlk في مجلد settings بالبنية الجديدة."""
    from fmlk_engine.compiler import FMLKFormCompiler
    out = []
    for cand in [BASE_DIR / "odex" / "system" / "settings", BASE_DIR / "system" / "settings"]:
        if not cand.exists():
            continue
        for p in sorted(cand.glob("*.fmlk")):
            try:
                comp = FMLKFormCompiler(path=p)
                md = comp.fml_metadata()
                flds = comp.fields()
                out.append({
                    "file": p.name, "metadata": md,
                    "fields": len(flds),
                    "primary_keys": [f.name for f in flds if getattr(f, "primary_key", False)],
                })
            except Exception as e:
                out.append({"file": p.name, "metadata": {"displayName": p.stem},
                            "fields": 0, "primary_keys": [], "error": str(e)})
        break
    return out


def _settings_base_ctx():
    """سياق مشترك لصفحات الإعدادات: CML + FMLK + connections + models + onboarding."""
    from .models import Connection
    items = _cml_scan()
    system_items = [i for i in items if i["scope"] == "system"]
    app_items = [i for i in items if i["scope"] != "system"]
    by_app = {}
    for i in app_items:
        by_app.setdefault(i["app"], []).append(i)
    try:
        connections = [c.to_dict() for c in Connection.objects.all().order_by("name")]
    except Exception:
        connections = []
    models = _scan_fml_models()
    all_apps = [{"name": a["name"], "ar": a.get("ar") or a["name"]} for a in _load_system_apps()]
    fmlk_items = _settings_fmlk_scan()
    fmlk_by_category = {}
    for f in fmlk_items:
        cat = (f.get("metadata") or {}).get("category") or "عام"
        fmlk_by_category.setdefault(cat, []).append(f)
    try:
        from .models import Company, Branch
        needs_setup = Company.objects.count() == 0 or Branch.objects.count() == 0
    except Exception:
        needs_setup = False
    return {
        "system_items": system_items,
        "app_items": app_items,
        "by_app": by_app,
        "total": len(items) + len(fmlk_items),
        "all_rules": _cml_all_rules(items),
        "connections": connections,
        "models": models,
        "all_apps": all_apps,
        "fmlk_items": fmlk_items,
        "fmlk_by_category": fmlk_by_category,
        "fmlk_side_groups": _group_files_by_category(fmlk_items),
        "needs_setup": needs_setup,
    }


def _scan_fml_models():
    """سجل الموديلات: كل .fmlk في النظام مع الجدول والسكима وعدد الحقول و PK."""
    from fmlk_engine.compiler import FMLKFormCompiler
    out = []
    for system in [BASE_DIR / "odex" / "system", BASE_DIR / "system"]:
        if not system.exists():
            continue
        for app_dir in sorted([d for d in system.iterdir() if d.is_dir()]):
            for p in sorted(app_dir.glob("*.fmlk")):
                try:
                    comp = FMLKFormCompiler(path=p)
                    md = comp.fml_metadata()
                    flds = comp.fields()
                    pks = [f.name for f in flds if getattr(f, "primary_key", False)]
                    out.append({
                        "app": app_dir.name, "file": p.name,
                        "table_ar": md.get("displayName") or p.stem,
                        "table_en": md.get("table") or md.get("name"),
                        "model_type": md.get("model_type") or md.get("modelType") or "form",
                        "schema": md.get("schema") or "public",
                        "fields": len(flds), "primary_keys": pks,
                    })
                except Exception as e:
                    out.append({"app": app_dir.name, "file": p.name, "table_ar": p.stem,
                                "table_en": "", "model_type": "form", "schema": "",
                                "fields": 0, "primary_keys": [], "error": str(e)})
        break
    return out


def settings_home(request):
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    return render(request, "settings.html", _settings_base_ctx())


def settings_setup(request):
    """First-run wizard: company → db init/migrate → seed local connection from FMLK."""
    ctx = _settings_base_ctx()
    try:
        from .models import Country, City, Currency
        ctx["countries"] = [c.to_dict() for c in Country.objects.filter(is_active=True).order_by("name")]
        ctx["cities"] = [c.to_dict() for c in City.objects.filter(is_active=True).order_by("name")]
        ctx["currencies"] = [c.to_dict() for c in Currency.objects.filter(is_active=True).order_by("code")]
    except Exception:
        ctx["countries"] = []
        ctx["cities"] = []
        ctx["currencies"] = []
    try:
        import json as _jw
        ctx["wizard_json"] = _jw.dumps(_wizard_status_payload(), ensure_ascii=False)
    except Exception:
        ctx["wizard_json"] = "{}"
    return render(request, "setup.html", ctx)


@csrf_exempt
def api_setup_create(request):
    """POST /api/setup/create/ — إنشاء الشركة الأولى + الفرع + سكيما company_branch_year."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        from .models import Company, Branch, build_schema_name
        code = (data.get("company_code") or "").strip()
        name = (data.get("company_name") or "").strip()
        bcode = (data.get("branch_code") or "MAIN").strip() or "MAIN"
        bname = (data.get("branch_name") or "الفرع الرئيسي").strip()
        try:
            year = int(data.get("fiscal_year") or 2026)
        except Exception:
            year = 2026
        if not code or not name:
            return JsonResponse({"error": "company_code و company_name مطلوبان"}, status=400)
        if Company.objects.exists():
            return JsonResponse({"error": "توجد شركة بالفعل — البوابة مغلقة"}, status=400)
        comp = Company.objects.create(
            code=code, name=name, name_en=data.get("company_name_en", ""),
            tax_number=data.get("tax_number", ""),
            country_id=data.get("country") or None, city_id=data.get("city") or None,
            currency_id=data.get("currency") or None, fiscal_year=year,
        )
        branch = Branch(company=comp, code=bcode, name=bname,
                        city_id=data.get("city") or None, branch_type="main")
        branch.schema_name = build_schema_name(comp.code, branch.code, comp.fiscal_year)
        branch.save()
        # NOTE: physical schema creation happens in wizard step 2
        # (POST /api/setup/wizard/migrate/) — DB-aware (postgres/sqlite).
        return JsonResponse({"ok": True, "company": comp.to_dict(), "branch": branch.to_dict(),
                             "schema": branch.schema_name})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ── app.conf + DB password envelope: canonical implementation in config/dbconf.py ──
# (kept importable here so all call sites below stay unchanged)
from config.dbconf import (
    APPCONF_KEYS,
    DBPASS_ROUNDS,
    read_appconf as _appconf_read,
    write_appconf as _appconf_write,
    dbpass_encrypt as _dbpass_encrypt,
    dbpass_decrypt as _dbpass_decrypt,
    dbpass_resolve as _dbpass_resolve,
    has_db_details as _has_db_details,
)


# ── First-run wizard: company → db init/migrate → seed local connection from FMLK ──
WIZARD_FMLK_FILES = [
    "01_countries.fmlk", "02_cities.fmlk", "03_currencies.fmlk",
    "04_companies.fmlk", "05_branches.fmlk", "06_users.fmlk",
    "connections.fmlk", "models.fmlk", "permissions.fmlk",
]


def _wizard_first_company():
    """First Company + its first Branch (or Nones)."""
    from .models import Company, Branch
    try:
        comp = Company.objects.order_by("id").first()
    except Exception:
        return None, None
    if not comp:
        return None, None
    try:
        br = Branch.objects.filter(company=comp).order_by("id").first()
    except Exception:
        br = None
    return comp, br


def _wizard_fmlk_specs():
    """The 9 settings models: {file, table, schema, fields} read from each .fmlk."""
    from fmlk_engine.compiler import FMLKFormCompiler
    specs = []
    for fname in WIZARD_FMLK_FILES:
        try:
            path = _find_fml_path(fname, "settings")
            if not path or not path.exists():
                specs.append({"file": fname, "ok": False, "error": "file not found"})
                continue
            comp = FMLKFormCompiler(path=path)
            meta = comp.fml_metadata()
            specs.append({
                "file": fname,
                "ok": True,
                "table": meta.get("table") or meta.get("name"),
                "schema": meta.get("schema") or "public",
                "fields": len(comp.fields()),
            })
        except Exception as e:
            specs.append({"file": fname, "ok": False, "error": str(e)})
    return specs


def _wizard_translate_ddl(ddl, vendor):
    """Split DDL into statements; translate PG-isms for sqlite.

    - sqlite has no CREATE SCHEMA → drop that statement (single-file DB).
    - SERIAL PRIMARY KEY → INTEGER PRIMARY KEY AUTOINCREMENT.
    - "schema"."table" → "table".
    """
    if vendor == "sqlite":
        ddl = "\n".join(
            l for l in (ddl or "").splitlines()
            if not l.strip().upper().startswith("CREATE SCHEMA")
        )
        ddl = _re_pg.sub(r'"id"\s+SERIAL\s+PRIMARY\s+KEY', '"id" INTEGER PRIMARY KEY AUTOINCREMENT', ddl)
        ddl = _re_pg.sub(r'"[^"]+"\."([^"]+)"', r'"\1"', ddl)
    return [s.strip() for s in (ddl or "").split(";") if s.strip()]


def _wizard_field_add_stmt(vendor, schema, table, f):
    """ALTER TABLE ... ADD COLUMN stmt for one FMLK field (None when inapplicable).

    New columns stay NULLABLE (static DEFAULT only) so existing rows never break.
    """
    if not getattr(f, "name", None):
        return None
    from fmlk_engine.engine import FMLKFormEngine
    dt = ((f.data_type or "VARCHAR")).upper().split("(")[0].strip()
    typ = FMLKFormEngine.DATA_TYPE_MAP.get(dt, "TEXT")
    default_sql = ""
    d = f.default
    if d is not None and str(d).strip() != "" and not getattr(f, "formula", None):
        ds = str(d).strip()
        if dt in ("INTEGER", "INT", "BIGINT", "NUMERIC", "DECIMAL", "FLOAT") \
                and _re_pg.fullmatch(r"-?\d+(\.\d+)?", ds):
            default_sql = f" DEFAULT {ds}"
        elif dt == "BOOLEAN" and ds.lower() in ("true", "false", "1", "0"):
            default_sql = " DEFAULT TRUE" if ds.lower() in ("true", "1") else " DEFAULT FALSE"
        else:
            default_sql = " DEFAULT '%s'" % ds.replace("'", "''")
    if vendor == "postgresql":
        return f'ALTER TABLE "{schema}"."{table}" ADD COLUMN IF NOT EXISTS "{f.name}" {typ}{default_sql}'
    return f'ALTER TABLE "{table}" ADD COLUMN "{f.name}" {typ}{default_sql}'


def _wizard_pg_describe(pg, schema, table):
    cur = pg.cursor()
    cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", (schema, table))
    rows = cur.fetchall()
    try:
        cur.close()
    except Exception:
        pass
    return rows


WIZARD_DATA_SOURCES = {
    "urs_country": "Country", "urs_city": "City", "urs_currency": "Currency",
    "urs_company": "Company", "urs_branch": "Branch", "urs_connection": "Connection",
}


def _wizard_seed_pg_table_data(pg, schema, table):
    """Copy Django ORM rows into a FRESH pg table (matching columns only). Returns n.

    Runs only when the target is empty (idempotent). Fixes sequences for id PKs.
    Tables without an ORM counterpart (sys_*) are skipped.
    """
    from django.apps import apps as _apps
    model_name = WIZARD_DATA_SOURCES.get(table)
    if not model_name:
        return 0
    try:
        model = _apps.get_model("urs", model_name)
    except Exception:
        return 0
    cur = pg.cursor()
    try:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
        if (cur.fetchone() or [1])[0] > 0:
            return 0
        rows = list(model.objects.all().values())
        if not rows:
            return 0
        tcols = {r[0] for r in _wizard_pg_describe(pg, schema, table)}
        use = [c for c in rows[0].keys() if c in tcols]
        if not use:
            return 0
        qcols = ", ".join('"%s"' % c for c in use)
        args = ",".join(
            cur.mogrify("(" + ",".join(["%s"] * len(use)) + ")",
                        [r.get(c) for c in use]).decode("utf-8")
            for r in rows)
        cur.execute(f'INSERT INTO "{schema}"."{table}" ({qcols}) VALUES {args}')
        n = cur.rowcount or 0
        if "id" in use:
            try:
                cur.execute("SELECT setval(pg_get_serial_sequence(%s, %s), "
                            f'(SELECT MAX(id) FROM "{schema}"."{table}"))',
                            [f'"{schema}"."{table}"', "id"])
            except Exception:
                pass
        return n
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _wizard_fmlk_add_missing_columns(vendor, schema, table, fcomp):
    """ADD COLUMN for FMLK fields absent from the real table (ORM-collision drift).

    Postgres: ADD COLUMN IF NOT EXISTS (race-safe). sqlite: pre-checked, unqualified.
    New columns stay NULLABLE (with static DEFAULT when the FMLK declares one) so
    existing rows never break. Returns [added names].
    """
    from django.db import connection as _dj
    try:
        with _dj.cursor() as cur:
            real = {c.name.lower() for c in _dj.introspection.get_table_description(cur, table)}
    except Exception:
        return []
    added = []
    for f in (fcomp.fields() or []):
        if not f.name or f.name.lower() in real:
            continue
        stmt = _wizard_field_add_stmt(vendor, schema, table, f)
        if not stmt:
            continue
        try:
            with _dj.cursor() as cur:
                cur.execute(stmt)
            added.append(f.name)
            real.add(f.name.lower())
        except Exception:
            continue
    return added


def _wizard_status_payload():
    from django.db import connection as _dj
    from .models import Connection
    comp, br = _wizard_first_company()
    vendor = _dj.vendor
    try:
        local = Connection.objects.filter(name="urs_local").first()
    except Exception:
        local = None
    try:
        existing = set(_dj.introspection.table_names())
    except Exception:
        existing = set()
    tables = []
    for spec in _wizard_fmlk_specs():
        if not spec.get("ok"):
            tables.append({**spec, "exists": False, "missing": []})
        else:
            exists = (spec["table"] in existing)
            missing = []
            if exists:
                try:
                    from fmlk_engine.compiler import FMLKFormCompiler
                    _cf = FMLKFormCompiler(path=_find_fml_path(spec["file"], "settings"))
                    with _dj.cursor() as _cur:
                        _real = {c.name.lower() for c in _dj.introspection.get_table_description(_cur, spec["table"])}
                    missing = [f.name for f in (_cf.fields() or []) if f.name and f.name.lower() not in _real]
                except Exception:
                    missing = []
            tables.append({**spec, "exists": exists, "missing": missing})
    return {
        "backend": vendor,
        "has_company": bool(comp),
        "appconf_has_db": _has_db_details(),
        "company": comp.to_dict() if comp else None,
        "branch": br.to_dict() if br else None,
        "schema": (br.schema_name if br and br.schema_name else None),
        "urs_local": local.to_dict() if local else None,
        "tables": tables,
        "tables_ready": all(t.get("exists") for t in tables if t.get("ok")),
    }


@csrf_exempt
def api_setup_wizard_status(request):
    """GET /api/setup/wizard/status/ — wizard state for all 4 steps."""
    try:
        return JsonResponse({"ok": True, **_wizard_status_payload()},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_setup_wizard_migrate(request):
    """POST /api/setup/wizard/migrate/ — step 2: Django migrate + ensure company schema.

    DB-aware: postgres → CREATE SCHEMA; sqlite → no-op (single file).
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from django.core.management import call_command
        from django.db import connection as _dj
        comp, br = _wizard_first_company()
        if not comp:
            return JsonResponse({"error": "أنشئ الشركة أولاً (الخطوة 1)"}, status=400)
        call_command("migrate", verbosity=0, interactive=False)
        # migrate INTO the schema of the connection in app.conf (branch schema when unset)
        _ac = _appconf_read()
        schema = (_ac.get("DB_SCHEMA") or "").strip() or (br.schema_name if br and br.schema_name else None)
        schema_ok, schema_note = False, ""
        if _dj.vendor == "postgresql" and schema:
            with _dj.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
            schema_ok, schema_note = True, f"schema {schema} ready"
        elif _dj.vendor == "sqlite":
            schema_ok, schema_note = True, "single-file DB: no schema needed"
        else:
            schema_note = f"backend {_dj.vendor}: schema step skipped"
        return JsonResponse({"ok": True, "backend": _dj.vendor, "migrate_ok": True,
                             "schema": schema, "schema_ok": schema_ok, "note": schema_note},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _wizard_retarget_fmlk_schema(text, new_schema):
    """Rewrite `schema=` inside the <fml_metadata> tag only (minimal diff)."""
    pat = r'(<fml_metadata\b[^>]*?)\bschema\s*=\s*"[^"]*"'
    new_text, n = _re_pg.subn(pat, r'\g<1>schema="%s"' % new_schema, text, count=1, flags=_re_pg.S)
    if n == 0:
        pat2 = r'<fml_metadata\b(?![^>]*\bschema\s*=)'
        new_text, n = _re_pg.subn(pat2, '<fml_metadata schema="%s"' % new_schema, text, count=1)
    return new_text, n


@csrf_exempt
def api_setup_wizard_seed(request):
    """POST /api/setup/wizard/seed/ — step 4: seed local connection + create FMLK tables.

    - Ensures Connection `urs_local` (is_local, schema = branch schema).
    - Builds DDL per settings .fmlk and executes it on the Django backend.
    - Optional body {schema, rewrite_files}: migrate INTO the schema of the connection
      (app.conf DB_SCHEMA): tables are created there and, when rewrite_files is true,
      each file's <fml_metadata schema> is rewritten to match (verified by re-parse).
    Idempotent: ensure-id-1 + CREATE TABLE IF NOT EXISTS + ADD COLUMN.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from django.db import connection as _dj
        from .models import Connection
        from fmlk_engine.compiler import FMLKFormCompiler
        from fmlk_engine.engine import FMLKFormEngine
        comp, br = _wizard_first_company()
        if not comp:
            return JsonResponse({"error": "أنشئ الشركة أولاً (الخطوة 1)"}, status=400)
        try:
            _body = json.loads(request.body.decode() or "{}")
        except Exception:
            _body = {}
        override_schema = (_body.get("schema") or "").strip()
        rewrite_files = bool(_body.get("rewrite_files"))
        vendor = _dj.vendor
        settings_dict = _dj.settings_dict
        # Never clobber a configured row with Django defaults: seed params only on FIRST create.
        _row = Connection.objects.filter(name="urs_local").first()
        if _row is None:
            local, forced_id1 = _wizard_ensure_urs_local({
                "host": settings_dict.get("HOST") or "127.0.0.1",
                "port": int(settings_dict.get("PORT") or 5432),
                "user": settings_dict.get("USER") or "postgres",
                "password": settings_dict.get("PASSWORD") or "",
                "instance": str(settings_dict.get("NAME") or "urs"),
                "engine": "postgres",
                "conn_type": "database",
                "is_local": True,
                "is_queryable": True,
                "schema": (br.schema_name if br and br.schema_name else ""),
                "description": "seeded by first-run wizard",
            })
        else:
            local, forced_id1 = _row, False
            if not (local.schema or "") and br and br.schema_name:
                local.schema = br.schema_name
                try:
                    local.save(update_fields=["schema"])
                except Exception:
                    pass
        # DDL target: the EFFECTIVE default connection (postgres) when reachable,
        # else the Django backend (previous behavior). Seed never writes sqlite
        # params over a configured row.
        pg, pg_target = None, ""
        _eff = _wizard_effective_conn()
        # unify: app.conf wins → sync row #1 to it (so RML/direct-row readers agree too)
        row_synced = False
        if _eff is not None:
            try:
                _ac0 = _appconf_read()
                _r0 = Connection.objects.filter(id=1).first()
                if _r0 is not None:
                    _upd = {}
                    if (_ac0.get("DB_ENGINE") or "").strip() and _r0.engine != _eff.engine:
                        _upd["engine"] = _eff.engine
                    if (_ac0.get("DB_HOST") or "").strip():
                        if _r0.host != _eff.host:
                            _upd["host"] = _eff.host
                        try:
                            _p0 = int(_eff.port or 5432)
                        except (TypeError, ValueError):
                            _p0 = 5432
                        if _r0.port != _p0:
                            _upd["port"] = _p0
                    if (_ac0.get("DB_USER") or "").strip() and _r0.user != _eff.user:
                        _upd["user"] = _eff.user
                    if _eff.password and _r0.password != _eff.password:
                        _upd["password"] = _eff.password
                    if (_ac0.get("DB_NAME") or "").strip() and _r0.instance != _eff.instance:
                        _upd["instance"] = _eff.instance
                    if (_ac0.get("DB_SCHEMA") or "").strip() and _r0.schema != _eff.schema:
                        _upd["schema"] = _eff.schema
                    if _upd:
                        for _k, _v in _upd.items():
                            setattr(_r0, _k, _v)
                        _r0.save()
                        row_synced = True
            except Exception:
                pass
            try:
                local = Connection.objects.filter(name="urs_local").first() or local
            except Exception:
                pass
        if _eff is not None and (_eff.engine or "postgres") == "postgres" and (_eff.host or ""):
            try:
                import psycopg2
                pg = psycopg2.connect(dbname=(_eff.instance or "urs"), user=_eff.user,
                                      password=_eff.password or "", host=_eff.host,
                                      port=int(_eff.port or 5432), connect_timeout=8)
                pg.autocommit = True
                pg_target = f"{_eff.host}/{_eff.instance or 'urs'}"
            except Exception as e:
                try:
                    if pg:
                        pg.close()
                except Exception:
                    pass
                pg = None
                return JsonResponse({"error": f"تعذر الوصول للاتصال الافتراضي ({_eff.host}): {str(e)[:200]}"}, status=400)
        target = ("effective:" + pg_target) if pg else ("django:" + vendor)
        results = []
        for spec in _wizard_fmlk_specs():
            if not spec.get("ok"):
                results.append({**spec, "created": False})
                continue
            try:
                path = _find_fml_path(spec["file"], "settings")
                fcomp = FMLKFormCompiler(path=path)
                feng = FMLKFormEngine(fcomp, None)
                dst_schema = override_schema or spec["schema"]
                schema_rewritten, schema_from = False, spec["schema"]
                if override_schema and override_schema != spec["schema"] and rewrite_files:
                    import xml.etree.ElementTree as _ET2
                    raw0 = path.read_text(encoding="utf-8")
                    new_text, n0 = _wizard_retarget_fmlk_schema(raw0, override_schema)
                    if n0 > 0:
                        got0 = _ET2.fromstring(new_text).find("fml_metadata").get("schema", "")
                        if got0 == override_schema:
                            path.write_text(new_text, encoding="utf-8")
                            # recompile so DDL/columns follow the new schema
                            fcomp = FMLKFormCompiler(path=path)
                            feng = FMLKFormEngine(fcomp, None)
                            schema_rewritten = True
                ddl = feng.build_create_table_ddl(schema=dst_schema, table=spec["table"])
                cols, added, data_n = [], [], 0
                if pg is not None:
                    # effective postgres: native DDL, schema-qualified introspection
                    _pgcur = pg.cursor()
                    try:
                        _pgcur.execute(f'CREATE SCHEMA IF NOT EXISTS "{dst_schema}";')
                        for st in [s.strip() for s in ddl.split(";") if s.strip()]:
                            _pgcur.execute(st)
                        _real = {r[0].lower() for r in _wizard_pg_describe(pg, dst_schema, spec["table"])}
                        for f in (fcomp.fields() or []):
                            if not f.name or f.name.lower() in _real:
                                continue
                            _stmt = _wizard_field_add_stmt("postgresql", dst_schema, spec["table"], f)
                            if not _stmt:
                                continue
                            try:
                                _pgcur.execute(_stmt)
                                added.append(f.name)
                                _real.add(f.name.lower())
                            except Exception:
                                continue
                        cols = [r[0] for r in _wizard_pg_describe(pg, dst_schema, spec["table"])]
                    finally:
                        try:
                            _pgcur.close()
                        except Exception:
                            pass
                    # fill fresh tables with live Django rows (ORM counterparts only)
                    try:
                        data_n = _wizard_seed_pg_table_data(pg, dst_schema, spec["table"])
                    except Exception:
                        data_n = 0
                else:
                    if vendor == "postgresql":
                        # the FMLK-declared schema itself must exist first (else CREATE TABLE fails)
                        with _dj.cursor() as cur:
                            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{dst_schema}";')
                    stmts = _wizard_translate_ddl(ddl, vendor)
                    with _dj.cursor() as cur:
                        for st in stmts:
                            cur.execute(st)
                    added = _wizard_fmlk_add_missing_columns(vendor, dst_schema, spec["table"], fcomp)
                    try:
                        with _dj.cursor() as cur:
                            cols = [c.name for c in _dj.introspection.get_table_description(cur, spec["table"])]
                    except Exception:
                        cols = []
                results.append({**spec, "schema": dst_schema, "schema_from": schema_from,
                                "schema_rewritten": schema_rewritten, "target": target,
                                "created": True, "added": added, "data": data_n,
                                "columns": cols, "ncols": len(cols)})
            except Exception as e:
                results.append({**spec, "created": False, "error": str(e)})
        try:
            if pg is not None:
                pg.close()
        except Exception:
            pass
        return JsonResponse({"ok": True, "backend": vendor, "target": target,
                             "connection": local.to_dict(), "row_synced": row_synced,
                             "forced_id1": forced_id1, "results": results,
                             "ready": all(r.get("created") for r in results)},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


WIZARD_DB_ENGINES = ["postgres", "oracle", "sqlserver", "mysql"]
WIZARD_ENGINE_PORTS = {"postgres": 5432, "oracle": 1521, "sqlserver": 1433, "mysql": 3306}


def _wizard_ensure_urs_local(defaults):
    """Seed/update the default connection, forcing id=1 on a fresh table.

    Returns (obj, forced_id1: bool). Existing rows keep their id (FK-safe:
    RML connection_id and IoTMirror reference global ids).
    """
    from .models import Connection
    from django.db import connection as _dj
    existing = Connection.objects.filter(name="urs_local").first()
    if existing:
        for k, v in (defaults or {}).items():
            setattr(existing, k, v)
        existing.save()
        return existing, False
    if Connection.objects.exists():
        return Connection.objects.create(name="urs_local", **(defaults or {})), False
    obj = Connection(name="urs_local", id=1, **(defaults or {}))
    obj.save(force_insert=True)
    try:
        tbl = Connection._meta.db_table
        with _dj.cursor() as cur:
            if _dj.vendor == "postgresql":
                cur.execute(
                    f'SELECT setval(pg_get_serial_sequence(%s, %s), (SELECT MAX(id) FROM "{tbl}"))',
                    [tbl, "id"],
                )
            elif _dj.vendor == "sqlite":
                cur.execute(f'SELECT seq FROM sqlite_sequence WHERE name=%s', [tbl])
                row = cur.fetchone()
                if row is None:
                    cur.execute(f'INSERT INTO sqlite_sequence (name, seq) VALUES (%s, (SELECT MAX(id) FROM "{tbl}"))', [tbl])
                else:
                    cur.execute(f'UPDATE sqlite_sequence SET seq=(SELECT MAX(id) FROM "{tbl}") WHERE name=%s', [tbl])
    except Exception:
        pass
    return obj, True


def _wizard_ensure_schema_on_conn(engine, host, port, user, password, dbname, schema):
    """CREATE SCHEMA on the `urs` database THROUGH the default connection params.

    Returns (ok: bool, note: str). postgres fully; sqlserver best-effort; else manual-note.
    """
    if not schema:
        return False, "no schema"
    try:
        if engine == "postgres":
            import psycopg2
            conn = psycopg2.connect(dbname=dbname or "urs", user=user, password=password or "",
                                    host=host, port=int(port or 5432), connect_timeout=5)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
            conn.close()
            return True, f"schema {schema} ready on {dbname or 'urs'}"
        if engine == "sqlserver":
            import pyodbc
            conn = pyodbc.connect(
                f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={host},{int(port or 1433)};"
                f"DATABASE={dbname or 'master'};UID={user};PWD={password or ''};"
                f"TrustServerCertificate=yes;Connect Timeout=5;", timeout=5)
            cur = conn.cursor()
            cur.execute("IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = ?) EXEC(N'CREATE SCHEMA [%s]')" % schema.replace("]", "]]"), (schema,))
            conn.commit()
            conn.close()
            return True, f"schema {schema} ready on {dbname or 'master'}"
    except ImportError as e:
        return False, f"driver missing: {e}"
    except Exception as e:
        return False, str(e)[:200]
    return False, f"schema {schema} must be created manually for {engine}"


@csrf_exempt
def api_setup_wizard_connection(request):
    """POST /api/setup/wizard/connection/ — step 3: نوع القاعدة + باراميترات الاتصال الافتراضي.

    Body: {engine, host, port, user, password?, instance, schema, description}
    Saves (update_or_create) `urs_local`, tests it via _test_connection_obj (non-blocking:
    unreachable DB still saves, `tested.ok=false` warns), stamps last_check_*.
    Empty password = keep stored one.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from django.utils import timezone
        from .models import Connection
        data = json.loads(request.body.decode() or "{}")
        engine = str(data.get("engine") or "postgres").strip().lower()
        if engine not in WIZARD_DB_ENGINES:
            return JsonResponse({"error": f"engine must be one of {WIZARD_DB_ENGINES}"}, status=400)
        try:
            port = int(data.get("port") or WIZARD_ENGINE_PORTS[engine])
        except (TypeError, ValueError):
            port = WIZARD_ENGINE_PORTS[engine]
        host = (data.get("host") or "").strip()
        if not host:
            return JsonResponse({"error": "host required"}, status=400)
        instance_name = (data.get("instance_name") or "").strip()
        if engine == "sqlserver" and not instance_name:
            return JsonResponse({"error": "اسم المثيل مطلوب لـ SQL Server (مثال SQLEXPRESS)"}, status=400)
        comp, br = _wizard_first_company()
        defaults = {
            "host": host, "port": port,
            "user": (data.get("user") or "").strip() or "postgres",
            "instance": (data.get("instance") or "").strip() or "urs",
            "instance_name": instance_name,
            "engine": engine, "conn_type": "database",
            "is_local": True, "is_queryable": True,
            "schema": (data.get("schema") or "").strip() or (br.schema_name if br and br.schema_name else ""),
            "description": data.get("description") or "default connection (wizard)",
        }
        typed = (data.get("password") or "")
        if typed != "":
            defaults["password"] = typed
        local, forced_id1 = _wizard_ensure_urs_local(defaults)
        # cleartext for one-off test+schema only: typed > stored row > app.conf envelope.
        # Row is rewritten only when typed; app.conf keeps its envelope unless retyped.
        _ac_prev = _appconf_read()
        resolved_pw, pw_source = typed, "typed"
        if not resolved_pw and (local.password or ""):
            resolved_pw, pw_source = local.password, "stored"
        if not resolved_pw:
            _dec = _dbpass_resolve(_ac_prev.get("DB_PASS") or "")
            if _dec:
                resolved_pw, pw_source = _dec, "appconf"
        if resolved_pw:
            local.password = resolved_pw  # in-memory for test/schema below; persisted only if typed
        ok, payload, _status = _test_connection_obj(local)
        try:
            local.last_check_ok = bool(ok)
            local.last_check_at = timezone.now()
            local.save(update_fields=["last_check_ok", "last_check_at"])
        except Exception:
            pass
        # schema auto-fill defaults to branch schema; create it on the `urs` DB via this connection
        schema_ok, schema_note = _wizard_ensure_schema_on_conn(
            engine, host, port, local.user, resolved_pw,
            defaults.get("instance") or "urs", defaults.get("schema"))
        # mirror connection info into app.conf (password as PBKDF2 envelope, never plaintext)
        _ac_updates = {
            "DB_ENGINE": engine, "DB_HOST": host, "DB_PORT": port,
            "DB_USER": local.user,
            "DB_NAME": defaults.get("instance") or "urs", "DB_SCHEMA": defaults.get("schema") or "",
            "DB_INSTANCENAME": instance_name,
        }
        if typed != "":
            _ac_updates["DB_PASS"] = _dbpass_encrypt(typed)
        appconf_saved = _appconf_write(_ac_updates)
        return JsonResponse({"ok": True, "connection": local.to_dict(),
                             "forced_id1": forced_id1, "appconf_saved": appconf_saved,
                             "password_source": pw_source if resolved_pw else "none",
                             "schema_ok": schema_ok, "schema_note": schema_note,
                             "tested": {"ok": bool(ok), **(payload or {})}},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_setup_wizard_connection_from_appconf(request):
    """POST /api/setup/wizard/connection/from-appconf/ — seed default connection FROM app.conf.

    Launch-time entry used by run.bat: reads DB_* (decrypts the PBKDF2 envelope),
    then delegates to api_setup_wizard_connection (same validation/test/schema/id-1).
    400 with skipped:true when app.conf carries no DB (fresh box → use the wizard UI).
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        from config.dbconf import read_appconf, dbpass_resolve
        ac = read_appconf()
        host = (ac.get("DB_HOST") or "").strip()
        if not host:
            return JsonResponse({"ok": False, "skipped": True,
                                 "reason": "no DB in app.conf — use the setup wizard UI"}, status=400)
        _payload = {
            "engine": (ac.get("DB_ENGINE") or "postgres"),
            "host": host,
            "port": ac.get("DB_PORT") or "",
            "user": ac.get("DB_USER") or "",
            "password": dbpass_resolve(ac.get("DB_PASS") or ""),
            "instance": ac.get("DB_NAME") or "",
            "schema": ac.get("DB_SCHEMA") or "",
            "description": "default connection (app.conf seed)",
        }

        class _StubRequest:
            method = "POST"
            body = json.dumps(_payload).encode("utf-8")

        return api_setup_wizard_connection(_StubRequest())
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _wizard_scan_fmlk_connections():
    """Every odex/system app .fmlk with its current `connection` attr."""
    from fmlk_engine.compiler import FMLKFormCompiler
    out = []
    base = BASE_DIR / "odex" / "system"
    if not base.exists():
        return out
    for path in sorted(base.rglob("*.fmlk")):
        try:
            rel = path.relative_to(base)
            app = rel.parts[0] if len(rel.parts) > 1 else ""
            comp = FMLKFormCompiler(path=path)
            meta = comp.fml_metadata()
            out.append({"app": app, "file": path.name,
                        "connection": (meta.get("connection") or "").strip(),
                        "table": meta.get("table") or meta.get("name")})
        except Exception as e:
            out.append({"app": "", "file": path.name, "connection": "", "error": str(e)})
    return out


def api_setup_wizard_fmlk(request):
    """GET /api/setup/wizard/fmlk/ — list FMLK files + current connection attr."""
    try:
        items = _wizard_scan_fmlk_connections()
        return JsonResponse({"ok": True, "total": len(items), "items": items},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _wizard_retarget_fmlk_text(text, new_conn):
    """Rewrite `connection=` inside the <fml_metadata> tag only (minimal diff)."""
    pat = r'(<fml_metadata\b[^>]*?)\bconnection\s*=\s*"[^"]*"'
    new_text, n = _re_pg.subn(pat, r'\g<1>connection="%s"' % new_conn, text, count=1, flags=_re_pg.S)
    if n == 0:
        pat2 = r'<fml_metadata\b(?![^>]*\bconnection\s*=)'
        new_text, n = _re_pg.subn(pat2, '<fml_metadata connection="%s"' % new_conn, text, count=1)
    return new_text, n


@csrf_exempt
def api_setup_wizard_retarget(request):
    """POST /api/setup/wizard/retarget/ — rewrite `connection` attr of given FMLK files.

    Body: {files: [{app, file}], connection="urs_local"} — validates names, resolves under
    odex/system/<app>/, rewrites only the <fml_metadata> tag, re-parses to verify.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        import re as _re_loc
        import xml.etree.ElementTree as _ET
        data = json.loads(request.body.decode() or "{}")
        new_conn = (data.get("connection") or "urs_local").strip()
        if not new_conn:
            return JsonResponse({"error": "connection required"}, status=400)
        base = BASE_DIR / "odex" / "system"
        results = []
        for item in (data.get("files") or []):
            app = (item.get("app") or "").strip()
            fname = (item.get("file") or "").strip()
            if (not _re_loc.fullmatch(r"[A-Za-z_][\w\-]*", app or "")) or (not fname.endswith(".fmlk")) \
                    or ("/" in fname) or ("\\" in fname) or (".." in fname):
                results.append({"app": app, "file": fname, "ok": False, "error": "invalid app/file"})
                continue
            path = base / app / fname
            try:
                if not path.exists():
                    results.append({"app": app, "file": fname, "ok": False, "error": "file not found"})
                    continue
                raw = path.read_text(encoding="utf-8")
                try:
                    prev = _ET.fromstring(raw).find("fml_metadata").get("connection", "")
                except Exception:
                    prev = ""
                new_text, n = _wizard_retarget_fmlk_text(raw, new_conn)
                if n == 0:
                    results.append({"app": app, "file": fname, "ok": False, "error": "fml_metadata tag not found"})
                    continue
                # verify: must still parse and carry the new connection
                root = _ET.fromstring(new_text)
                got = root.find("fml_metadata").get("connection", "")
                if got != new_conn:
                    results.append({"app": app, "file": fname, "ok": False, "error": "verify failed"})
                    continue
                path.write_text(new_text, encoding="utf-8")
                results.append({"app": app, "file": fname, "ok": True, "previous": prev, "connection": got})
            except Exception as e:
                results.append({"app": app, "file": fname, "ok": False, "error": str(e)})
        return JsonResponse({"ok": True, "results": results,
                             "updated": sum(1 for r in results if r.get("ok"))},
                            json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def settings_detail(request, scope, app, file):
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    if (file or "").endswith(".fmlk"):
        # نماذج الإعدادات تُفتح بمشغل النماذج مباشرة
        return redirect(f"/app/settings/form/{file}/")
    from cml_engine.compiler import CMLCompiler
    from cml_engine.engine import CMLEngine
    path = _cml_resolve(scope, app, file)
    if not path:
        return render(request, "settings.html", {
            "system_items": [i for i in _cml_scan() if i["scope"] == "system"],
            "app_items": [], "by_app": {}, "total": 0,
            "all_rules": [],
            "error": "ملف الإعدادات غير موجود",
        })
    comp = CMLCompiler(path=path)
    eng = CMLEngine(comp)
    vpath = _cml_values_path(path)
    values = {}
    records = []
    if vpath.exists():
        try:
            raw = json.loads(vpath.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "records" in raw:
                records = raw["records"]
                values = records[0] if records else {}
            elif isinstance(raw, dict):
                values = raw
        except Exception:
            pass
    items = _cml_scan()
    import json as _json
    active_json = _json.dumps({
        "scope": scope, "app": app, "file": path.name, "metadata": comp.cml_metadata(),
        "controls": [c.to_dict() for c in eng.controls],
        "rules": [r.to_dict() for r in eng.rules],
        "values": values, "records_count": len(records),
    }, ensure_ascii=False)
    return render(request, "settings.html", {
        "system_items": [i for i in items if i["scope"] == "system"],
        "app_items": [i for i in items if i["scope"] != "system"],
        "by_app": {},
        "total": len(items),
        "all_rules": _cml_all_rules(items),
        "active": {"scope": scope, "app": app, "file": path.name, "metadata": comp.cml_metadata()},
        "active_json": active_json,
    })


def api_settings_list(request):
    return JsonResponse({"items": _cml_scan(), "total": len(_cml_scan())})


def api_cml_metadata(request):
    scope = request.GET.get("scope", "system")
    app = request.GET.get("app", "settings")
    file = request.GET.get("file", "")
    path = _cml_resolve(scope, app, file)
    if not path:
        return JsonResponse({"error": "not found"}, status=404)
    try:
        from cml_engine.compiler import CMLCompiler
        comp = CMLCompiler(path=path)
        return JsonResponse(comp.to_dict())
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def api_cml_validate(request):
    try:
        data = json.loads(request.body.decode() or "{}")
        scope = data.get("scope", request.GET.get("scope", "system"))
        app = data.get("app", request.GET.get("app", "settings"))
        file = data.get("file", request.GET.get("file", ""))
        values = data.get("values", {})
        path = _cml_resolve(scope, app, file)
        if not path:
            return JsonResponse({"error": "not found"}, status=404)
        from cml_engine.compiler import CMLCompiler
        from cml_engine.engine import CMLEngine
        eng = CMLEngine(CMLCompiler(path=path))
        errors = eng.validate(values)
        return JsonResponse({"ok": not errors, "errors": errors, "values": eng.apply_defaults(values)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def api_cml_save(request):
    try:
        data = json.loads(request.body.decode() or "{}")
        scope = data.get("scope", "system")
        app = data.get("app", "settings")
        file = data.get("file", "")
        values = data.get("values", {})
        path = _cml_resolve(scope, app, file)
        if not path:
            return JsonResponse({"error": "not found"}, status=404)
        from cml_engine.compiler import CMLCompiler
        from cml_engine.engine import CMLEngine
        eng = CMLEngine(CMLCompiler(path=path))
        errors = eng.validate(values)
        if errors:
            return JsonResponse({"ok": False, "errors": errors}, status=400)
        vals = eng.apply_defaults(values)
        vpath = _cml_values_path(path)
        records = []
        if vpath.exists():
            try:
                raw = json.loads(vpath.read_text(encoding="utf-8"))
                records = raw["records"] if isinstance(raw, dict) and "records" in raw else []
            except Exception:
                records = []
        # Enforce unique rules across records
        for r in eng.rules:
            if r.type == "unique" and r.target and r.target in vals:
                for rec in records:
                    if str(rec.get(r.target, "")) == str(vals[r.target]) and str(vals[r.target]).strip() != "":
                        return JsonResponse({"ok": False, "errors": {r.target: r.message or "القيمة يجب أن تكون فريدة"}}, status=400)
        records.append(vals)
        vpath.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        return JsonResponse({"ok": True, "records": len(records)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_cml_values(request):
    scope = request.GET.get("scope", "system")
    app = request.GET.get("app", "settings")
    file = request.GET.get("file", "")
    path = _cml_resolve(scope, app, file)
    if not path:
        return JsonResponse({"error": "not found"}, status=404)
    vpath = _cml_values_path(path)
    if not vpath.exists():
        return JsonResponse({"records": [], "total": 0})
    try:
        raw = json.loads(vpath.read_text(encoding="utf-8"))
        records = raw["records"] if isinstance(raw, dict) and "records" in raw else []
        return JsonResponse({"records": records, "total": len(records)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

# ── DML Document Engine (printable document templates, *.dml) ─────────────
# Naming rule: <source>_<N>.dml  (source = parent RML/FMLK base name, N = doc number)
# Storage: odex/system/<app>/documents/  (auto-created)

import re as _re_dml

_DML_NAME_RE = _re_dml.compile(r"^([A-Za-z0-9_\-]+)_(\d+)\.dml$")


def _dml_documents_dir(app_name):
    """Resolve (and auto-create) odex/system/<app>/documents/."""
    base = BASE_DIR / "odex" / "system" / app_name
    if not base.exists():
        alt = BASE_DIR / "system" / app_name
        if alt.exists():
            base = alt
    docs = base / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    return docs


def _resolve_dml_target(app_name, file):
    """Validate DML naming rule and resolve target path. Returns (docs_dir, target, error)."""
    file = (file or "").strip()
    if not file:
        return None, None, "file required"
    if not file.endswith(".dml"):
        file += ".dml"
    if "/" in file or "\\" in file or ".." in file:
        return None, None, "invalid file name"
    m = _DML_NAME_RE.match(file)
    if not m:
        return None, None, "file name must follow <source>_<N>.dml (e.g. invoice_report_1.dml)"
    docs = _dml_documents_dir(app_name)
    return docs, docs / file, None


def _write_dml_xml(meta, variables, header, sections, footer):
    """Build pretty DML XML from designer payload."""
    import xml.etree.ElementTree as ET, xml.dom.minidom
    dml = ET.Element("dml")
    dml.set("paper", str((meta or {}).get("paper", "A4") or "A4"))
    if (meta or {}).get("orientation"):
        dml.set("orientation", str(meta.get("orientation")))
    if (meta or {}).get("margin"):
        dml.set("margin", str(meta.get("margin")))
    if (meta or {}).get("title"):
        dml.set("title", str(meta.get("title")))
    if (meta or {}).get("name"):
        dml.set("name", str(meta.get("name")))
    if (meta or {}).get("source"):
        dml.set("source", str(meta.get("source")))
    _dt = str((meta or {}).get("doctype", "kashf") or "kashf").strip().lower()
    if _dt not in ("kashf", "sanad", "daftar"):
        _dt = "kashf"
    dml.set("doctype", _dt)
    if (meta or {}).get("key_column"):
        dml.set("key_column", str(meta.get("key_column")))
    vars_el = ET.SubElement(dml, "variables")
    for idx, v in enumerate(variables or [], start=1):
        v_el = ET.SubElement(vars_el, "var")
        v_el.set("id", str(v.get("id", idx)))
        v_el.set("name", str(v.get("name", f"var{idx}")))
        if v.get("type"):
            v_el.set("type", str(v.get("type")))
    for tag in ("header", "footer"):
        blk = (header if tag == "header" else footer) or {}
        el = ET.SubElement(dml, tag)
        if blk.get("style"):
            el.set("style", str(blk.get("style")))
        if blk.get("html"):
            el.text = str(blk.get("html"))
    secs_el = ET.SubElement(dml, "sections")
    for idx, s in enumerate(sections or [], start=1):
        s_el = ET.SubElement(secs_el, "section")
        s_el.set("id", str(s.get("id", idx)))
        if s.get("name"):
            s_el.set("name", str(s.get("name")))
        if s.get("style"):
            s_el.set("style", str(s.get("style")))
        if s.get("html"):
            s_el.text = str(s.get("html"))
        if s.get("print_before"):
            s_el.set("print_before", str(s.get("print_before")))
        if s.get("print_after"):
            s_el.set("print_after", str(s.get("print_after")))
        tv = s.get("tableview") or {}
        if tv and (tv.get("source") or tv.get("style") or tv.get("th") or tv.get("tr") or tv.get("td") or tv.get("variant") or tv.get("total_row") or tv.get("count_row")):
            tv_el = ET.SubElement(s_el, "tableview")
            tv_el.set("source", str(tv.get("source", "player:main")))
            for k in ("style", "th", "tr", "td"):
                if tv.get(k):
                    tv_el.set(k, str(tv.get(k)))
            if tv.get("variant"):
                tv_el.set("variant", str(tv.get("variant")))
            if tv.get("layout"):
                tv_el.set("layout", str(tv.get("layout")))
            if tv.get("total_row"):
                tv_el.set("total_row", "1")
            if tv.get("count_row"):
                tv_el.set("count_row", "1")
    raw = ET.tostring(dml, encoding="utf-8")
    pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    return "\n".join([l for l in pretty.split("\n") if l.strip()])


def app_dml_designer(request, app_name):
    """DML visual designer page (no-code CSS + variables + sections)."""
    _g = _gate_redirect(request)
    if _g is not None:
        return _g
    app_meta, fml_files, rml_files = _load_app_context(app_name)
    docs = []
    try:
        ddir = _dml_documents_dir(app_name)
        for p in sorted(ddir.glob("*.dml")):
            try:
                from dml_python.compiler import DMLCompiler
                comp = DMLCompiler(path=p)
                docs.append({"file": p.name, "metadata": comp.metadata()})
            except Exception as e:
                docs.append({"file": p.name, "error": str(e), "metadata": {}})
    except Exception:
        pass
    return render(request, "dml_designer.html", {
        "app": app_meta,
        "app_name": app_name,
        "fml_files": fml_files,
        "rml_files": rml_files,
        "dml_files": docs,
        "dml_files_json": json.dumps(docs, ensure_ascii=False),
    })


def api_dml_list(request, app_name):
    """GET /api/apps/<app>/dml/list/?source=<base> → DML docs (optionally filtered by source)."""
    src = (request.GET.get("source") or "").strip()
    try:
        ddir = _dml_documents_dir(app_name)
        out = []
        for p in sorted(ddir.glob("*.dml")):
            m = _DML_NAME_RE.match(p.name)
            psrc = m.group(1) if m else ""
            if src and psrc != src:
                continue
            meta = {}
            try:
                from dml_python.compiler import DMLCompiler
                meta = DMLCompiler(path=p).metadata()
            except Exception:
                pass
            out.append({"file": p.name, "source": psrc or meta.get("source", ""),
                        "metadata": meta})
        return JsonResponse({"docs": out})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_dml_suggest(request, app_name):
    """GET /api/apps/<app>/dml/suggest/?source=<base> → next <source>_<N>.dml name."""
    src = (request.GET.get("source") or "").strip()
    src = "".join(ch for ch in src if ch.isalnum() or ch in ("_", "-")) or "document"
    try:
        ddir = _dml_documents_dir(app_name)
        taken = set()
        for p in ddir.glob("%s_*.dml" % src):
            m = _DML_NAME_RE.match(p.name)
            if m and m.group(1) == src:
                taken.add(int(m.group(2)))
        n = 1
        while n in taken:
            n += 1
        return JsonResponse({"file": "%s_%d.dml" % (src, n), "source": src, "number": n})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def api_dml_metadata(request):
    """GET /api/dml/metadata?dml=<file>&app=<app> → full DML JSON."""
    dml = request.GET.get("dml", "")
    app = request.GET.get("app")
    try:
        from dml_python.compiler import DMLCompiler
        _, target, err = _resolve_dml_target(app, dml)
        if err:
            return JsonResponse({"error": err}, status=400)
        if not target.exists():
            return JsonResponse({"error": "file not found"}, status=404)
        comp = DMLCompiler(path=target)
        d = comp.to_dict()
        d["file"] = target.name
        try:
            from dml_python.engine import SYSTEM_VARS
            d["system_variables"] = SYSTEM_VARS
        except Exception:
            d["system_variables"] = []
        return JsonResponse(d, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def api_dml_create(request, app_name):
    """POST /api/apps/<app>/dml/create/ — write a new *.dml (naming rule enforced)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        _, target, err = _resolve_dml_target(app_name, data.get("file", ""))
        if err:
            return JsonResponse({"error": err}, status=400)
        if target.exists():
            return JsonResponse({"error": "file already exists"}, status=400)
        pretty = _write_dml_xml(data.get("metadata", {}), data.get("variables", []),
                                data.get("header", {}), data.get("sections", []),
                                data.get("footer", {}))
        target.write_text(pretty, encoding="utf-8")
        return JsonResponse({"ok": True, "file": target.name, "path": str(target)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_dml_update(request, app_name):
    """POST /api/apps/<app>/dml/update/ — overwrite an existing *.dml."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body.decode() or "{}")
        _, target, err = _resolve_dml_target(app_name, data.get("file", ""))
        if err:
            return JsonResponse({"error": err}, status=400)
        if not target.exists():
            return JsonResponse({"error": "file not found, use create"}, status=404)
        pretty = _write_dml_xml(data.get("metadata", {}), data.get("variables", []),
                                data.get("header", {}), data.get("sections", []),
                                data.get("footer", {}))
        target.write_text(pretty, encoding="utf-8")
        return JsonResponse({"ok": True, "file": target.name, "path": str(target), "updated": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _dml_system_vars(request, doc_meta=None):
    """System (global) variable values: user, company/branch, date/time, doc title."""
    from dml_python.engine import get_system_vars
    sysd = get_system_vars()
    try:
        u = getattr(request, "user", None)
        if u is not None and getattr(u, "is_authenticated", False):
            sysd["current_user"] = (u.get_full_name() or u.get_username() or "")
    except Exception:
        pass
    try:
        from .models import Company, Branch
        comp = Company.objects.filter(is_active=True).order_by("code").first()
        if comp is not None:
            sysd["company_name"] = comp.name or ""
            sysd["company_name_en"] = comp.name_en or ""
            sysd["company_code"] = comp.code or ""
            sysd["tax_number"] = comp.tax_number or ""
            sysd["fiscal_year"] = str(comp.fiscal_year or "")
            br = Branch.objects.filter(company=comp, is_active=True).order_by("code").first()
            if br is not None:
                sysd["branch_name"] = br.name or ""
    except Exception:
        pass
    try:
        if doc_meta and doc_meta.get("title"):
            sysd["doc_title"] = doc_meta.get("title")
    except Exception:
        pass
    return sysd


@csrf_exempt
def api_dml_xlsx(request):
    """POST /api/dml/xlsx → XLSX download بنفس نسق الطباعة.

    Two input styles (same output):
    A) Query-based (recommended, small body): {rml, app, exec_payload, data_vars,
       dml?, doc?} — the server executes the report (all rows) itself.
    B) Rows-based (legacy): {dml?, doc?, data, table, detail} with full rows.
    With a DML doc → full layout (title/header/sections/footer);
    without → plain styled table.
    """
    try:
        data = json.loads(request.body.decode() or "{}")
        from dml_python import xlsx as _dx

        def _status_label(col, v):
            try:
                sm = col.get("status_map") or col.get("statusMap") or []
                hit = next((s for s in sm
                            if isinstance(s, dict) and str(s.get("value", "") or "") == str(v if v is not None else "")), None)
                if hit is not None:
                    return str(hit.get("label", "") or ("" if v is None else v))
            except Exception:
                pass
            return "" if v is None else (v if isinstance(v, str) else str(v))

        table = data.get("table", {}) or {}
        data_vars = data.get("data_vars") or data.get("data", {}) or {}
        title_hint = ""
        if data.get("rml"):
            # A) نفّذ التقرير خادمياً بكل الصفوف — بلا نقل بيانات ذهاباً وإياباً
            rml = data.get("rml", "")
            app = data.get("app")
            exec_payload = dict(data.get("exec_payload") or {})
            exec_payload["page"] = 1
            exec_payload["pageSize"] = "all"
            pipe = _rml_get_pipeline(rml, app)
            try:
                meta = pipe.rpt_metadata() if hasattr(pipe, "rpt_metadata") else pipe.compiler.rpt_metadata()
            except Exception:
                meta = {}
            title_hint = str(meta.get("displayName") or meta.get("displayname") or rml or "")
            result = pipe.execute(exec_payload)
            cols_meta = result.get("columns", []) or []
            rows_raw = result.get("rows", []) or []
            _hide = {str(h) for h in (data.get("hide") or []) if str(h or "").strip() != ""}
            if _hide:
                cols_meta = [c for c in cols_meta
                             if str(c.get("alias") or c.get("name") or "") not in _hide]
            cols = [{"name": c.get("name") or "", "alias": c.get("alias") or c.get("name") or ""} for c in cols_meta]
            rows = []
            for rr in rows_raw:
                o = {}
                for c in cols_meta:
                    k = c.get("alias") or c.get("name") or ""
                    v = rr.get(k, rr.get(c.get("name") or "", ""))
                    if isinstance(v, dict):
                        v = ""
                    o[k] = _status_label(c, v)
                rows.append(o)
            table = {"columns": cols, "rows": rows, "groups": result.get("groups", [])}
            data_vars = dict(data_vars)
            data_vars.setdefault("row_count", str(result.get("total", len(rows))))
            if title_hint and not data_vars.get("report_title"):
                data_vars["report_title"] = title_hint
        title = ""
        if data.get("doc"):
            from dml_python.engine import render_preview_html
            from dml_python.compiler import DMLCompiler
            pretty = _write_dml_xml((data["doc"] or {}).get("metadata", {}),
                                    (data["doc"] or {}).get("variables", []),
                                    (data["doc"] or {}).get("header", {}),
                                    (data["doc"] or {}).get("sections", []),
                                    (data["doc"] or {}).get("footer", {}))
            sysd = _dml_system_vars(request, (data["doc"] or {}).get("metadata", {}))
            sysd.update(data_vars)
            comp = DMLCompiler(xml_text=pretty)
            title = (comp.metadata().get("title") or comp.metadata().get("name") or "").strip()
            html = render_preview_html(comp, data=sysd,
                                       table=table,
                                       detail=data.get("detail", {}))
            wb = _dx.html_to_workbook(html, title=title or "تقرير")
            fname = (title or "report").strip() or "report"
        elif data.get("dml"):
            from dml_python.engine import render_preview_html
            from dml_python.compiler import DMLCompiler
            dml = data.get("dml", "")
            app = data.get("app")
            _, target, err = _resolve_dml_target(app, dml)
            if err:
                return JsonResponse({"error": err}, status=400)
            if not target.exists():
                return JsonResponse({"error": "file not found"}, status=404)
            comp = DMLCompiler(path=target)
            title = (comp.metadata().get("title") or comp.metadata().get("name") or target.stem).strip()
            sysd = _dml_system_vars(request, comp.metadata())
            sysd.update(data_vars)
            _tbl = table
            if not any(getattr(s, "tableview", None) is not None for s in comp.sections()) and (_tbl.get("rows")):
                # المستند بلا جدول — export عادي بنفس التنسيق بدل ملف فارغ
                wb = _dx.table_to_workbook(_tbl.get("columns", []), _tbl.get("rows", []), title=title or "تقرير")
            else:
                html = render_preview_html(comp, data=sysd, table=_tbl, detail=data.get("detail", {}))
                wb = _dx.html_to_workbook(html, title=title or "تقرير")
            fname = target.stem
        else:
            cols = table.get("columns", []) or []
            rows = table.get("rows", []) or []
            title = str(data_vars.get("report_title", "") or title_hint or "تقرير")
            wb = _dx.table_to_workbook(cols, rows, title=title)
            fname = "report"
        content = _dx.workbook_to_bytes(wb)
        from urllib.parse import quote as _qurl
        if data.get("rml") and (fname or "report") == "report" and title_hint:
            fname = title_hint
        safe = _qurl((fname or "report") + ".xlsx")
        resp = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = "attachment; filename*=UTF-8''%s" % safe
        return resp
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_dml_pdf(request):
    """POST /api/dml/pdf → مستند الطباعة PDF (RTL عربي).

    نفس مدخل XLSX المبني على الاستعلام: {rml, app, exec_payload, hide?, data_vars?}
    — الخادم ينفذ التقرير بكل الصفوف ويبني PDF (Arial + تشكيل عربي).
    """
    try:
        data = json.loads(request.body.decode() or "{}")
        if not data.get("rml"):
            return JsonResponse({"error": "rml required"}, status=400)
        from dml_python import pdf as _dpdf
        rml = data.get("rml", "")
        app = data.get("app")
        exec_payload = dict(data.get("exec_payload") or {})
        exec_payload["page"] = 1
        exec_payload["pageSize"] = "all"
        pipe = _rml_get_pipeline(rml, app)
        try:
            meta0 = pipe.rpt_metadata() if hasattr(pipe, "rpt_metadata") else pipe.compiler.rpt_metadata()
        except Exception:
            meta0 = {}
        result = pipe.execute(exec_payload)
        cols_meta = result.get("columns", []) or []
        rows_raw = result.get("rows", []) or []
        _hide = {str(h) for h in (data.get("hide") or []) if str(h or "").strip() != ""}
        if _hide:
            cols_meta = [c for c in cols_meta
                         if str(c.get("alias") or c.get("name") or "") not in _hide]
        cols = [{"name": c.get("name") or "", "alias": c.get("alias") or c.get("name") or ""} for c in cols_meta]
        rows = []
        for rr in rows_raw:
            o = {}
            for c in cols_meta:
                k = c.get("alias") or c.get("name") or ""
                v = rr.get(k, rr.get(c.get("name") or "", ""))
                if isinstance(v, dict):
                    v = ""
                o[k] = "" if v is None else (v if isinstance(v, str) else str(v))
            rows.append(o)
        data_vars = data.get("data_vars") or {}
        title = str(data_vars.get("report_title") or meta0.get("displayName")
                    or meta0.get("displayname") or rml or "تقرير")
        import datetime as _dt
        pdf_bytes = _dpdf.build_pdf(
            cols, rows, title=title,
            meta={"row_count": str(result.get("total", len(rows))),
                  "date": _dt.date.today().isoformat()})
        from urllib.parse import quote as _qurl
        safe = _qurl(title + ".pdf")
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = "attachment; filename*=UTF-8''%s" % safe
        return resp
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_dml_doc_pdf(request):
    """POST /api/dml/doc-pdf {dml, app, data, table, detail?, paper?, orientation?}
    → مستند الطباعة PDF بنفس نسق المعاينة (Chrome headless print-to-pdf)."""
    try:
        data = json.loads(request.body.decode() or "{}")
        dml = data.get("dml", "")
        app = data.get("app")
        _, target, err = _resolve_dml_target(app, dml)
        if err:
            return JsonResponse({"error": err}, status=400)
        if not target.exists():
            return JsonResponse({"error": "file not found"}, status=404)
        from dml_python.engine import render_preview_html
        from dml_python.compiler import DMLCompiler
        comp = DMLCompiler(path=target)
        sysd = _dml_system_vars(request, comp.metadata())
        sysd.update(data.get("data", {}) or {})
        frag = render_preview_html(comp, data=sysd,
                                   table=data.get("table", {}),
                                   detail=data.get("detail", {}))
        paper = str(data.get("paper") or (comp.metadata().get("paper") or "A4")).upper()
        orient = str(data.get("orientation") or (comp.metadata().get("orientation") or "portrait")).lower()
        _sizes = {"A4": (8.27, 11.69), "A5": (5.83, 8.27), "A3": (11.69, 16.54),
                  "LETTER": (8.5, 11.0), "LEGAL": (8.5, 14.0)}
        pw, ph = _sizes.get(paper, (8.27, 11.69))
        if orient.startswith("land"):
            pw, ph = ph, pw
        low = (frag or "").lower()
        if "<html" in low:
            full = frag
        else:
            full = ("<!DOCTYPE html><html dir=\"rtl\" lang=\"ar\"><head><meta charset=\"utf-8\">"
                    f"<style>@page{{size:{pw}in {ph}in;margin:10mm}}"
                    "body{font-family:Arial,Tahoma,sans-serif;background:#fff}</style></head>"
                    f"<body>{frag}</body></html>")
        import os as _os, subprocess as _sp, tempfile as _tf
        _chrome = _os.environ.get("CHROME_BIN") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not _os.path.exists(_chrome):
            for _c in (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                       r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
                if _os.path.exists(_c):
                    _chrome = _c
                    break
        if not _os.path.exists(_chrome):
            return JsonResponse({"error": "متصفح Chrome غير متوفر على الخادم لطباعة PDF"}, status=500)
        with _tf.TemporaryDirectory(prefix="docpdf_") as _td:
            _src = _os.path.join(_td, "doc.html")
            _out = _os.path.join(_td, "doc.pdf")
            with open(_src, "w", encoding="utf-8") as _fh:
                _fh.write(full)
            _url = "file:///" + _src.replace("\\", "/")
            _cmd = [_chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--hide-scrollbars", "--print-to-pdf=" + _out,
                    "--print-to-pdf-no-header",
                    f"--paper-width={pw}", f"--paper-height={ph}",
                    "--virtual-time-budget=10000", _url]
            try:
                _pr = _sp.run(_cmd, timeout=90, capture_output=True)
            except FileNotFoundError:
                return JsonResponse({"error": "تعذر تشغيل Chrome"}, status=500)
            except _sp.TimeoutExpired:
                return JsonResponse({"error": "انتهت مهلة توليد PDF"}, status=504)
            if not _os.path.exists(_out):
                _err = ""
                try:
                    _err = (_pr.stderr or b"").decode("utf-8", "replace")[-300:]
                except Exception:
                    pass
                return JsonResponse({"error": "فشل توليد PDF" + (": " + _err if _err else "")}, status=500)
            with open(_out, "rb") as _fh:
                pdf_bytes = _fh.read()
        if not pdf_bytes:
            return JsonResponse({"error": "ملف PDF فارغ"}, status=500)
        from urllib.parse import quote as _qurl
        safe = _qurl(target.stem + ".pdf")
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = "attachment; filename*=UTF-8''%s" % safe
        return resp
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def api_dml_render(request):
    """POST /api/dml/render {dml, app, data, table} or {doc:{...}, data, table} → printable preview HTML."""
    try:
        data = json.loads(request.body.decode() or "{}")
        from dml_python.engine import render_preview_html
        if data.get("doc"):
            from dml_python.compiler import DMLCompiler
            pretty = _write_dml_xml((data["doc"] or {}).get("metadata", {}),
                                    (data["doc"] or {}).get("variables", []),
                                    (data["doc"] or {}).get("header", {}),
                                    (data["doc"] or {}).get("sections", []),
                                    (data["doc"] or {}).get("footer", {}))
            sysd = _dml_system_vars(request, (data["doc"] or {}).get("metadata", {}))
            sysd.update(data.get("data", {}) or {})
            html = render_preview_html(DMLCompiler(xml_text=pretty),
                                       data=sysd,
                                       table=data.get("table", {}),
                                       detail=data.get("detail", {}))
            return HttpResponse(html, content_type="text/html; charset=utf-8")
        dml = data.get("dml", "")
        app = data.get("app")
        _, target, err = _resolve_dml_target(app, dml)
        if err:
            return JsonResponse({"error": err}, status=400)
        if not target.exists():
            return JsonResponse({"error": "file not found"}, status=404)
        from dml_python.engine import render_preview_html
        from dml_python.compiler import DMLCompiler
        comp = DMLCompiler(path=target)
        sysd = _dml_system_vars(request, comp.metadata())
        sysd.update(data.get("data", {}) or {})
        html = render_preview_html(comp, data=sysd,
                                   table=data.get("table", {}),
                                   detail=data.get("detail", {}))
        return HttpResponse(html, content_type="text/html; charset=utf-8")
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
