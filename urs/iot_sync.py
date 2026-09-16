"""
IoT mirror sync — مزامنة اتصالات IoT (أجهزة البصمة) إلى جداول محلية.

- الجدول: iot_<engine>_<endpoint> على اتصال محلي (postgres) — مثال iot_zk_att
- السحب UNION ALL من كل أجهزة الاتصال (الرئيسي + devices) عبرnadpoint att
- المزامنة في خيط خلفي غير حاجب + تقدم لحظي في صف IoTMirror
- الأولى كاملة (شريط تقدم)، التالية تدريجية عبر watermarks + مسح سجل الجهاز (اختياري)
"""
from __future__ import annotations
import re
import threading
import time
import traceback
from typing import Dict, List, Tuple, Any

SESSION_TZ = "Asia/Aden"
BATCH = 1000

_MIRROR_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.{table} (
  id SERIAL PRIMARY KEY,
  device_ip VARCHAR(64) NOT NULL DEFAULT '',
  emp_name VARCHAR(128) NOT NULL DEFAULT '',
  emp_no VARCHAR(64) NOT NULL,
  punch_date DATE NOT NULL,
  punch_time TIME NOT NULL,
  punch_ts TIMESTAMPTZ NOT NULL,
  status INTEGER NOT NULL DEFAULT 0,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT {uq} UNIQUE (device_ip, emp_no, punch_ts)
);
CREATE INDEX IF NOT EXISTS {ix_ts} ON {schema}.{table} (punch_ts);
CREATE INDEX IF NOT EXISTS {ix_emp} ON {schema}.{table} (emp_no);
"""

_MIRROR_UPSERT = """
INSERT INTO {schema}.{table}
  (device_ip, emp_name, emp_no, punch_date, punch_time, punch_ts, status)
VALUES (%(device_ip)s, %(emp_name)s, %(emp_no)s, %(punch_date)s,
        %(punch_time)s, %(punch_ts)s, %(status)s)
ON CONFLICT (device_ip, emp_no, punch_ts) DO NOTHING
"""

_lock = threading.Lock()
_sweeper_started = False
_running_jobs: Dict[int, bool] = {}


def mirror_table_name(engine: str, endpoint: str) -> str:
    base = "iot_%s_%s" % (engine or "iot", endpoint or "data")
    base = re.sub(r"[^a-z0-9_]", "_", base.lower())
    base = re.sub(r"_+", "_", base).strip("_")
    return base or "iot_data"


def _q(ident: str) -> str:
    return '"%s"' % str(ident).replace('"', '""')


def local_pg(local_conn):
    """psycopg2 connection to a local postgres Connection record (Asia/Aden session)."""
    import psycopg2
    if str(getattr(local_conn, "engine", "") or "").lower() != "postgres":
        raise ValueError("الاتصال المحلي يجب أن يكون postgres (للجداول المرآة)")
    pg = psycopg2.connect(
        dbname=local_conn.instance or "urs",
        user=local_conn.user or "postgres",
        password=local_conn.password or "postgres",
        host=local_conn.host or "127.0.0.1",
        port=int(local_conn.port or 5432),
        connect_timeout=10,
    )
    try:
        cur = pg.cursor()
        cur.execute("SET TIME ZONE '%s'" % SESSION_TZ)
        cur.close()
    except Exception:
        pass
    return pg


def mirror_schema(local_conn) -> str:
    return (getattr(local_conn, "schema", "") or "").strip() or "public"


def ensure_mirror_table(pg, schema: str, table: str) -> None:
    short = re.sub(r"[^a-z0-9]", "", table.lower())[:40] or "iot"
    cur = pg.cursor()
    try:
        cur.execute(_MIRROR_DDL.format(
            schema=_q(schema), table=_q(table),
            uq=_q("uq_%s" % short), ix_ts=_q("ix_%s_ts" % short), ix_emp=_q("ix_%s_emp" % short)))
        pg.commit()
    finally:
        cur.close()


def _zk_pull(host: str, port: int, endpoint: str, since: str = None,
             limit: int = 1000000, timeout: int = 30):
    """Single device pull → JSON-serializable rows (fast path: one pull, in-memory)."""
    from odex.engines.zk import ZKEngine
    try:
        from odex.engines.zk import tolerant_zk_decode as _tzd
    except Exception:
        _tzd = None
    import contextlib as _cl
    ep = str(endpoint or "att").lower()
    zk = ZKEngine(ip=host, port=int(port or 4370), timeout=timeout, password=0)
    zk.connect()
    try:
        cm = _tzd() if _tzd else _cl.nullcontext()
        with cm:
            if ep in ("att",):
                rows = zk.att(limit=limit, since=since)
            else:
                rows = list(zk.list_all(ep, limit=limit) or [])
        return rows
    finally:
        try:
            zk.disconnect()
        except Exception:
            pass


def fetch_union(connection, endpoint: str = "att", limit: int = 1000000,
                timeout: int = 30) -> Dict[str, Any]:
    """UNION ALL من كل الأجهزة: {devices:[...], rows:[...]} — JSON جاهز."""
    devices = connection.device_list() if hasattr(connection, "device_list") else [
        (connection.host, int(connection.port or 4370))]
    all_rows: List[Dict[str, Any]] = []
    dev_info = []
    for host, port in devices:
        try:
            rows = _zk_pull(host, port, endpoint, timeout=timeout)
            for r in (rows or []):
                if isinstance(r, dict):
                    rr = dict(r)
                    rr.setdefault("device_ip", host)
                    all_rows.append(rr)
            dev_info.append({"host": host, "port": port, "ok": True,
                             "rows": len(rows or [])})
        except Exception as e:
            dev_info.append({"host": host, "port": port, "ok": False,
                             "rows": 0, "error": str(e)[:300]})
    return {"devices": dev_info, "rows": all_rows, "total": len(all_rows),
            "endpoint": endpoint}


def _mirror_progress(mirror_id: int, **kw) -> None:
    try:
        from django.db import connections as _conns
        _conns.close_all()
        from urs.models import IoTMirror
        IoTMirror.objects.filter(id=mirror_id).update(**kw)
    except Exception:
        pass


def _norm_att_row(r: Dict[str, Any], device_ip: str):
    try:
        emp_no = str(r.get("emp_no", "") or "").strip()
        ts = str(r.get("punch_ts", "") or "").strip()
        if not emp_no or not ts:
            return None
        try:
            status = int(r.get("status", 0) or 0)
        except Exception:
            status = 0
        return {
            "device_ip": device_ip,
            "emp_name": str(r.get("emp_name", "") or ""),
            "emp_no": emp_no,
            "punch_date": str(r.get("punch_date", "") or ts[:10]),
            "punch_time": str(r.get("punch_time", "") or ts[11:19]),
            "punch_ts": ts,
            "status": status,
        }
    except Exception:
        return None


def run_mirror_sync(mirror_id: int) -> Dict[str, Any]:
    """Full sync job (blocking — call in background thread). Updates progress row."""
    from django.db import connections as _conns
    try:
        _conns.close_all()
    except Exception:
        pass
    from urs.models import IoTMirror
    import json as _json
    try:
        m = IoTMirror.objects.select_related("connection", "local_connection").get(id=mirror_id)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    with _lock:
        if _running_jobs.get(mirror_id):
            return {"ok": False, "error": "مزامنة جارية بالفعل"}
        _running_jobs[mirror_id] = True
    try:
        return _do_sync(m)
    finally:
        with _lock:
            _running_jobs.pop(mirror_id, None)
        try:
            _conns.close_all()
        except Exception:
            pass


def _do_sync(m) -> Dict[str, Any]:
    import json as _json
    from urs.models import IoTMirror
    conn = m.connection
    devices = conn.device_list() if hasattr(conn, "device_list") else [
        (conn.host, int(conn.port or 4370))]
    if not devices:
        _mirror_progress(m.id, status="error", last_error="لا توجد أجهزة في الاتصال")
        return {"ok": False, "error": "لا توجد أجهزة في الاتصال"}
    try:
        wm = _json.loads(m.watermarks or "{}")
    except Exception:
        wm = {}
    _mirror_progress(m.id, status="running", progress_pct=0, devices_total=len(devices),
                     devices_done=0, rows_pulled=0, rows_new=0, last_error="")
    try:
        pg = local_pg(m.local_connection)
    except Exception as e:
        _mirror_progress(m.id, status="error", last_error=str(e)[:500])
        return {"ok": False, "error": str(e)}
    import datetime as _dt
    schema = mirror_schema(m.local_connection)
    sql_upsert = _MIRROR_UPSERT.format(schema=_q(schema), table=_q(m.table_name))
    total_pulled = total_new = done = 0
    try:
        ensure_mirror_table(pg, schema, m.table_name)
        for host, port in devices:
            since = wm.get(host)
            try:
                rows = _zk_pull(host, port, m.endpoint, since=since)
            except Exception as e:
                done += 1
                _mirror_progress(m.id, devices_done=done,
                                 progress_pct=int(done * 100 / max(len(devices), 1)),
                                 last_error="جهاز %s: %s" % (host, str(e)[:200]))
                continue
            normed = []
            for r in (rows or []):
                n = _norm_att_row(r if isinstance(r, dict) else {}, host)
                if n:
                    normed.append(n)
            total_pulled += len(normed)
            new_here = 0
            if normed:
                cur = pg.cursor()
                try:
                    for i in range(0, len(normed), BATCH):
                        cur.executemany(sql_upsert, normed[i:i + BATCH])
                        try:
                            new_here += max(cur.rowcount or 0, 0)
                        except Exception:
                            pass
                        _mirror_progress(
                            m.id, rows_pulled=total_pulled, rows_new=total_new + new_here,
                            progress_pct=min(99, int(((done + (i + BATCH) / max(len(normed), 1)) * 100) / max(len(devices), 1))))
                    pg.commit()
                finally:
                    cur.close()
            total_new += new_here
            # watermark = max punch_ts seen
            try:
                mx = max([n["punch_ts"] for n in normed]) if normed else None
                if mx and (host not in wm or mx > wm[host]):
                    wm[host] = mx
            except Exception:
                pass
            # clear device log to keep next syncs instant
            if m.clear_device and normed:
                try:
                    from odex.engines.zk import ZKEngine
                    zk = ZKEngine(ip=host, port=int(port or 4370), timeout=15, password=0)
                    zk.connect()
                    try:
                        zk.truncate("attendance")
                    finally:
                        try:
                            zk.disconnect()
                        except Exception:
                            pass
                except Exception:
                    pass
            done += 1
            _mirror_progress(m.id, devices_done=done, rows_pulled=total_pulled,
                             rows_new=total_new,
                             progress_pct=int(done * 100 / max(len(devices), 1)))
        try:
            pg.close()
        except Exception:
            pass
        IoTMirror.objects.filter(id=m.id).update(
            status="done", progress_pct=100, watermarks=_json.dumps(wm, ensure_ascii=False),
            last_sync_at=_dt.datetime.now(_dt.timezone.utc), last_error="")
        return {"ok": True, "devices": len(devices), "pulled": total_pulled, "new": total_new}
    except Exception as e:
        try:
            pg.close()
        except Exception:
            pass
        _mirror_progress(m.id, status="error",
                         last_error="%s\n%s" % (str(e)[:300], traceback.format_exc(limit=3)[-800:]))
        return {"ok": False, "error": str(e)}


def start_sync_async(mirror_id: int) -> bool:
    """Launch sync in daemon thread (non-blocking). Returns False if already running."""
    with _lock:
        if _running_jobs.get(mirror_id):
            return False
    t = threading.Thread(target=run_mirror_sync, args=(mirror_id,), daemon=True,
                         name="iot-mirror-%s" % mirror_id)
    t.start()
    return True


def _sweeper_tick(now=None) -> int:
    """Single sweeper pass (testable): recover stale `running` + start due syncs.

    Returns number of syncs started. A `running` mirror whose row hasn't been
    touched for 2×interval (min 10 min) is a leftover of a killed process —
    it's reset to idle so auto-sync resumes instead of hanging forever.
    """
    import datetime as _dt
    from django.db import connections as _conns
    try:
        _conns.close_all()
    except Exception:
        pass
    try:
        from urs.models import IoTMirror
        mirrors = list(IoTMirror.objects.filter(auto_sync=True))
    except Exception:
        return 0
    now = now or _dt.datetime.now(_dt.timezone.utc)
    started = 0
    for m in mirrors:
        try:
            if str(m.status or "") == "running":
                try:
                    ref = m.updated_at or m.last_sync_at or now
                    stale_min = (now - ref).total_seconds() / 60.0
                except Exception:
                    stale_min = 0
                if stale_min >= max(2 * int(m.interval_min or 15), 10):
                    from urs.models import IoTMirror as _M
                    _M.objects.filter(id=m.id).update(
                        status="idle",
                        last_error="توقف مفاجئ أثناء مزامنة سابقة — أُعيدت الجدولة تلقائياً")
                    m.status = "idle"
                else:
                    continue
            due = True
            if m.last_sync_at:
                mins = (now - m.last_sync_at).total_seconds() / 60.0
                due = mins >= max(int(m.interval_min or 15), 1)
            if due and start_sync_async(m.id):
                started += 1
        except Exception:
            pass
    return started


def _sweeper_loop(interval_sec: int = 60) -> None:
    while True:
        try:
            _sweeper_tick()
        except Exception:
            pass
        time.sleep(max(int(interval_sec or 60), 30))


def ensure_sweeper() -> None:
    global _sweeper_started
    with _lock:
        if _sweeper_started:
            return
        _sweeper_started = True
    t = threading.Thread(target=_sweeper_loop, daemon=True, name="iot-sweeper")
    t.start()
