@echo off
REM ============================================================
REM  تسجيل مهمة المزامنة التلقائية في Task Scheduler (تشغيل كمسؤول)
REM  المهمة: "URS ZK Sync" — كل 30 دقيقة — zk_sync.bat بجانب هذا الملف
REM  للإزالة: schtasks /delete /tn "URS ZK Sync" /f
REM ============================================================
setlocal
set ROOT=%~dp0
schtasks /create /tn "URS ZK Sync" /tr "\"%ROOT%zk_sync.bat\"" /sc minute /mo 30 /f
if errorlevel 1 (
  echo [!] فشل التسجيل — شغّل هذا الملف كمسؤول (Run as administrator)
  pause
  exit /b 1
)
echo [OK] تم تسجيل المهمة — تحقق: schtasks /query /tn "URS ZK Sync"
echo      السجل: %ROOT%zk_sync.log
pause
endlocal
