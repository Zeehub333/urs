"""
db_init — Postgres 5432 / 7496 probe + migrate urs app
Usage: python db_init.py
Connects with postgres/postgres, creates DB if needed, runs migrations, syncs 15 ERP apps from system/*/metadata.json
"""
import os
import sys
import socket
import pathlib
import json

BASE_DIR = pathlib.Path(__file__).resolve().parent

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

    # Sync 20 apps from odex/system/*/metadata.json → DB (fallback to system/)
    from urs.models import App
    candidates = [BASE_DIR / "odex" / "system", BASE_DIR / "system"]
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
