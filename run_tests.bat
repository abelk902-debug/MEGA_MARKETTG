@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0..\venv\Scripts\activate.bat" (set "VENV=%~dp0..\venv") else (set "VENV=%~dp0.venv")
if not exist "%VENV%\Scripts\activate.bat" py -3.12 -m venv "%VENV%"
call "%VENV%\Scripts\activate.bat"
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto :end
python -m pytest -v > test_results.txt 2>&1
type test_results.txt
echo.
echo Results saved to test_results.txt
:end
pause
