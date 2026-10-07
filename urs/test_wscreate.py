"""Workspace full-provision wizard tests: schema naming, apps-zip staging,
db_init.migrate_schema seams, create/migrate endpoints, modal markup.

No live DB: sqlite test DB for ORM rows, fakes for psycopg2/Django migrate.
"""
import io
import json
import pathlib
import sys
import tempfile
import zipfile
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, TestCase

from urs import views as _v


def _make_apps_zip(extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("apps/hr/metadata.json", '{"name": "hr", "ar": "الموارد"}')
        z.writestr("apps/hr/hr.fmlk", "<form/>")
        z.writestr("apps/settings/modals/x.html", "<b>must be ignored</b>")
        z.writestr("apps/../evil.txt", "slip-root")
        z.writestr("apps/hr/../../evil2.txt", "slip-deep")
        for name, body in (extra or []):
            z.writestr(name, body)
    buf.seek(0)
    return buf.read()


def _upload_file(raw, name="apps.zip"):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, raw, content_type="application/zip")


class WsSchemaNameTests(SimpleTestCase):
    def test_company_branch_year(self):
        from urs.models import build_schema_name
        self.assertEqual(build_schema_name("CMP", "MAIN", 2026), "cmp_main_2026")
        self.assertEqual(build_schema_name("cmp-1", "ryd 01", "2026"), "cmp_1_ryd_01_2026")

    def test_arabic_falls_back(self):
        from urs.models import build_schema_name
        self.assertEqual(build_schema_name("الشركة", "MAIN", 2026), "app_main_2026")

    def test_bad_year_uses_current(self):
        import datetime as _dt
        from urs.models import build_schema_name
        self.assertTrue(build_schema_name("c", "b", "xx").endswith("_%d" % _dt.date.today().year))


class WsZipStageTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._base = mock.patch.object(_v, "BASE_DIR", pathlib.Path(self._td.name))
        self._base.start()
        self.addCleanup(self._base.stop)

    def test_stage_lists_apps_only(self):
        info = _v._ws_stage_apps_zip(_upload_file(_make_apps_zip()))
        self.assertEqual(info["apps"], ["hr"])
        self.assertTrue(info["stage"])
        self.assertGreater(info["files"], 0)

    def test_install_guards_slip_and_settings(self):
        info = _v._ws_stage_apps_zip(_upload_file(_make_apps_zip()))
        ws_apps = pathlib.Path(self._td.name) / "workspace_9" / "apps"
        out = _v._ws_install_staged_apps(info["stage"], ws_apps)
        self.assertEqual(out["apps"], ["hr"])
        self.assertTrue((ws_apps / "hr" / "metadata.json").is_file())
        self.assertTrue((ws_apps / "hr" / "hr.fmlk").is_file())
        self.assertFalse((ws_apps / "settings").exists())
        self.assertFalse((pathlib.Path(self._td.name) / "evil.txt").exists())
        self.assertFalse((ws_apps / "evil.txt").exists())
        self.assertFalse((ws_apps / "evil2.txt").exists())

    def test_rejects_non_zip_and_empty(self):
        with self.assertRaises(ValueError):
            _v._ws_stage_apps_zip(_upload_file(b"nope", name="a.rar"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hi")
        buf.seek(0)
        with self.assertRaises(ValueError):
            _v._ws_stage_apps_zip(_upload_file(buf.read()))

    def test_rejects_bad_stage(self):
        with self.assertRaises(ValueError):
            _v._ws_install_staged_apps("../../etc", pathlib.Path(self._td.name) / "apps")

    def test_upload_endpoint(self):
        with mock.patch.object(_v, "_ws_master_ok", mock.Mock(return_value=True)):
            req = self.rf.post("/api/workspaces/upload-apps/",
                               data={"zip": _upload_file(_make_apps_zip())})
            res = _v.api_workspaces_upload_apps(req)
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual(body["apps"], ["hr"])
        self.assertTrue(body.get("stage"))

    def test_upload_endpoint_requires_file(self):
        with mock.patch.object(_v, "_ws_master_ok", mock.Mock(return_value=True)):
            req = self.rf.post("/api/workspaces/upload-apps/", data={})
            res = _v.api_workspaces_upload_apps(req)
        self.assertEqual(res.status_code, 400)


class _FakeSqlFrag:
    def __init__(self, text):
        self.text = text

    def format(self, *a):
        return self

    def as_string(self, ctx):
        return self.text


class _FakePgCur:
    def __init__(self, owner):
        self.owner = owner
        self._rows = []

    def execute(self, sql, params=None):
        try:
            q = sql.as_string(None)
        except Exception:
            q = str(sql)
        self.owner.statements.append((q, params))
        if "pg_tables" in q:
            if q.strip().upper().startswith("SELECT 1"):
                self._rows = []  # existence probe → table missing by default
            else:
                self._rows = [("urs_app",), ("auth_user",)]

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _FakePgConn:
    instances = []

    def __init__(self):
        self.statements = []
        self.autocommit = False
        _FakePgConn.instances.append(self)

    def cursor(self):
        return _FakePgCur(self)

    def close(self):
        pass


class WsMigrateSchemaTests(SimpleTestCase):
    def _run(self, schema="cmp_main_2026", db_missing=True, users_table=""):
        import db_init as _dbi
        pg_calls = []

        def _connect(**kw):
            pg_calls.append(kw)
            if db_missing and kw.get("dbname") == "urs9" and len(pg_calls) == 1:
                raise Exception('database "urs9" does not exist')
            return _FakePgConn()

        fake_pg = mock.MagicMock()
        fake_pg.connect.side_effect = _connect
        fake_pg.sql.SQL.side_effect = lambda t: _FakeSqlFrag(t)
        fake_pg.sql.Identifier.side_effect = lambda n: _FakeSqlFrag('"%s"' % n)
        _FakePgConn.instances.clear()
        with mock.patch.dict(sys.modules, {"psycopg2": fake_pg}):
            with mock.patch("django.core.management.call_command") as _cc:
                with mock.patch("django.db.connections") as _conns:
                    fake_backend_cur = _FakePgCur(mock.Mock(statements=[]))
                    _conns.__getitem__.return_value.cursor.return_value = fake_backend_cur
                    out = _dbi.migrate_schema("h", 5432, "u", "p", "urs9", schema, users_table)
        return out, _cc, fake_backend_cur, pg_calls

    def test_validate_schema(self):
        import db_init as _dbi
        self.assertEqual(_dbi.validate_ws_schema("CMP_1"), "cmp_1")
        for bad in ("", "a-b", "1x", "x" * 64, "sch ema"):
            with self.assertRaises(ValueError):
                _dbi.validate_ws_schema(bad)

    def test_split_users_table(self):
        import db_init as _dbi
        self.assertEqual(_dbi._split_users_table("", "c_b_2026"), ("c_b_2026", "users"))
        self.assertEqual(_dbi._split_users_table("users", "c_b_2026"), ("c_b_2026", "users"))
        self.assertEqual(_dbi._split_users_table("c_b_2026.sys_users", "c_b_2026"),
                         ("c_b_2026", "sys_users"))
        self.assertEqual(_dbi._split_users_table("other_s.users", "c_b_2026"), (None, None))
        self.assertEqual(_dbi._split_users_table("a.b.c", "c_b_2026"), (None, None))
        self.assertEqual(_dbi._split_users_table("bad-name!", "c_b_2026"), (None, None))

    def test_migrate_creates_users_table_when_missing(self):
        out, _cc, _cur, _calls = self._run(schema="c_b_2026", users_table="c_b_2026.users")
        self.assertTrue(out["users_created"])
        self.assertEqual(out["users_table"], "c_b_2026.users")
        stmts = [s for inst in _FakePgConn.instances for (s, _p) in inst.statements]
        ddl = [s for s in stmts if "CREATE TABLE" in s]
        self.assertEqual(len(ddl), 1)
        for col in ("username", "full_name", "email", "password", "password_hash",
                    "role", "is_active"):
            self.assertIn(col, ddl[0])

    def test_migrate_skips_foreign_users_table(self):
        out, _cc, _cur, _calls = self._run(schema="c_b_2026", users_table="other_s.users")
        self.assertFalse(out["users_created"])
        self.assertEqual(out["users_table"], "")
        stmts = [s for inst in _FakePgConn.instances for (s, _p) in inst.statements]
        self.assertFalse(any("CREATE TABLE" in s for s in stmts))

    def test_migrate_creates_db_schema_and_lists_tables(self):
        import db_init as _dbi
        pg_calls = []

        def _connect(**kw):
            pg_calls.append(kw)
            if kw.get("dbname") == "urs9" and len(pg_calls) == 1:
                raise Exception('database "urs9" does not exist')
            return _FakePgConn()

        fake_pg = mock.MagicMock()
        fake_pg.connect.side_effect = _connect
        fake_pg.sql.SQL.side_effect = lambda t: _FakeSqlFrag(t)
        fake_pg.sql.Identifier.side_effect = lambda n: _FakeSqlFrag('"%s"' % n)
        _FakePgConn.instances.clear()
        with mock.patch.dict(sys.modules, {"psycopg2": fake_pg}):
            with mock.patch("django.core.management.call_command") as _cc:
                with mock.patch("django.db.connections") as _conns:
                    _conns.__getitem__.return_value.cursor.return_value.fetchall.return_value = [
                        ("urs_app",), ("auth_user",)]
                    out = _dbi.migrate_schema("h", 5432, "u", "p", "urs9", "CMP_MAIN_2026")
        self.assertTrue(out["migrated"])
        self.assertEqual(out["schema"], "cmp_main_2026")
        self.assertEqual(sorted(out["tables"]), ["auth_user", "urs_app"])
        _cc.assert_called_once()
        kw = _cc.call_args[1]
        self.assertEqual(kw.get("database"), "ws_cmp_main_2026")
        self.assertTrue(kw.get("run_syncdb"))
        # missing db → admin create → reconnect → users-table phase: 4 connects
        self.assertEqual(len(pg_calls), 4)
        stmts = [s for inst in _FakePgConn.instances for (s, _p) in inst.statements]
        self.assertTrue(any("CREATE DATABASE" in s for s in stmts))
        self.assertTrue(any("CREATE SCHEMA" in s for s in stmts))
        from django.conf import settings as _st
        self.assertNotIn("ws_cmp_main_2026", _st.DATABASES)

    def test_migrate_schema_helper_loader(self):
        mod = _v._ws_db_init_module()
        self.assertTrue(callable(getattr(mod, "migrate_schema", None)))

    def test_dynamic_alias_without_defaults_raises_time_zone(self):
        # Regression: KeyError 'TIME_ZONE' reported from workspace wizard.
        from django.db.backends.postgresql.base import DatabaseWrapper
        bare = {"ENGINE": "django.db.backends.postgresql", "NAME": "db",
                "USER": "u", "PASSWORD": "p", "HOST": "h", "PORT": "5432",
                "OPTIONS": {"options": "-c search_path=s"}}
        with self.assertRaises(KeyError) as _cm:
            DatabaseWrapper(dict(bare), "ws_bare").check_settings()
        self.assertEqual(_cm.exception.args, ("TIME_ZONE",))

    def test_migrate_registers_full_alias_defaults(self):
        import db_init as _dbi
        pg_calls = []

        def _connect(**kw):
            pg_calls.append(kw)
            return _FakePgConn()

        fake_pg = mock.MagicMock()
        fake_pg.connect.side_effect = _connect
        fake_pg.sql.SQL.side_effect = lambda t: _FakeSqlFrag(t)
        fake_pg.sql.Identifier.side_effect = lambda n: _FakeSqlFrag('"%s"' % n)
        _FakePgConn.instances.clear()
        from django.conf import settings as _st
        seen = {}

        def _snap(*a, **k):
            seen.update(dict(_st.DATABASES.get("ws_cmp_main_2026", {})))
            return None

        with mock.patch.dict(sys.modules, {"psycopg2": fake_pg}):
            with mock.patch("django.core.management.call_command", side_effect=_snap):
                with mock.patch("django.db.connections") as _conns:
                    _conns.__getitem__.return_value.cursor.return_value.fetchall.return_value = []
                    _dbi.migrate_schema("h", 5432, "u", "p", "urs9", "cmp_main_2026")
        for key in ("TIME_ZONE", "AUTOCOMMIT", "ATOMIC_REQUESTS", "CONN_MAX_AGE",
                    "CONN_HEALTH_CHECKS", "OPTIONS", "TEST"):
            self.assertIn(key, seen)
        self.assertIsNone(seen["TIME_ZONE"])
        self.assertNotIn("ws_cmp_main_2026", _st.DATABASES)


class WsCreateEndpointTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = pathlib.Path(self._td.name)
        self._p1 = mock.patch.object(_v, "BASE_DIR", self.tmp)
        self._p1.start()
        self.addCleanup(self._p1.stop)
        self._p2 = mock.patch("urs.workspace.workspace_dirs", return_value=[])
        self._p2.start()
        self.addCleanup(self._p2.stop)
        self._p3 = mock.patch.object(_v, "_ws_master_ok", mock.Mock(return_value=True))
        self._p3.start()
        self.addCleanup(self._p3.stop)
        self.mig_calls = []
        outer = self

        def _fake_mig(params, schema, users_table=""):
            outer.mig_calls.append((dict(params), schema, users_table))
            if getattr(outer, "_mig_fail", False):
                raise RuntimeError("تعذر الوصول للخادم")
            return {"schema": schema, "tables": ["urs_app", "auth_user"], "migrated": True,
                    "users_table": users_table or "", "users_created": True}

        self._p4 = mock.patch.object(_v, "_ws_migrate_workspace_schema", side_effect=_fake_mig)
        self._p4.start()
        self.addCleanup(self._p4.stop)

    def _payload(self, **kw):
        body = {"id": "workspace_9", "name": "ت", "brand": "Odex", "company": "c",
                "company_code": "CMP", "branch_code": "MAIN", "fiscal_year": "2026",
                "primary_connection": "ws9_conn", "from": "",
                "connection": {"host": "10.0.0.5", "port": "5433", "user": "u9",
                               "password": "s3cret", "instance": "db9"},
                "migrate": True}
        body.update(kw)
        return body

    def _post(self, body):
        req = self.rf.post("/api/workspaces/create/", data=json.dumps(body),
                           content_type="application/json")
        return _v.api_workspaces_create(req)

    def test_full_create_folder_conn_migrate(self):
        res = self._post(self._payload())
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("schema"), "cmp_main_2026")
        self.assertTrue(body.get("migrated"))
        self.assertEqual(body.get("imported_apps"), [])
        ws = self.tmp / "workspace_9"
        self.assertTrue((ws / "__init__.py").is_file())
        self.assertTrue((ws / "workspace.conf").is_file())
        self.assertTrue((ws / "apps" / "settings").is_dir())
        conf = (ws / "workspace.conf").read_text(encoding="utf-8")
        self.assertIn("SCHEMA=cmp_main_2026", conf)
        self.assertIn("USERS_TABLE=cmp_main_2026.users", conf)
        sp = (ws / "settings.py").read_text(encoding="utf-8")
        self.assertIn("'fiscal_year': '2026'", sp)
        from urs.models import Connection
        row = Connection.objects.filter(name="ws9_conn").first()
        self.assertIsNotNone(row)
        self.assertEqual((row.host, row.port, row.user, row.password,
                          row.instance, row.schema),
                         ("10.0.0.5", 5433, "u9", "s3cret", "db9", "cmp_main_2026"))
        self.assertEqual(len(self.mig_calls), 1)
        params, schema, users_table = self.mig_calls[0]
        self.assertEqual(schema, "cmp_main_2026")
        self.assertEqual(users_table, "cmp_main_2026.users")
        self.assertEqual(params["password"], "s3cret")
        self.assertEqual(params["instance"], "db9")

    def test_schema_override_and_migrate_opt_out(self):
        res = self._post(self._payload(schema="my_schema", migrate=False,
                                       users_table="my_schema.sys_users"))
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual(body.get("schema"), "my_schema")
        self.assertFalse(body.get("migrated"))
        self.assertEqual(self.mig_calls, [])
        conf = (self.tmp / "workspace_9" / "workspace.conf").read_text(encoding="utf-8")
        self.assertIn("USERS_TABLE=my_schema.sys_users", conf)

    def test_bad_schema_rejected(self):
        res = self._post(self._payload(schema="bad-name!"))
        self.assertEqual(res.status_code, 400)

    def test_migrate_failure_keeps_folder(self):
        self._mig_fail = True
        res = self._post(self._payload())
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 500, body)
        self.assertIn("فشل الترحيل", body.get("error") or "")
        self.assertTrue((self.tmp / "workspace_9" / "workspace.conf").is_file())

    def test_remigrate_endpoint(self):
        self._post(self._payload(migrate=False))
        from urs.models import Connection
        self.assertTrue(Connection.objects.filter(name="ws9_conn").exists())
        with mock.patch("urs.workspace.workspace_dirs",
                        return_value=[self.tmp / "workspace_9"]):
            req = self.rf.post("/api/workspaces/workspace_9/migrate/",
                               data=json.dumps({}), content_type="application/json")
            res = _v.api_workspace_migrate(req, "workspace_9")
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual(body.get("schema"), "cmp_main_2026")
        self.assertEqual(body.get("table_count"), 2)


class WsSharedFragmentsTests(SimpleTestCase):
    FRAGS = ["rml_wizard_modal.html", "rml_wizard_script.html",
             "forms_wizard_modal.html", "forms_wizard_script.html",
             "conn_stats.html"]

    def test_no_workspace_shadows_shared(self):
        import urs.workspace as _wsm
        for ws in _wsm.workspace_dirs():
            md = ws / "apps" / "settings" / "modals"
            if not md.is_dir():
                continue
            for n in self.FRAGS:
                self.assertFalse((md / n).exists(),
                                 "shadowed shared fragment: %s/%s" % (ws.name, n))

    def test_shared_dirs_resolve(self):
        import urs.workspace as _wsm
        dirs = _wsm.shared_modals_dirs()
        self.assertEqual(len(dirs), 2)
        for n in self.FRAGS:
            self.assertTrue(any((d / n).is_file() for d in dirs), n)

    def test_template_includes_resolve_from_shared(self):
        from django.template import engines
        for n in ("rml_wizard_modal.html", "rml_wizard_script.html",
                  "forms_wizard_modal.html", "forms_wizard_script.html"):
            t = engines["django"].get_template(n)
            self.assertIn("shared", str(t.origin.name).replace("\\", "/"))

    def test_wizard_flags_true(self):
        self.assertEqual(_v._wizard_flags(),
                         {"has_rml_wizard": True, "has_fml_wizard": True})

    def test_app_modal_shared_fallback(self):
        rf = RequestFactory()
        req = rf.get("/api/apps/settings/modals/modals/conn_stats.html")
        res = _v.api_app_modal(req, "settings", "modals/conn_stats.html")
        self.assertEqual(res.status_code, 200)
        self.assertIn("إحصائيات الاتصال", res.content.decode()[:5000])

    def test_copy_skips_shared_fragments(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            import pathlib as _pl
            src = _pl.Path(td) / "src"
            (src / "modals").mkdir(parents=True)
            (src / "modals" / "rml_wizard_script.html").write_text("x", encoding="utf-8")
            (src / "modals" / "custom.html").write_text("y", encoding="utf-8")
            (src / "m.fmlk").write_text("<fml/>", encoding="utf-8")
            dst = _pl.Path(td) / "dst"
            dst.mkdir()
            n = _v._ws_copy_settings_files(src, dst)
            self.assertFalse((dst / "modals" / "rml_wizard_script.html").exists())
            self.assertTrue((dst / "modals" / "custom.html").is_file())
            self.assertTrue((dst / "m.fmlk").is_file())
            self.assertGreater(n, 0)


class WsScopeTests(SimpleTestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = pathlib.Path(self._td.name)
        self.ws1 = self.tmp / "workspace_1"
        self.ws2 = self.tmp / "workspace_2"
        (self.ws1 / "apps").mkdir(parents=True)
        (self.ws2 / "apps").mkdir(parents=True)
        self._p = mock.patch("urs.workspace.workspace_dirs",
                             return_value=[self.ws1, self.ws2])
        self._p.start()
        self.addCleanup(self._p.stop)
        self.addCleanup(lambda: _wsm_reset(None))

    def test_unscoped_sees_all(self):
        import urs.workspace as _wsm
        roots = [str(p) for p in _wsm.system_roots()]
        self.assertTrue(any("workspace_1" in r for r in roots))
        self.assertTrue(any("workspace_2" in r for r in roots))

    def test_scoped_hides_other_workspace(self):
        import urs.workspace as _wsm
        tok = _wsm.set_active_ws("workspace_2")
        try:
            roots = [str(p) for p in _wsm.system_roots()]
        finally:
            _wsm.reset_active_ws(tok)
        self.assertFalse(any("workspace_1" in r for r in roots))
        self.assertTrue(any("workspace_2" in r for r in roots))

    def test_unknown_active_falls_back_unscoped(self):
        import urs.workspace as _wsm
        tok = _wsm.set_active_ws("workspace_9")
        try:
            roots = [str(p) for p in _wsm.system_roots()]
        finally:
            _wsm.reset_active_ws(tok)
        self.assertTrue(any("workspace_1" in r for r in roots))
        self.assertTrue(any("workspace_2" in r for r in roots))
        self.assertIsNone(_wsm.active_ws_id())

    def test_ensure_app_dir_prefers_active(self):
        import urs.workspace as _wsm
        tok = _wsm.set_active_ws("workspace_2")
        try:
            d = _wsm.ensure_app_dir("myapp")
        finally:
            _wsm.reset_active_ws(tok)
        self.assertEqual(d.parent, self.ws2 / "apps")
        self.assertTrue(d.is_dir())

    def test_middleware_pins_and_resets(self):
        import urs.workspace as _wsm
        from urs.middleware import WorkspaceGateMiddleware
        seen = {}

        def _get_response(request):
            seen["during"] = _wsm.active_ws_id()
            from django.http import JsonResponse
            return JsonResponse({"ok": True})

        mw = WorkspaceGateMiddleware(_get_response)
        req = SimpleNamespace(path="/apps/", GET={}, session={"workspace": "workspace_2"})
        with mock.patch("urs.workspace.workspaces_info",
                        return_value=[{"id": "workspace_1"}, {"id": "workspace_2"}]):
            with mock.patch("urs.workspace.workspace_status", return_value="active"):
                res = mw(req)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(seen.get("during"), "workspace_2")
        self.assertIsNone(_wsm.active_ws_id())

    def test_middleware_settings_param_overrides_session(self):
        import urs.workspace as _wsm
        from urs.middleware import WorkspaceGateMiddleware
        seen = {}

        def _get_response(request):
            seen["during"] = _wsm.active_ws_id()
            from django.http import JsonResponse
            return JsonResponse({"ok": True})

        mw = WorkspaceGateMiddleware(_get_response)
        req = SimpleNamespace(path="/apps/", GET={"settings": "workspace_1"},
                              session={"workspace": "workspace_2"})
        with mock.patch("urs.workspace.workspaces_info",
                        return_value=[{"id": "workspace_1"}, {"id": "workspace_2"}]):
            with mock.patch("urs.workspace.workspace_status", return_value="active"):
                mw(req)
        self.assertEqual(seen.get("during"), "workspace_1")
        self.assertIsNone(_wsm.active_ws_id())


def _wsm_reset(_tok):
    try:
        import urs.workspace as _wsm
        _wsm.set_active_ws(None)
    except Exception:
        pass


class WsRemapTests(SimpleTestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dst = pathlib.Path(self._td.name) / "settings"
        self.dst.mkdir(parents=True)

    def _write(self, name, text):
        (self.dst / name).write_text(text, encoding="utf-8")

    def test_remaps_connection_and_schema(self):
        self._write("a.fmlk", '<fml><fml_metadata name="x" connection="old_conn" schema="old_schema"/>'
                              '<field name="f" refTable="t" refFk="id"/></fml>')
        self._write("b.cml", '<cml><cml_metadata table="t" schema="old_schema" connection="old_conn"/></cml>')
        self._write("bad.fmlk", "<fml><unclosed")
        self._write("note.txt", 'connection="old_conn"')
        files, attrs = _v._ws_remap_copied_conns(self.dst, "new_conn", "new_schema")
        self.assertEqual(files, 2)
        self.assertEqual(attrs, 4)
        a = (self.dst / "a.fmlk").read_text(encoding="utf-8")
        self.assertIn('connection="new_conn"', a)
        self.assertIn('schema="new_schema"', a)
        self.assertIn('refTable="t"', a)
        self.assertIn('connection="old_conn"', (self.dst / "note.txt").read_text(encoding="utf-8"))

    def test_rejects_unsafe_names(self):
        self._write("a.fmlk", '<fml connection="x" schema="y"/>')
        self.assertEqual(_v._ws_remap_copied_conns(self.dst, 'a"b', "s"), (0, 0))
        self.assertEqual(_v._ws_remap_copied_conns(self.dst, "c", "bad-schema!"), (0, 0))
        self.assertIn('connection="x"', (self.dst / "a.fmlk").read_text(encoding="utf-8"))


class WsCreateRemapTests(__import__("django.test", fromlist=["TestCase"]).TestCase):
    def test_create_remaps_cloned_settings(self):
        import tempfile as _tf
        tmp = pathlib.Path(_tf.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(str(tmp), ignore_errors=True))
        src = tmp / "workspace_1"
        (src / "apps" / "settings").mkdir(parents=True)
        (src / "apps" / "settings" / "m.fmlk").write_text(
            '<fml><fml_metadata name="m" connection="src_conn" schema="src_schema"/></fml>',
            encoding="utf-8")
        rf = RequestFactory()
        body = {"id": "workspace_2", "company_code": "C", "branch_code": "B", "fiscal_year": "2026",
                "primary_connection": "new_conn", "from": "workspace_1",
                "connection": {"host": "h", "port": "1", "user": "u", "password": "p", "instance": "db"},
                "migrate": False}
        with mock.patch.object(_v, "BASE_DIR", tmp):
            with mock.patch("urs.workspace.workspace_dirs", return_value=[src]):
                with mock.patch.object(_v, "_ws_master_ok", mock.Mock(return_value=True)):
                    req = rf.post("/api/workspaces/create/", data=json.dumps(body),
                                  content_type="application/json")
                    res = _v.api_workspaces_create(req)
        data = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, data)
        self.assertGreater(data.get("remapped_files", 0), 0)
        out = (tmp / "workspace_2" / "apps" / "settings" / "m.fmlk").read_text(encoding="utf-8")
        self.assertIn('connection="new_conn"', out)
        self.assertIn('schema="c_b_2026"', out)


class WsDeleteEndpointTests(__import__("django.test", fromlist=["TestCase"]).TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = pathlib.Path(self._td.name)
        self.ws = self.tmp / "workspace_9"
        (self.ws / "apps" / "settings").mkdir(parents=True)
        (self.ws / "workspace.conf").write_text(
            "PRIMARY_CONNECTION=ws9_conn\nSCHEMA=cmp_main_2026\nSTATUS=active\n", encoding="utf-8")
        (self.ws / "__init__.py").write_text('"""ws"""\n', encoding="utf-8")
        self._p1 = mock.patch.object(_v, "BASE_DIR", self.tmp)
        self._p1.start()
        self.addCleanup(self._p1.stop)
        self._p2 = mock.patch("urs.workspace.workspace_dirs", return_value=[self.ws])
        self._p2.start()
        self.addCleanup(self._p2.stop)

    def _post(self, ws_id, body):
        req = self.rf.post("/api/workspaces/%s/delete/" % ws_id, data=json.dumps(body),
                           content_type="application/json")
        return _v.api_workspace_delete(req, ws_id)

    def test_wrong_master_password(self):
        with mock.patch.object(_v, "_ws_verify_master_password", return_value=False):
            res = self._post("workspace_9", {"master_password": "nope"})
        self.assertEqual(res.status_code, 403)

    def test_invalid_ids_rejected(self):
        with mock.patch.object(_v, "_ws_verify_master_password", return_value=True):
            for bad, code in (("foo", 400), ("../secret", 400), ("workspace_nope", 404)):
                res = self._post(bad, {"master_password": "x"})
                self.assertEqual(res.status_code, code, bad)
        self.assertTrue(self.ws.is_dir())

    def test_full_delete_folder_and_connection(self):
        from urs.models import Connection
        Connection.objects.create(name="ws9_conn", host="h", port=1, user="u",
                                  password="p", instance="db", schema="cmp_main_2026")
        with mock.patch.object(_v, "_ws_verify_master_password", return_value=True):
            res = self._post("workspace_9", {"master_password": "x"})
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("connection_deleted"))
        self.assertFalse(body.get("schema_dropped"))
        self.assertFalse(self.ws.exists())
        self.assertFalse(Connection.objects.filter(name="ws9_conn").exists())

    def test_drop_schema_runs_before_delete(self):
        from urs.models import Connection
        Connection.objects.create(name="ws9_conn", host="h", port=1, user="u",
                                  password="p", instance="db", schema="cmp_main_2026")
        seen = []

        class _Cur:
            def execute(self, sql, params=None):
                seen.append(sql)
            def close(self):
                pass

        class _Conn:
            def __init__(self):
                self.autocommit = False
            def cursor(self):
                return _Cur()
            def close(self):
                pass

        fake_pg = mock.MagicMock()
        fake_pg.connect.side_effect = lambda **kw: (seen.append(kw) or _Conn())
        with mock.patch.object(_v, "_ws_verify_master_password", return_value=True):
            with mock.patch.dict(sys.modules, {"psycopg2": fake_pg}):
                res = self._post("workspace_9", {"master_password": "x", "drop_schema": True})
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body.get("schema_dropped"))
        self.assertTrue(any("DROP SCHEMA" in str(s) and "cmp_main_2026" in str(s) for s in seen))
        self.assertFalse(self.ws.exists())

    def test_drop_schema_failure_aborts(self):
        from urs.models import Connection
        Connection.objects.create(name="ws9_conn", host="h", port=1, user="u",
                                  password="p", instance="db", schema="cmp_main_2026")
        fake_pg = mock.MagicMock()
        fake_pg.connect.side_effect = Exception("down")
        with mock.patch.object(_v, "_ws_verify_master_password", return_value=True):
            with mock.patch.dict(sys.modules, {"psycopg2": fake_pg}):
                res = self._post("workspace_9", {"master_password": "x", "drop_schema": True})
        self.assertEqual(res.status_code, 500)
        self.assertTrue(self.ws.is_dir())
        self.assertTrue(Connection.objects.filter(name="ws9_conn").exists())

    def test_refuses_non_workspace_dir(self):
        import shutil as _sh
        _sh.rmtree(str(self.ws / "apps"))
        self.ws.joinpath("workspace.conf").unlink()
        with mock.patch.object(_v, "_ws_verify_master_password", return_value=True):
            res = self._post("workspace_9", {"master_password": "x"})
        self.assertEqual(res.status_code, 400)
        self.assertTrue(self.ws.is_dir())


class WsCreateModalMarkupTests(SimpleTestCase):
    def test_sidebar_wizard_markup(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "workspace.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("wsC_sec_basic", "wsC_sec_conn", "wsC_sec_apps", "wsC_sec_final",
                       "upload-apps", "ترحيل وحفظ مساحة العمل", "wsC_company_code",
                       "wsC_branch_code", "wsC_fiscal_year", "wsC_schema_view",
                       "wsC_conn_schema", "wsC_zip", "wsC_migrate"):
            self.assertIn(needle, html)

    def test_workspace_info_exposes_schema(self):
        import tempfile as _tf
        import urs.workspace as _wsm
        with _tf.TemporaryDirectory() as td:
            info = _wsm.workspace_info(pathlib.Path(td) / "workspace_1")
            self.assertIn("schema", info)
            self.assertEqual(info["schema"], "")

    def test_delete_modal_markup(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "workspace.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("wsDeleteOpen", "wsDeleteModal", "wsDelGo", "wsDelPw", "wsDelSchema",
                       "كلمة الماستر", "حذف نهائي (10)", "DROP SCHEMA", "1000"):
            self.assertIn(needle, html)

    def test_create_modal_country_currency_selects(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "workspace.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="wsC_country"', html)
        self.assertIn('id="wsC_currency"', html)
        self.assertIn("/api/lookup/lists/", html)
        self.assertIn("اختر الدولة", html)
        self.assertIn("اختر العملة", html)


class WsScopedConnectionsTests(__import__("django.test", fromlist=["TestCase"]).TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        from urs.models import Connection
        Connection.objects.all().delete()
        self.c1 = Connection.objects.create(name="ws_primary", engine="postgres", host="127.0.0.1", port=5432, instance="db1", schema="sch_orig")
        self.c2 = Connection.objects.create(name="other_conn", engine="postgres", host="127.0.0.1", port=5432, instance="db2", schema="public")

    def test_api_connections_list_scoped_to_workspace_session(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            ws_dir = pathlib.Path(td) / "workspace_t"
            ws_dir.mkdir()
            (ws_dir / "workspace.conf").write_text("PRIMARY_CONNECTION=ws_primary\nSCHEMA=sch_ws\n", encoding="utf-8")
            with mock.patch("urs.workspace.workspace_dirs", return_value=[ws_dir]):
                req = self.rf.get("/api/connections/")
                req.session = {"workspace": "workspace_t", "fiscal_schema": "fiscal_2026"}
                res = _v.api_connections_list(req)
                data = json.loads(res.content.decode())
                conns = data.get("connections", [])
                self.assertEqual(len(conns), 1)
                self.assertEqual(conns[0]["name"], "ws_primary")
                self.assertEqual(conns[0]["schema"], "fiscal_2026")

    def test_api_connections_list_unscoped_when_no_session(self):
        req = self.rf.get("/api/connections/")
        req.session = {}
        res = _v.api_connections_list(req)
        data = json.loads(res.content.decode())
        names = {c["name"] for c in data.get("connections", [])}
        self.assertIn("ws_primary", names)
        self.assertIn("other_conn", names)

    def test_api_connection_tables_scoped_to_session_schema(self):
        req = self.rf.get(f"/api/connections/{self.c1.id}/tables/")
        req.session = {"workspace": "workspace_t", "fiscal_schema": "fiscal_2026"}
        with mock.patch("urs.views._ws_session_context", return_value=("workspace_t", "fiscal_2026", "ws_primary")), \
             mock.patch("urs.views._list_tables_obj", return_value=[{"name": "tbl1", "schema": "fiscal_2026", "full": "fiscal_2026.tbl1"}]) as mock_list:
            res = _v.api_connection_tables(req, self.c1.id)
            data = json.loads(res.content.decode())
            self.assertEqual(res.status_code, 200)
            mock_list.assert_called_once_with(mock.ANY, schema_override="fiscal_2026")
            self.assertEqual(data["schema"], "fiscal_2026")

    def test_api_connection_table_columns_scoped_to_session_schema(self):
        req = self.rf.get(f"/api/connections/{self.c1.id}/tables/users/columns/")
        req.session = {"workspace": "workspace_t", "fiscal_schema": "fiscal_2026"}
        with mock.patch("urs.views._ws_session_context", return_value=("workspace_t", "fiscal_2026", "ws_primary")), \
             mock.patch("urs.views._table_columns_obj", return_value=[{"name": "id", "type": "INTEGER", "db_type": "int"}]) as mock_cols:
            res = _v.api_connection_table_columns(req, self.c1.id, "users")
            data = json.loads(res.content.decode())
            self.assertEqual(res.status_code, 200)
            mock_cols.assert_called_once_with(mock.ANY, "fiscal_2026", "users")
            self.assertEqual(data["table"], "fiscal_2026.users")

