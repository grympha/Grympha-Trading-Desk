@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -c "import sys,struct,tkinter; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8" >nul 2>nul
  if not errorlevel 1 (
    py -3.11 launcher.py %*
    if errorlevel 1 pause
    exit /b
  )
)
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
  "%LocalAppData%\Programs\Python\Python311\python.exe" -c "import sys,struct,tkinter; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8" >nul 2>nul
  if not errorlevel 1 (
    "%LocalAppData%\Programs\Python\Python311\python.exe" launcher.py %*
    if errorlevel 1 pause
    exit /b
  )
)
python -c "import sys,struct,tkinter; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8" >nul 2>nul
if not errorlevel 1 (
  python launcher.py %*
  if errorlevel 1 pause
  exit /b
)
echo.
echo Grympha Automated Trading Agent - Python requirement not ready
echo Install Windows Python 3.11 64-bit from python.org.
echo Include: pip, Tcl/Tk and IDLE, and Python Launcher.
echo Select Add python.exe to PATH. Then double-click START_APP.cmd again.
echo Official download: https://www.python.org/downloads/release/python-3119/
echo For MT5 installation and first-run steps, open LAUNCHER_GUIDE.md.
echo No packages or trading software have been installed by this check.
echo.
choice /C YN /M "Open official Python download page"
if errorlevel 2 goto done
start "" "https://www.python.org/downloads/release/python-3119/"
:done
pause
