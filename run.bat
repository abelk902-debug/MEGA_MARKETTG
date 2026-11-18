@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0..\venv\Scripts\activate.bat" (set "VENV=%~dp0..\venv") else (set "VENV=%~dp0.venv")
if not exist "%VENV%\Scripts\activate.bat" py -3.12 -m venv "%VENV%"
if errorlevel 1 goto :error
call "%VENV%\Scripts\activate.bat"
python -c "import aiogram, sqlalchemy, aiosqlite, pydantic_settings" 2>nul
if errorlevel 1 python -m pip install -r requirements.txt
if errorlevel 1 goto :error
if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example. Put BOT_TOKEN in it, then run again.
  pause
  exit /b 1
)
python main.py
if errorlevel 1 goto :error
exit /b 0
:error
echo.
echo Something failed. Read the messages above.
pause
exit /b 1
