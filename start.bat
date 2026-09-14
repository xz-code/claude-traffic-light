@echo off
rem ============================================================
rem  AI Traffic Light - launcher
rem  Double-click this file to start the traffic light.
rem  (Messages are ASCII-only on purpose: a .bat with non-ASCII
rem   text garbles under the default OEM code page.)
rem ============================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto nodeps
if not exist "src\main.py" goto nosrc

rem pythonw.exe = no console window. start "" detaches so this
rem cmd window closes right away instead of staying open.
start "" ".venv\Scripts\pythonw.exe" "src\main.py"
exit /b 0

:nodeps
echo.
echo   Dependencies are not installed yet. Run these first:
echo.
echo     python -m venv .venv
echo     .venv\Scripts\pip install -r requirements.txt
echo     python install.py install
echo.
pause
exit /b 1

:nosrc
echo.
echo   src\main.py not found - is this the right folder?
echo.
pause
exit /b 1
