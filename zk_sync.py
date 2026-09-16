#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
zk_sync.py — مزامنة البصمات من أجهزة ZK مباشرة إلى قاعدة urs (بدون برنامج BioTime).

- يقرأ الأجهزة تلقائياً من جدول الاتصالات (engine='zk') — لا IPs ثابتة هنا.
- يسحب سجلات الحضور عبر بروتوكول ZK ويدخل الجديد فقط في public.zk_attendance
  (INSERT .. ON CONFLICT DO NOTHING على terminal_id + emp_code + punch_time).
- أوقات الأجهزة المحلية تُفسَّر بتوقيت Asia/Aden (جلسة Postgres مضبوطة عليه).
- الاستخدام:  zk_sync.py [--once] [--limit N] [--clear-device]
              [--devices 172.16.22.203:4370,172.16.22.204:4370]
  --clear-device يمسح سجل الجهاز بعد المزامنة الناجحة (اختياري، الافتراضي: إبقاء).
"""
from __future__ import annotations
import argparse
import datetime as _dt
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

SESSION_TZ = "Asia/Aden"
BATCH = 1000

DDL = """
CREATE TABLE IF NOT EXISTS public.zk_attendance (
  id SERIAL PRIMARY KEY,
  terminal_id INTEGER NOT NULL DEFAULT 0,
  device_host VARCHAR(64) NOT NULL DEFAULT '',
  uid INTEGER,
  emp_code VARCHAR(64) NOT NULL,
  punch_time TIMESTAMPTZ NOT NULL,
  punch_state VARCHAR(16) NOT NULL DEFAULT '',
  punch SMALLINT,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_zk_attendance UNIQUE (terminal_id, emp_code, punch_time)
);
CREATE INDEX IF NOT EXISTS ix_zk_attendance_time ON public.zk_attendance (punch_time);
CREATE INDEX IF NOT EXISTS ix_zk_attendance_emp ON public.zk_attendance (emp_code);
CREATE TABLE IF NOT EXISTS public.zk_users (
  id SERIAL PRIMARY KEY,
  terminal_id INTEGER NOT NULL DEFAULT 0,
  device_host VARCHAR(64) NOT NULL DEFAULT '',
  uid INTEGER,
  emp_code VARCHAR(64) NOT NULL,
  name VARCHAR(128) NOT NULL DEFAULT '',
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_zk_users UNIQUE (terminal_id, emp_code)
);
CREATE INDEX IF NOT EXISTS ix_zk_users_emp ON public.zk_users (emp_code);
"""

UPSERT = """
INSERT INTO public.zk_attendance
  (terminal_id, device_host, uid, emp_code, punch_time, punch_state, punch)
VALUES (%(terminal_id)s, %(device_host)s, %(uid)s, %(emp_code)s,
        %(punch_time)s, %(punch_state)s, %(punch)s)
ON CONFLICT (terminal_id, emp_code, punch_time) DO NOTHING
"""


def log(msg: str) -> None:
    print("[%s] %s" % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def _django_setup() -> bool:
    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django  # noqa
        django.setup()
        return True
    except Exception as e:
        log("Django setup failed (%s) — سيُستخدم --devices فقط" % e)
        return False


def get_zk_devices(cli_devices):
    """[(terminal_id, host, port, label)] — من قاعدة الاتصالات أو --devices."""
    if cli_devices:
        out = []
        for i, d in enumerate(cli_devices.split(",")):
            d = d.strip()
            if not d:
                continue
            host, _, port = d.partition(":")
            try:
                p = int(port or 4370)
            except ValueError:
                p = 4370
            out.append((900 + i, host.strip(), p, host.strip()))
        return out
    from urs.models import Connection
    out = []
    for c in Connection.objects.filter(engine="zk").order_by("id"):
        try:
            p = int(c.port or 4370)
        except (TypeError, ValueError):
            p = 4370
        out.append((int(c.id), str(c.host or "").strip(), p, str(c.name or c.host)))
    return out


def get_target_dsn():
    from django.conf import settings
    db = settings.DATABASES["default"]
    return {
        "host": db.get("HOST") or "127.0.0.1",
        "port": int(db.get("PORT") or 5432),
        "dbname": db.get("NAME") or "urs",
        "user": db.get("USER") or "postgres",
        "password": db.get("PASSWORD") or "postgres",
    }


def pull_device(host, port, limit):
    from odex.engines.zk import ZKEngine
    try:
        from odex.engines.zk import tolerant_zk_decode as _tzd
    except Exception:
        _tzd = None
    import contextlib as _cl
    zk = ZKEngine(ip=host, port=port, timeout=30, password=0)
    zk.connect()
    try:
        cm = _tzd() if _tzd else _cl.nullcontext()
        with cm:
            rows = list(zk.list_all("attendance", limit=limit) or [])
        return rows, zk
    except Exception:
        try:
            zk.disconnect()
        except Exception:
            pass
        raise


def norm_row(r):
    try:
        emp = str(r.get("user_id", "") or "").strip()
        ts_raw = str(r.get("timestamp", "") or "").strip()
        if not emp or not ts_raw:
            return None
        ts = _dt.datetime.fromisoformat(ts_raw)  # naive = توقيت الجهاز المحلي (عدن)
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        try:
            uid = int(r.get("uid")) if r.get("uid") is not None else None
        except (TypeError, ValueError):
            uid = None
        try:
            punch = int(r.get("punch")) if r.get("punch") is not None else None
        except (TypeError, ValueError):
            punch = None
        return {
            "uid": uid,
            "emp_code": emp,
            "punch_time": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "punch_state": str(r.get("status", "") or ""),
            "punch": punch,
        }
    except Exception:
        return None


def sync_one(pg, terminal_id, host, port, label, limit, clear_device):
    rows, zk = pull_device(host, port, limit)
    log("%s: سُحب %d سجلاً من الجهاز" % (label, len(rows)))
    normed = []
    for r in rows:
        n = norm_row(r)
        if n:
            n["terminal_id"] = terminal_id
            n["device_host"] = host
            normed.append(n)
    log("%s: %d سجلاً صالحاً بعد التنقية" % (label, len(normed)))
    new = 0
    if normed:
        cur = pg.cursor()
        try:
            for i in range(0, len(normed), BATCH):
                cur.executemany(UPSERT, normed[i:i + BATCH])
                try:
                    new += max(cur.rowcount or 0, 0)
                except Exception:
                    pass
            pg.commit()
        finally:
            cur.close()
    log("%s: جديد=%d مكرر=%d" % (label, new, len(normed) - new))
    if clear_device and normed:
        try:
            zk.conn.clear_attendance()
            log("%s: تم مسح سجل الجهاز" % label)
        except Exception as e:
            log("%s: تعذر مسح السجل (%s)" % (label, e))
    try:
        zk.disconnect()
    except Exception:
        pass
    return len(rows), new


def main() -> int:
    ap = argparse.ArgumentParser(description="مزامنة البصمات ZK → urs.zk_attendance")
    ap.add_argument("--devices", default="", help="تجاوز: ip:port مفصولة بفواصل")
    ap.add_argument("--limit", type=int, default=1000000, help="حد السحب للاختبار")
    ap.add_argument("--once", action="store_true", help="تشغيلة واحدة (وضع BAT/المجدول)")
    ap.add_argument("--clear-device", action="store_true", help="مسح سجل الجهاز بعد المزامنة")
    args = ap.parse_args()

    has_django = _django_setup()
    if not has_django and not args.devices:
        log("لا Django ولا --devices — لا توجد أجهزة")
        return 2
    devices = get_zk_devices(args.devices)
    if not devices:
        log("لا توجد أجهزة ZK (جدول الاتصالات فارغ ولا --devices)")
        return 2
    log("الأجهزة: %s" % ", ".join("%s (%s:%s)" % (l, h, p) for _, h, p, l in devices))

    import psycopg2
    dsn = get_target_dsn()
    log("الهدف: %(user)s@%(host)s:%(port)s/%(dbname)s :: zk_attendance" % dsn)
    pg = psycopg2.connect(connect_timeout=10, **dsn)
    try:
        cur = pg.cursor()
        cur.execute("SET TIME ZONE '%s'" % SESSION_TZ)
        for stmt in [s for s in DDL.split(";") if s.strip()]:
            cur.execute(stmt)
        pg.commit()
        cur.close()
    except Exception as e:
        log("تعذر تجهيز الجدول الهدف: %s" % e)
        pg.close()
        return 2

    ok, fail, total_new = 0, 0, 0
    for terminal_id, host, port, label in devices:
        try:
            _, new = sync_one(pg, terminal_id, host, port, label, args.limit, args.clear_device)
            total_new += new
            ok += 1
        except Exception as e:
            fail += 1
            log("%s: فشل (%s)" % (label, str(e)[:300]))
    pg.close()
    log("انتهى: ناجح=%d فاشل=%d جديد=%d" % (ok, fail, total_new))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
