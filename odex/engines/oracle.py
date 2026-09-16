"""
Oracle Engine — 35 DYNAMIC data functions (no static tables/columns)

All functions accept `table` and dynamic `data`/`filters` dicts.
Oracle-specific: placeholders :name, pagination OFFSET/FETCH, RETURNING, quoted identifiers.
"""
from typing import Any, Dict, List, Optional, Tuple
try:
    import oracledb
except ImportError:
    oracledb = None


def _q(ident: str) -> str:
    # quote identifier, handle schema.table
    return ".".join(f'"{p.replace(chr(34), chr(34)*2)}"' for p in ident.split("."))

def _where(filters: Dict[str, Any], start: int = 1) -> Tuple[str, Dict[str, Any]]:
    if not filters:
        return "", {}
    clauses, params = [], {}
    for i, (k, v) in enumerate(filters.items(), start):
        p = f"p{i}"
        clauses.append(f"{_q(k)}=:{p}")
        params[p] = v
    return " WHERE " + " AND ".join(clauses), params


class OracleEngine:
    def __init__(self, dsn: str, user: str, password: str):
        self.dsn, self.user, self.password, self.conn = dsn, user, password, None
    def connect(self):
        if oracledb is None: raise RuntimeError("pip install oracledb")
        self.conn = oracledb.connect(user=self.user, password=self.password, dsn=self.dsn); return self.conn
    def disconnect(self):
        if self.conn: self.conn.close(); self.conn=None
    def _exec(self, sql: str, params: Dict[str, Any]=None, commit=False):
        cur=self.conn.cursor(); cur.execute(sql, params or {})
        if commit: self.conn.commit()
        return cur

    # 1 create one dynamic row
    def create(self, table: str, data: Dict[str, Any]) -> int:
        cols=", ".join(_q(k) for k in data); ph=", ".join(f":{k}" for k in data)
        cur=self._exec(f"INSERT INTO {_q(table)} ({cols}) VALUES ({ph})", data, True); return cur.rowcount
    # 2 bulk create
    def bulk_create(self, table: str, rows: List[Dict[str, Any]]) -> int:
        if not rows: return 0
        cols=list(rows[0].keys()); colsql=", ".join(_q(c) for c in cols); ph=", ".join(f":{c}" for c in cols)
        cur=self.conn.cursor()
        try:
            cur.executemany(f"INSERT INTO {_q(table)} ({colsql}) VALUES ({ph})", rows); self.conn.commit(); return cur.rowcount
        finally:
            try:
                cur.close()
            except Exception:
                pass
    # 3 get by pk
    def get_by_id(self, table: str, pk_value: Any, pk: str="id") -> Optional[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(pk)}=:v", {"v": pk_value}); row=cur.fetchone()
        return dict(zip([d[0].lower() for d in cur.description], row)) if row else None
    # 4 get one by filters
    def get_one(self, table: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        where, params=_where(filters); cur=self._exec(f"SELECT * FROM {_q(table)}{where} FETCH NEXT 1 ROWS ONLY", params); row=cur.fetchone()
        return dict(zip([d[0].lower() for d in cur.description], row)) if row else None
    # 5 list all
    def list_all(self, table: str, limit: int=100, offset: int=0, order_by: str="id") -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} ORDER BY {_q(order_by)} OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY", {"off": offset, "lim": limit}); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 6 filter
    def filter(self, table: str, filters: Dict[str, Any], limit: int=100, offset: int=0, order_by: str="id") -> List[Dict[str, Any]]:
        where, params=_where(filters); params.update({"off": offset, "lim": limit})
        cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY", params); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 7 search LIKE
    def search(self, table: str, field: str, pattern: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} LIKE :pat FETCH NEXT :lim ROWS ONLY", {"pat": f"%{pattern}%", "lim": limit}); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 8 count
    def count(self, table: str, filters: Dict[str, Any]=None) -> int:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT COUNT(*) FROM {_q(table)}{where}", params); return cur.fetchone()[0]
    # 9 exists
    def exists(self, table: str, filters: Dict[str, Any]) -> bool:
        return self.count(table, filters) > 0
    # 10 update by id
    def update_by_id(self, table: str, pk_value: Any, data: Dict[str, Any], pk: str="id") -> int:
        sets=", ".join(f"{_q(k)}=:{k}" for k in data); params=dict(data); params["__pk"]=pk_value
        cur=self._exec(f"UPDATE {_q(table)} SET {sets} WHERE {_q(pk)}=:__pk", params, True); return cur.rowcount
    # 11 update where
    def update_where(self, table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> int:
        sets=", ".join(f"{_q(k)}=:s_{k}" for k in data); where, wparams=_where(filters, start=100)
        params={f"s_{k}": v for k,v in data.items()}; params.update(wparams)
        cur=self._exec(f"UPDATE {_q(table)} SET {sets}{where}", params, True); return cur.rowcount
    # 12 upsert (MERGE)
    def upsert(self, table: str, data: Dict[str, Any], unique_field: str) -> int:
        cols=list(data.keys()); vals=", ".join(f":{c}" for c in cols); sets=", ".join(f"{_q(c)}=:{c}" for c in cols if c!=unique_field)
        sql=f"MERGE INTO {_q(table)} USING DUAL ON ({_q(unique_field)}=:{unique_field}) WHEN MATCHED THEN UPDATE SET {sets} WHEN NOT MATCHED THEN INSERT ({', '.join(_q(c) for c in cols)}) VALUES ({vals})"
        cur=self._exec(sql, data, True); return cur.rowcount
    # 13 increment field
    def increment(self, table: str, pk_value: Any, field: str, delta: float=1, pk: str="id") -> int:
        cur=self._exec(f"UPDATE {_q(table)} SET {_q(field)}={_q(field)}+:d WHERE {_q(pk)}=:pk", {"d": delta, "pk": pk_value}, True); return cur.rowcount
    # 14 delete by id
    def delete_by_id(self, table: str, pk_value: Any, pk: str="id") -> int:
        cur=self._exec(f"DELETE FROM {_q(table)} WHERE {_q(pk)}=:v", {"v": pk_value}, True); return cur.rowcount
    # 15 delete where
    def delete_where(self, table: str, filters: Dict[str, Any]) -> int:
        where, params=_where(filters); cur=self._exec(f"DELETE FROM {_q(table)}{where}", params, True); return cur.rowcount
    # 16 truncate
    def truncate(self, table: str) -> int:
        cur=self._exec(f"TRUNCATE TABLE {_q(table)}"); return cur.rowcount
    # 17 distinct
    def distinct(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT DISTINCT {_q(field)} FROM {_q(table)}{where}", params); return [r[0] for r in cur.fetchall()]
    # 18 aggregate
    def aggregate(self, table: str, func: str, field: str, filters: Dict[str, Any]=None) -> Any:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {func.upper()}({_q(field)}) FROM {_q(table)}{where}", params); return cur.fetchone()[0]
    # 19 sum
    def sum_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "SUM", field, filters)
    # 20 avg
    def avg_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "AVG", field, filters)
    # 21 min
    def min_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "MIN", field, filters)
    # 22 max
    def max_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "MAX", field, filters)
    # 23 group by
    def group_by(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {_q(field)}, COUNT(*) cnt FROM {_q(table)}{where} GROUP BY {_q(field)}", params); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 24 join select
    def join_select(self, base_table: str, join_table: str, on: str, filters: Dict[str, Any]=None, limit: int=100) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); params["lim"]=limit
        cur=self._exec(f"SELECT * FROM {_q(base_table)} JOIN {_q(join_table)} ON {on}{where} FETCH NEXT :lim ROWS ONLY", params); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 25 raw query (select)
    def raw_query(self, sql: str, params: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        cur=self._exec(sql, params or {}); cols=[d[0].lower() for d in cur.description] if cur.description else []; return [dict(zip(cols,r)) for r in cur.fetchall()] if cols else []
    # 26 raw execute (insert/update/delete)
    def raw_execute(self, sql: str, params: Dict[str, Any]=None) -> int:
        cur=self._exec(sql, params or {}, True); return cur.rowcount
    # 27 paginate
    def paginate(self, table: str, page: int=1, page_size: int=20, filters: Dict[str, Any]=None, order_by: str="id") -> Dict[str, Any]:
        where, params=_where(filters or {}); total=self.count(table, filters); offset=(page-1)*page_size
        params.update({"off": offset, "lim": page_size})
        cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY", params); cols=[d[0].lower() for d in cur.description]; rows=[dict(zip(cols,r)) for r in cur.fetchall()]
        return {"total": total, "page": page, "page_size": page_size, "rows": rows}
    # 28 bulk update
    def bulk_update(self, table: str, rows: List[Dict[str, Any]], pk: str="id") -> int:
        total=0
        for r in rows:
            pk_val=r.pop(pk); total+=self.update_by_id(table, pk_val, r, pk); r[pk]=pk_val
        return total
    # 29 bulk delete
    def bulk_delete(self, table: str, ids: List[Any], pk: str="id") -> int:
        if not ids: return 0
        ph=", ".join(f":p{i}" for i in range(len(ids))); params={f"p{i}": v for i,v in enumerate(ids)}
        cur=self._exec(f"DELETE FROM {_q(table)} WHERE {_q(pk)} IN ({ph})", params, True); return cur.rowcount
    # 30 copy row
    def copy_row(self, table: str, pk_value: Any, overrides: Dict[str, Any]=None, pk: str="id") -> int:
        row=self.get_by_id(table, pk_value, pk)
        if not row: return 0
        row.pop(pk, None); row.pop(pk.lower(), None)
        if overrides: row.update(overrides)
        return self.create(table, row)
    # 31 clone table
    def clone_table(self, new_table: str, source_table: str) -> int:
        cur=self._exec(f"CREATE TABLE {_q(new_table)} AS SELECT * FROM {_q(source_table)} WHERE 1=0", commit=True)
        cur=self._exec(f"INSERT INTO {_q(new_table)} SELECT * FROM {_q(source_table)}", commit=True); return cur.rowcount
    # 32 rename column (ALTER TABLE)
    def rename_column(self, table: str, old: str, new: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} RENAME COLUMN {_q(old)} TO {_q(new)}", commit=True); return cur.rowcount
    # 33 add column
    def add_column(self, table: str, column_def: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} ADD {column_def}", commit=True); return cur.rowcount
    # 34 drop column
    def drop_column(self, table: str, column: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} DROP COLUMN {_q(column)}", commit=True); return cur.rowcount
    # 35 get schema (describe)
    def get_schema(self, table: str) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT column_name, data_type, nullable FROM user_tab_columns WHERE table_name=:t", {"t": table.upper()}); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 36 bulk upsert
    def bulk_upsert(self, table: str, rows: List[Dict[str, Any]], unique_field: str) -> int:
        return sum(self.upsert(table, r, unique_field) for r in rows)
    # 37 find or create
    def find_or_create(self, table: str, filters: Dict[str, Any], defaults: Dict[str, Any]=None) -> Dict[str, Any]:
        row=self.get_one(table, filters)
        if row: return row
        data={**filters, **(defaults or {})}; self.create(table, data); return self.get_one(table, filters)
    # 38 first
    def first(self, table: str, filters: Dict[str, Any]=None, order_by: str="id") -> Optional[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} FETCH NEXT 1 ROWS ONLY", params); row=cur.fetchone(); return dict(zip([d[0].lower() for d in cur.description], row)) if row else None
    # 39 last
    def last(self, table: str, filters: Dict[str, Any]=None, order_by: str="id") -> Optional[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} DESC FETCH NEXT 1 ROWS ONLY", params); row=cur.fetchone(); return dict(zip([d[0].lower() for d in cur.description], row)) if row else None
    # 40 sample random
    def sample(self, table: str, n: int=5, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); params["lim"]=n; cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY DBMS_RANDOM.VALUE FETCH NEXT :lim ROWS ONLY", params); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 41 pluck column values
    def pluck(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {_q(field)} FROM {_q(table)}{where}", params); return [r[0] for r in cur.fetchall()]
    # 42 where in
    def where_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        if not values: return []
        ph=", ".join(f":v{i}" for i in range(len(values))); params={f"v{i}": v for i,v in enumerate(values)}; params["lim"]=limit
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} IN ({ph}) FETCH NEXT :lim ROWS ONLY", params); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 43 where not in
    def where_not_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        if not values: return self.list_all(table, limit)
        ph=", ".join(f":v{i}" for i in range(len(values))); params={f"v{i}": v for i,v in enumerate(values)}; params["lim"]=limit
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} NOT IN ({ph}) FETCH NEXT :lim ROWS ONLY", params); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 44 where between
    def where_between(self, table: str, field: str, low: Any, high: Any, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} BETWEEN :low AND :high FETCH NEXT :lim ROWS ONLY", {"low": low, "high": high, "lim": limit}); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 45 where null
    def where_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} IS NULL FETCH NEXT :lim ROWS ONLY", {"lim": limit}); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 46 where not null
    def where_not_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(field)} IS NOT NULL FETCH NEXT :lim ROWS ONLY", {"lim": limit}); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 47 order by limit dynamic
    def order_by_limit(self, table: str, order_by: str, direction: str="ASC", limit: int=100, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); params["lim"]=limit; cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} {direction.upper()} FETCH NEXT :lim ROWS ONLY", params); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    # 48 lock row for update
    def lock_for_update(self, table: str, pk_value: Any, pk: str="id") -> Optional[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(pk)}=:v FOR UPDATE", {"v": pk_value}); row=cur.fetchone(); return dict(zip([d[0].lower() for d in cur.description], row)) if row else None
    # 49 explain plan
    def explain(self, table: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"EXPLAIN PLAN FOR SELECT * FROM {_q(table)}{where}", params); cur=self._exec("SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY)"); cols=[d[0].lower() for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()] if cur.description else []
    # 50 export to csv
    def export_csv(self, table: str, filepath: str, filters: Dict[str, Any]=None) -> int:
        import csv; rows=self.filter(table, filters or {}, limit=100000) if filters else self.list_all(table, limit=100000)
        if not rows: return 0
        with open(filepath, "w", newline="", encoding="utf-8") as f: w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows); return len(rows)

# ── DB REFLECTION (100) ──
    def list_schemas(self, ) -> list:
        """list all schemas — reflection"""
        sql="""SELECT username FROM all_users ORDER BY username"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_tables(self, schema: str = None) -> list:
        """list tables — reflection"""
        sql="""SELECT table_name FROM all_tables WHERE owner=NVL(:schema, owner) ORDER BY table_name"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_views(self, schema: str = None) -> list:
        """list views — reflection"""
        sql="""SELECT view_name FROM all_views WHERE owner=NVL(:schema, owner) ORDER BY view_name"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_materialized_views(self, schema: str = None) -> list:
        """list mviews — reflection"""
        sql="""SELECT mview_name FROM all_mviews WHERE owner=NVL(:schema, owner)"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_tables_with_type(self, table_type: str = 'TABLE') -> list:
        """list tables with type — reflection"""
        sql="""SELECT table_name, tablespace_name FROM all_tables WHERE table_name LIKE :table_type"""
        params={"table_type": table_type}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_temporary_tables(self, ) -> list:
        """list temp tables — reflection"""
        sql="""SELECT table_name FROM all_tables WHERE temporary='Y'"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_system_tables(self, ) -> list:
        """list system tables — reflection"""
        sql="""SELECT table_name FROM all_tables WHERE owner='SYS'"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_info(self, table: str, schema: str = None) -> list:
        """get table info — reflection"""
        sql="""SELECT * FROM all_tables WHERE table_name=:table AND owner=NVL(:schema, owner)"""
        params={"table": table, "schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_columns(self, table: str, schema: str = None) -> list:
        """get columns for table — reflection"""
        sql="""SELECT column_name, data_type FROM all_tab_columns WHERE table_name=:table AND owner=NVL(:schema, owner) ORDER BY column_id"""
        params={"table": table, "schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_info(self, table: str, column: str, schema: str = None) -> list:
        """get column info — reflection"""
        sql="""SELECT * FROM all_tab_columns WHERE table_name=:table AND column_name=:column"""
        params={"table": table, "column": column, "schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_exists(self, table: str, column: str, schema: str = None) -> list:
        """check column exists — reflection"""
        sql="""SELECT COUNT(*) FROM all_tab_columns WHERE table_name=:table AND column_name=:column"""
        params={"table": table, "column": column, "schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_type(self, table: str, column: str) -> list:
        """get column type — reflection"""
        sql="""SELECT data_type FROM all_tab_columns WHERE table_name=:table AND column_name=:column"""
        params={"table": table, "column": column}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_default(self, table: str, column: str) -> list:
        """get column default — reflection"""
        sql="""SELECT data_default FROM all_tab_columns WHERE table_name=:table AND column_name=:column"""
        params={"table": table, "column": column}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_nullable(self, table: str, column: str) -> list:
        """check nullable — reflection"""
        sql="""SELECT nullable FROM all_tab_columns WHERE table_name=:table AND column_name=:column"""
        params={"table": table, "column": column}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_comment(self, table: str, column: str) -> list:
        """get column comment — reflection"""
        sql="""SELECT comments FROM all_col_comments WHERE table_name=:table AND column_name=:column"""
        params={"table": table, "column": column}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_primary_keys(self, table: str, schema: str = None) -> list:
        """list pks — reflection"""
        sql="""SELECT constraint_name FROM all_constraints WHERE table_name=:table AND constraint_type='P'"""
        params={"table": table, "schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_primary_key(self, table: str) -> list:
        """get pk — reflection"""
        sql="""SELECT cols.column_name FROM all_constraints c JOIN all_cons_columns cols ON c.constraint_name=cols.constraint_name WHERE c.table_name=:table AND c.constraint_type='P'"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_foreign_keys(self, table: str = None) -> list:
        """list fks — reflection"""
        sql="""SELECT constraint_name FROM all_constraints WHERE constraint_type='R'"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_foreign_key(self, table: str, column: str) -> list:
        """get fk — reflection"""
        sql="""SELECT * FROM all_constraints WHERE table_name=:table AND constraint_type='R' AND search_condition LIKE :column"""
        params={"table": table, "column": column}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_unique_constraints(self, table: str = None) -> list:
        """list unique constraints — reflection"""
        sql="""SELECT constraint_name FROM all_constraints WHERE constraint_type='U'"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_check_constraints(self, table: str = None) -> list:
        """list check constraints — reflection"""
        sql="""SELECT constraint_name FROM all_constraints WHERE constraint_type='C'"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_not_null_constraints(self, table: str = None) -> list:
        """list not null — reflection"""
        sql="""SELECT constraint_name FROM all_constraints WHERE constraint_type='C' AND search_condition LIKE '%NOT NULL%'"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_constraint_info(self, constraint: str) -> list:
        """get constraint info — reflection"""
        sql="""SELECT * FROM all_constraints WHERE constraint_name=:constraint"""
        params={"constraint": constraint}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_indexes(self, table: str = None) -> list:
        """list indexes — reflection"""
        sql="""SELECT index_name FROM all_indexes WHERE table_name=NVL(:table, table_name)"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_index_info(self, index: str) -> list:
        """get index info — reflection"""
        sql="""SELECT * FROM all_indexes WHERE index_name=:index"""
        params={"index": index}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_index_columns(self, index: str) -> list:
        """get index columns — reflection"""
        sql="""SELECT column_name FROM all_ind_columns WHERE index_name=:index ORDER BY column_position"""
        params={"index": index}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_unique_indexes(self, table: str = None) -> list:
        """list unique indexes — reflection"""
        sql="""SELECT index_name FROM all_indexes WHERE uniqueness='UNIQUE'"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_sequences(self, schema: str = None) -> list:
        """list sequences — reflection"""
        sql="""SELECT sequence_name FROM all_sequences WHERE sequence_owner=NVL(:schema, sequence_owner)"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_sequence_info(self, sequence: str) -> list:
        """get sequence info — reflection"""
        sql="""SELECT * FROM all_sequences WHERE sequence_name=:sequence"""
        params={"sequence": sequence}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_triggers(self, table: str = None) -> list:
        """list triggers — reflection"""
        sql="""SELECT trigger_name FROM all_triggers WHERE table_name=NVL(:table, table_name)"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_trigger_info(self, trigger: str) -> list:
        """get trigger info — reflection"""
        sql="""SELECT * FROM all_triggers WHERE trigger_name=:trigger"""
        params={"trigger": trigger}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_procedures(self, schema: str = None) -> list:
        """list procedures — reflection"""
        sql="""SELECT object_name FROM all_objects WHERE object_type='PROCEDURE' AND owner=NVL(:schema, owner)"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_procedure_info(self, procedure: str) -> list:
        """get procedure info — reflection"""
        sql="""SELECT * FROM all_objects WHERE object_name=:procedure AND object_type='PROCEDURE'"""
        params={"procedure": procedure}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_functions(self, schema: str = None) -> list:
        """list functions — reflection"""
        sql="""SELECT object_name FROM all_objects WHERE object_type='FUNCTION'"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_function_info(self, function: str) -> list:
        """get function info — reflection"""
        sql="""SELECT * FROM all_objects WHERE object_name=:function AND object_type='FUNCTION'"""
        params={"function": function}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_packages(self, schema: str = None) -> list:
        """list packages (oracle) — reflection"""
        sql="""SELECT object_name FROM all_objects WHERE object_type='PACKAGE'"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_package_info(self, package: str) -> list:
        """get package info — reflection"""
        sql="""SELECT * FROM all_objects WHERE object_name=:package AND object_type='PACKAGE'"""
        params={"package": package}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_synonyms(self, schema: str = None) -> list:
        """list synonyms — reflection"""
        sql="""SELECT synonym_name FROM all_synonyms"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_synonym_info(self, synonym: str) -> list:
        """get synonym info — reflection"""
        sql="""SELECT * FROM all_synonyms WHERE synonym_name=:synonym"""
        params={"synonym": synonym}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_types(self, schema: str = None) -> list:
        """list types — reflection"""
        sql="""SELECT type_name FROM all_types"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_type_info(self, type_name: str) -> list:
        """get type info — reflection"""
        sql="""SELECT * FROM all_types WHERE type_name=:type_name"""
        params={"type_name": type_name}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_domains(self, schema: str = None) -> list:
        """list domains — reflection"""
        sql="""SELECT domain_name FROM all_domains"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_domain_info(self, domain: str) -> list:
        """get domain info — reflection"""
        sql="""SELECT * FROM all_domains WHERE domain_name=:domain"""
        params={"domain": domain}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_extensions(self, ) -> list:
        """list extensions — reflection"""
        sql="""SELECT * FROM all_extensions"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_extension_info(self, extension: str) -> list:
        """get extension info — reflection"""
        sql="""SELECT * FROM all_extensions WHERE extension_name=:extension"""
        params={"extension": extension}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_roles(self, ) -> list:
        """list roles — reflection"""
        sql="""SELECT role FROM dba_roles"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_role_info(self, role: str) -> list:
        """get role info — reflection"""
        sql="""SELECT * FROM dba_roles WHERE role=:role"""
        params={"role": role}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_users_db(self, ) -> list:
        """list db users — reflection"""
        sql="""SELECT username FROM all_users"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_user_info(self, user: str) -> list:
        """get user info — reflection"""
        sql="""SELECT * FROM all_users WHERE username=:user"""
        params={"user": user}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_privileges(self, ) -> list:
        """list privileges — reflection"""
        sql="""SELECT privilege FROM dba_sys_privs"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_privileges(self, table: str) -> list:
        """get table privileges — reflection"""
        sql="""SELECT * FROM all_tab_privs WHERE table_name=:table"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_grants(self, user: str = None) -> list:
        """list grants — reflection"""
        sql="""SELECT * FROM dba_role_privs WHERE grantee=NVL(:user, grantee)"""
        params={"user": user}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_grant_info(self, grantee: str) -> list:
        """get grant info — reflection"""
        sql="""SELECT * FROM dba_role_privs WHERE grantee=:grantee"""
        params={"grantee": grantee}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_database_info(self, ) -> list:
        """get db info — reflection"""
        sql="""SELECT name, open_mode FROM v$database"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_database_version(self, ) -> list:
        """get version — reflection"""
        sql="""SELECT banner FROM v$version WHERE rownum=1"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_database_size(self, ) -> list:
        """get db size — reflection"""
        sql="""SELECT SUM(bytes) FROM dba_data_files"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_size(self, table: str) -> list:
        """get table size — reflection"""
        sql="""SELECT bytes FROM dba_segments WHERE segment_name=:table"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_index_size(self, index: str) -> list:
        """get index size — reflection"""
        sql="""SELECT bytes FROM dba_segments WHERE segment_name=:index"""
        params={"index": index}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_row_count(self, table: str) -> list:
        """get row count — reflection"""
        sql="""SELECT COUNT(*) as cnt FROM all_tables WHERE table_name=:table"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_estimated_row_count(self, table: str) -> list:
        """get estimated count — reflection"""
        sql="""SELECT num_rows FROM all_tables WHERE table_name=:table"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_partitions(self, table: str = None) -> list:
        """list partitions — reflection"""
        sql="""SELECT partition_name FROM all_tab_partitions WHERE table_name=NVL(:table, table_name)"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_partition_info(self, partition: str) -> list:
        """get partition info — reflection"""
        sql="""SELECT * FROM all_tab_partitions WHERE partition_name=:partition"""
        params={"partition": partition}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_subpartitions(self, table: str = None) -> list:
        """list subpartitions — reflection"""
        sql="""SELECT subpartition_name FROM all_tab_subpartitions"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_subpartition_info(self, subpartition: str) -> list:
        """get subpartition info — reflection"""
        sql="""SELECT * FROM all_tab_subpartitions WHERE subpartition_name=:subpartition"""
        params={"subpartition": subpartition}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_tablespaces(self, ) -> list:
        """list tablespaces — reflection"""
        sql="""SELECT tablespace_name FROM dba_tablespaces"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_tablespace_info(self, tablespace: str) -> list:
        """get tablespace info — reflection"""
        sql="""SELECT * FROM dba_tablespaces WHERE tablespace_name=:tablespace"""
        params={"tablespace": tablespace}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_datafiles(self, ) -> list:
        """list datafiles — reflection"""
        sql="""SELECT file_name FROM dba_data_files"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_datafile_info(self, datafile: str) -> list:
        """get datafile info — reflection"""
        sql="""SELECT * FROM dba_data_files WHERE file_name=:datafile"""
        params={"datafile": datafile}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_clusters(self, ) -> list:
        """list clusters — reflection"""
        sql="""SELECT cluster_name FROM all_clusters"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_db_links(self, ) -> list:
        """list db links — reflection"""
        sql="""SELECT db_link FROM all_db_links"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_db_link_info(self, db_link: str) -> list:
        """get db link info — reflection"""
        sql="""SELECT * FROM all_db_links WHERE db_link=:db_link"""
        params={"db_link": db_link}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_jobs(self, ) -> list:
        """list jobs — reflection"""
        sql="""SELECT job FROM dba_jobs"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_job_info(self, job: str) -> list:
        """get job info — reflection"""
        sql="""SELECT * FROM dba_jobs WHERE job=:job"""
        params={"job": job}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_queues(self, ) -> list:
        """list queues — reflection"""
        sql="""SELECT name FROM all_queues"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_queue_info(self, queue: str) -> list:
        """get queue info — reflection"""
        sql="""SELECT * FROM all_queues WHERE name=:queue"""
        params={"queue": queue}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_mviews(self, schema: str = None) -> list:
        """list mviews — reflection"""
        sql="""SELECT mview_name FROM all_mviews"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_mview_info(self, mview: str) -> list:
        """get mview info — reflection"""
        sql="""SELECT * FROM all_mviews WHERE mview_name=:mview"""
        params={"mview": mview}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_dependencies(self, object_name: str) -> list:
        """list dependencies — reflection"""
        sql="""SELECT referenced_name FROM all_dependencies WHERE name=:object_name"""
        params={"object_name": object_name}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_dependency_info(self, object_name: str) -> list:
        """get dependency info — reflection"""
        sql="""SELECT * FROM all_dependencies WHERE name=:object_name"""
        params={"object_name": object_name}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_invalid_objects(self, schema: str = None) -> list:
        """list invalid objects — reflection"""
        sql="""SELECT object_name FROM all_objects WHERE status='INVALID'"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_object_info(self, object_name: str) -> list:
        """get object info — reflection"""
        sql="""SELECT * FROM all_objects WHERE object_name=:object_name"""
        params={"object_name": object_name}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_object_type(self, object_name: str) -> list:
        """get object type — reflection"""
        sql="""SELECT object_type FROM all_objects WHERE object_name=:object_name"""
        params={"object_name": object_name}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_objects_by_type(self, object_type: str) -> list:
        """list objects by type — reflection"""
        sql="""SELECT object_name FROM all_objects WHERE object_type=:object_type"""
        params={"object_type": object_type}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_ddl(self, object_type: str, object_name: str) -> list:
        """get ddl — reflection"""
        sql="""SELECT DBMS_METADATA.GET_DDL(:object_type, :object_name) FROM dual"""
        params={"object_type": object_type, "object_name": object_name}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_create_statement(self, table: str) -> list:
        """get create statement — reflection"""
        sql="""SELECT DBMS_METADATA.GET_DDL('TABLE', :table) FROM dual"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def describe_table(self, table: str) -> list:
        """describe table — reflection"""
        sql="""SELECT column_name, data_type FROM all_tab_columns WHERE table_name=:table"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def describe_view(self, view: str) -> list:
        """describe view — reflection"""
        sql="""SELECT text FROM all_views WHERE view_name=:view"""
        params={"view": view}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_column_privileges(self, table: str = None) -> list:
        """list column privileges — reflection"""
        sql="""SELECT * FROM all_col_privs"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_comment(self, table: str) -> list:
        """get table comment — reflection"""
        sql="""SELECT comments FROM all_tab_comments WHERE table_name=:table"""
        params={"table": table}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_view_definition(self, view: str) -> list:
        """get view definition — reflection"""
        sql="""SELECT text FROM all_views WHERE view_name=:view"""
        params={"view": view}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_view_columns(self, view: str) -> list:
        """list view columns — reflection"""
        sql="""SELECT column_name FROM all_tab_columns WHERE table_name=:view"""
        params={"view": view}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_sequence_next_value(self, sequence: str) -> list:
        """get sequence next value — reflection (fixed: identifier via _q, not bind)"""
        sql = f"SELECT {_q(sequence)}.NEXTVAL FROM dual"
        cur = self._exec(sql)
        try:
            cols = [d[0].lower() for d in cur.description] if cur.description else []
            return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def list_enum_types(self, schema: str = None) -> list:
        """list enum types — reflection"""
        sql="""SELECT type_name FROM all_types WHERE typecode='ENUM'"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_enum_values(self, enum_name: str) -> list:
        """get enum values — reflection"""
        sql="""SELECT * FROM all_types WHERE type_name=:enum_name"""
        params={"enum_name": enum_name}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_collations(self, ) -> list:
        """list collations — reflection"""
        sql="""SELECT collation FROM v$nls_valid_values WHERE parameter='COLLATION'"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_collation_info(self, collation: str) -> list:
        """get collation info — reflection"""
        sql="""SELECT * FROM v$nls_valid_values WHERE value=:collation"""
        params={"collation": collation}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_operators(self, ) -> list:
        """list operators — reflection"""
        sql="""SELECT operator_name FROM all_operators"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_operator_info(self, operator: str) -> list:
        """get operator info — reflection"""
        sql="""SELECT * FROM all_operators WHERE operator_name=:operator"""
        params={"operator": operator}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_casts(self, ) -> list:
        """list casts — reflection"""
        sql="""SELECT * FROM all_casts"""
        cur=self._exec(sql)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def reflect_all(self, schema: str = None) -> list:
        """comprehensive reflection — reflection"""
        sql="""SELECT table_name FROM all_tables WHERE owner=NVL(:schema, owner)"""
        params={"schema": schema}
        cur=self._exec(sql, params)
        cols=[d[0].lower() for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

