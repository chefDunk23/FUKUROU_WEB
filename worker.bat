@echo off
rem worker.bat
rem ====================================
rem ジョブワーカーを起動する。ダブルクリックで実行可能。
rem DB管理画面（start.bat / dev.bat のいずれで起動していてもOK）で
rem JV-Link同期・DB同期ボタンを押した後、これを叩くとジョブが処理される。
rem
rem 動作: 起動すると、DB管理画面のボタン等でキューに溜まっている
rem       ジョブ（JV-Link同期・DB同期・成績取り込み等）を全て順番に処理し、
rem       新規ジョブが来ないまま2分経過すると自動的に終了する。
rem       常駐させたくない場合は処理が終わるまで待ってから閉じてOK。
rem
rem 常駐させたい場合（旧来の挙動）は、環境変数 WORKER_IDLE_EXIT_SECONDS=0 を
rem 設定してから実行すること。

echo ====================================
echo  Fukurou Job Worker
echo  溜まっているジョブを処理します...
echo  (新規ジョブなしで2分経過すると自動終了します)
echo ====================================
echo.

cd /d C:\workspace\fukurou_v2_app
py -m shared.worker.job_runner

echo.
echo ====================================
echo  ワーカーを終了しました。
echo ====================================
pause
