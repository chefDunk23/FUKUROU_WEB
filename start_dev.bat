@echo off
echo ====================================
echo  フクロウ AI - 起動
echo ====================================
echo.

echo [1/4] 予測 API (port 8002)...
start "Fukurou-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v2.main:app --port 8002 --reload"
timeout /t 2 /nobreak > nul

echo [2/4] 管理 API (port 8003, localhost only)...
start "Fukurou-Admin-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_admin.main:app --host 127.0.0.1 --port 8003 --reload"
timeout /t 2 /nobreak > nul

echo [3/4] フロントエンド (port 5173)...
start "Fukurou-Frontend" cmd /k "cd /d C:\workspace\fukurou_v2_app\frontend && npm run dev"
timeout /t 2 /nobreak > nul

echo [4/4] ジョブワーカー...
start "Fukurou-Worker" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m shared.worker.job_runner"

echo.
echo ====================================
echo  フクロウ AI: http://localhost:5173
echo  管理 API:    http://localhost:8003 (内部専用)
echo ====================================
echo.
pause
