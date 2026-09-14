@echo off
rem ============================================================
rem  AI Traffic Light - stop the running instance
rem
rem  Normally you would use the tray icon's "exit" menu. This is
rem  the escape hatch for when the tray icon is hidden, the window
rem  is off-screen, or you changed code and need a clean restart.
rem
rem  All the work is in tools\stop.py, which stops the process by
rem  the PID recorded in %LOCALAPPDATA%\ai-traffic-light\light.pid.
rem  (Matching on the command line was tried first and was wrong
rem   twice over: it missed processes started with a relative path,
rem   and it matched the calling shell's own command line.)
rem ============================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto nodeps
".venv\Scripts\python.exe" "tools\stop.py"

rem Brief delay so a double-clicked window stays readable.
rem (Not `timeout`: under Git Bash that is GNU timeout, which
rem  rejects the Windows /t flag and prints an error.)
ping -n 3 127.0.0.1 >nul
exit /b 0

:nodeps
echo.
echo   .venv not found - nothing to stop.
echo.
pause
exit /b 1
