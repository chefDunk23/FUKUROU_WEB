#!/bin/bash
# 使い方: ./run_with_retry.sh "Claudeに渡すプロンプト" "ログファイル名"
# このブランチの .claude/settings.local.json (Edit/Write/Bash(*) 全許可) を前提に
# --dangerously-skip-permissions なしで無人実行する。
PROMPT="$1"
LOGFILE="${2:-task.log}"
MAX_RETRY=20
COUNT=0

while [ $COUNT -lt $MAX_RETRY ]; do
  claude --print "$PROMPT" >> "$LOGFILE" 2>/tmp/run_err.log
  EXIT_CODE=$?

  if [ $EXIT_CODE -eq 0 ]; then
    echo "完了: $(date)" >> "$LOGFILE"
    exit 0
  fi

  if grep -qi "rate limit\|usage limit\|5-hour\|reset" /tmp/run_err.log; then
    echo "$(date): 制限検知。5時間10分待機して再開します" >> "$LOGFILE"
    sleep 18600  # 5時間10分（リセットの取りこぼし防止のバッファ込み）
    COUNT=$((COUNT+1))
  else
    echo "$(date): 想定外エラー。停止します" >> "$LOGFILE"
    cat /tmp/run_err.log >> "$LOGFILE"
    exit 1
  fi
done
echo "リトライ上限到達" >> "$LOGFILE"
exit 1
