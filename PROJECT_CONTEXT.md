# URS2 PROJECT CONTEXT (LLM window)

> Root: `C:\Users\admin\Documents\Default Project\urs2` (run all paths relative to it). Django 4.1 + RTL Arabic ERP builder. DB: postgres `172.16.10.101:5432/urs/postgres/postgres` (`config/settings.py:79-88`). Run: `python manage.py check` + `runserver 127.0.0.1:8004`. Source of truth details: `MEMORY.md` (build log), `USER_GUIDE_AR.md` (user manual).

## 1. TREE (pruned, 541 files total)
```
config/{settings,urls,wsgi,asgi}.py manage.py db_init.py
urs/{models:570L,views:5613L/171funcs,admin,apps,iot_sync}.py
urs/templates/{base,home,app_detail,forms_player:185KB,report_player:223KB,report_designer,forms_designer,data_diagram,dml_designer,dml_designer_script,settings,setup,my_docs,my_reports,dashboards}.html
rml_python/{compiler,engine:250KB,oracle_engine,namespaces,rulevars,pipeline,api}.py examples/emp_report.rml
fmlk_engine/{compiler,engine,field_types,api}.py examples/hr_form.fmlk
cml_engine/{compiler,engine}.py  dml_python/{compiler,engine,pdf,xlsx}.py
permissions_engine/{engine,api}.py
odex/engines/{oracle,postgres,sqlserver,zk}.py odex/{main,ui_engine}.py odex/web/{server,ui_engine,binding}.py
odex/web/components/{buttons,charts,fields,grids,views,tables,search}/ (~375 JS: BarChart1 DataTable1 DataGrid1 TextField1...)
odex/system/*/metadata.json (21 apps, 20 metadata.json — human_resources/ HAS NONE)
odex/system/{sales/{sales_report,invoice_report.rml,documents/invoice*.dml},purchases/pri_report.rml,human_resources/{emp_logs,fp_logs,fp_report.rml},settings/{*.cml,*.fmlk,modals/rml_wizard_*}}
my_docs/index.json + *.html.enc  drivers/admin.json  zk_sync.py open_doc.py
```
Live doc counts: 6×.rml 9×.fmlk 4×.cml 3×.dml. System apps (21): accounting apps budget_management contracts data_protection field_service fixed_assets fleet helpdesk human_resources inventory invoicing maintenance manufacturing project_management purchases quality_management sales self_service settings treasury.

## 2. MODELS `urs/models.py`
Preset(type,app,name,filterJSON) App(name unique,name_ar,icon,category) Report(name,rml_path) FilePermission(file_name,file_type rml/fml/fmlk,owner,aces_json,inherit) UserDriver(user 1-1,driver_json,rml/fml_count) Country(code,name,is_default single) City(country FK) Currency(code,rate,is_default) Company(code,country/city/currency FK,fiscal_year=2026) Branch(company FK,code,city,schema_name auto `cmp_main_year` via build_schema_name:184) Connection(name,host=172.16.10.101,port,user,password,instance,engine oracle/sqlserver/zk/mysql/postgres,conn_type database/iot,is_local,is_queryable,schema,devices JSON,endpoint att/users/attendance,last_check_*) IoTMirror(connection→iot,endpoint,local_connection→pg,table_name `iot_<eng>_<ep>`,auto_sync,interval,status,watermarks JSON).

## 3. URLS `config/urls.py` (all urs_views)
Pages: `/ home` `app/<app>/` `app/<app>/form/<fml>/` `report/<rml>/` `report-designer|forms-designer|data-diagram|dml-designer` `settings/ setup/ settings/<scope>/<app>/<file>/` `my-reports|dashboards|my-docs/`. APIs: `api/apps/ <app>/files|rules|policy_save|delete|fml/create|rml/create|rml/update|dml/*|models/design|migrate|convert-cml|sync|modals/*` `api/connections/ create|test|stats|<id>/update|delete|test|<id>/tables|<table>/columns|preview|fks` `api/iot/endpoints|fetch|mirror|mirrors|mirror/status|sync` `api/cml/metadata|validate|save|values + api/settings + api/setup/create` `api/fmlk/metadata|lookup|options|lookups|field-types|preview_insert|create|update|delete|records|record|branch|branch/save|sql-functions|data-types` `api/rml/metadata|preview|execute|detail|detail-search|distinct|groups|joins` `api/presets/` `api/my-docs/list|save|delete|<id>/|<id>/thumb` `api/dml/metadata|render|xlsx|pdf|doc-pdf`. Auth: only `admin/` gated; 0×login_required, 44×csrf_exempt, onboarding gate `_gate_redirect:23`.

## 4. VIEWS `urs/views.py` index (use grep for bodies)
_gate/_load_app_context/_find_fml|rml_path, home:221 my_reports:273 dashboards:280, my_docs crypt _keys:296 encrypt:318 decrypt:335 + api_my_docs_*:433-588, app_detail:624 form_player:646 report_player:687 designers:738-764 modal:897, _fmlk_resolve_db:832 metadata:878 lookup:935 records:1532 branch:1560, _build_db_engine:1669 _rml_get_pipeline:1723 rml_metadata:1899 preview:1914 execute:2367 joins:2048 detail:2060 groups:2121 rules _scan:2150 save:2170 api:2210 presets:1994-2034, create_fml:2413 _render_rml_xml:2778 create_rml:3085 update_rml:3266, connections list:3344 create:3355 test:3625 stats:3862 tables:4201, iot fetch:3696 mirror:3722 sync:3807, cml_scan:4646 setup_create:4850 settings:4827-4947 validate:4963 save:4983, models design:1009 migrate:1244 convert:1361 sync:1427, dml list:5172 suggest:5196 metadata:5215 create:5240 xlsx:5314 pdf:5437 doc-pdf:5494 render:5577. PostgresEngine:52 `:bind→%(bind)s`, Asia/Aden TZ.

## 5. RML reports `rml_python/`
XML: `<rpt_metadata name displayName category connection schema namespace type master|detail|doc distinct>` `<fields><field name(=DBcol) type conn_id table_source>>` `<columns><column alias expr refTable+refFk+refDisplay join_type one_to_one|one_to_many col_type direct|computed|fk_lookup|aggregated connection_id where_clause icon distinct>>` `<rml_connections><connection id connection_id>>` `<rml_chart><chart type bar|line|pie x y>>` `<rules><rule><sources><variables type text|number|date|time|expression|boolean|choice><policies><policy priority is_default><match><values>` `<detail table master detail rel_type>` `<general_where>` `<groups><group level>` `<links><link from_table from_col to_table to_col rel_type>>` `<doc_template>{{alias}}>` `<table_opts>`. Expr: `[name] [table.col] [conn.table.col] $rule.var$` validated `namespaces.py:189` + `rulevars.expand:149`→`(CASE WHEN [f] IN(..) THEN .. ELSE .. END)`. SQL `engine.py`: FROM T0 + LEFT JOIN same-DB (_infer_join_key:2556), cross-DB python merge chunks 500, fk one_to_one subselect / one_to_many LISTAGG|STRING_AGG:909, filters equals|contains|between|inyear|inmonth + Arabic TRANSLATE:427 + fk EXISTS:534, `where_clause→CASE WHEN:763`, PG LIMIT/OFFSET vs Oracle OFFSET FETCH:3489, DISTINCT ON:3782. Payload `{activeTable,filters:[{field|column,op,value}],columnFilters,sort,page,pageSize:50|all,groupBy,summary}` → `{rows,total,sql,params,columns,metadata,charts,groups}`. Ex: `examples/emp_report.rml`, `sales/sales_report.rml`.

## 6. FMLK forms `fmlk_engine/`
XML: `<fml_metadata name displayName category connection schema table> <tabs><tab> <fields><field id name alias dataType inputType required nullable editable readonly primary_key default formula placeholder visibleIf(==/!= &&/||) tab category position refTable refFk refDisplay icon destination main|detail><validation pattern min_length max_length min max><options><option value color>>` `<details><detail table master detail rel_type>>`. 24 types `field_types.py:63-135`: text textarea number datetime boolean select list-input + cascading_select multiselect lookup status datamodal subform grid reorder tree badges polymorphic livesync autopopulate calculated matrix restricted_tags cascade_grid. Engine `engine.py:98 validate` + `lookup:165` + `_build_insert:288 (RETURNING pk)` + CRUD:435. Ex: `examples/hr_form.fmlk`.

## 7. CML DML PERMS
CML: `<cml_metadata scope system|app>` `<controls><control id name alias inputType dataType required default category refTable validation options>>` `<rules><rule type default|required|readonly|hidden|unique|min|max|regex|equals target message value>>`. `CMLEngine validate:35` + `*.values.json` save `views.py:4983`. Files: `settings/{companies,branches,users,permissions}.cml`.
DML: `<dml paper A4|A5 orientation margin> <variables><var type>> <header|sections><section><tableview source player:main|detail|static variant grid|striped|plain|compact layout rows|grid2 total_row count_row>> <footer>` + `%%field%%/[var]` (`compiler.py:239`). Export: xlsx RTL zebra `xlsx.py:286`, pdf reportlab+reshaper `pdf.py:64`, preview `engine.py:311`. System vars today/company/branch/current_user.
Perms: `ACE{principal user:X|role:X|*,actions view|edit|delete|create|execute|export|share|admin,effect allow|deny}` deny-wins `engine.py:59`; sidecar `<file>.perm.json:103`; driver hides aggregated w/o export:305.

## 8. DRIVERS + IOT + FRONT
Drivers `odex/engines/`: oracle(oracledb :name "id" OFFSET FETCH) postgres(psycopg2 %s LIMIT) sqlserver(pyodbc ? [id]) zk(pyzk get_users|get_attendance→users|attendance|att tables). No mysql.py (declared only). RML runtime `rml_python/oracle_engine.py` + pg sniff `type(db).__name__`.
IoT mirror (not live query): ZK UNION ALL → pg `iot_zk_att(device_ip,emp_no,punch_ts UNIQUE)` `urs/iot_sync.py:19-146` BATCH1000 watermark max(ts) `run_mirror_sync:182` thread `start_async:313` sweeper `324 (UrsConfig.ready)`. Legacy `zk_sync.py→public.zk_attendance`. Reports `human_resources/emp_logs.rml (fp_calc rule→دخول/خروج DISTINCT ON)` + `fp_logs.rml`.
Front: all extend `base.html` (topbar h-11, connStatusPill, notify(), goBackSmart, dark localStorage). report_player smart search `20260911→date range `-` between `,`=OR `&`=AND; forms_player toolbar CRUD+branch grids+docPrintPanel; dashboards `localStorage:dash_charts + /api/rml/groups`; my-docs MDE1 `MAGIC MDE1|salt16|iv16|AES256CBC|hmac32 PBKDF2(SECRET_KEY,200k)` `views.py:296 open_doc.py:31` (Fernet legacy auto-upgrade).

## 9. KNOWN GAPS (do not re-introduce)
countries table missing breaks settings/01_02; table explore pg-only; pageSize=1000 client-side; SECRET hardcoded DEBUG True HOST *; no view auth; MEMORY claims 50 HR .fmlk but now 0 in HR dir.
