@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "GIT=C:\Program Files\Git\cmd\git.exe"
if not exist "%GIT%" (
  echo [ERROR] Git not found: %GIT%
  pause
  exit /b 1
)

echo ========================================
echo  清理 Git 大件（video 5GB+）並重新 push
echo ========================================
echo.
echo  原因：video/ 課程片已誤 commit，GitHub 唔收 5GB push
echo  做法：刪除舊 .git，用新 .gitignore 重新 commit（只影響本機 git 記錄）
echo  你部機嘅 video/ 檔案唔會刪，只係唔會 upload GitHub
echo.
echo  按 Ctrl+C 取消，或
pause

echo.
echo [1/5] 移除舊 git 記錄...
if exist ".git" rmdir /s /q ".git"

echo [2/5] 初始化新 repo...
"%GIT%" init
"%GIT%" branch -M main

echo [3/5] 加入檔案（已 ignore video/）...
"%GIT%" add .
"%GIT%" status -sb

echo [4/5] commit...
"%GIT%" commit -m "9-edge screening UI and batch reports (no videos)"

echo [5/5] 設定 remote 並 push...
"%GIT%" remote remove origin 2>nul
"%GIT%" remote add origin https://github.com/kinaoc-ui/9edge-screening.git
"%GIT%" push -u origin main --force

if errorlevel 1 (
  echo.
  echo [ERROR] push 失敗 — 見上面訊息
  echo 若 repo 未建立，先去 github.com/new 開 9edge-screening
  pause
  exit /b 1
)

echo.
echo [OK] Push 完成！去 share.streamlit.io deploy app_9edge_ui.py
pause
