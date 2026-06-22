@echo off
REM Launch TradingView Desktop (Microsoft Store) with CDP debug port for MCP.
REM Close TradingView fully before running this script.

set "TV_EXE=C:\Program Files\WindowsApps\TradingView.Desktop_3.2.0.7916_x64__n534cwy3pjxzj\TradingView.exe"
set "PORT=9222"

echo Closing existing TradingView instances...
taskkill /F /IM TradingView.exe >nul 2>&1
timeout /t 2 /nobreak >nul

if not exist "%TV_EXE%" (
  echo Error: TradingView not found at:
  echo   %TV_EXE%
  echo Update TV_EXE in this script if Store updated the version folder.
  exit /b 1
)

echo Starting TradingView with --remote-debugging-port=%PORT%...
start "" "%TV_EXE%" --remote-debugging-port=%PORT%

echo Waiting for CDP on http://localhost:%PORT% ...
timeout /t 5 /nobreak >nul

:check
curl -s http://localhost:%PORT%/json/version >nul 2>&1
if %errorlevel% neq 0 (
  echo Still waiting...
  timeout /t 2 /nobreak >nul
  goto check
)

echo.
echo CDP ready. You can now use tv_health_check in Cursor.
curl -s http://localhost:%PORT%/json/version
echo.
