@echo off
REM setup.bat - Odex/URS NEW-SERVER guaranteed setup (Windows).
REM Guarantees, in order (each step verified before the next):
REM   [1] admin rights (needed for firewall + scheduled task; warns and continues without them)
REM   [2] git present (winget fallback)
REM   [3] code present (clone from GitHub when install.bat is missing here)
REM   [4] Python venv + requirements via install.bat /nopause
REM   [5] Django check + migrate on settings_local (SQLite, works offline)
REM   [6] firewall inbound rule for the app port (default 8004, override: setup.bat 8005)
REM   [7] Oracle Instant Client note (thin mode works without it) + ODBC driver check
REM   [8] optional ZK-sync scheduled task (every 30 min, admin only)
REM   [9] summary + next step (run.bat). Use setup.bat /run to launch run.bat at the end.
REM Safe to re-run anytime. Secrets are NEVER copied: app.config stays per-server
REM (the setup wizard creates it on first run).
setlocal EnableDelayedExpansion

set ROOT=%~dp0
pushd "%ROOT%" 2>nul || (echo [X] cannot enter %ROOT% & exit /b 1)
set PORT=8004
set DORUN=0
set NOPAUSE=0
if not "%~1"=="" (
  echo "%~1" | findstr /r "^\"*[0-9][0-9]*\"*$" >nul
  if not errorlevel 1 (
    for /f "delims=" %%p in ("%~1") do set "PORT=%%~p"
  ) else if /i "%~1"=="/run" (
    set DORUN=1
  ) else if /i "%~1"=="/nopause" (
    set NOPAUSE=1
  )
)
if not "%~2"=="" (
  if /i "%~2"=="/run" set DORUN=1
  if /i "%~2"=="/nopause" set NOPAUSE=1
)
set REPO=https://github.com/Zeehub333/urs.git

echo ========================================
echo  Odex/URS new-server setup
echo  ROOT: %ROOT%
echo  PORT: %PORT%
echo ========================================
echo.

REM ---- [1] admin rights ----
echo [1/9] checking admin rights ...
net session >nul 2>&1
if errorlevel 1 (
  set ISADMIN=0
  echo [--] NOT running as administrator.
  echo     Firewall rule + scheduled task will be SKIPPED.
  echo     (Right-click setup.bat -^> Run as administrator to enable them.)
) else (
  set ISADMIN=1
  echo [ok] running as administrator.
)
echo.

REM ---- [2] git ----
echo [2/9] checking git ...
git --version >nul 2>&1
if errorlevel 1 (
  if exist "%ProgramFiles%\Git\bin\git.exe" (
    set "PATH=%ProgramFiles%\Git\bin;!PATH!"
    echo [--] git found at %ProgramFiles%\Git\bin (session PATH only).
  ) else (
    echo [--] git missing - trying winget ...
    where winget >nul 2>&1
    if not errorlevel 1 (
      winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    )
    git --version >nul 2>&1
    if errorlevel 1 (
      if exist "%ProgramFiles%\Git\bin\git.exe" (
        set "PATH=%ProgramFiles%\Git\bin;!PATH!"
      )
    )
  )
)
git --version >nul 2>&1
if errorlevel 1 (
  echo [X] git still missing - install it, then re-run setup.bat:
  echo     winget install -e --id Git.Git
  call :maybe_pause
  exit /b 1
)
for /f "tokens=3" %%v in ('git --version 2^>^&1') do echo [ok] git %%v
echo.

REM ---- [3] code ----
echo [3/9] checking code ...
if not exist "%ROOT%install.bat" (
  echo [--] install.bat not here - cloning fresh copy ...
  if exist "%ROOT%.urs-temp" rmdir /s /q "%ROOT%.urs-temp" >nul 2>&1
  mkdir "%ROOT%.urs-temp" >nul 2>&1
  git clone "%REPO%" "%ROOT%.urs-temp"
  if errorlevel 1 (
    echo [X] clone failed - check network / URL: %REPO%
    rmdir /s /q "%ROOT%.urs-temp" >nul 2>&1
    call :maybe_pause
    exit /b 1
  )
  if not exist "%ROOT%.urs-temp\install.bat" (
    echo [X] clone looks empty - aborting.
    rmdir /s /q "%ROOT%.urs-temp" >nul 2>&1
    call :maybe_pause
    exit /b 1
  )
  set ROBOEXE=%SystemRoot%\System32\robocopy.exe
  "%ROBOEXE%" "%ROOT%.urs-temp" "%ROOT%" /E /NJH /NJS /NDL /NP /R:2 /W:2 /XJ /XD .git .urs-temp /XF app.config >nul
  if errorlevel 8 (
    echo [X] copy failed.
    rmdir /s /q "%ROOT%.urs-temp" >nul 2>&1
    call :maybe_pause
    exit /b 1
  )
  rmdir /s /q "%ROOT%.urs-temp" >nul 2>&1
  git -C "%ROOT%" rev-parse --short HEAD > "%ROOT%VERSION.txt" 2>nul
  echo [ok] code cloned.
) else (
  echo [ok] code present.
)
if not exist "%ROOT%manage.py" (
  echo [X] manage.py missing - folder is not an Odex checkout.
  call :maybe_pause
  exit /b 1
)
echo.

REM ---- [4] venv + requirements ----
echo [4/9] installing runtime (venv + requirements) ...
if not exist "%ROOT%install.bat" (
  echo [X] install.bat missing after step 3 - aborting.
  call :maybe_pause
  exit /b 1
)
call "%ROOT%install.bat" /nopause
if errorlevel 1 (
  echo [X] install.bat failed - fix it, then re-run setup.bat.
  call :maybe_pause
  exit /b 1
)
set VPY=%ROOT%odex\venv\Scripts\python.exe
if not exist "%VPY%" set VPY=python
"%VPY%" --version >nul 2>&1
if errorlevel 1 (
  echo [X] venv python does not run.
  call :maybe_pause
  exit /b 1
)
echo [ok] runtime ready: %VPY%
echo.

REM ---- [5] django check + migrate (SQLite, offline-safe) ----
echo [5/9] verifying Django + migrating local DB ...
"%VPY%" "%ROOT%manage.py" check --settings=config.settings_local
if errorlevel 1 (
  echo [X] manage.py check failed.
  call :maybe_pause
  exit /b 1
)
echo [ok] django check passed.
"%VPY%" "%ROOT%manage.py" migrate --settings=config.settings_local
if errorlevel 1 (
  echo [X] migrate failed.
  call :maybe_pause
  exit /b 1
)
echo [ok] local DB migrated.
for %%d in (logs workspace_1 workspace_2) do (
  if not exist "%ROOT%%%d" mkdir "%ROOT%%%d" >nul 2>&1
)
echo.

REM ---- [6] firewall ----
echo [6/9] firewall inbound rule for TCP %PORT% ...
if "%ISADMIN%"=="0" (
  echo [--] skipped (no admin). To open manually later:
  echo     netsh advfirewall firewall add rule name="Odex-URS-%PORT%" dir=in action=allow protocol=TCP localport=%PORT%
) else (
  netsh advfirewall firewall show rule name="Odex-URS-%PORT%" >nul 2>&1
  if not errorlevel 1 (
    echo [--] rule Odex-URS-%PORT% already exists.
  ) else (
    netsh advfirewall firewall add rule name="Odex-URS-%PORT%" dir=in action=allow protocol=TCP localport=%PORT% >nul
    if errorlevel 1 (
      echo [--] warning: could not add firewall rule - open TCP %PORT% manually.
    ) else (
      echo [ok] firewall rule Odex-URS-%PORT% added.
    )
  )
)
echo.

REM ---- [7] Oracle client note + ODBC driver ----
echo [7/9] database drivers ...
set OCIFOUND=0
where oci.dll >nul 2>&1
if not errorlevel 1 set OCIFOUND=1
if exist "%ROOT%oracle_config\oci.dll" set OCIFOUND=1
if "%OCIFOUND%"=="1" (
  echo [ok] Oracle Instant Client (oci.dll) found - thick mode available.
) else (
  echo [--] no Oracle Instant Client found - THIN mode still works out of the box.
  echo     Only install Instant Client if a connection needs thick mode,
  echo     then restart the server so PATH picks up oci.dll.
)
reg query "HKLM\SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers" /v "ODBC Driver 18 for SQL Server" >nul 2>&1
if not errorlevel 1 (
  echo [ok] ODBC Driver 18 for SQL Server present.
) else (
  reg query "HKLM\SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers" /v "ODBC Driver 17 for SQL Server" >nul 2>&1
  if not errorlevel 1 (
    echo [ok] ODBC Driver 17 for SQL Server present.
  ) else (
    echo [--] no MS ODBC Driver 17/18 found - SQL Server connections need it.
    echo     Trying winget install (best effort) ...
    where winget >nul 2>&1
    if not errorlevel 1 (
      winget install -e --id Microsoft.ODBCDriver.18ForSQLServer --silent --accept-package-agreements --accept-source-agreements
      if errorlevel 1 echo [--] winget install failed - download manually: https://aka.ms/downloadodbcdriverforsqlserver
    ) else (
      echo     winget missing - download manually: https://aka.ms/downloadodbcdriverforsqlserver
    )
  )
)
echo.

REM ---- [8] ZK-sync scheduled task (optional, harmless without devices) ----
echo [8/9] ZK-sync scheduled task (every 30 min) ...
if "%ISADMIN%"=="0" (
  echo [--] skipped (no admin). Register later with: zk_sync_schedule.bat
) else (
  if not exist "%ROOT%zk_sync.bat" (
    echo [--] zk_sync.bat not found - skipped.
  ) else (
    schtasks /create /tn "URS ZK Sync" /tr "\"%ROOT%zk_sync.bat\"" /sc minute /mo 30 /f >nul 2>&1
    if errorlevel 1 (
      echo [--] warning: could not register task - run zk_sync_schedule.bat as admin later.
    ) else (
      echo [ok] task "URS ZK Sync" registered (log: zk_sync.log).
    )
  )
)
echo.

REM ---- [9] summary ----
echo [9/9] final verification ...
"%VPY%" -c "import django, psycopg2, oracledb, spacy; print('     runtime imports ok - django', django.__version__, '- spacy', spacy.__version__)"
if errorlevel 1 (
  echo [X] runtime import check failed.
  call :maybe_pause
  exit /b 1
)
echo.
echo ========================================
echo  SETUP COMPLETE - Odex/URS ready.
echo    Folder : %ROOT%
echo    Python : %VPY%
echo    URL    : http://localhost:%PORT%/
echo    Next   : run.bat  (first run opens the setup wizard in the browser -
echo             it creates app.config + DB connection for THIS server)
echo  NOTES:
echo    - app.config is per-server and never copied by clone/sync.
echo    - Postgres/Oracle/SQLServer are configured in the wizard or
echo      Settings / Connections - SQLite works with zero config.
echo ========================================
if "%DORUN%"=="1" (
  echo.
  echo Starting run.bat ...
  call "%ROOT%run.bat"
) else (
  echo  Run: setup.bat /run   to finish with an auto-launch.
  call :maybe_pause
)
popd
endlocal
exit /b 0

:maybe_pause
if "%NOPAUSE%"=="0" pause >nul
goto :eof
