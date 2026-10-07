"""Document report UI: header cleanup, master-table results, designer-chosen
select source (connection/table/column) + searchable combobox.

No live DB: fakes for connections/engines.
"""
import json
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from urs import views as _v


class DocParamSourceModelTests(SimpleTestCase):
    def test_writer_emits_source_attrs(self):
        import xml.etree.ElementTree as _ET
        rml = _ET.Element("rml")
        ok = _v._write_doc_params_el(rml, _ET, [{
            "column": "bill_no", "label": "رقم السند", "type": "select",
            "op": "equals", "required": True,
            "source": {"connection": "7", "table": "sales.bills", "column": "bill_no"},
            "searchable": True,
        }])
        self.assertTrue(ok)
        el = rml.find("doc_params/doc_param")
        self.assertIsNotNone(el)
        self.assertEqual((el.get("src_conn"), el.get("src_table"), el.get("src_column")),
                         ("7", "sales.bills", "bill_no"))
        self.assertIsNone(el.get("searchable"))

    def test_writer_marks_unsearchable(self):
        import xml.etree.ElementTree as _ET
        rml = _ET.Element("rml")
        _v._write_doc_params_el(rml, _ET, [{
            "column": "c", "type": "select", "searchable": False,
            "src_conn": "7", "src_table": "t", "src_column": "c",
        }])
        el = rml.find("doc_params/doc_param")
        self.assertEqual(el.get("searchable"), "0")

    def test_compiler_extracts_source(self):
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(xml_text=(
            '<rml><doc_params>'
            '<doc_param id="dp_1" column="bill_no" label="رقم السند" type="select" '
            'op="equals" required="1" src_conn="7" src_table="sales.bills" '
            'src_column="bill_no" searchable="0"/>'
            '</doc_params></rml>'))
        params = comp.doc_params()
        self.assertEqual(len(params), 1)
        p = params[0]
        self.assertEqual((p.src_conn, p.src_table, p.src_column), ("7", "sales.bills", "bill_no"))
        self.assertFalse(p.searchable)
        d = p.to_dict()
        self.assertEqual(d["source"], {"connection": "7", "table": "sales.bills", "column": "bill_no"})
        self.assertEqual(d["srcTable"], "sales.bills")

    def test_compiler_defaults_no_source(self):
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(xml_text=(
            '<rml><doc_params><doc_param column="x" type="text"/></doc_params></rml>'))
        p = comp.doc_params()[0]
        self.assertEqual((p.src_conn, p.src_table, p.src_column), ("", "", ""))
        self.assertTrue(p.searchable)

    def test_writer_emits_param_refname(self):
        import xml.etree.ElementTree as _ET
        rml = _ET.Element("rml")
        ok = _v._write_doc_params_el(rml, _ET, [{
            "column": "bill_type", "label": "نوع الفاتورة", "type": "select",
            "op": "equals", "required": True,
            "param_refname": "b_type",
        }])
        self.assertTrue(ok)
        el = rml.find("doc_params/doc_param")
        self.assertIsNotNone(el)
        self.assertEqual(el.get("param_refname"), "b_type")

    def test_compiler_extracts_param_refname(self):
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(xml_text=(
            '<rml><doc_params>'
            '<doc_param id="dp_1" column="bill_type" label="نوع الفاتورة" type="text" '
            'param_refname="b_type"/>'
            '</doc_params></rml>'))
        params = comp.doc_params()
        self.assertEqual(len(params), 1)
        p = params[0]
        self.assertEqual(p.param_refname, "b_type")
        d = p.to_dict()
        self.assertEqual(d["param_refname"], "b_type")
        self.assertEqual(d["paramRefname"], "b_type")
        self.assertEqual(d["refname"], "b_type")

    def test_engine_expands_param_refname_in_expressions(self):
        from rml_python.compiler import RMLReportCompiler
        from rml_python.engine import RMLReportEngine
        xml = '''<rml>
          <rpt_metadata name="test_doc" displayName="Test Doc" type="doc"/>
          <doc_params>
            <doc_param id="dp_1" column="type_id" param_refname="t_id" type="number" default_value="1"/>
            <doc_param id="dp_2" column="mode_str" param_refname="m_str" type="text" default_value="CASH"/>
          </doc_params>
          <fields>
            <field name="amt" table_source="t1" type="number"/>
          </fields>
          <columns>
            <column id="1" name="c1" alias="calc1" expr="IF(@t_id = 1, [amt] * 2, [amt])"/>
            <column id="2" name="c2" alias="calc2" expr="IF([m_str] = 'CASH', 'نقداً', 'آجل')"/>
          </columns>
        </rml>'''
        comp = RMLReportCompiler(xml_text=xml)
        eng = RMLReportEngine(comp, mock.Mock())
        cols = eng._inline_column_refs(eng.columns, filters=[])
        self.assertIn("1 = 1", cols[0].expr)
        self.assertIn("'CASH' = 'CASH'", cols[1].expr)

        filters = [
            {"refname": "t_id", "value": "2"},
            {"refname": "m_str", "value": "CREDIT"}
        ]
        cols2 = eng._inline_column_refs(eng.columns, filters=filters)
        self.assertIn("2 = 1", cols2[0].expr)
        self.assertIn("'CREDIT' = 'CASH'", cols2[1].expr)


class ValuesSourceEndpointTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def _post(self, body):
        req = self.rf.post("/api/rml/values-source/", data=json.dumps(body),
                           content_type="application/json")
        return _v.api_rml_values_source(req)

    def test_rejects_bad_identifiers(self):
        for body in ({"connection": "1", "table": "t;x", "column": "c"},
                     {"connection": "", "table": "t", "column": "c"},
                     {"connection": "1", "table": "a.b.c", "column": "c"},
                     {"connection": "1", "table": "t", "column": "c c"}):
            res = self._post(body)
            self.assertEqual(res.status_code, 400, body)

    def test_unknown_connection_404(self):
        with mock.patch("urs.models.Connection") as _mc:
            _mc.objects.filter.return_value.first.return_value = None
            res = self._post({"connection": "nope", "table": "t", "column": "c"})
        self.assertEqual(res.status_code, 404)

    def test_postgres_distinct_with_search(self):
        row = mock.Mock()
        row.id = 7
        row.name = "pg7"
        mgr = mock.Mock()
        # int("pg7") raises before any query → straight to name lookup
        mgr.filter.return_value = mock.Mock(first=mock.Mock(return_value=row))
        with mock.patch("urs.models.Connection") as _mc:
            _mc.objects = mgr
            with mock.patch.object(_v, "_xsql_resolve_conn", return_value=(mock.Mock(), "postgres")) as _rc:
                with mock.patch.object(_v, "_xsql_exec_on_db",
                                       return_value=([{"v": "A"}, {"v": 5}, {"v": None}, {"v": "  "}], ["v"])) as _ex:
                    res = self._post({"connection": "pg7", "table": "sales.bills",
                                      "column": "bill_no", "search": "a'b%c_d", "limit": 50})
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        _rc.assert_called_once_with(7)
        sql = _ex.call_args[0][1]
        self.assertIn("SELECT DISTINCT", sql)
        self.assertIn("ILIKE", sql)
        self.assertIn('"sales"."bills"', sql)
        self.assertIn("a''b\\%c\\_d", sql)  # quote doubled, wildcards escaped
        self.assertEqual(body["values"], ["A", 5])
        self.assertFalse(body["truncated"])

    def test_truncated_flag_and_limit_cap(self):
        row = mock.Mock()
        row.id = 9
        row.name = "pg9"
        with mock.patch("urs.models.Connection") as _mc:
            _mc.objects.filter.return_value.first.return_value = row
            with mock.patch.object(_v, "_xsql_resolve_conn", return_value=(mock.Mock(), "postgres")):
                with mock.patch.object(_v, "_xsql_exec_on_db",
                                       return_value=([{"v": i} for i in range(505)], ["v"])) as _ex:
                    res = self._post({"connection": 9, "table": "t", "column": "c", "limit": 9999})
        body = json.loads(res.content.decode())
        self.assertEqual(res.status_code, 200, body)
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["values"]), 500)  # hard cap
        self.assertIn("LIMIT 501", _ex.call_args[0][1])

    def test_mssql_dialect(self):
        row = mock.Mock()
        row.id = 3
        row.name = "ms3"
        with mock.patch("urs.models.Connection") as _mc:
            _mc.objects.filter.return_value.first.return_value = row
            with mock.patch.object(_v, "_xsql_resolve_conn", return_value=(mock.Mock(), "sqlserver")):
                with mock.patch.object(_v, "_xsql_exec_on_db",
                                       return_value=([{"v": "x"}], ["v"])) as _ex:
                    res = self._post({"connection": 3, "table": "dbo.bills", "column": "no",
                                          "search": "77"})
        self.assertEqual(res.status_code, 200)
        sql = _ex.call_args[0][1]
        self.assertIn("TOP(", sql)
        self.assertIn("[dbo].[bills]", sql)
        self.assertIn("LIKE N'", sql)

    def test_unsupported_engine_rejected(self):
        row = mock.Mock()
        row.id = 4
        row.name = "mq4"
        with mock.patch("urs.models.Connection") as _mc:
            _mc.objects.filter.return_value.first.return_value = row
            with mock.patch.object(_v, "_xsql_resolve_conn", return_value=(mock.Mock(), "mysql")):
                res = self._post({"connection": 4, "table": "t", "column": "c"})
        self.assertEqual(res.status_code, 400)


class DocGuardTests(SimpleTestCase):
    def _eng(self, params, columns, rtype="doc"):
        from rml_python.engine import RMLReportEngine
        eng = RMLReportEngine.__new__(RMLReportEngine)
        eng.metadata = {"report_type": rtype}
        eng.compiler = SimpleNamespace(doc_params=lambda: params)
        eng.columns = columns
        return eng

    def test_resolved_expr_field_matches_alias_param(self):
        # المدخل مربوط بالاسم المعروض، والمشغل يرسل expr بعد resolveDbField
        params = [SimpleNamespace(column="تاريخ الحوالة", required=True)]
        cols = [SimpleNamespace(alias="تاريخ الحوالة", name="transfer_date",
                                expr="[c.t.transfer_date]")]
        eng = self._eng(params, cols)
        between = [{"field": "[c.t.transfer_date]", "op": "between",
                    "valFrom": "2026-01-01", "valTo": "2026-10-06"}]
        self.assertFalse(eng._doc_required_missing(between))
        self.assertFalse(eng._doc_required_missing(
            [{"field": "تاريخ الحوالة", "op": "between",
              "valFrom": "2026-01-01", "valTo": "2026-10-06"}]))
        self.assertTrue(eng._doc_required_missing([]))
        self.assertTrue(eng._doc_required_missing(
            [{"field": "other", "op": "equals", "value": "x"}]))
        self.assertTrue(eng._doc_required_missing(
            [{"field": "[c.t.transfer_date]", "op": "between"}]))  # بلا قيم

    def test_non_doc_never_missing(self):
        eng = self._eng([], [], rtype="master")
        self.assertFalse(eng._doc_required_missing([]))

    def test_no_params_requires_any_filter(self):
        eng = self._eng([], [])
        self.assertTrue(eng._doc_required_missing([]))
        self.assertFalse(eng._doc_required_missing(
            [{"field": "x", "op": "equals", "value": "1"}]))


class SettingsTemplateTests(SimpleTestCase):
    def test_settings_template_parses(self):
        from django.template import engines
        engines["django"].get_template("settings.html")  # TemplateSyntaxError on failure

    def test_no_js_fallback_in_django_tags(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "settings.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn('users_table ||', html)
        self.assertIn('users_table|default:', html)


class FuzzyIndexTests(SimpleTestCase):
    PAT = r"(\d+)(?!.*\d)"

    def _eng(self):
        from rml_python.engine import RMLReportEngine
        eng = RMLReportEngine.__new__(RMLReportEngine)
        eng._report_progress = lambda *a, **k: None
        return eng

    def _case(self):
        prows = [{"b": "حوالة رقم 123", "x": 1},
                 {"b": "بلا أرقام", "x": 2},
                 {"b": "سند 999 ثم 123", "x": 3},
                 {"b": None, "x": 4}]
        srows = [{"s": "A-123", "v": "first-123"},
                 {"s": "123", "v": "bare-123"},
                 {"s": "999", "v": "nine"},
                 {"s": "zzz", "v": "nomatch"}]
        return prows, srows

    def test_index_matches_full_scan(self):
        import copy
        from rml_python.xsql import regex_extract
        eng = self._eng()
        prows, srows = self._case()
        spec = {"norm": "sec", "key": ["b", "s"]}
        base = {"__fuzzy__": True, "rows": srows, "match": "regex",
                "pattern": self.PAT, "bcol": "b", "scol": "s"}
        idx_scan = dict(base, by_extract=None)
        byx = {}
        for r in srows:
            e = regex_extract(r["s"], self.PAT)
            if e is not None and e not in byx:
                byx[e] = r
        idx_fast = dict(base, by_extract=byx)
        out_scan = eng._merge_lazy(copy.deepcopy(prows), [idx_scan], [spec], None)
        out_fast = eng._merge_lazy(copy.deepcopy(prows), [idx_fast], [spec], None)
        self.assertEqual(out_scan, out_fast)
        # first-hit-wins: trailing digits of row3 = 123 → first 123 row
        self.assertEqual(out_fast[0].get("sec.v"), "first-123")
        self.assertEqual(out_fast[2].get("sec.v"), "first-123")
        self.assertNotIn("sec.v", out_fast[1])
        self.assertNotIn("sec.v", out_fast[3])

    def test_index_secondary_builds_by_extract(self):
        eng = self._eng()
        cur = SimpleNamespace(description=[("s",), ("v",)],
                              fetchall=lambda: [("A-1", "a"), ("B-2", "b"), ("C-1", "c")],
                              close=lambda: None)
        eng._resolve_live_db = lambda gid: (SimpleNamespace(connect=lambda: None), "postgres")
        eng._exec_on = lambda db, sql, params: cur
        idx = eng._index_secondary({"norm": "sec", "gid": "6", "key": ["b", "s"],
                                    "match": "regex", "pattern": r"(\d+)$"},
                                   [{"b": "x-1"}])
        self.assertTrue(idx.get("__fuzzy__"))
        self.assertEqual(sorted(idx["by_extract"].keys()), ["1", "2"])
        self.assertEqual(idx["by_extract"]["1"]["v"], "a")  # first wins
        self.assertEqual(len(idx["rows"]), 3)


class StyleFreezeTests(SimpleTestCase):
    def test_column_style_extract(self):
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(xml_text=(
            '<rml><columns>'
            '<column id="1" name="a" alias="A" expr="a" color="#ff0000" bg="#00ff00" '
            'weight="bold" frozen="1"/>'
            '<column id="2" name="b" alias="B" expr="b" color="red" frozen="0"/>'
            '</columns></rml>'))
        cols = {c.name: c for c in comp.columns()}
        a = cols["a"]
        self.assertEqual((a.color, a.bg, a.weight, a.frozen), ("#ff0000", "#00ff00", "bold", True))
        d = a.to_dict()
        self.assertEqual((d["color"], d["bgColor"], d["fontWeight"], d["frozen"]), ("#ff0000", "#00ff00", "bold", True))
        b = cols["b"]
        self.assertEqual((b.color, b.frozen), (None, False))

    def test_group_style_extract(self):
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(xml_text=(
            '<rml><groups><group id="1" name="G" order="1" color="#111111" bg="#eeeeee" '
            'weight="bold" frozen="1"><column alias="A"/></group></groups></rml>'))
        g = comp.groups()[0]
        self.assertEqual((g.color, g.bg, g.weight, g.frozen), ("#111111", "#eeeeee", "bold", True))
        self.assertEqual(g.to_dict()["bgColor"], "#eeeeee")

    def test_writer_roundtrip(self):
        import xml.etree.ElementTree as _ET
        from rml_python.compiler import RMLReportCompiler
        rml = _ET.Element("rml")
        cols_el = _ET.SubElement(rml, "columns")
        col_el = _ET.SubElement(cols_el, "column")
        col_el.set("id", "1")
        col_el.set("name", "a")
        col_el.set("alias", "A")
        col_el.set("expr", "a")
        _v._write_col_style(col_el, {"color": "#abcdef", "bg": "#123456", "weight": "bold",
                                     "frozen": True, "bgColor": "nope"})
        _v._write_groups_el(rml, _ET, [{"id": "1", "name": "G", "order": 1, "columns": ["A"],
                                        "color": "#111111", "weight": "bold", "frozen": True,
                                        "bg": "zzz"}])
        raw = _ET.tostring(rml, encoding="utf-8").decode("utf-8")
        self.assertIn('color="#abcdef"', raw)
        self.assertIn('frozen="1"', raw)
        self.assertNotIn("nope", raw)
        self.assertNotIn('bg="zzz"', raw)
        comp = RMLReportCompiler(xml_text=raw)
        a = comp.columns()[0]
        self.assertEqual((a.color, a.bg, a.weight, a.frozen), ("#abcdef", "#123456", "bold", True))
        g = comp.groups()[0]
        self.assertEqual((g.color, g.weight, g.frozen, g.bg), ("#111111", "bold", True, None))

    def test_player_style_freeze_markup(self):
        html = self._html() if hasattr(self, "_html") else None
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("colTextStyle", "applyColFreeze", "colFrozenRun", "colDataAttr",
                       "data-col=", "totalWrap"):
            self.assertIn(needle, html)

    def test_designer_style_markup(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                 "rml_wizard_script.html"), encoding="utf-8") as f:
            js = f.read()
        for needle in ("التنسيق المرئي", "تجميد العمود", "تجميد المجموعة", "تنسيق رأس المجموعة",
                       "'color',this.value", "'frozen',this.checked", "updateWizardGroup("):
            self.assertIn(needle, js)

    def test_preview_manual_only(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_script.html"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("rptPrevStale", js)
        self.assertIn("بلا إخفاء وبلا جلب من الخادم", js)
        self.assertIn("loadedOnce", js)
        # no debounced auto-fetch remains
        self.assertNotIn("setTimeout(()=>{ try{ rptPreviewFetch(false); }catch(e){} }, 700)", js)


class AltRowColorsTests(SimpleTestCase):
    def test_metadata_extract(self):
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(xml_text=(
            '<rml><rpt_metadata name="r" displayName="R" alt_row_color_1="#ff0000" '
            'alt_row_color_2="#00ff00"/></rml>'))
        md = comp.rpt_metadata()
        self.assertEqual(md["alt_row_color_1"], "#ff0000")
        self.assertEqual(md["altRowColor2"], "#00ff00")

    def test_metadata_invalid_ignored(self):
        from rml_python.compiler import RMLReportCompiler
        comp = RMLReportCompiler(xml_text=(
            '<rml><rpt_metadata name="r" displayName="R" alt_row_color_1="red" '
            'alt_row_color_2="#12345"/></rml>'))
        md = comp.rpt_metadata()
        self.assertIsNone(md["alt_row_color_1"])
        self.assertIsNone(md["alt_row_color_2"])

    def test_writer_persists_and_clears(self):
        import xml.etree.ElementTree as _ET
        rml = _ET.Element("rml")
        out = _v._render_rml_xml(
            "r", "R", "fa-table", "HR", "s", "", None,
            [], [], [{"id": "1", "name": "a", "alias": "A", "expr": "a"}], [], [],
            "master", None, [], "", [], False, [], 1, "",
            {}, [], None,
            alt_row_color_1="#abcdef", alt_row_color_2="not-a-color")
        md = _ET.fromstring(out.encode("utf-8")).find("rpt_metadata")
        self.assertEqual(md.get("alt_row_color_1"), "#abcdef")
        self.assertIsNone(md.get("alt_row_color_2"))

    def test_player_zebra_markup(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("zebraPair", "alt_row_color_1", "altRowColor2",
                       "tbody tr:hover", "_zb", "closest('tr')"):
            self.assertIn(needle, html)

    def test_designer_alt_colors_markup(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_modal.html"), encoding="utf-8") as f:
            modal = f.read()
        self.assertIn('id="rmlAltRow1"', modal)
        self.assertIn('id="rmlAltRow2"', modal)
        self.assertIn("اللون 1", modal)
        self.assertIn("اللون 2", modal)
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_script.html"), encoding="utf-8") as f:
            js = f.read()
        for needle in ("rmlAltRowColors", "rmlAltRowLoad", "alt_row_color_1",
                       "_zbPrev", "...rmlAltRowColors()"):
            self.assertIn(needle, js)


class DocPlayerMarkupTests(SimpleTestCase):
    def _html(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            return f.read()

    def test_no_doc_criteria_label(self):
        self.assertNotIn("معايير المستند", self._html())

    def test_fetch_button_is_icon_only(self):
        html = self._html()
        self.assertNotIn("<span>جلب البيانات</span>", html)
        self.assertIn('id="docHeaderFetchBtn"', html)
        self.assertIn("fa-magnifying-glass", html)
        self.assertIn("أيقونة البحث", html)

    def test_doc_results_use_master_table(self):
        html = self._html()
        self.assertIn("الجدول الإجمالي العادي", html)
        self.assertIn("docSrcComboHTML", html)
        self.assertIn("/api/rml/values-source", html)

    def test_hidden_cols_designer_wins(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("userHiddenCols", "userShownCols", "rmlHiddenCols", "refreshHiddenFromRml",
                       "{h:[...userHiddenCols], s:[...userShownCols]}", "stored prefs win afterwards"):
            if needle == "stored prefs win afterwards":
                self.assertNotIn(needle, html)
            else:
                self.assertIn(needle, html)

    def test_no_progressbar_on_first_load(self):
        html = self._html()
        self.assertIn("_rptFirstLoad", html)
        self.assertIn("مودال التنفيذ الخلفي محذوف نهائياً", html)
        self.assertIn("payload.skipTotal = true;", html)
        self.assertIn('id="totalWrap"', html)
        self.assertIn("lazyTotalPages = -1", html)

    def test_player_cells_centered(self):
        html = self._html()
        self.assertIn("#dataTable td.excel-cell", html)
        self.assertIn("text-align: center", html)
        self.assertIn("#dataTable thead th > div { justify-content: center", html)

    def test_sort_sends_alias_not_formula(self):
        # ORA-00972: sort payload must carry the alias; backend resolves ordinal
        html = self._html()
        self.assertIn("{column:activeSort.column, direction:activeSort.direction}", html)
        self.assertNotIn("column:resolveDbField(activeSort.column)", html)

    def test_doc_filter_identity_is_real_column(self):
        # ORA-00904: doc filter field must be the real column, never the @ref
        html = self._html()
        self.assertIn("alias: p.column || p.field || p.name || p.param_refname", html)

    def test_designer_source_ui(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                 "rml_wizard_script.html"), encoding="utf-8") as f:
            js = f.read()
        for needle in ("src_conn", "src_table", "src_column", "searchable", "مصدر قيم صندوق الاختيار"):
            self.assertIn(needle, js)

    def test_doc_filters_have_no_tags(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            html = f.read()
        # doc-originated filters are marked and skipped in tag render (URL keeps the mark)
        self.assertGreater(html.count("_doc: true"), 5)
        self.assertIn("if(f && f._doc) return;", html)
        self.assertIn("_doc: !!f._doc", html)

    def test_rules_empty_states_have_add_button(self):
        # rule-adding button must sit NEXT TO the "no rules" text (not only in foot)
        import os as _os
        import re as _re
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_script.html"), encoding="utf-8") as f:
            js = f.read()
        for m in _re.finditer(r"لا (?:توجد )?قواعد بعد", js):
            window = js[max(0, m.start() - 200):m.end() + 600]
            self.assertIn("addWizardRule", window)

    def test_compact_rule_sources_dropdown(self):
        # 200-field wall replaced by searchable compact multi-select
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_script.html"), encoding="utf-8") as f:
            js = f.read()
        for needle in ("ruleSrcDropHtml", "ruleSrcCheck", "ruleSrcAll",
                       "ruleSrcFilter", "ruleSrcToggleDrop", "بحث عن حقل",
                       "تحديد الكل", "مسح الكل"):
            self.assertIn(needle, js)

    def test_number_counter_locks_width(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            p_html = f.read()
        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_script.html"), encoding="utf-8") as f:
            w_js = f.read()
        for content, name in ((p_html, "player"), (w_js, "designer")):
            self.assertIn("num-tnum", content, name)
            self.assertIn("tabular-nums", content, name)
            self.assertIn("minWidth", content, name)
        for content, name in ((p_html, "player"), (w_js, "designer")):
            self.assertIn("_colLockW", content, name)
            self.assertIn("tableLayout", content, name)

    def test_param_refname_markup(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            p_html = f.read()
        self.assertIn("param_refname", p_html)

        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_script.html"), encoding="utf-8") as f:
            w_html = f.read()
        self.assertIn("param_refname", w_html)
        self.assertIn("الاسم المرجعي للمدخل (param_refname)", w_html)

    def test_custom_doc_param_writer_and_compiler(self):
        import xml.etree.ElementTree as _ET
        from rml_python.compiler import RMLReportCompiler
        rml = _ET.Element("rml")
        ok = _v._write_doc_params_el(rml, _ET, [{
            "column": "v_calc_type",
            "label": "نوع الاحتساب",
            "type": "select",
            "op": "equals",
            "required": True,
            "param_refname": "V_1",
            "options": [
                {"name": "o1", "value": "نقد"},
                {"name": "o2", "value": "اجل"}
            ],
            "is_custom": True,
            "no_filter": True
        }])
        self.assertTrue(ok)
        xml_str = _ET.tostring(rml, encoding="utf-8").decode("utf-8")
        self.assertIn('is_custom="1"', xml_str)
        self.assertIn('no_filter="1"', xml_str)
        self.assertIn('name="o1"', xml_str)
        self.assertIn('value="نقد"', xml_str)

        comp = RMLReportCompiler(xml_text=xml_str)
        params = comp.doc_params()
        self.assertEqual(len(params), 1)
        p = params[0]
        self.assertEqual(p.column, "v_calc_type")
        self.assertEqual(p.param_refname, "V_1")
        self.assertTrue(p.is_custom)
        self.assertTrue(p.no_filter)
        self.assertEqual(p.options, [
            {"name": "o1", "value": "نقد"},
            {"name": "o2", "value": "اجل"}
        ])

        d = p.to_dict()
        self.assertTrue(d["is_custom"])
        self.assertTrue(d["no_filter"])
        self.assertEqual(d["options"], [
            {"name": "o1", "value": "نقد"},
            {"name": "o2", "value": "اجل"}
        ])

    def test_custom_doc_param_engine_no_sql_filter(self):
        from rml_python.compiler import RMLReportCompiler
        from rml_python.engine import RMLReportEngine
        xml = '''<rml>
          <rpt_metadata name="doc_custom_test" displayName="Custom Test" type="doc"/>
          <fields>
            <field name="id" table_source="t1" type="number"/>
          </fields>
          <columns>
            <column id="1" name="id" alias="رقم" expr="id"/>
            <column id="2" name="mode_display" alias="الوضع المعروض" expr="IF(@V_1 = o1, 'نقداً', 'آجل')"/>
          </columns>
          <doc_params>
            <doc_param id="dp_1" column="bill_id" label="رقم الفاتورة" type="text" op="equals" required="1"/>
            <doc_param id="dp_2" column="v_mode" label="الوضع" type="select" op="equals" required="1"
                       param_refname="V_1" is_custom="1" no_filter="1">
              <option name="o1" value="نقد">نقد</option>
              <option name="o2" value="اجل">اجل</option>
            </doc_param>
          </doc_params>
        </rml>'''
        comp = RMLReportCompiler(xml_text=xml)
        eng = RMLReportEngine(comp, mock.Mock())

        no_flt = eng._doc_no_filter_keys()
        self.assertIn("v_mode", no_flt)
        self.assertIn("v_1", no_flt)
        self.assertIn("dp_2", no_flt)
        self.assertNotIn("bill_id", no_flt)

        filters = [
            {"field": "bill_id", "op": "equals", "value": "1001"},
            {"field": "v_mode", "op": "equals", "value": "o1", "refname": "V_1"}
        ]
        base_where, outer = eng._split_filters(filters, comp.columns())
        # v_mode must NOT be present in base_where or outer
        self.assertEqual(len(base_where), 1)
        self.assertEqual(base_where[0]["field"], "bill_id")

        # required missing check should see both as satisfied
        self.assertFalse(eng._doc_required_missing(filters))

        # expression expansion: @V_1 = o1 should auto-quote o1 and replace @V_1 with 'o1' -> 'o1' = 'o1'
        eng._active_filters = filters
        expanded = eng._rewrite_value_expr("IF(@V_1 = o1, 'نقداً', 'آجل')", {})
        self.assertIn("'o1' = 'o1'", expanded)
        self.assertNotIn("@V_1", expanded)

    def test_custom_doc_param_ui_integration(self):
        import os as _os
        base = _os.path.dirname(__file__)
        with open(_os.path.join(base, "templates", "report_player.html"), encoding="utf-8") as f:
            p_html = f.read()
        self.assertIn("options", p_html)
        self.assertIn("is_custom", p_html)
        self.assertIn("no_filter", p_html)
        self.assertIn("f.options", p_html)

        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_modal.html"), encoding="utf-8") as f:
            m_html = f.read()
        self.assertIn("createCustomDocParam()", m_html)
        self.assertIn("+ مدخل مخصص حر", m_html)

        with open(_os.path.join(base, "..", "rml_python", "shared",
                                "rml_wizard_script.html"), encoding="utf-8") as f:
            s_html = f.read()
            self.assertIn("createCustomDocParam", s_html)
            self.assertIn("updateWizardDocParamOptionRow", s_html)
            self.assertIn("addWizardDocParamOption", s_html)
            self.assertIn("خيارات صندوق الاختيار المخصصة", s_html)
            self.assertIn("الاسم (في التعبير)", s_html)
            self.assertIn("القيمة (المعروضة)", s_html)
            self.assertIn("مدخل مخصص للتعبيرات فقط", s_html)
            self.assertIn("is_custom", s_html)
            self.assertIn("no_filter", s_html)

