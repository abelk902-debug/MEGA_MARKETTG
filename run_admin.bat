@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0..\venv\Scripts\activate.bat" (set "VENV=%~dp0..\venv") else (set "VENV=%~dp0.venv")
call "%VENV%\Scripts\activate.bat"
python -m uvicorn admin_panel.app:app --host 127.0.0.1 --port 8080
if errorlevel 1 pause
