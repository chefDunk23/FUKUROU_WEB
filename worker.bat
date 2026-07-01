@echo off
rem worker.bat
rem ====================================
rem Starts the job worker. Double-click to run.
rem Works with either start.bat or dev.bat running the API.
rem After pressing JV-Link sync / DB sync buttons on the DB admin
rem screen, run this to actually process the queued jobs.
rem
rem Behavior: processes every queued job (JV-Link sync, DB sync,
rem results import, etc.) one by one, then automatically exits
rem after 2 minutes with no new jobs. It is safe to just wait for
rem it to finish and close the window (worker is not meant to stay
rem resident).
rem
rem To keep it resident like before, set the environment variable
rem WORKER_IDLE_EXIT_SECONDS=0 before running.

rem Switch console codepage to UTF-8 so the worker's Japanese log
rem messages display correctly instead of as garbled text.
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

echo ====================================
echo  Fukurou Job Worker
echo  Processing queued jobs...
echo  (exits automatically after 2 min with no new jobs)
echo ====================================
echo.

cd /d C:\workspace\fukurou_v2_app
py -m shared.worker.job_runner

echo.
echo ====================================
echo  Worker finished.
echo ====================================
pause
