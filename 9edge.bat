@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_EXE="
where py >nul 2>&1 && set "PYTHON_EXE=py"
if not defined PYTHON_EXE (
  where python >nul 2>&1 && set "PYTHON_EXE=python"
)
if not defined PYTHON_EXE (
  if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  )
)
if not defined PYTHON_EXE (
  if exist "C:\Windows\py.exe" set "PYTHON_EXE=C:\Windows\py.exe"
)

if not defined PYTHON_EXE (
  echo [ERROR] Python not found in PATH.
  echo Install Python from python.org then rerun this file.
  pause
  exit /b 1
)

if exist "C:\Program Files\Git\cmd" set "PATH=C:\Program Files\Git\cmd;%PATH%"
if exist "C:\Program Files (x86)\Git\cmd" set "PATH=C:\Program Files (x86)\Git\cmd;%PATH%"

echo ========================================
echo  9-Edge Launcher - all actions in UI
echo ========================================
echo.
echo  Browser: http://127.0.0.1:8501
echo.

if /I "%~1"=="--check" (
  "%PYTHON_EXE%" -V
  "%PYTHON_EXE%" -m streamlit --version
  if errorlevel 1 (
    echo [ERROR] Streamlit is not available.
    exit /b 1
  )
  "%PYTHON_EXE%" -c "import app_9edge_ui; print('[OK] app_9edge_ui import OK')"
  if errorlevel 1 exit /b 1
  echo [OK] 9edge launcher check passed.
  exit /b 0
)

"%PYTHON_EXE%" -m pip install -q streamlit 1>nul 2>nul
"%PYTHON_EXE%" -m streamlit run app_9edge_ui.py --server.headless true --server.address 127.0.0.1 --browser.gatherUsageStats false

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start Streamlit UI.
  echo Try: %PYTHON_EXE% -m pip install streamlit
  pause
  exit /b 1
)

