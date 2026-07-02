# SNS実績追跡システム 設計書

**設計日:** 2026-06-27
**目的:** 推奨した馬の的中/不的中を記録し、ランク別累計的中率を算出・SNS表示する

---

## 1. テーブル設計

### 1-1. `tipster_picks` テーブル（推奨馬記録）

```sql
CREATE TABLE tipster_picks (
    id             BIGSERIAL PRIMARY KEY,
    pick_date      DATE           NOT NULL,          -- 推奨日（レース当日）
    race_id        VARCHAR(20)    NOT NULL,           -- races.id
    horse_id       VARCHAR(20)    NOT NULL,           -- horses.id
    umaban         INTEGER        NOT NULL,           -- 馬番
    horse_name     VARCHAR(50),                       -- 馬名（非正規化・表示用）
    strategy_name  VARCHAR(50)    NOT NULL,           -- 戦略名（例: dirt_mid_all_s）
    rank_label     VARCHAR(10)    NOT NULL,           -- S / A / B / anaba
    pattern_label  VARCHAR(200),                      -- 条件組み合わせ名（表示用）
    segment        VARCHAR(30),                       -- ダート中距離|全体 等
    popularity     INTEGER,                           -- 人気（1=1番人気）
    published_at   TIMESTAMPTZ    DEFAULT now(),      -- SNS投稿タイムスタンプ（任意）
    -- 結果（レース後に更新）
    actual_rank    INTEGER,                           -- 実際の着順（NULL=未確定）
    win_flag       BOOLEAN,                           -- 1着かどうか
    place_flag     BOOLEAN,                           -- 3着以内かどうか
    win_payout     INTEGER,                           -- 単勝払戻（円、なければNULL）
    place_payout   INTEGER,                           -- 複勝払戻（円）
    result_updated_at TIMESTAMPTZ,                   -- 結果更新日時
    UNIQUE (race_id, horse_id, rank_label)            -- 同一レース×馬×ランクの重複防止
);

CREATE INDEX idx_picks_date ON tipster_picks (pick_date);
CREATE INDEX idx_picks_rank ON tipster_picks (rank_label, pick_date);
CREATE INDEX idx_picks_strategy ON tipster_picks (strategy_name, pick_date);
```

### 1-2. `tipster_daily_stats` ビュー（日別集計）

```sql
CREATE VIEW tipster_daily_stats AS
SELECT
    pick_date,
    rank_label,
    COUNT(*)                                          AS total_picks,
    SUM(CASE WHEN place_flag THEN 1 ELSE 0 END)      AS place_hits,
    SUM(CASE WHEN win_flag   THEN 1 ELSE 0 END)      AS win_hits,
    ROUND(
        100.0 * SUM(CASE WHEN place_flag THEN 1 ELSE 0 END)
        / NULLIF(COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END), 0),
        1
    )                                                 AS place_rate_pct,
    SUM(place_payout)                                 AS place_return,
    SUM(win_payout)                                   AS win_return,
    COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END) AS settled_count
FROM tipster_picks
GROUP BY pick_date, rank_label;
```

---

## 2. 結果更新フロー

```
[レース後 ~18:00]
    JV-Link 結果取得 (sync_jvdata --dataspecs RACE)
         ↓
    update_feature_stores ジョブ実行
    (admin API → races / race_entries テーブル更新)
         ↓
    [スクリプト] scripts/update_pick_results.py
      - race_entries.confirmed_rank を tipster_picks.actual_rank に反映
      - place_flag = actual_rank <= 3
      - win_flag   = actual_rank == 1
      - race_payouts から win_payout / place_payout を取得
      - result_updated_at = now()
```

### update_pick_results.py の設計（実装時の参考）

```python
# 未確定ピックを取得
pending = SELECT id, race_id, umaban FROM tipster_picks WHERE actual_rank IS NULL

# race_entries から結果を JOIN
result = SELECT e.confirmed_rank, p.fukusho AS place_payout, t.tansho AS win_payout
         FROM race_entries e
         LEFT JOIN race_payouts p ON ...
         WHERE e.race_id = :race_id AND e.horse_number = :umaban

# tipster_picks を UPDATE
UPDATE tipster_picks SET actual_rank=..., place_flag=..., result_updated_at=now()
WHERE id = :id
```

---

## 3. 累計的中率クエリ設計

### 3-1. ランク別累計（SNSプロフィール表示用）

```sql
-- 直近30日 / 直近90日 / 累計
SELECT
    rank_label,
    COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END)          AS settled,
    SUM(CASE WHEN place_flag THEN 1 ELSE 0 END)                  AS place_hits,
    ROUND(
        100.0 * SUM(CASE WHEN place_flag THEN 1 ELSE 0 END)
        / NULLIF(COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END), 0),
        1
    )                                                             AS place_rate,
    SUM(CASE WHEN win_flag THEN 1 ELSE 0 END)                    AS win_hits,
    ROUND(
        100.0 * SUM(CASE WHEN win_flag THEN 1 ELSE 0 END)
        / NULLIF(COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END), 0),
        1
    )                                                             AS win_rate
FROM tipster_picks
WHERE pick_date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY rank_label
ORDER BY rank_label;
```

### 3-2. 回収率算出（投資効果確認用）

```sql
SELECT
    rank_label,
    COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END)  AS n,
    ROUND(
        SUM(place_payout)::NUMERIC
        / NULLIF(COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END), 0) / 100.0,
        2
    )                                                     AS place_roi_pct,
    ROUND(
        SUM(win_payout)::NUMERIC
        / NULLIF(COUNT(CASE WHEN actual_rank IS NOT NULL THEN 1 END), 0) / 100.0,
        2
    )                                                     AS win_roi_pct
FROM tipster_picks
WHERE pick_date >= :from_date
GROUP BY rank_label;
```

---

## 4. SNSプロフィール表示フロー

### 4-1. データ更新スケジュール

```
毎週月曜 朝 (バッチ処理)
  1. scripts/update_pick_results.py 実行
  2. SELECT ランク別累計的中率クエリ実行
  3. 結果を data/output/tipster/sns_stats.json に出力
  4. SNS表示テキスト生成

JSONフォーマット例:
{
  "updated_at": "2026-06-30T09:00:00+09:00",
  "period_30d": {
    "S": {"settled": 12, "place_hits": 9, "place_rate": 75.0, "win_rate": 25.0},
    "A": {"settled": 24, "place_hits": 14, "place_rate": 58.3, "win_rate": 20.8},
    "B": {"settled": 48, "place_hits": 25, "place_rate": 52.1, "win_rate": 16.7},
    "anaba": {"settled": 8, "place_hits": 3, "place_rate": 37.5, "win_rate": 12.5}
  },
  "cumulative": { ... }
}
```

### 4-2. SNSプロフィール文言テンプレート

```
[一押し] 直近30日: 9/12頭 的中率75.0%
[二押し] 直近30日: 14/24頭 的中率58.3%
[三押し] 直近30日: 25/48頭 的中率52.1%
※複勝（3着以内）的中率。バックテスト期間2025-2026年。
```

---

## 5. ランク→戦略名マッピング（参考）

| ランク | 戦略名 | セグメント | 条件 |
|--------|--------|----------|------|
| S-1 | `dirt_mid_hill_sire` | ダート中距離\|坂あり | margin+class_ok+f3_top+hill_fit+sire_venue |
| S-2 | `dirt_mid_all_sire` | ダート中距離\|全体 | class_ok+interval_ok+surface_ok+f3_top+sire_venue |
| S-3 | `dirt_mid_all_sire6` | ダート中距離\|全体 | margin+class_ok+interval_ok+surface_ok+f3_top+sire_venue |
| A-1 | `turf_mid_all_fit` | 芝中距離\|全体 | weight_ok+f3_top+straight_fit+hill_fit+sire_surface |
| A-2 | `dirt_mid_long_sire` | ダート中距離\|長直線 | interval_ok+surface_ok+f3_top+sire_venue+sire_surface |
| B-1 | `dirt_mid_base5` | ダート中距離\|全体 | class_ok+interval_ok+surface_ok+f3_top+sire_dist |
| B-2 | `dirt_mid_base6` | ダート中距離\|全体 | margin+class_ok+interval_ok+surface_ok+f3_top |
| anaba-1 | `turf_short_noshiba_anaba` | 芝短距離\|野芝 | margin+jockey_ok+sire_venue+sire_surface (4番人気以降) |
| anaba-2 | `turf_short_noshiba_anaba5` | 芝短距離\|野芝 | margin+jockey_ok+weight_ok+sire_venue+sire_surface (4番人気以降) |

---

## 6. 実装優先度

| 優先度 | タスク | 理由 |
|--------|--------|------|
| 最高 | `tipster_picks` テーブル作成 | 推奨記録の起点 |
| 高 | `update_pick_results.py` | 結果自動反映 |
| 中 | SNS stats JSON 生成スクリプト | プロフィール更新自動化 |
| 低 | Web UI (HTML) での可視化 | 追跡ダッシュボード |

最小実装: テーブル作成 + 手動 UPDATE で的中記録 → SQL クエリで的中率を随時確認
