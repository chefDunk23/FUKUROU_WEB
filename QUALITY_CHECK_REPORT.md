# 品質確認レポート (Quality Check Report)

実施日: 2026-06-27
対象コミット: a71a63f (BET-7: S-1条件の本番実装)
ブランチ: auto-harness-1

---

## チェック1: バグ再発確認

### データ系

| 項目 | 結果 | 確認内容 |
|------|------|----------|
| payout race_id 12桁 | ✅ PASS | `jvdl_parser/sink.py:48` `build_payout_race_id()` 4+4+2+2=12桁固定。テスト `test_jvdl_parser_sink.py:418` でアサート済み |
| build_payout_race_id 公開関数 | ✅ PASS | `sink.py:54` ドキュメント「他モジュールからも参照可能な公開関数」明記。`tests/test_jvdl_parser_sink.py:35` でインポート済み |
| PIT化（sire_feature_store） | ✅ PASS | `conditions_v2.py:566` "PIT-safe: HorseContext.sire_venue_top3 はレース日以前の最新 sire_feature_store スナップショット" |
| TRAIN_END_DATE / EVAL_START_DATE | ✅ PASS | `shared/config.py` で定義済み（前回確認済み） |
| verify_data_split | - SKIP | parquetファイルが未存在環境のため実行不可。ガード実装は確認済み（前回） |

### 条件系

| 項目 | 結果 | 確認内容 |
|------|------|----------|
| weight_favor 変化なし(0kg) → False | ✅ PASS | `conditions_v2.py` `diff < 0` のみ True（前回確認済み） |
| 加速ラップ同タイム → False | ✅ PASS | `training_ranker.py:143` `a > b > c > d` (厳密 >、>= 不使用)、`tipster/training_ranker.py:147` ウッド終い2F も同様 |
| 3値化（True/False/None）実装 | ✅ PASS | engine.py の required=false + `passed is None` 非カウント設計（前回確認済み） |
| v2_f3_top / v2_hill_fit / v2_sire_venue 実装 | ✅ PASS | `tipster/conditions_v2.py` に実装済み。pytest 661 passed 確認 |

### picks_report 系

| 項目 | 結果 | 確認内容 |
|------|------|----------|
| race_id[8:10] で場コード | ✅ PASS | `generate_picks_report.py` place_code = race_id[8:10]（前回確認済み） |
| race_id[14:16] でレース番号 | ✅ PASS | race_num = int(race_id[14:16])（前回確認済み） |
| baba_score が一押しランク非影響 | ✅ PASS | S-tier は `min(s1_cleared, key=tan_odds)` で選択。`_rerank_by_baba()` は S-tier パスで非呼出。`generate_picks_report.py:1143` コメント確認 |
| S-1条件セット（5条件ALL-True必須） | ✅ PASS | `clear_count == _S1_N_CONDS(5)` フィルタ実装。required=false + 後処理の設計 |
| 推奨なし表示（クリア馬0頭時） | ✅ PASS | `s1_top = None` 時に空リスト返却（前回実動作確認済み） |

### DB系

| 項目 | 結果 | 確認内容 |
|------|------|----------|
| sanrenpuku / sanrenfuku 表記統一 | ✅ PASS | `combo_backtest.py` gen_sanrenfuku_combos / sanrenpuku キー一貫（前回確認済み） |
| sync_races バグ履歴 | ✅ PASS | 過去のfixコミット（9439b2a 等）で解決済み。`job_runner.py:301` 構文確認OK |

---

## チェック2: pytest全件パス

```
661 passed in 42.27s
```

✅ **PASS** — 全661テスト通過（s1_pattern.json type="segment" 修正で BET-2/BET-3 テスト誤検知も解消済み）

---

## チェック3: 画面アプリ動作確認

| 確認項目 | 結果 | 詳細 |
|----------|------|------|
| api_v2/main.py 構文 | ✅ PASS | py_compile OK |
| api_admin/main.py 構文 | ✅ PASS | py_compile OK |
| api_v2/routers/public_races.py 構文 | ✅ PASS | py_compile OK |
| tipster モジュール群 構文 | ✅ PASS | conditions_v2 / engine / backtest / models 全件 OK |
| GET /healthz 応答 | ✅ PASS | HTTP 200 `{"status":"ok","api":"v2"}` |
| 認証エンドポイント GET /api/v2/races/weekend | ✅ PASS | HTTP 401（認証ガード正常動作） |

フロントエンド（TypeScript/Vite）は起動確認のみ（DB接続なし環境のため実データ表示は割愛）。

---

## チェック4: DB最新化フロー確認

`shared/worker/job_runner.py` に以下の全ジョブが登録済み（構文OK）:

| ジョブ | 用途 |
|--------|------|
| `sync_jvdata` | JVLink経由 JVDL最新化 |
| `sync_races_from_jvdl` | JVDL → keiba_v2 レースデータ同期 |
| `update_feature_stores` | フィーチャーストア再計算 |
| `recompute_predictions` | AI予測スコア再計算 |
| `update_tipster_results` | レース確定結果取り込み（confirmed_rank） |
| `run_tipster_evaluation` | 戦略評価実行 |
| `run_tipster_backtest` | バックテスト実行 |

Admin API `POST /jobs` でジョブ投入可能。フロー設計上の問題なし ✅

---

## 総合判定

| チェック | 判定 |
|----------|------|
| チェック1 バグ再発確認 | ✅ PASS（全項目クリア、verify_data_split はSKIP） |
| チェック2 pytest全件 | ✅ PASS（661/661） |
| チェック3 画面アプリ | ✅ PASS（API 200/401確認） |
| チェック4 DB最新化フロー | ✅ PASS（全ジョブ登録・構文OK） |

**総合: ✅ 全チェック通過**

---

## 今後の対応候補（Priority順）

1. **クラス変化⚪修正（Task #8）**: 過去走クラスレベルが None になるケース（API payloadにclass_label未含有）を残課題として継続
2. **JVDL週次同期の自動化**: jockey_idデータ取得のため毎週月曜 sync_jvdata ジョブ自動実行
3. **TKレコード収録（馬場状態当日取得）**: 良馬場判定による推奨調整
4. **B-2パターン検証**: `run_b2_validation.py` で二押し（ダート中距離・非坂あり）の S-2 候補探索
