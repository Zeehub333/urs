@echo off
REM ============================================================
REM  URS ZK Sync — سحب البصمات من الأجهزة إلى قاعدة urs (بدون BioTime)
REM  الاستخدام اليدوي: zk_sync.bat
REM  المجدولة: تُستدعى تلقائياً عبر "URS ZK Sync" في Task Scheduler
REM  السجل: zk_sync.log بجانب هذا الملف
REM ============================================================
setlocal
set ROOT=%~dp0
set VENV=%ROOT%odex\venv\Scripts\python.exe
if not exist "%VENV%" set VENV=python

echo [%date% %time%] ===== بدء المزامنة ===== >> "%ROOT%zk_sync.log"
"%VENV%" "%ROOT%zk_sync.py" --once >> "%ROOT%zk_sync.log" 2>&1
echo [%date% %time%] ===== انتهت (رمز %errorlevel%) ===== >> "%ROOT%zk_sync.log"
endlocal
