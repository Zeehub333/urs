"""
ZK Engine — 35 DYNAMIC data functions for ZKTeco devices

Dynamic table/entity abstraction over pyzk. Tables: user, attendance, etc. are device collections.
All functions take (table, data/filters) dynamically — no static column hardcoding.
"""
from typing import Any, Dict, List, Optional
try:
    from zk import ZK
except ImportError:
    ZK = None

# Canonical logical-table column definitions for ZK devices.
# Single source of truth for: RML engine API staging, connection metadata UI.
# (Device getters below return exactly these keys.)
ZK_TABLE_COLUMNS = {
    "users": [("uid", "INTEGER"), ("user_id", "VARCHAR"), ("name", "VARCHAR"),
              ("privilege", "INTEGER"), ("card", "VARCHAR"), ("group_id", "VARCHAR")],
    "attendance": [("uid", "INTEGER"), ("user_id", "VARCHAR"), ("timestamp", "TIMESTAMP"),
                   ("status", "INTEGER"), ("punch", "INTEGER")],
    # att: flattened attendance — employee name/number + local date/time split + status
    "att": [("emp_name", "VARCHAR"), ("emp_no", "VARCHAR"),
            ("punch_date", "DATE"), ("punch_time", "TIME"),
            ("punch_ts", "TIMESTAMP"), ("status", "INTEGER"),
            ("device_ip", "VARCHAR")],
}

# Mirror-table DDL column order for att (Postgres)
ATT_MIRROR_COLUMNS = ["device_ip", "emp_name", "emp_no", "punch_date",
                      "punch_time", "punch_ts", "status"]


from contextlib import contextmanager

@contextmanager
def tolerant_zk_decode():
    """Temporarily make pyzk timestamp decoding loss-tolerant (bad records -> None).

    Fingerprint devices with drifting clocks emit impossible dates that would
    otherwise abort the whole pull inside pyzk's parser. Scoped + restored;
    no-op when the installed pyzk layout differs.
    """
    try:
        from zk import base as _zkb
    except Exception:
        yield
        return
    _orig = getattr(_zkb.ZK, "_ZK__decode_time", None)
    if _orig is None or getattr(_orig, "_rml_tolerant", False):
        yield
        return

    def _tolerant(self, t):
        try:
            return _orig(self, t)
        except Exception:
            return None

    _tolerant._rml_tolerant = True
    _zkb.ZK._ZK__decode_time = _tolerant
    try:
        yield
    finally:
        try:
            if getattr(_zkb.ZK, "_ZK__decode_time", None) is _tolerant:
                _zkb.ZK._ZK__decode_time = _orig
        except Exception:
            pass

class ZKEngine:
    def __init__(self, ip: str, port: int=4370, timeout: int=5, password: int=0):
        self.ip, self.port, self.timeout, self.password, self.conn, self.zk = ip, port, timeout, password, None, None
    def connect(self):
        if ZK is None: raise RuntimeError("pip install pyzk")
        self.zk=ZK(self.ip, port=self.port, timeout=self.timeout, password=self.password, force_udp=False, ommit_ping=False); self.conn=self.zk.connect(); return self.conn
    def disconnect(self):
        if self.conn: self.conn.disconnect(); self.conn=None
    def _ensure(self):
        if not self.conn: raise RuntimeError("ZK not connected")

    # helpers to map dynamic table -> ZK API
    def _table_to_getter(self, table: str):
        t=table.lower()
        if t in ("user","users"): return lambda: [{"uid": u.uid, "user_id": u.user_id, "name": u.name, "privilege": u.privilege, "card": u.card, "group_id": u.group_id} for u in self.conn.get_users()]
        if t in ("attendance","attendances","transaction","transactions"): return lambda: [{"uid": r.uid, "user_id": r.user_id, "timestamp": (r.timestamp.isoformat() if getattr(r, "timestamp", None) else None), "status": r.status, "punch": r.punch} for r in self.conn.get_attendance()]
        if t in ("att",): return lambda: self.att(limit=100000)
        return lambda: []

    def att(self, limit: int=100000, since: str=None) -> List[Dict[str, Any]]:
        """Flattened attendance endpoint: employee name/number + local date/time split + status.

        Returns [{emp_name, emp_no, punch_date, punch_time, punch_ts, status, device_ip}]
        - punch_date: YYYY-MM-DD, punch_time: HH:MM:SS (device local time as-is)
        - punch_ts: combined 'YYYY-MM-DD HH:MM:SS' for timestamptz storage
        - since: optional 'YYYY-MM-DD HH:MM:SS' — only rows with punch_ts >= since
        """
        self._ensure()
        try:
            users = {str(u.user_id): (u.name or "") for u in self.conn.get_users()}
        except Exception:
            users = {}
        try:
            logs = self.conn.get_attendance()
        except Exception:
            return []
        out = []
        for r in (logs or []):
            try:
                ts = getattr(r, "timestamp", None)
                if ts is None:
                    continue
                date_s = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
                time_s = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)[11:19]
                emp_no = str(getattr(r, "user_id", "") or "")
                if not emp_no:
                    continue
                punch_ts = "%s %s" % (date_s, time_s)
                if since and punch_ts < str(since):
                    continue
                try:
                    status = int(getattr(r, "status", 0) or 0)
                except Exception:
                    status = 0
                out.append({
                    "emp_name": users.get(emp_no, ""),
                    "emp_no": emp_no,
                    "punch_date": date_s,
                    "punch_time": time_s,
                    "punch_ts": punch_ts,
                    "status": status,
                    "device_ip": getattr(self, "ip", ""),
                })
                if len(out) >= limit:
                    break
            except Exception:
                continue
        return out

    def create(self, table: str, data: Dict[str, Any]) -> int:
        self._ensure(); t=table.lower()
        if t in ("user","users"):
            self.conn.set_user(uid=int(data.get("uid", data.get("user_id", 0))), name=data.get("name",""), privilege=int(data.get("privilege",0)), password=str(data.get("password","")), group_id=str(data.get("group_id","")), user_id=str(data.get("user_id","")), card=int(data.get("card",0))); return 1
        return 0
    def bulk_create(self, table: str, rows: List[Dict[str, Any]]) -> int:
        return sum(self.create(table, r) for r in rows)
    def get_by_id(self, table: str, pk_value: Any, pk: str="uid") -> Optional[Dict[str, Any]]:
        self._ensure()
        for row in self._table_to_getter(table)():
            if str(row.get(pk))==str(pk_value) or str(row.get("user_id"))==str(pk_value): return row
        return None
    def get_one(self, table: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self._ensure()
        for row in self._table_to_getter(table)():
            if all(str(row.get(k))==str(v) for k,v in filters.items()): return row
        return None
    def list_all(self, table: str, limit: int=100, offset: int=0, order_by: str="uid") -> List[Dict[str, Any]]:
        self._ensure(); rows=self._table_to_getter(table)(); return sorted(rows, key=lambda x: x.get(order_by, 0))[offset:offset+limit]
    def filter(self, table: str, filters: Dict[str, Any], limit: int=100, offset: int=0, order_by: str="uid") -> List[Dict[str, Any]]:
        self._ensure(); rows=[r for r in self._table_to_getter(table)() if all(str(r.get(k))==str(v) for k,v in filters.items())]; return sorted(rows, key=lambda x: x.get(order_by,0))[offset:offset+limit]
    def search(self, table: str, field: str, pattern: str, limit: int=100) -> List[Dict[str, Any]]:
        self._ensure(); return [r for r in self._table_to_getter(table)() if pattern.lower() in str(r.get(field,"")).lower()][:limit]
    def count(self, table: str, filters: Dict[str, Any]=None) -> int:
        self._ensure()
        if not filters: return len(self._table_to_getter(table)())
        return len(self.filter(table, filters, limit=100000))
    def exists(self, table: str, filters: Dict[str, Any]) -> bool: return self.count(table, filters)>0
    def update_by_id(self, table: str, pk_value: Any, data: Dict[str, Any], pk: str="uid") -> int:
        self._ensure(); t=table.lower()
        if t in ("user","users"):
            cur=self.get_by_id(table, pk_value, pk)
            if not cur: return 0
            merged={**cur, **data}; self.delete_by_id(table, pk_value, pk); self.create(table, merged); return 1
        return 0
    def update_where(self, table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> int:
        self._ensure(); rows=self.filter(table, filters, limit=100000); c=0
        for r in rows: c+=self.update_by_id(table, r.get("uid") or r.get("user_id"), data)
        return c
    def upsert(self, table: str, data: Dict[str, Any], unique_field: str) -> int:
        self._ensure()
        if self.exists(table, {unique_field: data[unique_field]}): return self.update_where(table, {unique_field: data[unique_field]}, data)
        return self.create(table, data)
    def increment(self, table: str, pk_value: Any, field: str, delta: float=1, pk: str="uid") -> int:
        row=self.get_by_id(table, pk_value, pk)
        if not row: return 0
        row[field]=float(row.get(field,0))+delta; return self.update_by_id(table, pk_value, {field: row[field]}, pk)
    def delete_by_id(self, table: str, pk_value: Any, pk: str="uid") -> int:
        self._ensure(); t=table.lower()
        if t in ("user","users"): self.conn.delete_user(uid=int(pk_value), user_id=str(pk_value)); return 1
        return 0
    def delete_where(self, table: str, filters: Dict[str, Any]) -> int:
        self._ensure(); rows=self.filter(table, filters, limit=100000); return sum(self.delete_by_id(table, r.get("uid") or r.get("user_id")) for r in rows)
    def truncate(self, table: str) -> int:
        self._ensure(); t=table.lower()
        if t in ("user","users"): self.conn.clear_data(); return 1
        if t in ("attendance","attendances","transaction"): self.conn.clear_attendance(); return 1
        return 0
    def distinct(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        rows=self.filter(table, filters or {}, limit=100000) if filters else self.list_all(table, limit=100000); return list({r.get(field) for r in rows})
    def aggregate(self, table: str, func: str, field: str, filters: Dict[str, Any]=None) -> Any:
        vals=[r.get(field) for r in (self.filter(table, filters, limit=100000) if filters else self.list_all(table, limit=100000)) if r.get(field) is not None]
        if not vals: return None
        f=func.lower()
        if f=="count": return len(vals)
        if f=="sum": return sum(float(v) for v in vals)
        if f=="avg": return sum(float(v) for v in vals)/len(vals)
        if f=="min": return min(vals)
        if f=="max": return max(vals)
        return None
    def sum_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "sum", field, filters)
    def avg_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "avg", field, filters)
    def min_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "min", field, filters)
    def max_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "max", field, filters)
    def group_by(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        from collections import Counter
        rows=self.filter(table, filters, limit=100000) if filters else self.list_all(table, limit=100000); cnt=Counter(r.get(field) for r in rows); return [{field: k, "cnt": v} for k,v in cnt.items()]
    def join_select(self, base_table: str, join_table: str, on: str, filters: Dict[str, Any]=None, limit: int=100) -> List[Dict[str, Any]]:
        left=self.list_all(base_table, limit=limit); right={str(r.get(on.split("=")[0].strip().split(".")[-1])): r for r in self.list_all(join_table, limit=100000)}
        out=[]
        for l in left:
            if filters and not all(str(l.get(k))==str(v) for k,v in filters.items()): continue
            merged=dict(l); merged.update(right.get(str(l.get(on.split("=")[0].strip().split(".")[-1])), {})); out.append(merged)
        return out[:limit]
    def raw_query(self, sql: str, params: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        self._ensure(); return []  # no SQL on ZK
    def raw_execute(self, sql: str, params: Dict[str, Any]=None) -> int:
        self._ensure(); return 0
    def paginate(self, table: str, page: int=1, page_size: int=20, filters: Dict[str, Any]=None, order_by: str="uid") -> Dict[str, Any]:
        total=self.count(table, filters); offset=(page-1)*page_size; rows=self.filter(table, filters or {}, limit=page_size, offset=offset, order_by=order_by) if filters else self.list_all(table, limit=page_size, offset=offset, order_by=order_by); return {"total": total, "page": page, "page_size": page_size, "rows": rows}
    def bulk_update(self, table: str, rows: List[Dict[str, Any]], pk: str="uid") -> int:
        return sum(self.update_by_id(table, r.pop(pk), r, pk) for r in rows if pk in r)
    def bulk_delete(self, table: str, ids: List[Any], pk: str="uid") -> int:
        return sum(self.delete_by_id(table, i, pk) for i in ids)
    def copy_row(self, table: str, pk_value: Any, overrides: Dict[str, Any]=None, pk: str="uid") -> int:
        row=self.get_by_id(table, pk_value, pk)
        if not row: return 0
        row.pop(pk, None); 
        if overrides: row.update(overrides)
        return self.create(table, row)
    def clone_table(self, new_table: str, source_table: str) -> int:
        self._ensure(); return 0  # physical device, no clone
    def rename_column(self, table: str, old: str, new: str) -> int: return 0
    def add_column(self, table: str, column_def: str) -> int: return 0
    def drop_column(self, table: str, column: str) -> int: return 0
    def get_schema(self, table: str) -> List[Dict[str, Any]]:
        rows=self.list_all(table, limit=1)
        return [{"column": k, "type": type(v).__name__} for k,v in (rows[0].items() if rows else {})]
    def bulk_upsert(self, table: str, rows: List[Dict[str, Any]], unique_field: str) -> int: return sum(self.upsert(table, r, unique_field) for r in rows)
    def find_or_create(self, table: str, filters: Dict[str, Any], defaults: Dict[str, Any]=None) -> Dict[str, Any]:
        row=self.get_one(table, filters)
        if row: return row
        data={**filters, **(defaults or {})}; self.create(table, data); return self.get_one(table, filters)
    def first(self, table: str, filters: Dict[str, Any]=None, order_by: str="uid") -> Optional[Dict[str, Any]]:
        rows=self.filter(table, filters or {}, limit=1, order_by=order_by) if filters else self.list_all(table, limit=1, order_by=order_by); return rows[0] if rows else None
    def last(self, table: str, filters: Dict[str, Any]=None, order_by: str="uid") -> Optional[Dict[str, Any]]:
        rows=self.list_all(table, limit=100000, order_by=order_by) if not filters else self.filter(table, filters, limit=100000, order_by=order_by); return rows[-1] if rows else None
    def sample(self, table: str, n: int=5, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        import random; rows=self.filter(table, filters or {}, limit=100000) if filters else self.list_all(table, limit=100000); return random.sample(rows, min(n, len(rows))) if rows else []
    def pluck(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        rows=self.filter(table, filters or {}, limit=100000) if filters else self.list_all(table, limit=100000); return [r.get(field) for r in rows]
    def where_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        vals=set(str(v) for v in values); return [r for r in self.list_all(table, limit=100000) if str(r.get(field)) in vals][:limit]
    def where_not_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        vals=set(str(v) for v in values); return [r for r in self.list_all(table, limit=100000) if str(r.get(field)) not in vals][:limit]
    def where_between(self, table: str, field: str, low: Any, high: Any, limit: int=100) -> List[Dict[str, Any]]:
        return [r for r in self.list_all(table, limit=100000) if low <= r.get(field, low) <= high][:limit]
    def where_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        return [r for r in self.list_all(table, limit=100000) if r.get(field) is None][:limit]
    def where_not_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        return [r for r in self.list_all(table, limit=100000) if r.get(field) is not None][:limit]
    def order_by_limit(self, table: str, order_by: str, direction: str="ASC", limit: int=100, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        rows=self.filter(table, filters or {}, limit=100000, order_by=order_by) if filters else self.list_all(table, limit=100000, order_by=order_by); reverse=(direction.upper()=="DESC"); return sorted(rows, key=lambda x: x.get(order_by, 0), reverse=reverse)[:limit]
    def lock_for_update(self, table: str, pk_value: Any, pk: str="uid") -> Optional[Dict[str, Any]]: return self.get_by_id(table, pk_value, pk)
    def explain(self, table: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]: return [{"table": table, "filters": filters, "count": self.count(table, filters)}]
    def export_csv(self, table: str, filepath: str, filters: Dict[str, Any]=None) -> int:
        import csv; rows=self.filter(table, filters or {}, limit=100000) if filters else self.list_all(table, limit=100000)
        if not rows: return 0
        with open(filepath, "w", newline="", encoding="utf-8") as f: w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows); return len(rows)

# ── DB REFLECTION (100) ──
    def list_schemas(self, ) -> list:
        """list all schemas — reflection (ZK device)"""
        self._ensure()
        if "list_schemas" == "list_tables": return ["users","attendance"]
        if "list_schemas" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_tables(self, schema: str = None) -> list:
        """list tables — reflection (ZK device)"""
        self._ensure()
        if "list_tables" == "list_tables": return ["users","attendance","att"]
        if "list_tables" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_views(self, schema: str = None) -> list:
        """list views — reflection (ZK device)"""
        self._ensure()
        if "list_views" == "list_tables": return ["users","attendance"]
        if "list_views" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_materialized_views(self, schema: str = None) -> list:
        """list mviews — reflection (ZK device)"""
        self._ensure()
        if "list_materialized_views" == "list_tables": return ["users","attendance"]
        if "list_materialized_views" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_tables_with_type(self, table_type: str = 'TABLE') -> list:
        """list tables with type — reflection (ZK device)"""
        self._ensure()
        if "list_tables_with_type" == "list_tables": return ["users","attendance"]
        if "list_tables_with_type" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_temporary_tables(self, ) -> list:
        """list temp tables — reflection (ZK device)"""
        self._ensure()
        if "list_temporary_tables" == "list_tables": return ["users","attendance"]
        if "list_temporary_tables" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_system_tables(self, ) -> list:
        """list system tables — reflection (ZK device)"""
        self._ensure()
        if "list_system_tables" == "list_tables": return ["users","attendance"]
        if "list_system_tables" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_table_info(self, table: str, schema: str = None) -> list:
        """get table info — reflection (ZK device)"""
        self._ensure()
        if "get_table_info" == "list_tables": return ["users","attendance"]
        if "get_table_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_table_columns(self, table: str, schema: str = None) -> list:
        """get columns for table — reflection (ZK device)"""
        self._ensure()
        if "get_table_columns" == "list_tables": return ["users","attendance"]
        if "get_table_columns" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_column_info(self, table: str, column: str, schema: str = None) -> list:
        """get column info — reflection (ZK device)"""
        self._ensure()
        if "get_column_info" == "list_tables": return ["users","attendance"]
        if "get_column_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_column_exists(self, table: str, column: str, schema: str = None) -> list:
        """check column exists — reflection (ZK device)"""
        self._ensure()
        if "get_column_exists" == "list_tables": return ["users","attendance"]
        if "get_column_exists" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_column_type(self, table: str, column: str) -> list:
        """get column type — reflection (ZK device)"""
        self._ensure()
        if "get_column_type" == "list_tables": return ["users","attendance"]
        if "get_column_type" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_column_default(self, table: str, column: str) -> list:
        """get column default — reflection (ZK device)"""
        self._ensure()
        if "get_column_default" == "list_tables": return ["users","attendance"]
        if "get_column_default" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_column_nullable(self, table: str, column: str) -> list:
        """check nullable — reflection (ZK device)"""
        self._ensure()
        if "get_column_nullable" == "list_tables": return ["users","attendance"]
        if "get_column_nullable" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_column_comment(self, table: str, column: str) -> list:
        """get column comment — reflection (ZK device)"""
        self._ensure()
        if "get_column_comment" == "list_tables": return ["users","attendance"]
        if "get_column_comment" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_primary_keys(self, table: str, schema: str = None) -> list:
        """list pks — reflection (ZK device)"""
        self._ensure()
        if "list_primary_keys" == "list_tables": return ["users","attendance"]
        if "list_primary_keys" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_primary_key(self, table: str) -> list:
        """get pk — reflection (ZK device)"""
        self._ensure()
        if "get_primary_key" == "list_tables": return ["users","attendance"]
        if "get_primary_key" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_foreign_keys(self, table: str = None) -> list:
        """list fks — reflection (ZK device)"""
        self._ensure()
        if "list_foreign_keys" == "list_tables": return ["users","attendance"]
        if "list_foreign_keys" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_foreign_key(self, table: str, column: str) -> list:
        """get fk — reflection (ZK device)"""
        self._ensure()
        if "get_foreign_key" == "list_tables": return ["users","attendance"]
        if "get_foreign_key" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_unique_constraints(self, table: str = None) -> list:
        """list unique constraints — reflection (ZK device)"""
        self._ensure()
        if "list_unique_constraints" == "list_tables": return ["users","attendance"]
        if "list_unique_constraints" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_check_constraints(self, table: str = None) -> list:
        """list check constraints — reflection (ZK device)"""
        self._ensure()
        if "list_check_constraints" == "list_tables": return ["users","attendance"]
        if "list_check_constraints" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_not_null_constraints(self, table: str = None) -> list:
        """list not null — reflection (ZK device)"""
        self._ensure()
        if "list_not_null_constraints" == "list_tables": return ["users","attendance"]
        if "list_not_null_constraints" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_constraint_info(self, constraint: str) -> list:
        """get constraint info — reflection (ZK device)"""
        self._ensure()
        if "get_constraint_info" == "list_tables": return ["users","attendance"]
        if "get_constraint_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_indexes(self, table: str = None) -> list:
        """list indexes — reflection (ZK device)"""
        self._ensure()
        if "list_indexes" == "list_tables": return ["users","attendance"]
        if "list_indexes" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_index_info(self, index: str) -> list:
        """get index info — reflection (ZK device)"""
        self._ensure()
        if "get_index_info" == "list_tables": return ["users","attendance"]
        if "get_index_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_index_columns(self, index: str) -> list:
        """get index columns — reflection (ZK device)"""
        self._ensure()
        if "get_index_columns" == "list_tables": return ["users","attendance"]
        if "get_index_columns" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_unique_indexes(self, table: str = None) -> list:
        """list unique indexes — reflection (ZK device)"""
        self._ensure()
        if "list_unique_indexes" == "list_tables": return ["users","attendance"]
        if "list_unique_indexes" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_sequences(self, schema: str = None) -> list:
        """list sequences — reflection (ZK device)"""
        self._ensure()
        if "list_sequences" == "list_tables": return ["users","attendance"]
        if "list_sequences" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_sequence_info(self, sequence: str) -> list:
        """get sequence info — reflection (ZK device)"""
        self._ensure()
        if "get_sequence_info" == "list_tables": return ["users","attendance"]
        if "get_sequence_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_triggers(self, table: str = None) -> list:
        """list triggers — reflection (ZK device)"""
        self._ensure()
        if "list_triggers" == "list_tables": return ["users","attendance"]
        if "list_triggers" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_trigger_info(self, trigger: str) -> list:
        """get trigger info — reflection (ZK device)"""
        self._ensure()
        if "get_trigger_info" == "list_tables": return ["users","attendance"]
        if "get_trigger_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_procedures(self, schema: str = None) -> list:
        """list procedures — reflection (ZK device)"""
        self._ensure()
        if "list_procedures" == "list_tables": return ["users","attendance"]
        if "list_procedures" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_procedure_info(self, procedure: str) -> list:
        """get procedure info — reflection (ZK device)"""
        self._ensure()
        if "get_procedure_info" == "list_tables": return ["users","attendance"]
        if "get_procedure_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_functions(self, schema: str = None) -> list:
        """list functions — reflection (ZK device)"""
        self._ensure()
        if "list_functions" == "list_tables": return ["users","attendance"]
        if "list_functions" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_function_info(self, function: str) -> list:
        """get function info — reflection (ZK device)"""
        self._ensure()
        if "get_function_info" == "list_tables": return ["users","attendance"]
        if "get_function_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_packages(self, schema: str = None) -> list:
        """list packages (oracle) — reflection (ZK device)"""
        self._ensure()
        if "list_packages" == "list_tables": return ["users","attendance"]
        if "list_packages" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_package_info(self, package: str) -> list:
        """get package info — reflection (ZK device)"""
        self._ensure()
        if "get_package_info" == "list_tables": return ["users","attendance"]
        if "get_package_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_synonyms(self, schema: str = None) -> list:
        """list synonyms — reflection (ZK device)"""
        self._ensure()
        if "list_synonyms" == "list_tables": return ["users","attendance"]
        if "list_synonyms" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_synonym_info(self, synonym: str) -> list:
        """get synonym info — reflection (ZK device)"""
        self._ensure()
        if "get_synonym_info" == "list_tables": return ["users","attendance"]
        if "get_synonym_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_types(self, schema: str = None) -> list:
        """list types — reflection (ZK device)"""
        self._ensure()
        if "list_types" == "list_tables": return ["users","attendance"]
        if "list_types" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_type_info(self, type_name: str) -> list:
        """get type info — reflection (ZK device)"""
        self._ensure()
        if "get_type_info" == "list_tables": return ["users","attendance"]
        if "get_type_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_domains(self, schema: str = None) -> list:
        """list domains — reflection (ZK device)"""
        self._ensure()
        if "list_domains" == "list_tables": return ["users","attendance"]
        if "list_domains" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_domain_info(self, domain: str) -> list:
        """get domain info — reflection (ZK device)"""
        self._ensure()
        if "get_domain_info" == "list_tables": return ["users","attendance"]
        if "get_domain_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_extensions(self, ) -> list:
        """list extensions — reflection (ZK device)"""
        self._ensure()
        if "list_extensions" == "list_tables": return ["users","attendance"]
        if "list_extensions" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_extension_info(self, extension: str) -> list:
        """get extension info — reflection (ZK device)"""
        self._ensure()
        if "get_extension_info" == "list_tables": return ["users","attendance"]
        if "get_extension_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_roles(self, ) -> list:
        """list roles — reflection (ZK device)"""
        self._ensure()
        if "list_roles" == "list_tables": return ["users","attendance"]
        if "list_roles" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_role_info(self, role: str) -> list:
        """get role info — reflection (ZK device)"""
        self._ensure()
        if "get_role_info" == "list_tables": return ["users","attendance"]
        if "get_role_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_users_db(self, ) -> list:
        """list db users — reflection (ZK device)"""
        self._ensure()
        if "list_users_db" == "list_tables": return ["users","attendance"]
        if "list_users_db" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_user_info(self, user: str) -> list:
        """get user info — reflection (ZK device)"""
        self._ensure()
        if "get_user_info" == "list_tables": return ["users","attendance"]
        if "get_user_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_privileges(self, ) -> list:
        """list privileges — reflection (ZK device)"""
        self._ensure()
        if "list_privileges" == "list_tables": return ["users","attendance"]
        if "list_privileges" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_table_privileges(self, table: str) -> list:
        """get table privileges — reflection (ZK device)"""
        self._ensure()
        if "get_table_privileges" == "list_tables": return ["users","attendance"]
        if "get_table_privileges" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_grants(self, user: str = None) -> list:
        """list grants — reflection (ZK device)"""
        self._ensure()
        if "list_grants" == "list_tables": return ["users","attendance"]
        if "list_grants" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_grant_info(self, grantee: str) -> list:
        """get grant info — reflection (ZK device)"""
        self._ensure()
        if "get_grant_info" == "list_tables": return ["users","attendance"]
        if "get_grant_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_database_info(self, ) -> list:
        """get db info — reflection (ZK device)"""
        self._ensure()
        if "get_database_info" == "list_tables": return ["users","attendance"]
        if "get_database_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_database_version(self, ) -> list:
        """get version — reflection (ZK device)"""
        self._ensure()
        if "get_database_version" == "list_tables": return ["users","attendance"]
        if "get_database_version" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_database_size(self, ) -> list:
        """get db size — reflection (ZK device)"""
        self._ensure()
        if "get_database_size" == "list_tables": return ["users","attendance"]
        if "get_database_size" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_table_size(self, table: str) -> list:
        """get table size — reflection (ZK device)"""
        self._ensure()
        if "get_table_size" == "list_tables": return ["users","attendance"]
        if "get_table_size" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_index_size(self, index: str) -> list:
        """get index size — reflection (ZK device)"""
        self._ensure()
        if "get_index_size" == "list_tables": return ["users","attendance"]
        if "get_index_size" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_row_count(self, table: str) -> list:
        """get row count — reflection (ZK device)"""
        self._ensure()
        if "get_row_count" == "list_tables": return ["users","attendance"]
        if "get_row_count" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_estimated_row_count(self, table: str) -> list:
        """get estimated count — reflection (ZK device)"""
        self._ensure()
        if "get_estimated_row_count" == "list_tables": return ["users","attendance"]
        if "get_estimated_row_count" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_partitions(self, table: str = None) -> list:
        """list partitions — reflection (ZK device)"""
        self._ensure()
        if "list_partitions" == "list_tables": return ["users","attendance"]
        if "list_partitions" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_partition_info(self, partition: str) -> list:
        """get partition info — reflection (ZK device)"""
        self._ensure()
        if "get_partition_info" == "list_tables": return ["users","attendance"]
        if "get_partition_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_subpartitions(self, table: str = None) -> list:
        """list subpartitions — reflection (ZK device)"""
        self._ensure()
        if "list_subpartitions" == "list_tables": return ["users","attendance"]
        if "list_subpartitions" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_subpartition_info(self, subpartition: str) -> list:
        """get subpartition info — reflection (ZK device)"""
        self._ensure()
        if "get_subpartition_info" == "list_tables": return ["users","attendance"]
        if "get_subpartition_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_tablespaces(self, ) -> list:
        """list tablespaces — reflection (ZK device)"""
        self._ensure()
        if "list_tablespaces" == "list_tables": return ["users","attendance"]
        if "list_tablespaces" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_tablespace_info(self, tablespace: str) -> list:
        """get tablespace info — reflection (ZK device)"""
        self._ensure()
        if "get_tablespace_info" == "list_tables": return ["users","attendance"]
        if "get_tablespace_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_datafiles(self, ) -> list:
        """list datafiles — reflection (ZK device)"""
        self._ensure()
        if "list_datafiles" == "list_tables": return ["users","attendance"]
        if "list_datafiles" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_datafile_info(self, datafile: str) -> list:
        """get datafile info — reflection (ZK device)"""
        self._ensure()
        if "get_datafile_info" == "list_tables": return ["users","attendance"]
        if "get_datafile_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_clusters(self, ) -> list:
        """list clusters — reflection (ZK device)"""
        self._ensure()
        if "list_clusters" == "list_tables": return ["users","attendance"]
        if "list_clusters" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_db_links(self, ) -> list:
        """list db links — reflection (ZK device)"""
        self._ensure()
        if "list_db_links" == "list_tables": return ["users","attendance"]
        if "list_db_links" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_db_link_info(self, db_link: str) -> list:
        """get db link info — reflection (ZK device)"""
        self._ensure()
        if "get_db_link_info" == "list_tables": return ["users","attendance"]
        if "get_db_link_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_jobs(self, ) -> list:
        """list jobs — reflection (ZK device)"""
        self._ensure()
        if "list_jobs" == "list_tables": return ["users","attendance"]
        if "list_jobs" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_job_info(self, job: str) -> list:
        """get job info — reflection (ZK device)"""
        self._ensure()
        if "get_job_info" == "list_tables": return ["users","attendance"]
        if "get_job_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_queues(self, ) -> list:
        """list queues — reflection (ZK device)"""
        self._ensure()
        if "list_queues" == "list_tables": return ["users","attendance"]
        if "list_queues" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_queue_info(self, queue: str) -> list:
        """get queue info — reflection (ZK device)"""
        self._ensure()
        if "get_queue_info" == "list_tables": return ["users","attendance"]
        if "get_queue_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_mviews(self, schema: str = None) -> list:
        """list mviews — reflection (ZK device)"""
        self._ensure()
        if "list_mviews" == "list_tables": return ["users","attendance"]
        if "list_mviews" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_mview_info(self, mview: str) -> list:
        """get mview info — reflection (ZK device)"""
        self._ensure()
        if "get_mview_info" == "list_tables": return ["users","attendance"]
        if "get_mview_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_dependencies(self, object_name: str) -> list:
        """list dependencies — reflection (ZK device)"""
        self._ensure()
        if "list_dependencies" == "list_tables": return ["users","attendance"]
        if "list_dependencies" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_dependency_info(self, object_name: str) -> list:
        """get dependency info — reflection (ZK device)"""
        self._ensure()
        if "get_dependency_info" == "list_tables": return ["users","attendance"]
        if "get_dependency_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_invalid_objects(self, schema: str = None) -> list:
        """list invalid objects — reflection (ZK device)"""
        self._ensure()
        if "list_invalid_objects" == "list_tables": return ["users","attendance"]
        if "list_invalid_objects" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_object_info(self, object_name: str) -> list:
        """get object info — reflection (ZK device)"""
        self._ensure()
        if "get_object_info" == "list_tables": return ["users","attendance"]
        if "get_object_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_object_type(self, object_name: str) -> list:
        """get object type — reflection (ZK device)"""
        self._ensure()
        if "get_object_type" == "list_tables": return ["users","attendance"]
        if "get_object_type" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_objects_by_type(self, object_type: str) -> list:
        """list objects by type — reflection (ZK device)"""
        self._ensure()
        if "list_objects_by_type" == "list_tables": return ["users","attendance"]
        if "list_objects_by_type" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_ddl(self, object_type: str, object_name: str) -> list:
        """get ddl — reflection (ZK device)"""
        self._ensure()
        if "get_ddl" == "list_tables": return ["users","attendance"]
        if "get_ddl" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_create_statement(self, table: str) -> list:
        """get create statement — reflection (ZK device)"""
        self._ensure()
        if "get_create_statement" == "list_tables": return ["users","attendance"]
        if "get_create_statement" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def describe_table(self, table: str) -> list:
        """describe table — reflection (ZK device)"""
        self._ensure()
        if "describe_table" == "list_tables": return ["users","attendance"]
        if "describe_table" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def describe_view(self, view: str) -> list:
        """describe view — reflection (ZK device)"""
        self._ensure()
        if "describe_view" == "list_tables": return ["users","attendance"]
        if "describe_view" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_column_privileges(self, table: str = None) -> list:
        """list column privileges — reflection (ZK device)"""
        self._ensure()
        if "list_column_privileges" == "list_tables": return ["users","attendance"]
        if "list_column_privileges" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_table_comment(self, table: str) -> list:
        """get table comment — reflection (ZK device)"""
        self._ensure()
        if "get_table_comment" == "list_tables": return ["users","attendance"]
        if "get_table_comment" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_view_definition(self, view: str) -> list:
        """get view definition — reflection (ZK device)"""
        self._ensure()
        if "get_view_definition" == "list_tables": return ["users","attendance"]
        if "get_view_definition" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_view_columns(self, view: str) -> list:
        """list view columns — reflection (ZK device)"""
        self._ensure()
        if "list_view_columns" == "list_tables": return ["users","attendance"]
        if "list_view_columns" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_sequence_next_value(self, sequence: str) -> list:
        """get sequence next value — reflection (ZK device)"""
        self._ensure()
        if "get_sequence_next_value" == "list_tables": return ["users","attendance"]
        if "get_sequence_next_value" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_enum_types(self, schema: str = None) -> list:
        """list enum types — reflection (ZK device)"""
        self._ensure()
        if "list_enum_types" == "list_tables": return ["users","attendance"]
        if "list_enum_types" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_enum_values(self, enum_name: str) -> list:
        """get enum values — reflection (ZK device)"""
        self._ensure()
        if "get_enum_values" == "list_tables": return ["users","attendance"]
        if "get_enum_values" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_collations(self, ) -> list:
        """list collations — reflection (ZK device)"""
        self._ensure()
        if "list_collations" == "list_tables": return ["users","attendance"]
        if "list_collations" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_collation_info(self, collation: str) -> list:
        """get collation info — reflection (ZK device)"""
        self._ensure()
        if "get_collation_info" == "list_tables": return ["users","attendance"]
        if "get_collation_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_operators(self, ) -> list:
        """list operators — reflection (ZK device)"""
        self._ensure()
        if "list_operators" == "list_tables": return ["users","attendance"]
        if "list_operators" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def get_operator_info(self, operator: str) -> list:
        """get operator info — reflection (ZK device)"""
        self._ensure()
        if "get_operator_info" == "list_tables": return ["users","attendance"]
        if "get_operator_info" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def list_casts(self, ) -> list:
        """list casts — reflection (ZK device)"""
        self._ensure()
        if "list_casts" == "list_tables": return ["users","attendance"]
        if "list_casts" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

    def reflect_all(self, schema: str = None) -> list:
        """comprehensive reflection — reflection (ZK device)"""
        self._ensure()
        if "reflect_all" == "list_tables": return ["users","attendance"]
        if "reflect_all" == "get_database_info": return [{"ip": self.ip, "port": self.port}]
        return []

