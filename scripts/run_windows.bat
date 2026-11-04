@echo off
REM Creates a virtual environment on first run, then starts TatkalMate.
cd /d "%~dp0\.."
if not exist .venv (
  py -3 -m venv .venv || goto :error
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt || goto :error
) else (
  call .venv\Scripts\activate.bat
)
start "" pythonw main.py
exit /b 0
:error
echo Setup failed. Install Python 3.11+ from python.org and try again.
pause
