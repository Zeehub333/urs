@echo off
REM install.bat - Odex cross-server venv installer (Windows).
REM Steps: [1] resolve system python (py / python / versioned)
REM        [2] show where-selection, first path = default execution
REM        [3] pip-freeze check, drop broken venv
REM        [4] create venv + install + verify + wire app.config
REM Safe to re-run anytime. Usage: install.bat [/nopause]
setlocal EnableDelayedExpansion

set ROOT=%~dp0
pushd "%ROOT%" 2>nul || (echo [X] cannot enter %ROOT% & exit /b 1)
set PY=
set PYVER=
set PYBASE=
set PYARGS=
set PYRUN=
set NOPAUSE=0
if /i "%~1"=="/nopause" set NOPAUSE=1

echo === Odex venv installer ===
echo ROOT: %ROOT%
echo.

REM ---- STEP 1: resolve system python ----
echo [1/5] resolving system python ...
call :try_candidate "py -3" py -3
if not defined PY call :try_candidate "python" python
if not defined PY call :try_candidate "python3" python3
if not defined PY call :try_candidate "python3.13" python3.13
if not defined PY call :try_candidate "python3.12" python3.12
if not defined PY call :try_candidate "python3.11" python3.11
if not defined PY call :try_candidate "python3.10" python3.10

if not defined PY (
  echo [--] no Python 3.10+ on PATH - trying winget ...
  where winget >nul 2>&1
  if not errorlevel 1 (
    winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements
    call :try_candidate "py -3" py -3
    if not defined PY call :try_candidate "python" python
  )
)
if not defined PY (
  echo [X] Python 3.10+ not found and auto-install failed.
  echo     Install manually, then re-run install.bat:
  echo       winget install -e --id Python.Python.3.13
  echo     or download from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH".
  call :maybe_pause
  exit /b 1
)
echo [ok] system python: %PY% ^(%PYVER%^)

REM split base exe + extra args (e.g. "py -3" -^> base=py args=-3)
for /f "tokens=1*" %%b in ("%PY%") do (
  set "PYBASE=%%b"
  set "PYARGS=%%c"
)

REM ---- STEP 2: where-selection, first path wins ----
echo.
echo [2/5] interpreter locations (first path = default execution) ...
where "%PYBASE%" 2>nul
if errorlevel 1 (
  echo [--] "where %PYBASE%" returned nothing - using "%PY%" from PATH search order.
) 
set PYFULL=
for /f "delims=" %%p in ('where "%PYBASE%" 2^>nul') do (
  if not defined PYFULL set "PYFULL=%%p"
)
if defined PYFULL (
  echo [*] default execution: %PYFULL% %PYARGS%
  if defined PYARGS ( set "PYRUN="%PYFULL%" %PYARGS%" ) else ( set "PYRUN="%PYFULL%"" )
  REM guard: first PATH hit can be a stub (e.g. WindowsApps alias) - fall back to bare command
  %PYRUN% --version >nul 2>&1
  if errorlevel 1 (
    echo [--] pinned path does not run - falling back to PATH-ordered "%PY%".
    set "PYRUN=%PY%"
  )
) else (
  set "PYRUN=%PY%"
  echo [*] default execution: %PYRUN% (unresolved, PATH order applies)
)

REM ---- STEP 3: pip-freeze check, drop broken venv ----
set VPY=%ROOT%odex\venv\Scripts\python.exe
echo.
echo [3/5] checking existing venv ...
if not exist "%VPY%" (
  echo [--] no venv yet at odex\venv.
) else (
  "%VPY%" --version >nul 2>&1
  if errorlevel 1 (
    echo [--] venv interpreter dead - removing odex\venv ...
    rmdir /s /q "%ROOT%odex\venv"
    if errorlevel 1 (
      echo [X] cannot remove venv - close programs using it, then re-run.
      call :maybe_pause
      exit /b 1
    )
  ) else (
    "%VPY%" -m pip freeze >nul 2>&1
    if errorlevel 1 (
      echo [--] venv pip broken (freeze failed) - removing odex\venv ...
      rmdir /s /q "%ROOT%odex\venv"
      if errorlevel 1 (
        echo [X] cannot remove venv - close programs using it, then re-run.
        call :maybe_pause
        exit /b 1
      )
    ) else (
      set NFREEZE=0
      for /f %%n in ('"%VPY%" -m pip freeze 2^>nul') do set /a NFREEZE+=1
      echo [ok] existing venv runs - !NFREEZE! packages frozen.
    )
  )
)

REM ---- STEP 4: create venv if missing ----
echo.
if not exist "%VPY%" (
  echo [4/5] creating venv with: %PYRUN% ...
  %PYRUN% -m venv "%ROOT%odex\venv"
  if errorlevel 1 (
    echo [--] standard venv failed - retrying without pip + ensurepip ...
    %PYRUN% -m venv --without-pip "%ROOT%odex\venv"
    if errorlevel 1 (
      echo [X] venv creation failed. On some systems install the venv package first.
      call :maybe_pause
      exit /b 1
    )
    "%VPY%" -m ensurepip --upgrade
    if errorlevel 1 (
      echo [X] ensurepip failed - venv has no pip.
      call :maybe_pause
      exit /b 1
    )
  )
  echo [ok] venv created.
) else (
  echo [4/5] venv present - skipping creation.
)

REM ---- STEP 5: install + verify + wire ----
echo.
echo [5/5] upgrading pip ...
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 (
  echo [X] pip upgrade failed - check network and retry install.bat.
  call :maybe_pause
  exit /b 1
)

echo.
echo [5/5] installing requirements (takes a few minutes) ...
set REQS=
if exist "%ROOT%odex\requirements.txt" set REQS=%REQS% -r "%ROOT%odex\requirements.txt"
if exist "%ROOT%rml_python\requirements.txt" set REQS=%REQS% -r "%ROOT%rml_python\requirements.txt"
if exist "%ROOT%fmlk_engine\requirements.txt" set REQS=%REQS% -r "%ROOT%fmlk_engine\requirements.txt"
if exist "%ROOT%permissions_engine\requirements.txt" set REQS=%REQS% -r "%ROOT%permissions_engine\requirements.txt"
if "%REQS%"=="" (
  echo [--] no requirements files found - installing runtime extras only.
)
"%VPY%" -m pip install %REQS% cryptography openpyxl reportlab arabic-reshaper python-bidi
if errorlevel 1 (
  echo [X] pip install failed - check network and retry install.bat.
  call :maybe_pause
  exit /b 1
)

echo.
echo [5/5] verifying ...
"%VPY%" -c "import django, psycopg2, oracledb, pyodbc, fastapi, lxml, reportlab, openpyxl, cryptography; from zk import ZK; print('venv ok, django', django.__version__)"
if errorlevel 1 (
  echo [X] verify failed - a required package did not import.
  call :maybe_pause
  exit /b 1
)

echo.
echo [5/5] wiring app.config ...
set SEEDROOT=%ROOT%
"%VPY%" manage.py shell --settings=config.settings_local -c "import pathlib,os; from config.dbconf import write_appconf; print('app.config:', write_appconf({'PYTHON': str(pathlib.Path(os.environ.get('SEEDROOT','')) / 'odex' / 'venv' / 'Scripts' / 'python.exe')}))"
if errorlevel 1 (
  echo [--] app.config wiring skipped - non-fatal - set PYTHON manually.
)

echo.
echo ========================================
echo  DONE - venv ready: odex\venv
echo  interpreter: %PYRUN%
echo  Run run.bat to launch.
popd
if "%NOPAUSE%"=="0" pause >nul
endlocal
exit /b 0

REM ============ subroutines ============
:try_candidate
REM %1 = label (ignored), %2.. = command + args. Needs Python ^>= 3.10.
set "_LBL=%~1"
shift
set "_CMD=%1 %2 %3 %4"
REM trim trailing spaces via for
for /f "tokens=*" %%t in ("%_CMD%") do set "_CMD=%%t"
:trim_loop
if "!_CMD:~-1!"==" " (
  set "_CMD=!_CMD:~0,-1!"
  goto trim_loop
)
set "_V="
for /f "tokens=2" %%v in ('%_CMD% --version 2^>^&1') do (
  if not defined _V set "_V=%%v"
)
if not defined _V goto :eof
for /f "tokens=1,2 delims=." %%a in ("%_V%") do (
  set "_MAJ=%%a"
  set "_MIN=%%b"
)
REM numeric major check without delayed-expansion pitfalls: major must be exactly 3
if not "%_MAJ%"=="3" goto :eof
REM minor must be numeric and ^= 10
set "_MIN=!_MIN!"
for /f "delims=0123456789" %%d in ("!_MIN!") do goto :eof
if !_MIN! geq 10 (
  set "PY=%_CMD%"
  set "PYVER=%_V%"
  echo     found %_LBL%: Python %_V%
)
goto :eof

:maybe_pause
if "%NOPAUSE%"=="0" pause >nul
goto :eof
