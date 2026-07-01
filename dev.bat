@echo off
rem dev.bat
rem ====================================
rem 開発用の起動バッチ。コードを編集しながら動作確認したい時に使う
rem （API・管理APIとも --reload 付きで起動するため、ファイル保存のたびに
rem   自動再起動する）。
rem
rem 日常運用（開発しない時）は dev.bat ではなく start.bat を使うこと
rem （--reload なしの方が安定動作する）。

echo ====================================
echo  Fukurou - 開発起動 (dev.bat, --reload あり)
echo ====================================
echo.

echo [1/4] 予測API (port 8002, --reload) を起動...
start "Fukurou-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v2.main:app --port 8002 --reload"
timeout /t 2 /nobreak > nul

echo [2/4] 管理API (port 8003, --reload, localhost only) を起動...
start "Fukurou-Admin-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_admin.main:app --host 127.0.0.1 --port 8003 --reload"
timeout /t 2 /nobreak > nul

echo [3/4] フロントエンド (port 5173) を起動...
start "Fukurou-Frontend" cmd /k "cd /d C:\workspace\fukurou_v2_app\frontend && npm run dev"
timeout /t 2 /nobreak > nul

echo [4/4] ジョブワーカーを起動...
echo   -^> 溜まっているジョブを処理し、新規ジョブなしで2分経過すると自動終了します
start "Fukurou-Worker" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m shared.worker.job_runner"

echo.
echo ====================================
echo  フクロウ AI: http://localhost:5173
echo  管理 API:    http://localhost:8003 (内部専用)
echo ====================================
echo.
pause
