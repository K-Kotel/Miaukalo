@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Сначала запустите build.bat — он установит всё нужное.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python main.py
