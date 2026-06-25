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

tr0_done() {
  grep -q "TR-0: 完了" PROGRESS.md 2>/dev/null
}

for i in $(seq $START_LOOP $MAX_LOOPS); do
  echo "=== Loop $i: Generator ===" >> "$LOGFILE"

  GEN_PROMPT="あなたはGeneratorです。PLAN.mdの仕様（依存関係順を厳守、特にBET-0完了前にBET-3/BET-5に着手しない、
TR-0完了前にTR-1に着手しない）とPROGRESS.mdの最新フィードバックを読み、未完了/要修正の項目を1つ実装してください。"

  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    GEN_PROMPT="$GEN_PROMPT

【最初に必ず確認】git status上に未コミットの変更が存在します。前回ループが中断された際の作業中ファイル、
または人間（Planner）が直接編集したPLAN.md/PROGRESS.mdの可能性があります。
着手前に、これらの未コミット変更が現行のPLAN.md（特にrace_id変換ロジックの仕様§1-1・BET-0参照、
BET-3/BET-5の検証対象賭式が単勝/複勝/馬連/ワイドの4賭式に変更されたこと、
ワークストリームC（TR-0/TR-1、調教AIフィルタリング）が追加されたこと）の方針と一致しているか確認してください。
一致しない箇所（古い前提に基づく実装等）があれば、そのまま続行せず書き直してください。
PLAN.md/PROGRESS.md自体の差分はあなたの作業に支障しなければそのままで構いません。"
  fi

  if ! bet0_done; then
    GEN_PROMPT="$GEN_PROMPT

【機械的制約】PROGRESS.mdに「BET-0: 完了」という行がまだ存在しません。
BET-0（払戻データ基盤）が未完了とみなします。BET-3・BET-5には絶対に着手しないでください。
今回はBET-0、BET-1、BET-2、BET-4、DBM系、またはTR-0（BET-0とは無関係に並行着手可能）のいずれか1つを選んでください。
BET-0が完了したら、PROGRESS.mdに必ず単独の行で「BET-0: 完了」と記録してください。"
  else
    GEN_PROMPT="$GEN_PROMPT

【次に着手する項目（人間からの指示）】PLAN.md §4の依存関係上、現時点で着手可能な項目は
BET-4（データ分割明文化）、BET-5（条件パターンの実験管理）、TR-1（調教AIフィルタリング：優先度①〜⑦の抽出・順位付けロジック）です。
TR-0は完了済みのため、TR-1着手のブロッカーは解消されています。
この3つの中からどれを選ぶかはGeneratorの判断に委ねます（並行可能なチェーンのため優先順位は固定しません）。
ただしPROGRESS.mdのEvaluatorフィードバックに既存項目（BET-0〜BET-3等）への未解決の指摘が残っている場合は、
新規項目より先にその修正を優先してください。

【全ての回収率/スコア出力に適用する出力規約（BET-3で確立、人間からの指示・必ず守ること）】
- 回収率やスコアを出力する際は、必ず「該当レース数」と「該当ベット数（購入点数合計、該当する場合）」を
  回収率/スコアと同じ階層で算出・出力すること。件数が分からない回収率/スコアは判断材料にならない。
- 検証対象賭式は単勝・複勝・馬連・ワイドの4賭式とする（三連複は2026-06-25よりPLAN.md上で対象外。
  既存の三連複対応コード・payoutsの三連複データは削除不要、出力規約の対象に含めないだけ）。
- この規約はBET-3だけでなく、BET-4・BET-5・TR-0/TR-1を含む今後の全ての回収率/スコア出力に適用する。

【TR-1に着手する場合の留意事項（人間からの指示・必ず守ること）】
- TR0_FINDINGS.mdの対応表（\`lap_lX_lY\`系フィールド⇔優先度条件①〜⑦、\`center_cd\`⇔栗東/美浦、
  テーブル分離（training_slope/training_wood）⇔坂路/ウッドの区別）をそのまま使用すること。
  再調査・再定義は不要（TR-0で確定済み）。
- 「加速ラップ」は厳密な単調減少として実装すること（例: lap_l4_l3 > lap_l3_l2 > lap_l2_l1 > lap_l1。
  同タイムが連続する場合は停滞であり加速ラップとはみなさない。\`>=\`ではなく\`>\`で判定する）。
- 出力は推奨順位リストの提示までとし、買い目（賭式・点数）の構築ロジックは一切含めないこと
  （G5b相当の制約。これに違反した場合Evaluatorは無条件で不合格とする）。
- TR-1が完了したら、PROGRESS.mdに必ず単独の行で「TR-1: 完了」と記録してください。"
  fi

  GEN_PROMPT="$GEN_PROMPT

完了したらPROGRESS.mdに作業ログ（実装内容・対応したPLAN.md項目ID）を追記し、
変更したファイルをgit addしてコミットしてください（コミットメッセージは
'feat(harness-loop-$i): <項目ID> <概要>' の形式。変更がなければコミット不要）。"

  run_step "$GEN_PROMPT" "Generator"

  echo "=== Loop $i: Evaluator ===" >> "$LOGFILE"
  run_step "あなたはEvaluatorです。PLAN.mdの評価基準（G1-G4, G5a, G5b, G-TR0〜G-TR3含む）に基づき現在の実装をテストしてください。
スコアと具体的な問題点をPROGRESS.mdに追記してください。
Blocker項目（G1-G4, G5a, G5b, G-TR0, G-TR1, G-TR2）が1つでも不合格なら、その他がどれだけ高得点でも全体を不合格としてください。
BET-3またはBET-5が実装されている場合、PROGRESS.mdに「BET-0: 完了」の記録がなければ、
データ基盤が整っていない状態での実装と判断し、無条件で不合格としてください。
TR-1（優先度抽出・順位付けロジック）が実装されている場合、PROGRESS.mdに「TR-0: 完了」の記録がなければ、
TR-0未完了のまま着手したと判断し、無条件で不合格としてください。
BET-3・BET-4・BET-5のいずれかが実装されている場合、PLAN.md §5-3の
「回収率に件数（サンプルサイズ）が併記されているか」Blockerを必ず確認してください。
検証対象は単勝・複勝・馬連・ワイドの4賭式です（三連複は2026-06-25よりPLAN.md上で検証対象外のため、
三連複の出力有無はこの基準の判定に影響しません）。4賭式いずれかの回収率出力に該当レース数・該当ベット数が
併記されていなければ、他がどれだけ高得点でも無条件で不合格としてください。回収率100%超えの結果が1件でもあれば、
その隣に表示される件数が実際のレース数・ベット数と一致するか実際に再現して目視確認してください。
TR-1が実装されている場合、出力が推奨順位の提示までであり買い目（賭式・点数）を含まないことをG-TR2として確認し、
含んでいれば無条件で不合格としてください。
TR-1が実装されている場合、「加速ラップ」判定が厳密な単調減少（不等号は>であり>=ではない）として
実装されているかをコードで確認し、同タイムの連続を加速と誤判定する実装になっていればG-TR1の不合格としてください。
全Blocker合格かつ対象機能のDone条件を満たしたら最終行に'ALL_PASS'とだけ書いてください。" "Evaluator"

  # PROGRESS.mdには過去ループのALL_PASSが履歴として残るため、grep -q ALL_PASS（ファイル全体検索）
  # では古いALL_PASSに誤反応して即終了してしまう（Loop6で実際に発生したバグ）。
  # 「最後の空行でない行」が厳密にALL_PASSと一致する場合のみ終了する。
  LAST_LINE=$(tac PROGRESS.md | grep -m1 .)
  if [ "$LAST_LINE" = "ALL_PASS" ]; then
    echo "$(date): 全基準合格。終了。" >> "$LOGFILE"
    break
  fi
done
