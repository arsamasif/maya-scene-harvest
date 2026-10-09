@echo off
rem Double-click to open the Scene Harvest app.
rem The first run creates a .venv next to this file with a 64-bit Python
rem 3.10-3.13 (pyarrow and PySide6 have no wheels for 32-bit Python) and
rem installs the app into it. Later runs just open the window.

setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"

if exist "%VENV%\Scripts\pythonw.exe" goto launch

echo First run: setting up a Python environment in .venv, this takes a minute...
set "PYTHON="
for %%V in (3.13 3.12 3.11 3.10) do if not defined PYTHON call :find_python %%V
if not defined PYTHON goto no_python

"%PYTHON%" -m venv "%VENV%" || goto failed
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto failed
"%VENV%\Scripts\python.exe" -m pip install --quiet -e ".[ui]" || goto failed

:launch
start "" "%VENV%\Scripts\pythonw.exe" -m scene_harvest.ui
exit /b 0

:find_python
for /f "delims=" %%P in ('py -%1-64 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
exit /b 0

:no_python
echo.
echo Could not find a 64-bit Python 3.10 to 3.13. Install one from python.org,
echo then run this file again.
pause
exit /b 1

:failed
echo.
echo Setup failed, see the messages above. Delete the .venv folder and try again.
if exist "%VENV%" rmdir /s /q "%VENV%"
pause
exit /b 1
