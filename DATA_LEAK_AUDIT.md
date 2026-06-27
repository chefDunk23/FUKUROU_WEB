# データリーク総点検 監査報告書

**監査日:** 2026-06-27
**対象:** Phase 1 / Phase 2 全結果（S/A/B ランクパターン + 穴推奨パターン）
**調査方針:** コード修正なし、調査と報告のみ

---

## 監査サマリー

| 検証項目 | 判定 | 重大度 |
|---------|------|--------|
| 検証1-a: 条件計算の時系列リーク（馬個体情報） | **OK** | — |
| 検証1-b: sire_venue/sire_surface の時系列リーク | **NG** | 高 |
| 検証1-c: jockey_ok（yr_wins部）の時系列リーク | 要注意 | 低 |
| 検証2: データ分割境界（EVAL_START_DATE）遵守 | 要注意 | 中 |
| 検証3: 探索と検証の重複（同一データ期間） | **NG** | 高 |
| 検証4: 種牡馬適性データの集計方法 | **NG** | 高（検証1-bと同根） |

---

## 検証1: 時系列リークの確認

### 1-a: 馬個体の条件計算（全条件）

すべての条件は `df.groupby("horse_id").shift(N)` によって前走/前々走のデータを取得している。
シフト前の値は当走の結果であり、当走は `eval_date` の race_entries から取得される。

| 条件 | 参照データ | 判定 | 根拠 |
|------|----------|------|------|
| `margin` | `prev1_margin = g["this_margin"].shift(1)` | **OK** | 前走（より過去の行）のtime_seconds差のみ |
| `class_ok` | `prev1_class = g["class_level"].shift(1)` | **OK** | 前走時点のクラス |
| `interval_ok` | `df["date"] - df["prev_race_date"]` (shift済) | **OK** | 前走日付との差分 |
| `surface_ok` | `prev1-3_surface`, `prev1-3_rank` (shift済) | **OK** | 過去3走の馬場・着順のみ |
| `f3_top` | `prev1_f3pct = g["f3_rank_pct"].shift(1)` | **OK** | 前走のf3順位パーセンタイル |
| `heavy_ok` | `prev1-3_tc`, `prev1-3_rank` (shift済) | **OK** | 過去3走の馬場状態・着順のみ |
| `rc_fit` | `prev1-3_place_code`, `prev1-3_rank` (shift済) | **OK** | 過去3走の競馬場・着順のみ |
| `straight_fit` | 同上 | **OK** | 同上 |
| `hill_fit` | 同上 | **OK** | 同上 |
| `weight_ok` | `prev_burden_weight = g["burden_weight"].shift(1)` | **OK** | 前走の斤量のみ |
| `turf_type_fit` | `prev1-3_place_code` (shift済) | **OK** | 過去走データのみ |
| `sire_surf` / `sire_dist` | `bloodline_feature_store` (race_id+horse_id キー) | **OK** | 詳細は 1-c で説明 |

**結論: 馬個体の条件計算に時系列リークなし**。`shift(N)` により前走以前のデータのみ参照している。

---

### 1-b: 種牡馬適性条件のリーク（NG）

**対象条件:** `sire_venue`、`sire_surface`（`run_racecourse_search.py`、`run_step3_sim.py`）

**問題のSQLクエリ（`run_racecourse_search.py` 行144-161）:**
```sql
SELECT DISTINCT ON (sire_id) sire_id, top3_rate AS sire_top3_rate,
       venue_01_top3_rate, venue_01_count, ...
FROM sire_feature_store
ORDER BY sire_id, target_date DESC   -- ← 最新スナップショットを取得
```

**問題の本質:**
- `sire_feature_store` は正しくポイントインタイム設計されている（`target_date` で日次スナップショット保持）
- しかしこのSQLは `ORDER BY target_date DESC` で**最新のスナップショット（2026-06-18）を取得**している
- 評価対象レース（2025-07-01 など）の時点では存在しない将来の産駒成績が含まれている

**実データで確認（同一種牡馬 `1140007127` の東京成績変化）:**

| target_date | top3_rate(全体) | venue_05_top3_rate(東京) | venue_05_count(東京出走数) |
|------------|-----------------|--------------------------|---------------------------|
| 2025-07-01 | 0.333 | 0.556 | 9頭 |
| 2025-10-01 | 0.303 | 0.556 | 9頭 |
| 2026-01-01 | 0.297 | 0.417 | 12頭 |
| 2026-06-18 | 0.352 | 0.393 | 28頭 |

- 2025-07-01時点: venue_05_count=9 → **閾値10頭未満 → cond_sire_venue=None（判定保留）**
- 2026-06-18使用時: venue_05_count=28 → 閾値クリア → **cond_sire_venue=True/False に評価される**

**この種牡馬の場合、2025年7月の東京レースを評価する際:**
- 正しい処理: データ不足でスキップ（None）
- 実際の処理: 2026年分も含む28頭のデータで評価（temporal leak）

**影響を受けるパターン:**

| ランク | パターン | 影響条件 | 重大度 |
|--------|---------|---------|--------|
| S-1 | ダート中距離\|全体 `margin+class_ok+interval_ok+surface_ok+f3_top+sire_venue` | sire_venue | 高 |
| S-2 | ダート中距離\|坂あり `margin+class_ok+f3_top+hill_fit+sire_venue` | sire_venue | 高 |
| S-3 | ダート中距離\|全体 `class_ok+interval_ok+surface_ok+f3_top+sire_venue` | sire_venue | 高 |
| A-1 | 芝中距離\|全体 `weight_ok+f3_top+straight_fit+hill_fit+sire_surface` | sire_surface | 中 |
| A-2 | ダート中距離\|長直線 `interval_ok+surface_ok+f3_top+sire_venue+sire_surface` | 両方 | 高 |
| 穴-1/2 | 芝短距離\|野芝 穴馬 `...+sire_venue+sire_surface` | 両方 | 中 |

**影響を受けないパターン（sire_venue/sire_surface を使わない）:**

| ランク | パターン |
|--------|---------|
| **B-1** | `class_ok+interval_ok+surface_ok+f3_top+sire_dist` → bloodline_feature_store 経由（OK） |
| **B-2** | `margin+class_ok+interval_ok+surface_ok+f3_top` → sire条件なし（完全クリーン） |
| 準穴-1 | `interval_ok+sire_dist+rc_fit+sire_surface` → sire_surface含む（中影響） |
| 準穴-2 | `margin+weight_ok+surface_ok+sire_surf` → bloodline経由（OK） |

**バイアスの方向:**
- `sire_venue` は「種牡馬の同会場実績が全体実績より高い」かを判定
- 将来データを含むと、会場ごとの成績集計が「より正確」（サンプル多）になる
- 実際の運用では使用時点のデータしか参照できないため、backtestの条件通過率は過小または過大評価される
- どちらに振れるかは種牡馬・会場ごとに異なる（系統的なバイアスの方向は不明）

---

### 1-c: jockey_ok（yr_wins部）のリーク（要注意）

**対象スクリプト:** `run_segment_search.py` のみ（`run_racecourse_search.py` では jr_wins 未取得のため発症しない）

**問題:**
```python
yr_wins = df["jockey_yr_wins_db"]  # jockeys.yr_wins を参照
lead = yr_wins >= 30               # リーディング騎手判定
```

`jockeys.yr_wins` は現在の年間勝利数（更新日時不明）。2025年7月のレースを評価する際、
2026年の勝利数が入っている可能性がある。

**影響の限定性:**
- `jockey_ok = cont OR lead`
- `cont`（継続騎乗）部分は正確
- `lead` 部分がリークの可能性あり
- しかし `run_segment_search.py` の結果（全セグメントの基本パターン探索）の数値には影響する

**影響を受けるスクリプトと結果:**
- `run_segment_search.py` → Phase2 セグメント別探索（jockey_ok を含むパターン）
- `run_racecourse_search.py` → `jockey_yr_wins_db` が未取得のため **影響なし**

---

### 1-d: bloodline_feature_store（OK）

`bloodline_feature_store` は `(horse_id, race_id)` をキーとして、各レース時点でのポイントインタイム特徴量が保存されている（`enrich_pedigree_v1.py` が `merge_asof` で正しくJOIN）。

```
enrich_pedigree_v1.py: "pandas.merge_asof で race_date <= target_date の最新スナップを JOIN"
```

`sire_turf_wr`, `sire_dirt_wr`, `sire_sprint_wr` 等はすべてこのテーブル経由。**リークなし**。

---

## 検証2: データ分割境界の遵守確認

**設定（shared/config.py）:**
```python
TRAIN_END_DATE  = "2025-05-31"   # ML学習データ終端
EVAL_START_DATE = "2025-06-01"   # ML検証データ開始
```

**Phase 2 分析スクリプトの対応:**

| スクリプト | EVAL_START_DATE 参照 | 実際の開始日 | 判定 |
|-----------|-------------------|------------|------|
| `run_segment_search.py` | **なし** | 2025-06-27 (arg) | 要注意 |
| `run_racecourse_search.py` | **なし** | 2025-06-27 (arg) | 要注意 |
| `run_final_validation.py` | **なし** | 2025-06-27 (arg) | 要注意 |
| `run_step3_sim.py` | **なし** | 自動検出（DB最新） | 要注意 |
| `tipster/backtest.py` | ○（定数定義のみ） | コメントで言及 | 要注意 |

**実害の有無:**
- 実際に使用された開始日 `2025-06-27` は `EVAL_START_DATE (2025-06-01)` より **26日後**
- ML学習データ（〜2025-05-31）は Phase 2 の探索期間と重複していない
- ただし `EVAL_START_DATE` はガードコードとして実装されておらず、誤って古い日付を渡すことができる

**結論:** 偶然に正しい日付が使われているが、コードレベルのガードはない。意図的な保護とは言えない。

---

## 検証3: 探索と検証の重複（NG）

**問題の核心:** Phase 1/2 の全工程が同一データ期間を使用している。

| フェーズ | 使用期間 | 目的 |
|---------|---------|------|
| Phase 2 セグメント別探索（`run_segment_search.py`） | 2025-06-27〜2026-06-27 | 「効く条件」を発見 |
| Phase 2 競馬場特性探索（`run_racecourse_search.py`） | 2025-06-27〜2026-06-27 | さらに絞り込み |
| 安定性検証（3期間分割） | 2025-06-27〜2026-06-27 | 同上データを3分割 |
| 最終検証 Step3（`run_step3_sim.py`） | 2026-05-16〜2026-06-14 | 直近の期間（探索範囲内） |

**何が問題か:**
- 探索フェーズで「複勝率66%」のパターンを発見した後、同じデータで安定性を確認した
- 3期間分割（P1/P2/P3）は「同じデータの中を3つに区切った」もの（真のホールドアウトではない）
- 数百のパターンを試した中から良いものだけを選んでいる → **多重比較問題**（Selection Bias）
- 66%という数字は「この1年に最も合致した条件を選んだ後の結果」であり、過去の再現性を保証しない

**真のホールドアウトデータが存在するか:**

| 期間 | 状態 | 備考 |
|------|------|------|
| 〜2025-05-31 | ML学習データ | tipsterは基本的に未使用だが、明示的な禁止コードはない |
| 2025-06-01〜2025-06-26 | 未使用（灰色地帯） | 小さい期間（26日） |
| 2025-06-27〜2026-06-27 | **全探索に使用** | ホールドアウトなし |
| 2026-06-27以降 | 未来（リアル検証） | ペーパートレードが唯一の検証 |

**正直な評価:**
「ダート中距離sire_venue条件の複勝率66-70%」という数字は、2025-06-27〜2026-06-27 という
特定の1年間に最もフィットした条件を選んだ結果であり、未来の同一条件での再現性を直接保証するものではない。

この点はリスクとして認識した上で実運用に移行する必要がある。

---

## 検証4: 種牡馬適性データの集計方法

**集計バッチ（`ml/batch/external_factor_store.py`）の設計:**

```
コメント: "target_date 未満のレース結果を一括 SELECT（リーク防止）"
```

集計バッチ自体は**正しくポイントインタイム設計**されている。
`target_date` 以前のレース結果のみを使って集計し、翌日分のスナップショットを保存する。

| 設計 | 内容 |
|------|------|
| 集計対象期間 | 2019-01-01〜2026-06-18（`MAX(target_date)`） |
| リーク防止 | target_date **未満** のデータで集計 |
| 最小サンプル数 | `_MIN_SAMPLES = 3`（3戦以上でスコア計算） |
| sire_venue の閾値 | 条件計算時に `count < 10` で None → 実質10戦以上必要 |

**問題はバッチではなく使用側のSQL:**

| 使用箇所 | SQL | 問題 |
|---------|-----|------|
| `run_racecourse_search.py` | `ORDER BY sire_id, target_date DESC` | 最新スナップのみ使用 → NG |
| `run_step3_sim.py` | 同上 | 同上 → NG |
| `enrich_pedigree_v1.py`（ML学習用） | `merge_asof(race_date, target_date)` | PIT正確 → OK |

**結論:** バッチ設計は正しいが、tipsterの条件探索スクリプトがPIT lookupを実装していない。
バッチが生成した正確なPITデータを捨てて、最新スナップのみ使っている。

---

## 影響範囲まとめ

### パターン別リーク状況

| ランク | パターン | sire_venue leak | 探索-検証重複 | 総合判定 |
|--------|---------|----------------|-------------|---------|
| S-1 | margin+class_ok+interval_ok+surface_ok+f3_top+sire_venue | **NG** | NG | **要再検証** |
| S-2 | margin+class_ok+f3_top+hill_fit+sire_venue | **NG** | NG | **要再検証** |
| S-3 | class_ok+interval_ok+surface_ok+f3_top+sire_venue | **NG** | NG | **要再検証** |
| A-1 | weight_ok+f3_top+straight_fit+hill_fit+sire_surface | 中程度NG | NG | 要注意 |
| A-2 | interval_ok+surface_ok+f3_top+sire_venue+sire_surface | **NG** | NG | **要再検証** |
| **B-1** | class_ok+interval_ok+surface_ok+f3_top+sire_dist | **OK** | NG | 条件リーク無し |
| **B-2** | margin+class_ok+interval_ok+surface_ok+f3_top | **OK（sire無し）** | NG | **最もクリーン** |
| 穴-1/2 | ...+sire_venue+sire_surface | NG | NG | 要注意 |

### B-2パターンの特別な位置づけ

`margin+class_ok+interval_ok+surface_ok+f3_top` は:
- sire条件を一切含まない → **時系列リークなし**
- bloodline_feature_store も不使用 → **完全クリーン**
- Phase2で53.2% / 387頭 / ROI87.9% という結果は条件リーク由来ではない

ただしこのパターンも「探索-検証重複」問題は残る（同一データで探索・検証）。

---

## 推奨修正方針（コード修正は別途）

### 1. sire_venue/sire_surface のPIT化（最優先）

**修正内容:**
```sql
-- 現在（NG）:
SELECT DISTINCT ON (sire_id) ... FROM sire_feature_store ORDER BY sire_id, target_date DESC

-- 修正案（OK）:
SELECT DISTINCT ON (sf.sire_id) sf.sire_id, sf.top3_rate AS sire_top3_rate, ...
FROM sire_feature_store sf
JOIN (
    SELECT sire_id, MAX(target_date) AS latest_before_race
    FROM sire_feature_store
    WHERE target_date < r.date  -- ← レース日より前
    GROUP BY sire_id
) pit ON sf.sire_id = pit.sire_id AND sf.target_date = pit.latest_before_race
```

または pandas での代替:
```python
# sire_df を target_date 付きで取得し、merge_asof で各レース日にJOIN
sire_all = pd.read_sql("SELECT * FROM sire_feature_store ORDER BY sire_id, target_date", engine)
df = pd.merge_asof(df.sort_values("date"), sire_all.sort_values("target_date"),
                   left_on="date", right_on="target_date",
                   left_by="sire_id", right_by="sire_id",
                   direction="backward")
```

**期待される影響:** sire_venue/sire_surface の通過率・的中率が変化する。
データ少の初期産駒は venue_count<10 でNoneになるケースが増える可能性。

### 2. EVAL_START_DATE のコードガード（中優先）

```python
# 各スクリプトの引数パース後に追加
from shared.config import EVAL_START_DATE
if from_date < date.fromisoformat(EVAL_START_DATE):
    raise ValueError(f"from_date {from_date} は EVAL_START_DATE {EVAL_START_DATE} より前。MLモデル学習データへのリーク防止のため中止。")
```

### 3. 探索-検証分離（中長期）

現実的な対処策:
- **A案（推奨）:** 2024-01-01〜2025-05-31 のデータをホールドアウトとして保持し、
  S/A ランクパターンをこの期間で再検証する
- **B案（簡易）:** 2026-06-28 以降のリアルレースをペーパートレードとして蓄積し、
  3ヶ月後に実績評価する（ユーザー指示どおりのSNS追跡）
- **C案（情報開示）:** 「このバックテスト結果は探索と検証が同一データ」であることをプロフィール等に明記

---

## 最終評価

### Phase 3 移行の可否

**移行可（留保条件付き）:**

1. **B-1・B-2パターン（sire条件なし）:** 条件リーク無し。探索-検証重複のみ。Phase 3 ペーパートレードから開始して実績を蓄積することで残リスクを解消可能。
2. **S/A ランクパターン:** sire_venue/sire_surface のリーク修正後に再バックテストを実施してから採用を最終判断すべき。現状の66-70%という数字は過大評価の可能性あり。
3. **穴推奨パターン:** sire_venue/sire_surface を含むため同上。複勝率の数値より ROI の信頼性に疑問。

**推奨アクション優先順位:**
1. `sire_feature_store` のPITルックアップ修正 → S/A パターンを再バックテスト
2. B-2パターン（`margin+class_ok+interval_ok+surface_ok+f3_top`）を最優先でペーパートレード開始（最もクリーンなパターン）
3. EVAL_START_DATE のコードガード実装
4. 並行して2024年データでの追加ホールドアウト検証
