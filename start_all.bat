@echo off
echo ====================================
echo  AI Fukurou - Start All
echo ====================================
echo.

echo [1/6] V2 Predict API (port 8002)...
start "Fukurou-V2-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v2.main:app --port 8002 --reload"
timeout /t 2 /nobreak > nul

echo [2/6] V1 Video API (port 8001)...
start "Fukurou-V1-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v1.main:app --port 8001 --reload"
timeout /t 2 /nobreak > nul

echo [3/6] Frontend UI (port 5173)...
start "Fukurou-Frontend" cmd /k "cd /d C:\workspace\fukurou_v2_app\frontend && npm run dev"
timeout /t 2 /nobreak > nul

echo [4/6] Admin API (port 8003, localhost only)...
start "Fukurou-Admin-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_admin.main:app --host 127.0.0.1 --port 8003 --reload"
timeout /t 2 /nobreak > nul

echo [5/6] Admin Frontend (port 5174)...
start "Fukurou-Admin-UI" cmd /k "cd /d C:\workspace\fukurou_v2_app\admin_frontend && npm run dev"
timeout /t 2 /nobreak > nul

echo [6/6] Job Worker...
start "Fukurou-Worker" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m shared.worker.job_runner"

echo.
echo ====================================
echo  User:  http://localhost:5173
echo  Admin: http://localhost:5174
echo  Worker: shared.worker.job_runner
echo ====================================
echo.
pause