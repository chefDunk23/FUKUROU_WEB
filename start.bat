@echo off
rem start.bat
rem ====================================
rem 日常運用でのメイン起動バッチ。これ1つで API・フロントエンドを一括起動する。
rem
rem 起動するもの: V2予測API / V1動画生成API / フロントエンド / 管理API / 管理UI
rem 起動しないもの: ジョブワーカー（常駐させない方針のため別扱い。
rem                 DB管理画面のボタンを押した後は worker.bat を叩くこと）
rem
rem コードを編集しながら動作確認したい場合はこれではなく dev.bat（--reload あり）を使う。

echo ====================================
echo  Fukurou - 日常運用起動 (start.bat)
echo  API・フロントエンドを一括起動します（ワーカーは含みません）
echo ====================================
echo.

echo [1/5] V2 Predict API (port 8002) を起動...
start "Fukurou-V2-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v2.main:app --port 8002"
timeout /t 2 /nobreak > nul

echo [2/5] V1 Video API (port 8001) を起動...
start "Fukurou-V1-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_v1.main:app --port 8001 --reload"
timeout /t 2 /nobreak > nul

echo [3/5] Frontend UI (port 5173) を起動...
start "Fukurou-Frontend" cmd /k "cd /d C:\workspace\fukurou_v2_app\frontend && npm run dev"
timeout /t 2 /nobreak > nul

echo [4/5] Admin API (port 8003, localhost only) を起動...
start "Fukurou-Admin-API" cmd /k "cd /d C:\workspace\fukurou_v2_app && py -m uvicorn api_admin.main:app --host 127.0.0.1 --port 8003"
timeout /t 2 /nobreak > nul

echo [5/5] Admin Frontend (port 5174) を起動...
start "Fukurou-Admin-UI" cmd /k "cd /d C:\workspace\fukurou_v2_app\admin_frontend && npm run dev"
timeout /t 2 /nobreak > nul

echo.
echo ====================================
echo  User:  http://localhost:5173
echo  Admin: http://localhost:5174
echo.
echo  ジョブワーカーはここでは起動していません。
echo  DB管理画面でJV-Link同期/DB同期ボタンを押した後は
echo  worker.bat をダブルクリックしてください（溜まったジョブを
echo  処理して自動的に終了します）。
echo ====================================
echo.
pause
