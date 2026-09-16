@echo off
REM push.bat - stage all (respects .gitignore), commit, push.
REM Usage: push.bat [commit message] [/nopause]
REM   push.bat
REM   push.bat "fix lazy loading"
setlocal EnableDelayedExpansion

set ROOT=%~dp0
pushd "%ROOT%" 2>nul || (echo [X] cannot enter %ROOT% & exit /b 1)
set NOPAUSE=0
set MSG=
for %%a in (%*) do (
  if /i "%%~a"=="/nopause" ( set "NOPAUSE=1" ) else (
    if defined MSG ( set "MSG=!MSG! %%~a" ) else ( set "MSG=%%~a" )
  )
)
if not defined MSG set MSG=update %date% %time%

REM ---- find git ----
set GIT=git
git --version >nul 2>&1
if errorlevel 1 (
  if exist "C:\Program Files\Git\bin\git.exe" (
    set GIT="C:\Program Files\Git\bin\git.exe"
  ) else (
    echo [X] git not found - install it first: winget install -e --id Git.Git
    if "%NOPAUSE%"=="0" pause >nul
    exit /b 1
  )
)

REM ---- must be a repo with a remote ----
%GIT% rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
  echo [X] not a git repo - run inside the project folder.
  if "%NOPAUSE%"=="0" pause >nul
  exit /b 1
)
%GIT% remote get-url origin >nul 2>&1
if errorlevel 1 (
  echo [X] no "origin" remote - add one first:
  echo     git remote add origin https://github.com/USER/REPO.git
  if "%NOPAUSE%"=="0" pause >nul
  exit /b 1
)

echo === push: %MSG% ===
%GIT% add -A
if errorlevel 1 (
  echo [X] git add failed.
  if "%NOPAUSE%"=="0" pause >nul
  exit /b 1
)
%GIT% diff --cached --quiet
if errorlevel 1 (
  %GIT% commit -m "%MSG%"
  if errorlevel 1 (
    echo [X] commit failed.
    if "%NOPAUSE%"=="0" pause >nul
    exit /b 1
  )
) else (
  echo [--] nothing to commit - pushing current branch as-is.
)

REM push current branch; set upstream on first push
for /f "tokens=*" %%b in ('%GIT% branch --show-current 2^>nul') do set BR=%%b
if not defined BR set BR=main
%GIT% rev-parse --abbrev-ref --symbolic-full-name @{u} >nul 2>&1
if errorlevel 1 (
  %GIT% push -u origin %BR%
) else (
  %GIT% push
)
if errorlevel 1 (
  echo [X] push failed - check network / credentials.
  if "%NOPAUSE%"=="0" pause >nul
  exit /b 1
)

echo.
echo DONE - pushed %BR% to origin.
popd
if "%NOPAUSE%"=="0" pause >nul
endlocal
exit /b 0
