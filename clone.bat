@echo off
REM clone.bat - refresh this folder from GitHub, then install + run.
REM Steps: [1] mkdir .urs-temp  [2] clone repo into it
REM        [3] sync temp -^> base (update existing / append new, never delete)
REM        [4] rmdir .urs-temp  [5] install.bat  [6] run.bat
REM Run it from the folder you want updated (that folder is the base).
setlocal EnableDelayedExpansion

set BASE=%CD%
REM normalize trailing backslash: a drive-root base (e.g. H:\) would otherwise
REM merge the backslash with the closing quote in "%BASE%" and corrupt robocopy args
if "%BASE:~-1%"=="\" set "BASE=%BASE%."
set TEMP_DIR=%BASE%\.urs-temp
set REPO=https://github.com/Zeehub333/urs.git

echo === Odex clone + sync ===
echo BASE: %BASE%
echo REPO: %REPO%
echo.

REM ---- git must exist ----
git --version >nul 2>&1
if errorlevel 1 (
  if exist "C:\Program Files\Git\bin\git.exe" (
    set "PATH=C:\Program Files\Git\bin;!PATH!"
  ) else (
    echo [X] git not found - install it first: winget install -e --id Git.Git
    pause >nul
    exit /b 1
  )
)

REM ---- [1] mkdir .urs-temp ----
if exist "%TEMP_DIR%" (
  echo [--] removing leftover .urs-temp ...
  rmdir /s /q "%TEMP_DIR%"
  if errorlevel 1 (
    echo [X] cannot clear .urs-temp - close programs using it.
    pause >nul
    exit /b 1
  )
)
mkdir "%TEMP_DIR%"
if errorlevel 1 (
  echo [X] cannot create .urs-temp.
  pause >nul
  exit /b 1
)

REM ---- [2] clone into it ----
echo [2/6] cloning %REPO% ...
git clone "%REPO%" "%TEMP_DIR%"
if errorlevel 1 (
  echo [X] clone failed - check network / URL.
  rmdir /s /q "%TEMP_DIR%" >nul 2>&1
  pause >nul
  exit /b 1
)

REM ---- [3] sync temp -^> base: update existing + append new, never delete ----
REM Excludes: .git (keep base history/remote), .urs-temp (avoid self-copy loop),
REM           app.config (per-server secrets - never overwritten by sync).
echo [3/6] syncing into base (update + append, no deletes) ...
echo     from: %TEMP_DIR%
echo     to:   %BASE%
if exist "%TEMP_DIR%\install.bat" goto sync_go
echo [X] clone looks empty - aborting before sync.
rmdir /s /q "%TEMP_DIR%" >nul 2>&1
pause >nul
exit /b 1
:sync_go
"%SystemRoot%\System32\robocopy.exe" "%TEMP_DIR%" "%BASE%" /E /NJH /NJS /NDL /NP /R:2 /W:2 /XD .git .urs-temp /XF app.config
REM robocopy exit 0-7 = success (1=new files, 3=some updates); 8+ = failure
if errorlevel 8 (
  echo [X] sync failed.
  rmdir /s /q "%TEMP_DIR%" >nul 2>&1
  pause >nul
  exit /b 1
)

REM ---- [4] rmdir .urs-temp ----
echo [4/6] cleaning temp ...
rmdir /s /q "%TEMP_DIR%"
if errorlevel 1 echo [--] warning: could not remove .urs-temp - delete it manually.

REM ---- [5] install.bat ----
echo [5/6] running install.bat ...
if not exist "%BASE%\install.bat" (
  echo [X] install.bat missing after sync - aborting.
  pause >nul
  exit /b 1
)
call "%BASE%\install.bat" /nopause
if errorlevel 1 (
  echo [X] install.bat failed - fix it, then run run.bat manually.
  pause >nul
  exit /b 1
)

REM ---- [6] run.bat ----
echo [6/6] running run.bat ...
if not exist "%BASE%\run.bat" (
  echo [X] run.bat missing after sync - aborting.
  pause >nul
  exit /b 1
)
call "%BASE%\run.bat"

endlocal
exit /b 0
