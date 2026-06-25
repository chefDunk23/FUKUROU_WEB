#!/bin/bash
# Generator <-> Evaluator 自動ループ。
# .claude/settings.local.json (Edit/Write/Bash(*) 全許可) を前提に
# --dangerously-skip-permissions なしで無人実行する。
MAX_LOOPS=15
START_LOOP=${START_LOOP:-1}
LOGFILE="harness.log"

run_step() {
  local PROMPT="$1"
  local STEP_NAME="$2"
  # claude --print は使用量制限メッセージを stdout に出すため、
  # stdout+stderr を1つのファイルに合流させてからチェックする（stderrのみ見ていたバグの修正）。
  until claude --print "$PROMPT" > /tmp/step_out.log 2>&1
  do
    cat /tmp/step_out.log >> "$LOGFILE"
    if grep -qi "rate limit\|usage limit\|hit your limit\|5-hour\|try again\|reset" /tmp/step_out.log; then
      echo "$(date): [$STEP_NAME] 制限検知。5時間10分待機して再開" >> "$LOGFILE"
      sleep 18600
    else
      echo "$(date): [$STEP_NAME] 想定外エラー。停止" >> "$LOGFILE"
      exit 1
    fi
  done
  cat /tmp/step_out.log >> "$LOGFILE"
}

bet0_done() {
  grep -q "BET-0: 完了" PROGRESS.md 2>/dev/null
}

for i in $(seq $START_LOOP $MAX_LOOPS); do
  echo "=== Loop $i: Generator ===" >> "$LOGFILE"

  GEN_PROMPT="あなたはGeneratorです。PLAN.mdの仕様（依存関係順を厳守、特にBET-0完了前にBET-3/BET-5に着手しない）と
PROGRESS.mdの最新フィードバックを読み、未完了/要修正の項目を1つ実装してください。"

  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    GEN_PROMPT="$GEN_PROMPT

【最初に必ず確認】git status上に未コミットの変更が存在します。前回ループが中断された際の作業中ファイルです。
着手前に、これらの未コミット変更が現行のPLAN.md（特にrace_id変換ロジックの仕様、§1-1・BET-0参照）の方針と
一致しているか確認してください。一致しない箇所（古い前提に基づく実装等）があれば、そのまま続行せず書き直してください。
一致していればそのまま続行して構いません。"
  fi

  if ! bet0_done; then
    GEN_PROMPT="$GEN_PROMPT

【機械的制約】PROGRESS.mdに「BET-0: 完了」という行がまだ存在しません。
BET-0（払戻データ基盤）が未完了とみなします。BET-3・BET-5には絶対に着手しないでください。
今回はBET-0、BET-1、BET-2、BET-4、またはDBM系の項目のいずれか1つを選んでください。
BET-0が完了したら、PROGRESS.mdに必ず単独の行で「BET-0: 完了」と記録してください。"
  fi

  GEN_PROMPT="$GEN_PROMPT

完了したらPROGRESS.mdに作業ログ（実装内容・対応したPLAN.md項目ID）を追記し、
変更したファイルをgit addしてコミットしてください（コミットメッセージは
'feat(harness-loop-$i): <項目ID> <概要>' の形式。変更がなければコミット不要）。"

  run_step "$GEN_PROMPT" "Generator"

  echo "=== Loop $i: Evaluator ===" >> "$LOGFILE"
  run_step "あなたはEvaluatorです。PLAN.mdの評価基準（G1-G4, G5a, G5b含む）に基づき現在の実装をテストしてください。
スコアと具体的な問題点をPROGRESS.mdに追記してください。
Blocker項目（G1-G4, G5a, G5b）が1つでも不合格なら、その他がどれだけ高得点でも全体を不合格としてください。
BET-3またはBET-5が実装されている場合、PROGRESS.mdに「BET-0: 完了」の記録がなければ、
データ基盤が整っていない状態での実装と判断し、無条件で不合格としてください。
全Blocker合格かつ対象機能のDone条件を満たしたら最終行に'ALL_PASS'とだけ書いてください。" "Evaluator"

  if grep -q "ALL_PASS" PROGRESS.md; then
    echo "$(date): 全基準合格。終了。" >> "$LOGFILE"
    break
  fi
done
