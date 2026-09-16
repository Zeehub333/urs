@echo off
REM db_checker - verify main data exists before launch.
REM Verdict: ERRORLEVEL 0 = READY [company + urs_local + FMLK tables present]
REM          ERRORLEVEL 1 = MISSING [caller should show the first-run wizard]
REM Standalone use: db_checker.bat
setlocal

set ROOT=%~dp0
set PY=
set APP_HOST=127.0.0.1
set APP_PORT=8004
set APP_SETTINGS=config.settings_local

if exist "%ROOT%app.config" (
  for /f "usebackq eol=# tokens=1* delims==" %%a in ("%ROOT%app.config") do (
    if /i "%%a"=="PYTHON" set PY=%%b
    if /i "%%a"=="HOST" set APP_HOST=%%b
    if /i "%%a"=="PORT" set APP_PORT=%%b
    if /i "%%a"=="SETTINGS" set APP_SETTINGS=%%b
  )
)
if not defined PY set PY=python
"%PY%" --version >nul 2>&1
if errorlevel 1 set PY=python

set VERDICT=READY
set MISSING=

REM sqlite backend with no db file yet = nothing to check
echo %APP_SETTINGS% | findstr /i "local" >nul
if not errorlevel 1 (
  if not exist "%ROOT%db_local.sqlite3" set MISSING=db-file
)

if not defined MISSING call :check_data

if defined MISSING (
  echo [db_checker] MISSING: %MISSING%
  echo [db_checker] %VERDICT%
  echo [db_checker] wizard: http://%APP_HOST%:%APP_PORT%/settings/setup/
  endlocal & exit /b 1
)
echo [db_checker] READY - main data present.
endlocal & exit /b 0

:check_data
"%PY%" "%ROOT%manage.py" shell --settings=%APP_SETTINGS% -c "from urs.views import _wizard_status_payload as p; d=p(); ok=[bool(d['has_company']), bool(d['tables_ready']), bool(d['urs_local'])] == [True, True, True]; print('READY' if ok else 'MISS company=%%s tables=%%s conn=%%s' %% [d['has_company'], d['tables_ready'], bool(d['urs_local'])])" > "%TEMP%\urs_dbcheck.txt" 2>nul
if errorlevel 1 (
  set MISSING=db-error
  set VERDICT=manage.py shell failed - migrations not applied?
  goto :eof
)
set /p VERDICT=<"%TEMP%\urs_dbcheck.txt"
del "%TEMP%\urs_dbcheck.txt" >nul 2>&1
echo %VERDICT% | findstr /b "READY" >nul
if errorlevel 1 (
  set MISSING=main-data
  goto :eof
)
set VERDICT=READY
goto :eof
