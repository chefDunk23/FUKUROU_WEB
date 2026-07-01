@echo off
rem dev.bat
rem ====================================
rem Dev-mode launcher. Use this while actively editing code, since
rem API + admin API run with --reload (auto-restart on file save).
rem
rem For normal day-to-day use (not actively coding), use start.bat
rem instead (more stable without --reload).

echo ====================================
echo  Fukurou - Dev Startup (dev.bat, --reload enabled)
echo ====================================
echo.

rem chcp 65001 + PYTHONIOENCODING=utf-8 in each spawned window: prevents
rem garbled Japanese log messages in the Python process consoles.

echo [1/4] Starting Predict API (port 8002, --reload)...
call :check_port 8002
if "%PORT_BUSY%"=="1" (
    echo   -^> Port 8002 is already in use. Skipping ^(already running? close the old window first if not^).
) else (
    start "Fukurou-API" cmd /k "chcp 65001 > nul && set PYTHONIOENCODING=utf-8 && cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v2.main:app --port 8002 --reload"
)
timeout /t 2 /nobreak > nul

echo [2/4] Starting Admin API (port 8003, --reload, localhost only)...
call :check_port 8003
if "%PORT_BUSY%"=="1" (
    echo   -^> Port 8003 is already in use. Skipping ^(already running? close the old window first if not^).
) else (
    start "Fukurou-Admin-API" cmd /k "chcp 65001 > nul && set PYTHONIOENCODING=utf-8 && cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_admin.main:app --host 127.0.0.1 --port 8003 --reload"
)
timeout /t 2 /nobreak > nul

echo [3/4] Starting Frontend (port 5173)...
call :check_port 5173
if "%PORT_BUSY%"=="1" (
    echo   -^> Port 5173 is already in use. Skipping ^(vite would otherwise silently switch to 5174^).
) else (
    start "Fukurou-Frontend" cmd /k "cd /d C:\workspace\fukurou_v2_app\frontend && npm run dev"
)
timeout /t 2 /nobreak > nul

echo [4/4] Starting job worker...
echo   -^> Processes queued jobs, then exits automatically after 2 min idle
start "Fukurou-Worker" cmd /k "chcp 65001 > nul && set PYTHONIOENCODING=utf-8 && cd /d C:\workspace\fukurou_v2_app && py -m shared.worker.job_runner"

echo.
echo ====================================
echo  Fukurou AI: http://localhost:5173
echo  Admin API:  http://localhost:8003 (internal only)
echo ====================================
echo.
pause
exit /b 0

rem :check_port <port>
rem Sets PORT_BUSY=1 if a process is already LISTENING on the port, else 0.
rem Prevents double-launching (which either fails with WinError 10048 for
rem the Python APIs, or makes vite silently jump to the next free port
rem such as 5174 for the frontend).
:check_port
set PORT_BUSY=0
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %1 -State Listen -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 set PORT_BUSY=1
exit /b 0
