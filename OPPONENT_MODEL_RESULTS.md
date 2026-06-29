# 前走メンバーレベル AIサブモデル 検証結果

## モデル概要

- **モデル名**: opponent_model v1
- **目的**: 前走の相手関係×クラスから、その馬の実力を評価する
- **実装場所**: `pace_bias_ai/opponent_model/`
- **v1（展開×バイアスAI）とは完全に独立した別モデル**
- **LightGBM lambdarank** (objective=lambdarank, group=race_id)
- **学習日**: 2026-06-30

---

## 特徴量 (21列)

### opponent_next系（PIT化済み）
- `opponent_next_top3_rate`: 前走の対戦相手の次走3着以内率
- `opponent_next_win_rate`:  前走の対戦相手の次走勝率
- `opponent_next_avg_rank`:  前走の対戦相手の次走平均着順
- `opponent_count`:          PIT基準を満たした対戦相手の頭数

**PIT制約**: 次走が予測日（cur_race_id[:8]）より前に行われた相手のみカウント

### クラス関連
- `prev_class_rank`, `cur_class_rank`: クラス序列 (1=G1〜9=新馬)
- `class_change`: 今走クラス - 前走クラス (負=クラスアップ)
- `class_up`, `class_down`: クラスアップ/ダウンフラグ
- `grade_drop`: 前走G1/G2→今走格下フラグ

### 前走の負け方
- `prev_margin`: 前走勝ち馬との時計差（秒）
- `prev_rank`: 前走着順
- `prev_rank_norm`: 前走着順/出走頭数（正規化）

### 斤量
- `kinryo_change`: 前走比の斤量変化（kg）
- `kinryo_vs_field`: 今走フィールド内平均との差（kg）

### 条件変化
- `distance_change`, `surface_changed`, `venue_changed`

### 属性
- `horse_age`, `dist_cat`, `surface_code`

---

## 検証結果

### Walk-forward OOF（A期間）

| Fold | 評価期間 | カバー率 | 候補精度 | 複勝率 | iter | N |
|------|---------|---------|---------|-------|------|---|
| 1 | 2023年 | 0.966 | 0.937 | 0.543 | 75 | 6,205 |
| 2 | 2024年前半 | 0.964 | 0.937 | 0.553 | 101 | 2,979 |
| **合計** | | **0.965** | **0.937** | **0.546** | | |

ランダムベースライン (A期間): カバー率=0.831 / 候補精度=0.737

### ホールドアウト（フルモデル A期間学習）

| 期間 | カバー率 | 候補精度 | 複勝率 | ランダム(cover) | ランダム(cand) |
|------|---------|---------|-------|--------------|--------------|
| B期間 (2024-07〜12) | 0.960 | 0.927 | 0.524 | 0.839 | 0.751 |
| C期間 (2025-01〜12) | 0.957 | 0.915 | 0.523 | 0.809 | 0.699 |

**C期間のランダム比超過**: カバー率+14.8pt / 候補精度+21.6pt

---

## SHAP特徴量重要度（上位15）

| 順位 | 特徴量 | 重要度 | 方向 |
|------|-------|--------|------|
| 1 | prev_rank_norm | 0.321 | 正（低い=前走上位→高評価） |
| 2 | horse_age | 0.198 | 負（高齢→下評価） |
| 3 | prev_margin | 0.178 | 正（大差負け→下評価） |
| 4 | kinryo_vs_field | 0.111 | 正（重い斤量→フィールド内で苦しい） |
| 5 | distance_change | 0.082 | 負（延長が有利） |
| 6 | prev_rank | 0.055 | 正（前走着順） |
| 7 | venue_changed | 0.050 | 負（競馬場変わりは不利） |
| 8 | prev_class_rank | 0.049 | 正（前走クラス） |
| 9 | class_change | 0.037 | 正（クラス変動） |
| 10 | opponent_count | 0.031 | 負（相手の次走情報が多いほど情報の質が高い） |
| 11 | opponent_next_avg_rank | 0.029 | 負（対戦相手の次走平均着順が低いほど相手が強い） |
| 12 | cur_class_rank | 0.028 | 正（今走クラス） |
| 13 | surface_code | 0.026 | 負 |
| 14 | opponent_next_top3_rate | 0.019 | 負（対戦相手の次走3着内率が高い=相手強い） |
| 15 | kinryo_change | 0.014 | 正 |

### 考察

- **`prev_rank_norm` が最重要**: 前走着順の正規化値。単純な着順より出走頭数を考慮した位置が有効
- **`horse_age` が2位**: 高齢馬のネガティブ効果をモデルが自律学習
- **`prev_margin` が3位**: 着差（時計差）が着順より細かい情報を持つ
- **opponent_next系は4位以下**: 情報の欠損率(26.4%)が高く、前走着順・着差に比べて信号が弱い。ただし独自のシグナルとして貢献している

---

## v1との独立性

v1スコアとの直接比較はC期間parquetの形式が異なるため未実施。

**設計上の独立性:**
- v1 は「展開×バイアス」（先行力・コーナー位置・トラックバイアス）
- opponent_model は「前走相手関係×クラス」（前走着順・クラス・対戦相手の次走成績）
- 特徴量の重複はゼロ（horse_ageのみ共通だが学習データが独立）

---

## 実装ファイル

```
pace_bias_ai/opponent_model/
  __init__.py                      — モジュール宣言
  features.py                      — 特徴量生成（vectorized、PIT-safe）
  model.py                         — LightGBM学習ラッパー（layer2_model再利用）
  condition_mapper_opponent.py     — 日本語説明文生成
```

v1ファイル（一切未変更）:
```
pace_bias_ai/models/layer2_model.py  — v1のまま、opponent_modelはimportのみ
pace_bias_ai/features/condition_mapper.py — v1のまま
```

---

## condition_mapper への説明文追加

`pace_bias_ai/opponent_model/condition_mapper_opponent.py` の `OpponentConditionMapper` クラスが
このモデルの特徴量に対応した日本語説明文を生成する。

主要特徴量の説明文例:
- `opponent_next_top3_rate=0.45` → 「前走相手の次走3着以内率=45%（レベル高い）」
- `prev_margin=0.8` → 「前走は大差負け（着差0.80秒）」
- `class_change=-2` → 「クラスアップ（2ランク上昇）」
- `prev_rank_norm=0.15` → 「前走着順位置（全体の上位15%）」

使い方:
```python
from pace_bias_ai.opponent_model.condition_mapper_opponent import OpponentConditionMapper

mapper = OpponentConditionMapper()
expl = mapper.explain(horse_row, shap_values, FEATURE_COLS)
print(expl.to_text())
```
