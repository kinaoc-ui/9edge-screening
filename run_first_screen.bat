@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

REM ============================================================
REM  First Screen (D1-centric) + W1_NEAR_MA export
REM  Same job writes under reports\first_screen\:
REM    FirstScreen_*_comma.txt
REM    FirstScreen_*_W1_NEAR_MA_comma.txt   (### Sector — Industry)
REM    FirstScreen_*_W1_NEAR_MA.csv
REM    FirstScreen_*_W1_NEAR_MA_symbols.txt
REM
REM  Usage:
REM    run_first_screen.bat
REM    run_first_screen.bat "C:\path\to\screener.csv"
REM    run_first_screen.bat "C:\path\to\screener.csv" --limit 50
REM
REM  Windows Task Scheduler:
REM    Program/script : C:\path\to\repo\run_first_screen.bat
REM    Arguments      : "C:\path\to\screener.csv"
REM    Start in       : C:\path\to\repo
REM
REM  Equivalent CLI:
REM    python first_screen.py --csv "C:\path\to\screener.csv"
REM ============================================================

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
  exit /b 1
)

if not "%~1"=="" (
  echo ========================================
  echo  First Screen + W1_NEAR_MA
  echo  CSV: %~1
  echo ========================================
  "%PYTHON_EXE%" "%~dp0first_screen.py" --csv %*
  set "RC=!ERRORLEVEL!"
  goto :done
)

set "CSV_PATH="
for /f "delims=" %%F in ('dir /b /o-d "screener\new*.csv" 2^>nul') do (
  set "CSV_PATH=%~dp0screener\%%F"
  goto :got_default
)
for /f "delims=" %%F in ('dir /b /o-d "screener\*.csv" 2^>nul') do (
  set "CSV_PATH=%~dp0screener\%%F"
  goto :got_default
)
echo [ERROR] No CSV argument and no screener\*.csv found.
echo Usage: run_first_screen.bat "C:\path\to\screener.csv"
exit /b 1

:got_default
if not exist "!CSV_PATH!" (
  echo [ERROR] CSV not found: !CSV_PATH!
  exit /b 1
)
echo ========================================
echo  First Screen + W1_NEAR_MA
echo  CSV: !CSV_PATH!
echo ========================================
"%PYTHON_EXE%" "%~dp0first_screen.py" --csv "!CSV_PATH!"
set "RC=!ERRORLEVEL!"

:done
if not "!RC!"=="0" (
  echo [ERROR] first_screen.py failed with exit !RC!
  exit /b !RC!
)

echo.
echo [OK] First Screen done. Check reports\first_screen\ for:
echo   FirstScreen_*_comma.txt
echo   FirstScreen_*_W1_NEAR_MA_comma.txt   ^<-- Luk near-MA shortlist
echo   FirstScreen_*_W1_NEAR_MA.csv
exit /b 0
