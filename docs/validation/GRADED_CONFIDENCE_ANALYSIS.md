# 重賞用confidence判定 検証結果

**対象**: `grade_code` が A(G1)/B(G2)/C(G3)/L(リステッド)/E(OP特別) のレース
**実装**: `pace_bias_ai/features/graded_confidence.py`, `scripts/generate_ai_picks.py::_compute_confidence_graded`
**コミット**: `223e66c`

---

## 1. 背景

`scripts/generate_ai_picks.py::_compute_confidence` の自信度判定（+1/-1のポイント制、A(≥3)/B(1-2)/C(≤0)のラベル）は
通常レース向けに設計されたロジックであり、重賞・OP・リステッド（以下「重賞」と総称）では
本気ローテ判定（`is_genuine`）や叩き台疑惑判定（`is_step`）等の一部条件が実態と逆の効果を示すことが
分析（ユーザー側で実施）で判明した。これを受け、重賞専用の分岐ロジックを別途設計・実装した。

## 2. 学習期間の検証値（ユーザー提供、本セッションでは再現していない）

> **注意**: 以下の数値は本タスク実行前にユーザー側で実施された分析結果であり、
> 本セッションでは元データ・計算コードにアクセスしておらず、数値の再現・検証は行っていない。
> ユーザーからの申告値として、条件の採否根拠の記録目的でそのまま記載する。

**学習期間**: 2022-01〜2025-05 / **対象**: グレード+OP/L（N=4,092）

### 無効化した条件（重賞では効かないと判定）

| 条件 | 標準レースでの扱い | 重賞での実態 | 判定 |
|---|---|---|---|
| `is_step`（叩き台疑惑） | ネガ条件 | 重賞の計画的休養を誤検知 | 無効化 |
| `is_genuine`（本気ローテ） | +1加点 | 効果なし | 無効化 |
| long_rest（休み明け90日+、`is_step`の一部） | ネガ扱い（`is_step`経由） | 26.3%と最高水準 → ネガ扱い禁止 | 無効化（`is_step`無効化に包含） |
| `transport_flag`（輸送フラグ） | ネガ条件 | 再計測で-2〜3ptのみ | 無効化 |

### 採用した条件（新規追加）

| 条件 | 該当時の複勝率等 | 方向 |
|---|---|---|
| クラス移動: 格下げローテ | 29.9% | ポジ |
| クラス移動: 同格ローテ | 26.5% | ポジ |
| クラス移動: 格上挑戦（重賞/L経験あり） | 18.7% | ネガ |
| クラス移動: 条件戦からの挑戦（重賞/L経験なし） | 15.9% | ネガ |
| 調教①該当（`tipster/training_ranker.py` 条件①） | +5.4pt | ポジ |
| 度外視（前走G1/G2 かつ 着差0.5秒以内） | 32.4% | ポジ |
| 高齢（7歳以上） | （重賞でも有効、値は未提供） | ネガ |

### 検証期間（2025-05-31〜）での最終評価

一押し **61.0%**（N=41） / 見送り **44.2%**、分離幅 **16.8pt**

> **N=41 についての注意**: 検証期間の一押しサンプル数は41件と少なく、統計的な信頼区間は広い。
> この数値のみをもって本番運用の期待値として扱わないこと。継続的なモニタリング（実績記録の
> `rank_mode='graded'` フィルタでの追跡）を推奨する。

## 3. 条件の実装マッピング

ユーザー提供の条件名と、実コードでの実装対応は以下の通り。実装にあたり、既存コードに
同名だが定義が異なる項目が見つかったため、ユーザー確認の上で以下のように解決した。

| ユーザー提供の条件名 | 実装 | 備考 |
|---|---|---|
| クラス移動 | `classify_class_transition()` | `pace_bias_ai/features/rotation_flag.py` の `class_vs_best`（既存）+ 新規追加した `best_class_rank` から4分類を導出 |
| 調教①該当 | `tipster/training_ranker.py::rank_horses_by_training()` を呼び出し、`condition_label == "①"` を判定 | 既存のTR-1条件①ロジックをそのまま再利用（坂路ラスト1F≤11.9秒 かつ 全区間加速ラップ）。従来 `generate_ai_picks.py` からは呼ばれていなかったため新規に接続 |
| 度外視 | `is_excuse_margin_eligible()`: 前走 `grade_code in (A,B)` かつ `\|time_diff\|<=0.5秒` | 既存ドキュメント `EXCUSE_CONDITION_ANALYSIS.md` には「G1/G2帰り+今走格下」という別定義（`excuse_grade`、未実装）があったが、今回のユーザー確認により「着差0.5秒以内のみ」で判定する新規フラグとして実装（`excuse_grade` とは独立） |
| 高齢 | `is_age_veteran()`: `horse_age >= 7` | — |

## 4. 実装した判定ロジック（本セッションで確定した仕様）

```
grade_code in (A,B,C,L,E) の場合の自信度スコア:
  +1 得意セグメント（距離>1600m or 会場=東京/函館）※標準と同一
  +1 近走好成績（avg_rank_3<=3.5）※標準と同一
  ネガ条件: won_and_classup のみ判定（is_step/transport_flagは重賞で除外）
    該当なし: +1 / 該当: -1
  クラス移動: downgrade/same=+1、upgrade/from_conditions=-1、判定不能=0
  +1 調教①該当
  +1 度外視（前走G1/G2 かつ 着差0.5秒以内）
  -1 高齢（7歳以上）

ラベル: A(score>=3) / B(1<=score<=2) / C(score<=0)（標準と同一閾値）
```

閾値（A/B/Cの境界値）自体はユーザーから明示的な指定がなかったため、標準レースと同一の
`A>=3/B>=1/C<=0` をそのまま採用した。条件数が標準（4条件）より多い（8条件相当）ため、
スコアの取りうる範囲は標準より広い（理論上 -3〜+6）。学習期間の「一押し61.0%」の
正確な再現を意図した閾値較正ではないことに注意。

## 5. 実機スモークテスト結果（本セッションで実施・実測）

**対象**: 2026-06-27, 2026-06-28 開催の中央競馬全72レース（確定済み・実データ）
**方法**: `score_race_ai()` を「新ロジック（`is_graded` 判定あり）」と「旧ロジック
（`is_graded_race` を強制的に `False` にモンキーパッチした版）」の2通りで実行し、
`confidence_label` の変化を比較。

```sql
-- 対象レースのgrade_code分布（fukurou_keiba_v2.races）
SELECT grade_code, COUNT(*) FROM races
WHERE race_date IN ('2026-06-27','2026-06-28') AND keibajo_code <= '10'
GROUP BY grade_code;
→ C: 2件, E: 17件, NULL: 53件（合計72件）
```

### 結果

| 区分 | レース数 | 変化ありレース数 | 変化なしレース数 |
|---|---|---|---|
| 重賞対象（rank_mode=graded, grade_code∈{C,E}） | 19 | **19（100%）** | 0 |
| 標準（rank_mode=standard, grade_code=NULL） | 53 | **0（0%）** | **53（100%）** |

- 重賞対象レース: 全19レース229頭中170頭（**74.2%**）でconfidence_labelが変化
- 標準レース: 53レース全頭で**confidence_label・top_confidenceともに完全一致（変化ゼロ）**

標準レースの判定結果が新旧ロジックで一切変わらないことを実データで確認した。これは
「通常レースの判定ロジックに一切影響を出さない（分岐の追加のみ）」という要件が実装レベルで
満たされていることの直接的な証拠である。

### 重賞対象19レースの詳細

| race_id | grade_code | 変化頭数/出走頭数 | top_confidence (before→after) |
|---|---|---|---|
| 2026062702010509 | E | 9/9 | B→A |
| 2026062702010510 | E | 10/11 | B→A |
| 2026062702010511 | E | 12/14 | C→A |
| 2026062703020109 | E | 7/16 | B→B |
| 2026062703020110 | E | 9/11 | C→B |
| 2026062703020111 | E | 7/9 | B→B |
| 2026062710020109 | E | 4/8 | B→A |
| 2026062710020110 | E | 5/13 | B→B |
| 2026062710020111 | E | 13/15 | B→A |
| 2026062802010609 | E | 7/10 | A→A |
| 2026062802010610 | E | 9/11 | A→A |
| 2026062802010611 | C | 14/15 | C→B |
| 2026062802010612 | E | 13/14 | B→A |
| 2026062803020209 | E | 9/14 | A→A |
| 2026062803020210 | E | 9/11 | B→A |
| 2026062803020211 | C | 12/16 | C→C |
| 2026062810020209 | E | 5/8 | B→A |
| 2026062810020210 | E | 14/16 | B→A |
| 2026062810020211 | E | 2/8 | C→B |

> 検証対象期間（6/27-28）には grade_code A(G1)/B(G2)/L の該当レースが無かった
> （C(G3) 2件、E(OP特別) 17件のみ）。A/B/Lでの動作は回帰テスト
> （`tests/test_graded_confidence.py`）でのみ確認済みで、実機での確認は次回
> 該当レースが発生した際に追加検証が必要。

## 6. pytest / tsc

- `tests/test_graded_confidence.py`: 57件（`is_graded_race`/`classify_class_transition`/
  `class_transition_is_positive`/`is_excuse_margin_eligible`/`is_age_veteran`/`_TIME_DIFF_RE`の
  単体テスト、および `_compute_confidence`/`_compute_confidence_graded` の分岐・非干渉性テスト）
- pytest全体: 822 passed, 0 failed
- `npx tsc -b --noEmit`: エラーなし

## 7. 実績記録（`update_ai_tipster_results`）への反映

`score_race_ai()` は `generate_ai_picks()`（週末picks生成）と `update_ai_tipster_results`
ジョブ（実績記録バックフィル）の両方から共有される関数であり、`is_graded` 分岐は
`_compute_confidence` 内部で処理されるため、両経路に自動的に反映される。
`shared/worker/job_runner.py::_handle_update_ai_tipster_results` は
`compute_unified_rank(pick["rank"], pick["confidence_label"])` から `tipster_results.rank_label`
を算出しており、`pick["confidence_label"]` は既にgraded分岐適用後の値のため、
実績記録側の改修は不要だった（コードパスの構造上、自然に反映される設計）。

## 8. 画面/JSON表示

`rank_mode: "graded" | "standard"` をレースレベルの出力に追加（`generate_ai_picks.py`）。
`frontend/src/views/PicksView.tsx` の `AIRaceCard` で `rank_mode === 'graded'` の場合に
「重賞」バッジをランクバッジ横に表示する。

## 9. 未検証・今後の課題

- grade_code A(G1)/B(G2)/L(リステッド)での実機動作は未検証（回帰テストのみ）。次回該当レース発生時に確認が必要。
- 学習期間の検証値（61.0%/44.2%/16.8pt等）はユーザー提供の申告値であり、本セッションでは元データ・計算方法にアクセスしていないため独立検証はできていない。
- 度外視条件は今回「着差0.5秒以内のみ」で実装したが、既存ドキュメント（EXCUSE_CONDITION_ANALYSIS.md）の「G1/G2帰り+今走格下」という別定義との使い分け・統合要否は未整理。
