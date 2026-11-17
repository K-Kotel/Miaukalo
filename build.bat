@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo   Сборка Miaukalo.exe
echo ============================================

set PY=
py -3.12 --version >nul 2>&1 && set PY=py -3.12
if not defined PY py -3.13 --version >nul 2>&1 && set PY=py -3.13
if not defined PY py -3.11 --version >nul 2>&1 && set PY=py -3.11
if not defined PY python --version >nul 2>&1 && set PY=python
if not defined PY (
  echo Python не найден. Установите Python 3.12 с https://www.python.org/downloads/
  echo При установке поставьте галочку "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Использую: %PY%

if not exist .venv (
  %PY% -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller || goto :fail

pyinstaller --noconfirm Miaukalo.spec || goto :fail
copy /y "Инструкция.txt" "dist\Miaukalo\" >nul

echo.
echo Готово! Программа лежит в папке dist\Miaukalo\ (запускать Miaukalo.exe).
echo Для распространения используйте архив всей папки.
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\Miaukalo' -DestinationPath 'dist\Miaukalo.zip'" && echo Архив: dist\Miaukalo.zip
pause
exit /b 0

:fail
echo.
echo Сборка не удалась — пришлите текст ошибки выше.
pause
exit /b 1
