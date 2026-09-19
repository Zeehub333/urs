@echo off
REM Odex ERP - One-click launcher (Windows)
REM Reads interpreter + bind address from app.config [KEY=VALUE].
REM Offline-capable: SQLite fallback. For real Postgres, set HOST in config/settings.py
REM and run db_init.py manually.
REM First run with no app.config or no main data -> opens the setup wizard.
REM The wizard creates app.config (DB_* step) when it is missing.
setlocal

set ROOT=%~dp0

REM ---- defaults, used if app.config is missing keys ----
set PY=
set APP_HOST=0.0.0.0
set APP_PORT=8004
set APP_SETTINGS=config.settings_local

REM ---- load app.config (created by the setup wizard if missing) ----
set NOCONF=
if not exist "%ROOT%app.config" (
  set NOCONF=1
  echo [--] app.config missing - the setup wizard will create it on first run.
)
if exist "%ROOT%app.config" (
  for /f "usebackq eol=# tokens=1* delims==" %%a in ("%ROOT%app.config") do (
    if /i "%%a"=="PYTHON" set PY=%%b
    if /i "%%a"=="HOST" set APP_HOST=%%b
    if /i "%%a"=="PORT" set APP_PORT=%%b
    if /i "%%a"=="SETTINGS" set APP_SETTINGS=%%b
  )
)
if not defined PY set PY=%ROOT%odex\venv\Scripts\python.exe
REM URL host for browser/local probes (0.0.0.0 binds all but is not browsable)
set APP_URLHOST=%APP_HOST%
if "%APP_HOST%"=="0.0.0.0" set APP_URLHOST=127.0.0.1

REM ---- verify the interpreter actually runs, venv stub may be dead ----
"%PY%" --version >nul 2>&1
if errorlevel 1 (
  echo [^!] configured python does not run - falling back to system python.
  set PY=python
)
"%PY%" -c "import django" >nul 2>&1
if errorlevel 1 (
  echo [^!] Installing Django runtime ...
  "%PY%" -m pip install "Django>=4.1,<5.0" psycopg2-binary cryptography openpyxl reportlab arabic-reshaper python-bidi
  if errorlevel 1 (
    echo [X] pip install failed - check network/python and retry.
    pause >nul
    exit /b 1
  )
)
echo [ok] python: %PY%  /  settings: %APP_SETTINGS%  /  bind: %APP_HOST%:%APP_PORT%
"%PY%" -c "import django; print('     django', django.__version__)"

echo.
echo [1/4] migrate + sync 20 apps from odex/system
"%PY%" "%ROOT%manage.py" migrate --settings=%APP_SETTINGS%
if errorlevel 1 (
  echo [X] migrate failed.
  pause >nul
  exit /b 1
)
"%PY%" "%ROOT%manage.py" shell --settings=%APP_SETTINGS% -c "import json,pathlib; from urs.models import App; [App.objects.update_or_create(name=d.get('name'),defaults={'name_ar':d.get('ar',d.get('name')),'icon':d.get('icon','fa-cube'),'version':d.get('version','1.0.0'),'description':d.get('description',''),'category':d.get('category','General')}) for p in sorted(pathlib.Path('odex/system').glob('*/metadata.json')) for d in [json.loads(p.read_text(encoding='utf-8'))]]; print('apps =',App.objects.count())"

echo.
echo [2/4] Starting Django Home - http://%APP_HOST%:%APP_PORT%/  [autoreload ON]
start "Odex-Django-8004" /D "%ROOT%" "%PY%" manage.py runserver %APP_HOST%:%APP_PORT% --settings=%APP_SETTINGS%
timeout /t 4 >nul

echo [3/4] Starting static 375 components [8002] - http://127.0.0.1:8002/demo.html
start "Odex-Static-8002" /D "%ROOT%odex\web" "%PY%" -m http.server 8002
timeout /t 2 >nul

echo.
echo [4/4] Seeding default connection from app.conf [skips if no DB_*]
set TRIES=0
:seed_retry
"%PY%" "%ROOT%seed_conn.py" http://%APP_URLHOST%:%APP_PORT%
if errorlevel 2 goto seed_done
if not errorlevel 1 goto seed_done
set /a TRIES+=1
if %TRIES% GEQ 6 goto seed_done
echo [--] server not ready yet, retry %TRIES%/6 ...
timeout /t 5 >nul
goto seed_retry
:seed_done

REM NOTE: ports 8003/8005 [uvicorn rml/fmlk api] are legacy - players are proxied
REM inside Django now [MEMORY.md 2.3], so they are no longer started.

REM ---- main-data gate: no app.config / company / connection / tables -> setup wizard ----
call "%ROOT%db_checker.bat"
set DBCHECK=%errorlevel%
if defined NOCONF (
  set OPEN_URL=http://%APP_URLHOST%:%APP_PORT%/settings/setup/
  set OPEN_NOTE=FIRST RUN (no app.config) - opening the setup wizard ...
) else if %DBCHECK% neq 0 (
  set OPEN_URL=http://%APP_URLHOST%:%APP_PORT%/settings/setup/
  set OPEN_NOTE=FIRST RUN - opening the setup wizard ...
) else (
  set OPEN_URL=http://%APP_URLHOST%:%APP_PORT%/
  set OPEN_NOTE=opening home ...
)

echo.
echo ========================================
echo  Odex running (bind %APP_HOST%:%APP_PORT% - LAN reachable):
echo    Home [20 apps]            : http://%APP_URLHOST%:%APP_PORT%/
echo    Setup wizard [first run]  : http://%APP_URLHOST%:%APP_PORT%/settings/setup/
echo    Settings / Connections    : http://%APP_URLHOST%:%APP_PORT%/settings/
echo    375 Demo [static]         : http://127.0.0.1:8002/demo.html
echo ========================================
echo  Config: app.config   Checker: db_checker.bat
echo  NOTE: Postgres 172.16.10.101 unreachable here, so reports/forms show
echo  connection errors until a real DB is configured. UI + designers work.
echo  %OPEN_NOTE% Close the Odex-* windows to stop.
echo  Press any key to open the browser...
pause >nul
start %OPEN_URL%

endlocal
