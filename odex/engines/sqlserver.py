"""
SQL Server Engine — 35 DYNAMIC data functions

Dynamic table/column, pyodbc with ? placeholders, [quoted] identifiers, OFFSET/FETCH, OUTPUT INSERTED.
"""
from typing import Any, Dict, List, Optional, Tuple
try:
    import pyodbc
except ImportError:
    pyodbc = None

def _q(ident: str) -> str:
    return ".".join(f"[{p.replace(']', ']]')}]" for p in ident.split("."))

def _where(filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
    if not filters: return "", []
    return " WHERE " + " AND ".join(f"{_q(k)}=?" for k in filters), list(filters.values())

class SqlServerEngine:
    def __init__(self, conn_str: str): self.conn_str, self.conn = conn_str, None
    def connect(self):
        if pyodbc is None: raise RuntimeError("pip install pyodbc")
        self.conn=pyodbc.connect(self.conn_str); return self.conn
    def disconnect(self):
        if self.conn: self.conn.close(); self.conn=None
    def _exec(self, sql: str, params: Tuple=(), commit=False):
        cur=self.conn.cursor(); cur.execute(sql, params)
        if commit: self.conn.commit()
        return cur

    def create(self, table: str, data: Dict[str, Any]) -> int:
        cols=", ".join(_q(k) for k in data); cur=self._exec(f"INSERT INTO {_q(table)} ({cols}) VALUES ({', '.join('?' for _ in data)})", tuple(data.values()), True); return cur.rowcount
    def bulk_create(self, table: str, rows: List[Dict[str, Any]]) -> int:
        if not rows: return 0
        cols=list(rows[0].keys()); colsql=", ".join(_q(c) for c in cols); ph=", ".join("?" for _ in cols); cur=self.conn.cursor()
        try:
            cur.executemany(f"INSERT INTO {_q(table)} ({colsql}) VALUES ({ph})", [tuple(r[c] for c in cols) for r in rows]); self.conn.commit(); return cur.rowcount
        finally:
            try:
                cur.close()
            except Exception:
                pass
    def get_by_id(self, table: str, pk_value: Any, pk: str="id") -> Optional[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WHERE {_q(pk)}=?", (pk_value,)); row=cur.fetchone(); return dict(zip([d[0] for d in cur.description], row)) if row else None
    def get_one(self, table: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        where, params=_where(filters); cur=self._exec(f"SELECT TOP 1 * FROM {_q(table)}{where}", tuple(params)); row=cur.fetchone(); return dict(zip([d[0] for d in cur.description], row)) if row else None
    def list_all(self, table: str, limit: int=100, offset: int=0, order_by: str="id") -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} ORDER BY {_q(order_by)} OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", (offset, limit)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def filter(self, table: str, filters: Dict[str, Any], limit: int=100, offset: int=0, order_by: str="id") -> List[Dict[str, Any]]:
        where, params=_where(filters); cur=self._exec(f"SELECT * FROM {_q(table)}{where} ORDER BY {_q(order_by)} OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", tuple(params)+(offset, limit)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def search(self, table: str, field: str, pattern: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT TOP {limit} * FROM {_q(table)} WHERE {_q(field)} LIKE ?", (f"%{pattern}%",)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def count(self, table: str, filters: Dict[str, Any]=None) -> int:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT COUNT(*) FROM {_q(table)}{where}", tuple(params)); return cur.fetchone()[0]
    def exists(self, table: str, filters: Dict[str, Any]) -> bool: return self.count(table, filters)>0
    def update_by_id(self, table: str, pk_value: Any, data: Dict[str, Any], pk: str="id") -> int:
        sets=", ".join(f"{_q(k)}=?" for k in data); cur=self._exec(f"UPDATE {_q(table)} SET {sets} WHERE {_q(pk)}=?", tuple(data.values())+(pk_value,), True); return cur.rowcount
    def update_where(self, table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> int:
        sets=", ".join(f"{_q(k)}=?" for k in data); where, wparams=_where(filters); cur=self._exec(f"UPDATE {_q(table)} SET {sets}{where}", tuple(data.values())+tuple(wparams), True); return cur.rowcount
    def upsert(self, table: str, data: Dict[str, Any], unique_field: str) -> int:
        # MERGE
        cols=list(data.keys()); sets=", ".join(f"target.{_q(c)}=source.{_q(c)}" for c in cols if c!=unique_field); vals=", ".join(f"source.{_q(c)}" for c in cols); col_list=", ".join(_q(c) for c in cols); src_vals=", ".join("?" for _ in cols)
        sql=f"MERGE {_q(table)} AS target USING (SELECT {', '.join(f'? AS {_q(c)}' for c in cols)}) AS source ({col_list}) ON target.{_q(unique_field)}=source.{_q(unique_field)} WHEN MATCHED THEN UPDATE SET {sets} WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({vals});"
        cur=self._exec(sql, tuple(data.values())*2, True) if sets else self._exec(f"MERGE {_q(table)} AS t USING (SELECT ? AS {_q(unique_field)}) AS s ON t.{_q(unique_field)}=s.{_q(unique_field)} WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({', '.join('?' for _ in cols)});", tuple(data.values()), True); return cur.rowcount
    def increment(self, table: str, pk_value: Any, field: str, delta: float=1, pk: str="id") -> int:
        cur=self._exec(f"UPDATE {_q(table)} SET {_q(field)}={_q(field)}+? WHERE {_q(pk)}=?", (delta, pk_value), True); return cur.rowcount
    def delete_by_id(self, table: str, pk_value: Any, pk: str="id") -> int:
        cur=self._exec(f"DELETE FROM {_q(table)} WHERE {_q(pk)}=?", (pk_value,), True); return cur.rowcount
    def delete_where(self, table: str, filters: Dict[str, Any]) -> int:
        where, params=_where(filters); cur=self._exec(f"DELETE FROM {_q(table)}{where}", tuple(params), True); return cur.rowcount
    def truncate(self, table: str) -> int:
        cur=self._exec(f"TRUNCATE TABLE {_q(table)}", (), True); return cur.rowcount
    def distinct(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT DISTINCT {_q(field)} FROM {_q(table)}{where}", tuple(params)); return [r[0] for r in cur.fetchall()]
    def aggregate(self, table: str, func: str, field: str, filters: Dict[str, Any]=None) -> Any:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {func.upper()}({_q(field)}) FROM {_q(table)}{where}", tuple(params)); return cur.fetchone()[0]
    def sum_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "SUM", field, filters)
    def avg_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "AVG", field, filters)
    def min_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "MIN", field, filters)
    def max_field(self, table: str, field: str, filters: Dict[str, Any]=None) -> Any: return self.aggregate(table, "MAX", field, filters)
    def group_by(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {_q(field)}, COUNT(*) as cnt FROM {_q(table)}{where} GROUP BY {_q(field)}", tuple(params)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def join_select(self, base_table: str, join_table: str, on: str, filters: Dict[str, Any]=None, limit: int=100) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT TOP {limit} * FROM {_q(base_table)} JOIN {_q(join_table)} ON {on}{where}", tuple(params)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def raw_query(self, sql: str, params: Tuple=()) -> List[Dict[str, Any]]:
        cur=self._exec(sql, params); cols=[d[0] for d in cur.description] if cur.description else []; return [dict(zip(cols,r)) for r in cur.fetchall()] if cols else []
    def raw_execute(self, sql: str, params: Tuple=()) -> int:
        cur=self._exec(sql, params, True); return cur.rowcount
    def paginate(self, table: str, page: int=1, page_size: int=20, filters: Dict[str, Any]=None, order_by: str="id") -> Dict[str, Any]:
        total=self.count(table, filters); offset=(page-1)*page_size; rows=self.filter(table, filters or {}, page_size, offset, order_by); return {"total": total, "page": page, "page_size": page_size, "rows": rows}
    def bulk_update(self, table: str, rows: List[Dict[str, Any]], pk: str="id") -> int:
        total=0
        for r in rows: pk_val=r.pop(pk); total+=self.update_by_id(table, pk_val, r, pk); r[pk]=pk_val
        return total
    def bulk_delete(self, table: str, ids: List[Any], pk: str="id") -> int:
        if not ids: return 0
        ph=", ".join("?" for _ in ids); cur=self._exec(f"DELETE FROM {_q(table)} WHERE {_q(pk)} IN ({ph})", tuple(ids), True); return cur.rowcount
    def copy_row(self, table: str, pk_value: Any, overrides: Dict[str, Any]=None, pk: str="id") -> int:
        row=self.get_by_id(table, pk_value, pk)
        if not row: return 0
        row.pop(pk, None); 
        if overrides: row.update(overrides)
        return self.create(table, row)
    def clone_table(self, new_table: str, source_table: str) -> int:
        cur=self._exec(f"SELECT * INTO {_q(new_table)} FROM {_q(source_table)} WHERE 1=0", (), True); cur=self._exec(f"INSERT INTO {_q(new_table)} SELECT * FROM {_q(source_table)}", (), True); return cur.rowcount
    def rename_column(self, table: str, old: str, new: str) -> int:
        cur=self._exec(f"EXEC sp_rename '{table}.{old}', '{new}', 'COLUMN'", (), True); return cur.rowcount
    def add_column(self, table: str, column_def: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} ADD {column_def}", (), True); return cur.rowcount
    def drop_column(self, table: str, column: str) -> int:
        cur=self._exec(f"ALTER TABLE {_q(table)} DROP COLUMN {_q(column)}", (), True); return cur.rowcount
    def get_schema(self, table: str) -> List[Dict[str, Any]]:
        cur=self._exec("SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=?", (table,)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def bulk_upsert(self, table: str, rows: List[Dict[str, Any]], unique_field: str) -> int: return sum(self.upsert(table, r, unique_field) for r in rows)
    def find_or_create(self, table: str, filters: Dict[str, Any], defaults: Dict[str, Any]=None) -> Dict[str, Any]:
        row=self.get_one(table, filters)
        if row: return row
        data={**filters, **(defaults or {})}; self.create(table, data); return self.get_one(table, filters)
    def first(self, table: str, filters: Dict[str, Any]=None, order_by: str="id") -> Optional[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT TOP 1 * FROM {_q(table)}{where} ORDER BY {_q(order_by)} ASC", tuple(params)); row=cur.fetchone(); return dict(zip([d[0] for d in cur.description], row)) if row else None
    def last(self, table: str, filters: Dict[str, Any]=None, order_by: str="id") -> Optional[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT TOP 1 * FROM {_q(table)}{where} ORDER BY {_q(order_by)} DESC", tuple(params)); row=cur.fetchone(); return dict(zip([d[0] for d in cur.description], row)) if row else None
    def sample(self, table: str, n: int=5, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT TOP {n} * FROM {_q(table)}{where} ORDER BY NEWID()", tuple(params)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def pluck(self, table: str, field: str, filters: Dict[str, Any]=None) -> List[Any]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT {_q(field)} FROM {_q(table)}{where}", tuple(params)); return [r[0] for r in cur.fetchall()]
    def where_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        if not values: return []
        ph=", ".join("?" for _ in values); cur=self._exec(f"SELECT TOP {limit} * FROM {_q(table)} WHERE {_q(field)} IN ({ph})", tuple(values)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def where_not_in(self, table: str, field: str, values: List[Any], limit: int=100) -> List[Dict[str, Any]]:
        if not values: return self.list_all(table, limit)
        ph=", ".join("?" for _ in values); cur=self._exec(f"SELECT TOP {limit} * FROM {_q(table)} WHERE {_q(field)} NOT IN ({ph})", tuple(values)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def where_between(self, table: str, field: str, low: Any, high: Any, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT TOP {limit} * FROM {_q(table)} WHERE {_q(field)} BETWEEN ? AND ?", (low, high)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def where_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT TOP {limit} * FROM {_q(table)} WHERE {_q(field)} IS NULL", ()); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def where_not_null(self, table: str, field: str, limit: int=100) -> List[Dict[str, Any]]:
        cur=self._exec(f"SELECT TOP {limit} * FROM {_q(table)} WHERE {_q(field)} IS NOT NULL", ()); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def order_by_limit(self, table: str, order_by: str, direction: str="ASC", limit: int=100, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SELECT TOP {limit} * FROM {_q(table)}{where} ORDER BY {_q(order_by)} {direction.upper()}", tuple(params)); cols=[d[0] for d in cur.description]; return [dict(zip(cols,r)) for r in cur.fetchall()]
    def lock_for_update(self, table: str, pk_value: Any, pk: str="id") -> Optional[Dict[str, Any]]:
        cur=self._exec(f"SELECT * FROM {_q(table)} WITH (UPDLOCK, ROWLOCK) WHERE {_q(pk)}=?", (pk_value,)); row=cur.fetchone(); return dict(zip([d[0] for d in cur.description], row)) if row else None
    def explain(self, table: str, filters: Dict[str, Any]=None) -> List[Dict[str, Any]]:
        where, params=_where(filters or {}); cur=self._exec(f"SET SHOWPLAN_ALL ON; SELECT * FROM {_q(table)}{where}; SET SHOWPLAN_ALL OFF", tuple(params)); cols=[d[0] for d in cur.description] if cur.description else []; return [dict(zip(cols,r)) for r in cur.fetchall()] if cols else []
    def export_csv(self, table: str, filepath: str, filters: Dict[str, Any]=None) -> int:
        import csv; rows=self.filter(table, filters or {}, limit=100000) if filters else self.list_all(table, limit=100000)
        if not rows: return 0
        with open(filepath, "w", newline="", encoding="utf-8") as f: w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows); return len(rows)

# ── DB REFLECTION (100) ──
    def list_schemas(self, ) -> list:
        """list all schemas — reflection"""
        sql="""SELECT name FROM sys.schemas ORDER BY name"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_tables(self, schema: str = None) -> list:
        """list tables — reflection"""
        sql="""SELECT name FROM sys.tables WHERE schema_id=SCHEMA_ID(COALESCE(:schema, SCHEMA_NAME(schema_id)))"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_views(self, schema: str = None) -> list:
        """list views — reflection"""
        sql="""SELECT name FROM sys.views"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_materialized_views(self, schema: str = None) -> list:
        """list mviews — reflection"""
        sql="""SELECT name FROM sys.views WHERE is_indexed=1"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_tables_with_type(self, table_type: str = 'TABLE') -> list:
        """list tables with type — reflection"""
        sql="""SELECT name FROM sys.tables WHERE type=:table_type"""
        cur=self._exec(sql, (table_type,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_temporary_tables(self, ) -> list:
        """list temp tables — reflection"""
        sql="""SELECT name FROM tempdb.sys.tables"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_system_tables(self, ) -> list:
        """list system tables — reflection"""
        sql="""SELECT name FROM sys.objects WHERE is_ms_shipped=1"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_info(self, table: str, schema: str = None) -> list:
        """get table info — reflection"""
        sql="""SELECT * FROM sys.tables WHERE name=:table"""
        cur=self._exec(sql, (table, schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_columns(self, table: str, schema: str = None) -> list:
        """get columns for table — reflection"""
        sql="""SELECT c.name, t.name as type FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id WHERE object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table, schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_info(self, table: str, column: str, schema: str = None) -> list:
        """get column info — reflection"""
        sql="""SELECT * FROM sys.columns WHERE object_id=OBJECT_ID(:table) AND name=:column"""
        cur=self._exec(sql, (table, column, schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_exists(self, table: str, column: str, schema: str = None) -> list:
        """check column exists — reflection"""
        sql="""SELECT COUNT(*) FROM sys.columns WHERE object_id=OBJECT_ID(:table) AND name=:column"""
        cur=self._exec(sql, (table, column, schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_type(self, table: str, column: str) -> list:
        """get column type — reflection"""
        sql="""SELECT t.name FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id WHERE c.object_id=OBJECT_ID(:table) AND c.name=:column"""
        cur=self._exec(sql, (table, column,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_default(self, table: str, column: str) -> list:
        """get column default — reflection"""
        sql="""SELECT definition FROM sys.default_constraints WHERE parent_object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table, column,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_nullable(self, table: str, column: str) -> list:
        """check nullable — reflection"""
        sql="""SELECT is_nullable FROM sys.columns WHERE object_id=OBJECT_ID(:table) AND name=:column"""
        cur=self._exec(sql, (table, column,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_column_comment(self, table: str, column: str) -> list:
        """get column comment — reflection"""
        sql="""SELECT value FROM sys.extended_properties WHERE major_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table, column,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_primary_keys(self, table: str, schema: str = None) -> list:
        """list pks — reflection"""
        sql="""SELECT name FROM sys.key_constraints WHERE type='PK' AND parent_object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table, schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_primary_key(self, table: str) -> list:
        """get pk — reflection"""
        sql="""SELECT c.name FROM sys.key_constraints k JOIN sys.index_columns ic ON k.unique_index_id=ic.index_id WHERE k.parent_object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_foreign_keys(self, table: str = None) -> list:
        """list fks — reflection"""
        sql="""SELECT name FROM sys.foreign_keys"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_foreign_key(self, table: str, column: str) -> list:
        """get fk — reflection"""
        sql="""SELECT * FROM sys.foreign_keys WHERE name=:column"""
        cur=self._exec(sql, (table, column,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_unique_constraints(self, table: str = None) -> list:
        """list unique constraints — reflection"""
        sql="""SELECT name FROM sys.key_constraints WHERE type='UQ'"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_check_constraints(self, table: str = None) -> list:
        """list check constraints — reflection"""
        sql="""SELECT name FROM sys.check_constraints"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_not_null_constraints(self, table: str = None) -> list:
        """list not null — reflection"""
        sql="""SELECT name FROM sys.columns WHERE is_nullable=0"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_constraint_info(self, constraint: str) -> list:
        """get constraint info — reflection"""
        sql="""SELECT * FROM sys.objects WHERE name=:constraint"""
        cur=self._exec(sql, (constraint,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_indexes(self, table: str = None) -> list:
        """list indexes — reflection"""
        sql="""SELECT name FROM sys.indexes WHERE object_id=OBJECT_ID(COALESCE(:table, name))"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_index_info(self, index: str) -> list:
        """get index info — reflection"""
        sql="""SELECT * FROM sys.indexes WHERE name=:index"""
        cur=self._exec(sql, (index,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_index_columns(self, index: str) -> list:
        """get index columns — reflection"""
        sql="""SELECT c.name FROM sys.index_columns ic JOIN sys.columns c ON ic.column_id=c.column_id WHERE ic.object_id=OBJECT_ID(:index)"""
        cur=self._exec(sql, (index,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_unique_indexes(self, table: str = None) -> list:
        """list unique indexes — reflection"""
        sql="""SELECT name FROM sys.indexes WHERE is_unique=1"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_sequences(self, schema: str = None) -> list:
        """list sequences — reflection"""
        sql="""SELECT name FROM sys.sequences"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_sequence_info(self, sequence: str) -> list:
        """get sequence info — reflection"""
        sql="""SELECT * FROM sys.sequences WHERE name=:sequence"""
        cur=self._exec(sql, (sequence,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_triggers(self, table: str = None) -> list:
        """list triggers — reflection"""
        sql="""SELECT name FROM sys.triggers WHERE parent_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_trigger_info(self, trigger: str) -> list:
        """get trigger info — reflection"""
        sql="""SELECT * FROM sys.triggers WHERE name=:trigger"""
        cur=self._exec(sql, (trigger,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_procedures(self, schema: str = None) -> list:
        """list procedures — reflection"""
        sql="""SELECT name FROM sys.procedures"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_procedure_info(self, procedure: str) -> list:
        """get procedure info — reflection"""
        sql="""SELECT * FROM sys.procedures WHERE name=:procedure"""
        cur=self._exec(sql, (procedure,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_functions(self, schema: str = None) -> list:
        """list functions — reflection"""
        sql="""SELECT name FROM sys.objects WHERE type='FN'"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_function_info(self, function: str) -> list:
        """get function info — reflection"""
        sql="""SELECT * FROM sys.objects WHERE name=:function"""
        cur=self._exec(sql, (function,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_packages(self, schema: str = None) -> list:
        """list packages (oracle) — reflection"""
        sql="""SELECT name FROM sys.schemas"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_package_info(self, package: str) -> list:
        """get package info — reflection"""
        sql="""SELECT * FROM sys.schemas WHERE name=:package"""
        cur=self._exec(sql, (package,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_synonyms(self, schema: str = None) -> list:
        """list synonyms — reflection"""
        sql="""SELECT name FROM sys.synonyms"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_synonym_info(self, synonym: str) -> list:
        """get synonym info — reflection"""
        sql="""SELECT * FROM sys.synonyms WHERE name=:synonym"""
        cur=self._exec(sql, (synonym,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_types(self, schema: str = None) -> list:
        """list types — reflection"""
        sql="""SELECT name FROM sys.types"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_type_info(self, type_name: str) -> list:
        """get type info — reflection"""
        sql="""SELECT * FROM sys.types WHERE name=:type_name"""
        cur=self._exec(sql, (type_name,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_domains(self, schema: str = None) -> list:
        """list domains — reflection"""
        sql="""SELECT name FROM sys.types WHERE is_user_defined=1"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_domain_info(self, domain: str) -> list:
        """get domain info — reflection"""
        sql="""SELECT * FROM sys.types WHERE name=:domain"""
        cur=self._exec(sql, (domain,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_extensions(self, ) -> list:
        """list extensions — reflection"""
        sql="""SELECT * FROM sys.dm_exec_requests"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_extension_info(self, extension: str) -> list:
        """get extension info — reflection"""
        sql="""SELECT * FROM sys.dm_exec_requests WHERE session_id=:extension"""
        cur=self._exec(sql, (extension,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_roles(self, ) -> list:
        """list roles — reflection"""
        sql="""SELECT name FROM sys.database_principals WHERE type='R'"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_role_info(self, role: str) -> list:
        """get role info — reflection"""
        sql="""SELECT * FROM sys.database_principals WHERE name=:role"""
        cur=self._exec(sql, (role,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_users_db(self, ) -> list:
        """list db users — reflection"""
        sql="""SELECT name FROM sys.database_principals WHERE type='S'"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_user_info(self, user: str) -> list:
        """get user info — reflection"""
        sql="""SELECT * FROM sys.database_principals WHERE name=:user"""
        cur=self._exec(sql, (user,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_privileges(self, ) -> list:
        """list privileges — reflection"""
        sql="""SELECT permission_name FROM sys.fn_builtin_permissions(DEFAULT)"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_privileges(self, table: str) -> list:
        """get table privileges — reflection"""
        sql="""SELECT * FROM sys.database_permissions WHERE major_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_grants(self, user: str = None) -> list:
        """list grants — reflection"""
        sql="""SELECT * FROM sys.database_permissions WHERE grantee_principal_id=DATABASE_PRINCIPAL_ID(:user)"""
        cur=self._exec(sql, (user,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_grant_info(self, grantee: str) -> list:
        """get grant info — reflection"""
        sql="""SELECT * FROM sys.database_permissions WHERE grantee_principal_id=DATABASE_PRINCIPAL_ID(:grantee)"""
        cur=self._exec(sql, (grantee,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_database_info(self, ) -> list:
        """get db info — reflection"""
        sql="""SELECT DB_NAME() as name, DATABASEPROPERTYEX(DB_NAME(),'Status') as status"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_database_version(self, ) -> list:
        """get version — reflection"""
        sql="""SELECT @@VERSION"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_database_size(self, ) -> list:
        """get db size — reflection"""
        sql="""SELECT SUM(size)*8*1024 FROM sys.database_files"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_size(self, table: str) -> list:
        """get table size — reflection"""
        sql="""SELECT SUM(reserved_page_count)*8*1024 FROM sys.dm_db_partition_stats WHERE object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_index_size(self, index: str) -> list:
        """get index size — reflection"""
        sql="""SELECT SUM(used_page_count)*8*1024 FROM sys.dm_db_partition_stats WHERE object_id=OBJECT_ID(:index)"""
        cur=self._exec(sql, (index,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_row_count(self, table: str) -> list:
        """get row count — reflection"""
        sql="""SELECT COUNT(*) as cnt FROM sys.tables WHERE name=:table"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_estimated_row_count(self, table: str) -> list:
        """get estimated count — reflection"""
        sql="""SELECT rows FROM sys.partitions WHERE object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_partitions(self, table: str = None) -> list:
        """list partitions — reflection"""
        sql="""SELECT partition_number FROM sys.partitions WHERE object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_partition_info(self, partition: str) -> list:
        """get partition info — reflection"""
        sql="""SELECT * FROM sys.partitions WHERE partition_number=:partition"""
        cur=self._exec(sql, (partition,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_subpartitions(self, table: str = None) -> list:
        """list subpartitions — reflection"""
        sql="""SELECT * FROM sys.partitions"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_subpartition_info(self, subpartition: str) -> list:
        """get subpartition info — reflection"""
        sql="""SELECT * FROM sys.partitions WHERE partition_number=:subpartition"""
        cur=self._exec(sql, (subpartition,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_tablespaces(self, ) -> list:
        """list tablespaces — reflection"""
        sql="""SELECT name FROM sys.filegroups"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_tablespace_info(self, tablespace: str) -> list:
        """get tablespace info — reflection"""
        sql="""SELECT * FROM sys.filegroups WHERE name=:tablespace"""
        cur=self._exec(sql, (tablespace,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_datafiles(self, ) -> list:
        """list datafiles — reflection"""
        sql="""SELECT physical_name FROM sys.database_files"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_datafile_info(self, datafile: str) -> list:
        """get datafile info — reflection"""
        sql="""SELECT * FROM sys.database_files WHERE physical_name=:datafile"""
        cur=self._exec(sql, (datafile,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_clusters(self, ) -> list:
        """list clusters — reflection"""
        sql="""SELECT name FROM sys.clusters"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_db_links(self, ) -> list:
        """list db links — reflection"""
        sql="""SELECT name FROM sys.servers WHERE is_linked=1"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_db_link_info(self, db_link: str) -> list:
        """get db link info — reflection"""
        sql="""SELECT * FROM sys.servers WHERE name=:db_link"""
        cur=self._exec(sql, (db_link,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_jobs(self, ) -> list:
        """list jobs — reflection"""
        sql="""SELECT name FROM msdb.dbo.sysjobs"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_job_info(self, job: str) -> list:
        """get job info — reflection"""
        sql="""SELECT * FROM msdb.dbo.sysjobs WHERE name=:job"""
        cur=self._exec(sql, (job,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_queues(self, ) -> list:
        """list queues — reflection"""
        sql="""SELECT name FROM sys.service_queues"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_queue_info(self, queue: str) -> list:
        """get queue info — reflection"""
        sql="""SELECT * FROM sys.service_queues WHERE name=:queue"""
        cur=self._exec(sql, (queue,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_mviews(self, schema: str = None) -> list:
        """list mviews — reflection"""
        sql="""SELECT name FROM sys.views WHERE is_indexed=1"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_mview_info(self, mview: str) -> list:
        """get mview info — reflection"""
        sql="""SELECT * FROM sys.views WHERE name=:mview"""
        cur=self._exec(sql, (mview,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_dependencies(self, object_name: str) -> list:
        """list dependencies — reflection"""
        sql="""SELECT referenced_entity_name FROM sys.sql_expression_dependencies WHERE referencing_id=OBJECT_ID(:object_name)"""
        cur=self._exec(sql, (object_name,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_dependency_info(self, object_name: str) -> list:
        """get dependency info — reflection"""
        sql="""SELECT * FROM sys.sql_expression_dependencies WHERE referencing_id=OBJECT_ID(:object_name)"""
        cur=self._exec(sql, (object_name,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_invalid_objects(self, schema: str = None) -> list:
        """list invalid objects — reflection"""
        sql="""SELECT name FROM sys.objects WHERE is_ms_shipped=0 AND is_not_trusted=1"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_object_info(self, object_name: str) -> list:
        """get object info — reflection"""
        sql="""SELECT * FROM sys.objects WHERE name=:object_name"""
        cur=self._exec(sql, (object_name,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_object_type(self, object_name: str) -> list:
        """get object type — reflection"""
        sql="""SELECT type_desc FROM sys.objects WHERE name=:object_name"""
        cur=self._exec(sql, (object_name,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_objects_by_type(self, object_type: str) -> list:
        """list objects by type — reflection"""
        sql="""SELECT name FROM sys.objects WHERE type=:object_type"""
        cur=self._exec(sql, (object_type,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_ddl(self, object_type: str, object_name: str) -> list:
        """get ddl — reflection"""
        sql="""SELECT OBJECT_DEFINITION(OBJECT_ID(:object_name))"""
        cur=self._exec(sql, (object_type, object_name,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_create_statement(self, table: str) -> list:
        """get create statement — reflection"""
        sql="""SELECT OBJECT_DEFINITION(OBJECT_ID(:table))"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def describe_table(self, table: str) -> list:
        """describe table — reflection"""
        sql="""SELECT c.name, t.name FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id WHERE object_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def describe_view(self, view: str) -> list:
        """describe view — reflection"""
        sql="""SELECT definition FROM sys.sql_modules WHERE object_id=OBJECT_ID(:view)"""
        cur=self._exec(sql, (view,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_column_privileges(self, table: str = None) -> list:
        """list column privileges — reflection"""
        sql="""SELECT * FROM sys.column_privileges"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_table_comment(self, table: str) -> list:
        """get table comment — reflection"""
        sql="""SELECT value FROM sys.extended_properties WHERE major_id=OBJECT_ID(:table)"""
        cur=self._exec(sql, (table,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_view_definition(self, view: str) -> list:
        """get view definition — reflection"""
        sql="""SELECT definition FROM sys.sql_modules WHERE object_id=OBJECT_ID(:view)"""
        cur=self._exec(sql, (view,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_view_columns(self, view: str) -> list:
        """list view columns — reflection"""
        sql="""SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(:view)"""
        cur=self._exec(sql, (view,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_sequence_next_value(self, sequence: str) -> list:
        """get sequence next value — reflection (fixed: identifier via _q)"""
        sql = f"SELECT NEXT VALUE FOR {_q(sequence)}"
        cur = self._exec(sql)
        try:
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def list_enum_types(self, schema: str = None) -> list:
        """list enum types — reflection"""
        sql="""SELECT name FROM sys.types WHERE is_user_defined=1"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_enum_values(self, enum_name: str) -> list:
        """get enum values — reflection"""
        sql="""SELECT * FROM sys.types WHERE name=:enum_name"""
        cur=self._exec(sql, (enum_name,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_collations(self, ) -> list:
        """list collations — reflection"""
        sql="""SELECT name FROM sys.fn_helpcollations()"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_collation_info(self, collation: str) -> list:
        """get collation info — reflection"""
        sql="""SELECT * FROM sys.fn_helpcollations() WHERE name=:collation"""
        cur=self._exec(sql, (collation,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_operators(self, ) -> list:
        """list operators — reflection"""
        sql="""SELECT name FROM sys.operators"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def get_operator_info(self, operator: str) -> list:
        """get operator info — reflection"""
        sql="""SELECT * FROM sys.operators WHERE name=:operator"""
        cur=self._exec(sql, (operator,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def list_casts(self, ) -> list:
        """list casts — reflection"""
        sql="""SELECT * FROM sys.casts"""
        cur=self._exec(sql, ())
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

    def reflect_all(self, schema: str = None) -> list:
        """comprehensive reflection — reflection"""
        sql="""SELECT name FROM sys.objects"""
        cur=self._exec(sql, (schema,))
        cols=[d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []

