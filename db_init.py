"""
db_init — Postgres 5432 / 7496 probe + migrate urs app
Usage: python db_init.py
Connects with postgres/postgres, creates DB if needed, runs migrations, syncs 15 ERP apps from system/*/metadata.json
"""
import os
import sys
import re
import socket
import pathlib
import json

BASE_DIR = pathlib.Path(__file__).resolve().parent

_WS_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_SCHEMA_LEN = 63


def validate_ws_schema(name) -> str:
    """Validate an explicit workspace schema name (Postgres identifier).

    Returns the lowercase name. Raises ValueError (Arabic) when invalid.
    """
    v = str(name or "").strip().lower()
    if not v or len(v) > _MAX_SCHEMA_LEN or not _WS_SCHEMA_RE.fullmatch(v):
        raise ValueError("اسم السكيما: أحرف لاتينية/أرقام/_ فقط، يبدأ بحرف أو _")
    return v


_WS_USERS_DDL = """
CREATE TABLE IF NOT EXISTS "{schema}"."{table}" (
    id SERIAL PRIMARY KEY,
    username character varying(150) NOT NULL UNIQUE,
    full_name character varying(200) NOT NULL,
    email character varying(254) NOT NULL,
    password character varying(255) NOT NULL,
    password_hash character varying(255),
    role character varying(50) NOT NULL,
    phone character varying(50),
    company_code character varying(50),
    branch_code character varying(50),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
)
""".strip()


def _split_users_table(users_table, schema):
    """(schema, table) targeted for the users table, or (None, None) to skip.

    Only targets tables INSIDE the migrated schema (never foreign schemas).
    Bare names default to <schema>.users.
    """
    raw = str(users_table or "").strip()
    if not raw:
        return schema, "users"
    parts = [p.strip() for p in raw.split(".")]
    if len(parts) == 1:
        parts = [schema, parts[0]]
    if len(parts) != 2 or not all(parts):
        return None, None
    if any(len(p) > _MAX_SCHEMA_LEN or not _WS_SCHEMA_RE.fullmatch(p) for p in (parts[0].lower(), parts[1].lower())):
        return None, None
    if parts[0].lower() != schema:
        return None, None
    return parts[0].lower(), parts[1].lower()


def migrate_schema(host, port, user, password, dbname, schema, users_table=""):
    """Create schema `company_branch_year` and migrate ALL Django app tables into it.

    Steps: (CREATE DATABASE when missing) → CREATE SCHEMA IF NOT EXISTS →
    CREATE users table (when missing, sys_users-compatible shape) →
    temporary Django connection with ``search_path=<schema>`` →
    ``migrate --run-syncdb`` (every app incl. ``urs`` models, auth, django_migrations)
    → list created tables from ``pg_tables``.

    Returns {"schema", "tables", "migrated": True, "users_table", "users_created"}.
    Raises RuntimeError (Arabic) with the driver error attached. Never touches
    the default connection.
    """
    schema = validate_ws_schema(schema)
    host = str(host or "").strip() or "127.0.0.1"
    try:
        port = int(port or 5432)
    except Exception:
        port = 5432
    user = str(user or "").strip() or "postgres"
    password = str(password or "")
    dbname = str(dbname or "").strip() or "urs"
    try:
        import psycopg2
        from psycopg2 import sql as _sql
    except Exception as e:
        raise RuntimeError("مكتبة psycopg2 غير مثبتة: %s" % e)

    def _connect(db):
        return psycopg2.connect(dbname=db, user=user, password=password,
                                host=host, port=port, connect_timeout=10)

    try:
        try:
            conn = _connect(dbname)
        except Exception as e:
            # 3D000 invalid_catalog_name → create the database, then reconnect
            if "3D000" not in str(getattr(e, "pgcode", "") or "") and \
               "does not exist" not in str(e):
                raise
            admin = _connect("postgres")
            try:
                admin.autocommit = True
                cur = admin.cursor()
                try:
                    cur.execute(_sql.SQL("CREATE DATABASE {}").format(
                        _sql.Identifier(dbname)))
                except Exception as ce:
                    if "already exists" not in str(ce):
                        raise
                cur.close()
            finally:
                admin.close()
            conn = _connect(dbname)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(_sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                _sql.Identifier(schema)))
            cur.close()
        finally:
            conn.close()
    except (ValueError, RuntimeError):
        raise
    except Exception as e:
        raise RuntimeError("فشل الاتصال/إنشاء السكيما: %s" % e)

    # --- users table (login target) inside the migrated schema ---------------
    _usch, _utbl = _split_users_table(users_table, schema)
    users_target = "%s.%s" % (_usch, _utbl) if _usch and _utbl else ""
    users_created = False
    if users_target:
        try:
            conn = _connect(dbname)
        except Exception as e:
            raise RuntimeError("فشل الاتصال لإنشاء جدول المستخدمين: %s" % e)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1 FROM pg_tables WHERE schemaname = %s AND tablename = %s",
                            [_usch, _utbl])
                if cur.fetchone() is None:
                    cur.execute(_WS_USERS_DDL.format(schema=_usch, table=_utbl))
                    users_created = True
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            raise RuntimeError("فشل إنشاء جدول المستخدمين %s: %s" % (users_target, e))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # --- Django migrate into the schema via search_path ---------------------
    alias = "ws_%s" % schema
    try:
        from django.conf import settings as _dj_settings
        from django.db import connections as _conns
        from django.core.management import call_command as _migrate_cmd
    except Exception as e:
        raise RuntimeError("تعذر تحميل Django للترحيل: %s" % e)
    if not _dj_settings.configured:
        raise RuntimeError("إعدادات Django غير مهيأة — نفّذ عبر manage.py/shell")
    _dj_settings.DATABASES[alias] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": dbname,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": str(port),
        "OPTIONS": {"options": "-c search_path=%s" % schema},
        # Same defaults ConnectionHandler.configure_settings applies at
        # startup (runtime-added aliases skip them; missing TIME_ZONE raised
        # KeyError: 'TIME_ZONE' on connect via check_settings()).
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "TIME_ZONE": None,
        "TEST": {"CHARSET": None, "COLLATION": None, "MIGRATE": True,
                 "MIRROR": None, "NAME": None},
    }
    try:
        try:
            _migrate_cmd("migrate", database=alias, run_syncdb=True,
                         verbosity=0, interactive=False)
        except Exception as e:
            raise RuntimeError("فشل migrate داخل السكيما %s: %s" % (schema, e))
        try:
            conn = _conns[alias]
            cur = conn.cursor()
            try:
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s "
                            "ORDER BY tablename", [schema])
                tables = [str(r[0]) for r in (cur.fetchall() or [])]
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception as e:
            raise RuntimeError("تم الترحيل لكن تعذر سرد الجداول: %s" % e)
    finally:
        try:
            _conns[alias].close()
        except Exception:
            pass
        try:
            del _dj_settings.DATABASES[alias]
        except Exception:
            pass
    return {"schema": schema, "tables": tables, "migrated": True,
            "users_table": users_target, "users_created": users_created}

def probe_port(host="127.0.0.1", port=5432, timeout=1.0) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False

def find_pg_port():
    # Try 172.16.10.101 first (requested), then 127.0.0.1
    for host, p in [("172.16.10.101", 5432), ("127.0.0.1", 5432), ("127.0.0.1", 7496)]:
        if probe_port(host=host, port=p):
            print(f"[db_init] Postgres found on {host}:{p}")
            return p
    print("[db_init] No Postgres on 5432/7496 — will use SQLite fallback")
    return None

def test_pg_connection(port, db="urs", user="postgres", password="postgres", host="172.16.10.101"):
    try:
        import psycopg2
        conn = psycopg2.connect(dbname=db, user=user, password=password, host=host, port=port, connect_timeout=3)
        conn.close()
        print(f"[db_init] psycopg2 connect OK to {host}:{port}/{db} as {user}")
        return True
    except Exception as e:
        print(f"[db_init] psycopg2 connect failed to {host}:{port}: {e}")
        return False

def run_migrations(port=None):
    # Setup Django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # Override DB if port found
    if port:
        os.environ["PG_PORT"] = str(port)
        # Patch settings before django.setup
        import django
        from django.conf import settings
        if not settings.configured:
            pass
        # Re-configure settings.DATABASES if needed
        # Since settings already loaded, we patch directly
        try:
            # Use 172.16.10.101/urs as requested
            settings.DATABASES["default"] = {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "urs",
                "USER": "postgres",
                "PASSWORD": "postgres",
                "HOST": "172.16.10.101",
                "PORT": str(port),
            }
        except Exception as e:
            print(f"[db_init] patch settings failed: {e}")

    import django
    django.setup()

    from django.core.management import call_command
    print("[db_init] makemigrations urs...")
    call_command("makemigrations", "urs", verbosity=1)
    print("[db_init] migrate...")
    call_command("migrate", verbosity=1)
    print("[db_init] migrate done")

    # Sync apps from workspace_1/apps/*/metadata.json → DB (fallback odex/system, system/)
    from urs.models import App
    from urs.workspace import system_roots
    candidates = system_roots()
    synced = 0
    seen = set()
    for system in candidates:
        if not system.exists():
            continue
        for meta_path in sorted(system.glob("*/metadata.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                if data.get("name") in seen:
                    continue
                seen.add(data["name"])
                _pal = [("bg-emerald-50", "text-emerald-600"), ("bg-blue-50", "text-blue-600"),
                        ("bg-violet-50", "text-violet-600"), ("bg-amber-50", "text-amber-600"),
                        ("bg-rose-50", "text-rose-600"), ("bg-cyan-50", "text-cyan-600"),
                        ("bg-fuchsia-50", "text-fuchsia-600"), ("bg-lime-50", "text-lime-600"),
                        ("bg-orange-50", "text-orange-600"), ("bg-teal-50", "text-teal-600"),
                        ("bg-indigo-50", "text-indigo-600"), ("bg-pink-50", "text-pink-600")]
                _bg, _fg = _pal[abs(hash(data.get("name", ""))) % len(_pal)]
                app, created = App.objects.update_or_create(
                    name=data["name"],
                    defaults={
                        "name_ar": data.get("ar", data["name"]),
                        "name_en": data.get("en", ""),
                        "icon": data.get("icon", "fa-cube"),
                        "icon_bg": data.get("icon_bg") or _bg,
                        "icon_color": data.get("icon_color") or _fg,
                        "version": data.get("version", "1.0.0"),
                        "description": data.get("description", ""),
                        "description_ar": data.get("description", ""),
                        "category": data.get("category", "عام"),
                        "is_new": data.get("is_new", False),
                        "sort_order": data.get("sort_order", 0),
                    },
                )
                synced += 1
                print(f"[db_init] sync {'created' if created else 'updated'} {app.name} — {app.name_ar}")
            except Exception as e:
                print(f"[db_init] sync failed for {meta_path}: {e}")

    print(f"[db_init] synced {synced} apps from system/")
    # Show counts
    from urs.models import App as AppModel, Report
    print(f"[db_init] DB apps count: {AppModel.objects.count()}")
    print(f"[db_init] DB reports count: {Report.objects.count()}")

if __name__ == "__main__":
    port = find_pg_port()
    # Also test psycopg2 if available
    if port and not test_pg_connection(port):
        # Try alternative port
        alt = 7496 if port == 5432 else 5432
        if probe_port(port=alt) and test_pg_connection(alt):
            port = alt
        else:
            print("[db_init] psycopg2 test failed, will still try Django migrate (may fallback to sqlite)")
    run_migrations(port)
    print("[db_init] done — now run: python manage.py runserver")
