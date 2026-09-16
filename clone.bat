@echo off
REM clone.bat - refresh this folder from GitHub, then install + run.
REM Steps: [1] mkdir .urs-temp  [2] clone repo into it
REM        [3] sync temp -^> base (update existing / append new, never delete)
REM        [4] rmdir .urs-temp  [5] install.bat  [6] run.bat
REM Run it from the folder you want updated (that folder is the base).
setlocal EnableDelayedExpansion

set BASE=%CD%
REM normalize trailing backslash: a drive-root base (e.g. H:\) would otherwise
REM quoted paths need this (see :sync_go)
if "%BASE:~-1%"=="\" set "BASE=%BASE%."
set TEMP_DIR=%BASE%\.urs-temp
set REPO=https://github.com/Zeehub333/urs.git
REM drive-agnostic Git locations (no hardcoded drive; x86 var captured here -
REM %ProgramFiles(x86)% must never appear inside a paren block)
set GITDIR=%ProgramFiles%\Git\bin
set GITDIRX86=%ProgramFiles(x86)%\Git\bin

echo === Odex clone + sync ===
echo BASE: %BASE%
echo REPO: %REPO%
echo.

REM ---- git must exist ----
git --version >nul 2>&1
if errorlevel 1 (
  if exist "%GITDIR%\git.exe" (
    set "PATH=%GITDIR%;!PATH!"
  ) else if exist "%GITDIRX86%\git.exe" (
    set "PATH=%GITDIRX86%;!PATH!"
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
REM /XJ skips junctions (end-of-run ERROR 123).
REM Flags built in short lines: no line here
REM is long enough for any tool to wrap.
set RCOPY_FLAGS=/E /NJH /NJS /NDL
set RCOPY_FLAGS=%RCOPY_FLAGS% /NP /R:2
set RCOPY_FLAGS=%RCOPY_FLAGS% /W:2 /XJ
set RCOPY_FLAGS=%RCOPY_FLAGS% /XD .git
set RCOPY_FLAGS=%RCOPY_FLAGS% /XD .urs-temp
set RCOPY_FLAGS=%RCOPY_FLAGS% /XF app.config
set SYNCLOG=%TEMP%\urs-clone-sync.log
if exist "%SYNCLOG%" del "%SYNCLOG%" >nul 2>&1
set ROBOEXE=%SystemRoot%\System32\robocopy.exe
"%ROBOEXE%" "%TEMP_DIR%" "%BASE%" %RCOPY_FLAGS% /LOG:"%SYNCLOG%"
REM robocopy exit 0-7 = success; 8+ = failure
if not errorlevel 8 goto sync_ok
echo [X] sync failed - first errors:
findstr /I /C:" ERROR " "%SYNCLOG%" 2>nul
echo [i] full log: %SYNCLOG%
rmdir /s /q "%TEMP_DIR%" >nul 2>&1
pause >nul
exit /b 1
:sync_ok
git -C "%TEMP_DIR%" rev-parse --short HEAD > "%BASE%\VERSION.txt" 2>nul
echo [i] deployed commit:
type "%BASE%\VERSION.txt" 2>nul
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
if errorlevel 1 echo [--] warning: .urs-temp locked.

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
