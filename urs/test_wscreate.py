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
            self._rows = [("urs_app",), ("auth_user",)]

    def fetchall(self):
        return list(self._rows)

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
    def test_validate_schema(self):
        import db_init as _dbi
        self.assertEqual(_dbi.validate_ws_schema("CMP_1"), "cmp_1")
        for bad in ("", "a-b", "1x", "x" * 64, "sch ema"):
            with self.assertRaises(ValueError):
                _dbi.validate_ws_schema(bad)

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
        # missing db → admin create → reconnect: 3 connects
        self.assertEqual(len(pg_calls), 3)
        stmts = [s for inst in _FakePgConn.instances for (s, _p) in inst.statements]
        self.assertTrue(any("CREATE DATABASE" in s for s in stmts))
        self.assertTrue(any("CREATE SCHEMA" in s for s in stmts))
        from django.conf import settings as _st
        self.assertNotIn("ws_cmp_main_2026", _st.DATABASES)

    def test_migrate_schema_helper_loader(self):
        mod = _v._ws_db_init_module()
        self.assertTrue(callable(getattr(mod, "migrate_schema", None)))


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

        def _fake_mig(params, schema):
            outer.mig_calls.append((dict(params), schema))
            if getattr(outer, "_mig_fail", False):
                raise RuntimeError("تعذر الوصول للخادم")
            return {"schema": schema, "tables": ["urs_app", "auth_user"], "migrated": True}

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
        params, schema = self.mig_calls[0]
        self.assertEqual(schema, "cmp_main_2026")
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
