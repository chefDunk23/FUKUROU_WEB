@echo off
rem start.bat
rem ====================================
rem Daily-use launcher. One double-click starts API + frontend.
rem
rem Starts: V2 predict API / frontend / admin API
rem   (the admin UI now lives inside the main frontend at /admin,
rem    so there is no separate admin frontend anymore)
rem Does NOT start:
rem   - job worker (kept non-resident on purpose. After pressing sync
rem     buttons in the DB admin screen, run worker.bat)
rem   - V1 video API (port 8001): not used by the current frontend
rem     (no route calls it anymore). Start it manually only if you
rem     need the video generation feature:
rem       py -m uvicorn api_v1.main:app --port 8001 --reload
rem
rem If you are editing code and want auto-reload, use dev.bat instead.

echo ====================================
echo  Fukurou - Daily Startup (start.bat)
echo  Starting API + frontend (worker NOT included)
echo ====================================
echo.

rem chcp 65001 + PYTHONIOENCODING=utf-8 in each spawned window: prevents
rem garbled Japanese log messages (Python logging defaults to the console's
rem codepage, which mangles UTF-8 Japanese text unless the console is UTF-8).

echo [1/3] Starting V2 Predict API (port 8002)...
call :check_port 8002
if "%PORT_BUSY%"=="1" (
    echo   -^> Port 8002 is already in use. Skipping ^(already running? close the old window first if not^).
) else (
    start "Fukurou-V2-API" cmd /k "chcp 65001 > nul && set PYTHONIOENCODING=utf-8 && cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v2.main:app --port 8002"
)
timeout /t 2 /nobreak > nul

echo [2/3] Starting Frontend UI (port 5173)...
call :check_port 5173
if "%PORT_BUSY%"=="1" (
    echo   -^> Port 5173 is already in use. Skipping ^(vite would otherwise silently switch to 5174^).
) else (
    start "Fukurou-Frontend" cmd /k "cd /d C:\workspace\fukurou_v2_app\frontend && npm run dev"
)
timeout /t 2 /nobreak > nul

echo [3/3] Starting Admin API (port 8003, localhost only)...
call :check_port 8003
if "%PORT_BUSY%"=="1" (
    echo   -^> Port 8003 is already in use. Skipping ^(already running? close the old window first if not^).
) else (
    start "Fukurou-Admin-API" cmd /k "chcp 65001 > nul && set PYTHONIOENCODING=utf-8 && cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_admin.main:app --host 127.0.0.1 --port 8003"
)
timeout /t 2 /nobreak > nul

echo.
echo ====================================
echo  User:  http://localhost:5173
echo  Admin: http://localhost:5173/admin
echo.
echo  Job worker is NOT started here.
echo  After pressing JV-Link sync / DB sync buttons on the DB admin
echo  screen, double-click worker.bat to process the queued jobs.
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
