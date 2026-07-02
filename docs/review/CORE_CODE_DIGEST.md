# CORE_CODE_DIGEST.md

本番で使用している中核コードの抜粋集。全文ではなく、各ファイルの「役割・重要な関数・特に見てほしい箇所」を抜粋する。
行数はすべて実ファイルの行番号（読み取り時点、2026-07-02）。

---

## 1. `pace_bias_ai/pipeline.py`（全163行）

**役割**: 「展開×バイアス」エンジンの第1層（特徴量パイプライン）オーケストレーター。既存の脚質特徴量（`src/features/pace_features_v4.py`）・隊列予想（`src/features/pace_simulation_v1.py`）と、本モジュール配下の新規特徴量（`layer1_horse.py`, `layer1_bias.py`）を決まった順序で結合する。

**重要な関数**:
- `build_layer1_features(df: pd.DataFrame) -> pd.DataFrame` — DBなし版。Parquet一括処理向け。
- `build_layer1_features_with_db(df: pd.DataFrame, conn) -> pd.DataFrame` — DBあり版。当日バイアスを実DBから取得。
- `validate_layer1_output(df) -> dict[str, float]` — 各列のNaN率を返す品質チェック用ユーティリティ。

**抜粋（L1-30, L64-112）**:
```python
1	"""
2	pace_bias_ai/pipeline.py
6	【修正2: バイアス→隊列の正しい因果順】
7	    旧: pace_v4 → pace_sim → layer1_horse → bias
8	    新: pace_v4 → bias → pace_sim → layer1_horse → harmony
9	    理由:
10	        1. 当日バイアスを先に計算（騎手が「狙いたい位置」を決める材料）
11	        2. 隊列予想は「馬の傾向×バイアス情報を使える状態で」実行
12	        3. bias_position_harmony はバイアス+予測位置の両方を必要とするため最後に計算
...
30	"""
```
```python
65	def build_layer1_features(df: pd.DataFrame) -> pd.DataFrame:
77	    log.info("[Layer1] 第1層特徴量生成開始: %d行", len(df))
79	    # Step1: 脚質特徴量 (既存) — 馬の自然な脚質を把握
81	    df = create_pace_features_v4(df)
83	    # Step2: バイアス特徴量 (DBなし → デフォルト値)
86	    df = compute_venue_bias_features(df, conn=None)
87	    df = compute_day_bias_features(df, conn=None)
88	    df = attach_prev_week_bias_to_df(df, conn=None)
90	    # Step3: 隊列予想 (既存)
93	    if all(c in df.columns for c in ["avg_c1_norm_5", "umaban"]):
94	        df = create_pace_simulation_features(df)
95	    else:
96	        log.warning("[Layer1] avg_c1_norm_5 が未生成 — pace_simulationをスキップ")
97	        for col in PACE_SIM_COLS:
98	            if col not in df.columns:
99	                df[col] = 0.5
101	    # Step4: 馬単位新規特徴量
103	    df = create_layer1_horse_features(df)
105	    # Step5: バイアス×ポジション整合度
108	    df = compute_bias_position_harmony(df)
111	    return df
```

**特に見てほしい箇所**:
- L6-12: 特徴量計算の因果順序が「修正2」として明示的にドキュメント化されている（バイアス→隊列予想の順）。この順序を崩す変更は過去のリグレッションの再発リスクがある。
- L93-99: `avg_c1_norm_5` / `umaban` が欠けている場合、隊列予想をスキップして**全馬に固定値0.5を代入**するサイレントフォールバック。異常が起きても例外を投げず、ログ警告のみで処理が継続する。

---

## 2. `pace_bias_ai/models/layer2_model.py`（全381行）

**役割**: 第2層 LightGBM `lambdarank` モデルの学習・評価ロジック（walk-forward OOF、フィルター精度評価、SHAP重要度）。

**重要な関数**:
- `walk_forward_oof(df, feature_cols, folds=None, lgb_params=None) -> (oof_scores, fold_results, last_model)` — 時系列Fold単位でOOF予測を実行。
- `_build_target(df) -> np.ndarray` — 着順を関連度ラベル(0〜4)に変換。
- `compute_filter_metrics(df, score_col, n_top=5) -> dict` — カバー率・候補精度・複勝率を計算。
- `train_full_model(df, feature_cols, ...) -> lgb.Booster` — A期間全データでの本番用フルモデル学習。

**抜粋（L33-40, L90-105, L236-256）**:
```python
33	# ── デフォルト Folds ─────────────────────────────────────────────
34	DEFAULT_FOLDS: list[tuple[str, str, str, str]] = [
35	    # DBの race_entries_v2 は2022年以降のみ存在するため、2022年から設定
36	    ("2022-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
37	    ("2022-01-01", "2023-12-31", "2024-01-01", "2024-06-30"),
38	]
```
```python
90	def _build_target(df: pd.DataFrame) -> np.ndarray:
93	    """1着=4, 2着=3, 3着=2, 4〜5着=1, 6着以下=0"""
99	    rank = pd.to_numeric(df["kakutei_chakujun"], errors="coerce").fillna(99)
100	    relevance = np.select(
101	        [rank == 1, rank == 2, rank == 3, rank <= 5],
102	        [4,         3,         2,         1],
103	        default=0,
104	    )
105	    return relevance.astype(np.int32)
```
```python
236	        # 早期停止用: 学習データの最後3ヶ月を内部Validationに使用
237	        es_cutoff  = pd.Timestamp(tr_end) - pd.DateOffset(months=3)
238	        es_tr_mask = df_tr["race_date"] <= es_cutoff
239	        es_vl_mask = ~es_tr_mask
```

**特に見てほしい箇所**:
- L34-40: `DEFAULT_FOLDS` の日付範囲がハードコード。コメント「race_entries_v2 は2022年以降のみ存在」は事実として要確認（未確認。DBの実データレンジをレビュアー側で検証推奨）。
- L18-20（モジュールdocstring）: 「Fold境界: train_end < val_start を厳守」という設計方針があるが、それを**コード側で機械的に検証するアサーションは本関数内に見当たらない**（`DEFAULT_FOLDS` を書き換えるだけでリーク設定が可能）。
- L236-238: 早期停止用の内部バリデーションはFold内の学習データの直近3ヶ月を使う設計。`tr_end` より後を使わないためリークはしていないが、この境界もハードコードされた日付操作に依存。

---

## 3. `pace_bias_ai/opponent_model/features.py`（全454行）

**役割**: 「前走・前々走の対戦相手レベル」特徴量生成（v3: 前々走ベース+クラス変動対策）。全特徴量はPIT-safe設計（未来情報を混入させない）が明示的な設計目標。

**重要な関数**:
- `load_all_race_history(engine) -> (df_entries, df_races)` — 2019年以降の全レース履歴をDBからロード。
- `build_opponent_features(df_target, df_entries, df_races) -> pd.DataFrame` — メインのPIT-safe特徴量生成。
- `_build_opp_agg(slim, opp_next, prefix, include_top3_filter)` — 対戦相手の「次走」成績を集計。

**抜粋（L1-22, L118-136, L177-193）**:
```python
1	"""
2	前走メンバーレベル特徴量の生成モジュール（v3: 前々走ベース + クラス変動対策）。
4	全特徴量はPIT-safe（当該レース結果を使わない）。
5	opponent_next_* は「予測日より前に次走を走った馬のみ」でカウント。
20	クラス序列（class_rank: 低いほど上位）:
21	  1=G1, 2=G2, 3=G3, 4=OP/L, 5=3勝, 6=2勝, 7=1勝, 8=未勝利, 9=新馬
22	"""
```
```python
118	def load_all_race_history(engine) -> tuple[pd.DataFrame, pd.DataFrame]:
128	    with engine.connect() as conn:
129	        log.info("race_entries_v2 ロード中（2019年以降）...")
130	        df_entries = pd.read_sql(sqlalchemy.text("""
131	            SELECT blood_no, race_id, kakutei_chakujun, race_time,
132	                   kinryo, horse_age, horse_weight, umaban
133	            FROM race_entries_v2
134	            WHERE LEFT(race_id,8) >= '20190101'
135	              AND kakutei_chakujun IS NOT NULL
136	        """), conn)
```
```python
177	    slim_valid = slim.dropna(subset=['prev_race_id'])
179	    merged = slim_valid.merge(opp_next, on='prev_race_id', how='left')
180	    merged = merged[
181	        merged['opp_next_date'].notna() &
182	        (merged['opp_next_date'] < merged['_cur_date']) &
183	        (merged['opp_bn'] != merged['_bn'])
184	    ]
```

**特に見てほしい箇所**:
- L181-183: **PITフィルタの核心部分**。`opp_next_date < _cur_date`（対象馬の対戦相手の"次走"日付が、今回予測対象レースの日付より前）で未来情報の混入を防いでいる。この一行の論理が崩れるとリーク（未来情報の学習・推論混入）が起きる。レビュー時に最優先で検証すべき箇所。
- `opp_bn != _bn`（自分自身を対戦相手の集計から除外）も同じ行で担保されている。
- L134: `race_entries_v2` は2019年以降のみロード対象。この日付フィルタが本番の対象馬の初出走（2019年以前デビュー馬）を欠落させる可能性は未確認。

---

## 4. `scripts/generate_ai_picks.py`（全943行）

**役割**: 本番AI推奨の生成スクリプト。v1モデル（脚質・バイアス系）× opponent_v3モデル（対戦相手レベル系）のアンサンブル（α=0.5）で `data/output/tipster/ai_picks.json` を出力する。**現在の本番予測パイプラインのエントリポイント**。

**重要な関数**:
- `generate_ai_picks(target_dates=None) -> dict` — メイン処理。週末レース取得→モデルロード→レースごとにスコアリング→JSON出力。
- `score_race_ai(race_meta, entries, model_v1, model_opp, engine_jvdl, df_ent_hist, df_races_hist) -> dict | None` — 1レース分のスコアリング（バックフィル等でも再利用）。
- `_load_pace_v4_history(engine, horse_ids, before_date) -> pd.DataFrame` — 対象馬の過去走をJVDL DBから都度ロード。
- `_blend_normalized(v1_scores, opp_scores, alpha=0.5) -> (v1_norm, opp_norm, blend)` — レース内min-max正規化後にブレンド。

**抜粋（L1-14, L260-309, L386-397）**:
```python
1	"""
2	scripts/generate_ai_picks.py
4	v1 × opponent_v3 アンサンブル (α=0.5) で週末AI推奨を生成する。
8	設計方針:
9	  - 既存の generate_picks_report.py / conditions_v2.py は変更しない
10	  - 当日バイアスなし (day_front_bias_pit=0) で動作
11	  - PACE_V4_COLS は静的parquetではなく JVDL DB から対象馬の全確定済み過去走を
12	    都度ロードして計算する（opponent 特徴量と同じ設計。parquet陳腐化を構造的に防止）
14	"""
```
```python
260	def _load_pace_v4_history(
261	    engine: sqlalchemy.engine.Engine,
262	    horse_ids: list[str],
263	    before_date: str,
264	) -> pd.DataFrame:
265	    """対象馬の全確定済み過去走を JVDL DB から都度ロードする（parquet非依存）。
267	    静的parquetのように再生成を忘れると陳腐化する問題が構造的に起きない
268	    （opponent_model.features.load_all_race_history と同じ設計思想）。
275	    ...
280	    sql = sqlalchemy.text("""
281	        SELECT e.blood_no AS horse_id, e.race_id, e.umaban,
282	               e.corner_1, e.corner_4, e.kakutei_chakujun,
283	               e.kohan_3f AS go_3f_time, e.kishu_code AS jockey_cd,
284	               r.distance, r.track_code
285	        FROM race_entries_v2 e
286	        JOIN races_v2 r ON r.race_id = e.race_id
287	        WHERE e.blood_no IN :horse_ids
288	          AND LEFT(e.race_id, 8) < :before_date
289	          AND e.kakutei_chakujun IS NOT NULL
290	        ORDER BY e.blood_no, e.race_id
291	    """).bindparams(bindparam("horse_ids", expanding=True))
```
```python
386	    # field_size: 過去走は umaban(確定済み)の最大値から算出。
387	    # 予測対象レースは umaban(枠番)が未確定だと 0 になり出走頭数を著しく過小評価する
388	    # （例: 16頭立てが 2頭立て相当に crush → field_size_norm が誤って 0 になる）ため、
389	    # races.syusso_tosu (field_size_meta, 出走投票時点で既に確定) で補完する。
390	    combined["field_size"] = pd.to_numeric(
391	        combined.groupby("race_id")["umaban"].transform("max"), errors="coerce"
392	    )
392	    if "field_size_meta" in combined.columns:
394	        meta_fs = pd.to_numeric(combined["field_size_meta"], errors="coerce")
395	        needs_meta = combined["field_size"].fillna(0) <= 0
396	        combined.loc[needs_meta, "field_size"] = meta_fs[needs_meta]
```

**特に見てほしい箇所**:
- L11-12, L265-274: 「parquet陳腐化」バグの修正コード本体。旧実装は静的parquetファイルを特徴量ソースとして使っており、更新を忘れると古いデータのまま予測していた。現在はDBから都度ロードする設計に変更（`KNOWN_ISSUES_AND_HISTORY.md` 参照）。
- L288: `LEFT(e.race_id, 8) < :before_date` がPITガード。予測対象日以降のレースを過去走として混入させない。
- L386-397: `field_size` バグの修正コード。枠番未確定時に出走頭数が0扱いになり `field_size_norm` が壊れていた問題への対処（`races.syusso_tosu` でフォールバック）。
- L892-893, L785-788（本文参照): `score_race_ai` 内で v1特徴量計算が失敗すると `None` を返してレース自体をスキップする（`race_results` に含まれない=画面に出ない）ため、失敗の可視化がログのみになっている。

---

## 5. `tipster/engine.py`（全754行）

**役割**: 予想家フレームワークの条件ベース推奨エンジン。戦略JSON（`tipster/strategies/*.json`）をロードし、各馬に条件を順に適用して合否判定・ランキングする。

**重要な関数**:
- `evaluate_race(race_id, strategy) -> RaceEvaluation` — DB取得+評価のエントリポイント。
- `fetch_race_context(race_id) -> RaceContext` — `race_detail_cache` 優先、無ければライブ計算。
- `evaluate_race_context(race_ctx, strategy, max_selections=None) -> RaceEvaluation` — 純粋関数（DBアクセスなし）。バックテストでも再利用される。
- `select_honmei(candidates, umaban_map, ...) -> HorseEvaluation | None` — 本命選定（条件クリア数→合計スコア→AIスコア→馬番の順で決定的に1頭選ぶ）。

**抜粋（L113-135, L172-201, L378-385）**:
```python
113	def _fetch_past_race_extra(race_ids: set[str]) -> dict[str, dict]:
116	    """2026-07 修正: 従来 ml.db.engine (fukurou_jvdl) の races テーブル
117	    （JVDLフォーマット・旧スキーマ）を参照していたが、このテーブルは
118	    bulk_ingest_v2 が書き込まなくなって以降更新が止まっている
119	    「旧・未使用」テーブル（2026-06-14で停止）。実際に最新データが
120	    入り続けている races_v2 を参照するよう修正した。
121	    """
127	        rows = conn.execute(
128	            text("""
129	                SELECT race_id, grade_code, keibajo_code AS place_code, jyoken_cd_3
130	                FROM   races_v2
131	                WHERE  race_id = ANY(:ids)
132	            """),
```
```python
172	def _fetch_race_meta(race_id: str) -> dict:
173	    """2026-07 修正: races（旧・未使用テーブル）ではなく races_v2 を参照する。
174	    races_v2 には course_type(日本語表記)・date(DATE型)の列が無いため、
175	    track_code から変換し、race_id の先頭8桁から日付を復元する。
176	    """
```
```python
378	def _to_db_race_id(race_id: str) -> str:
379	    """race_detail_cache の payload.extra.past_races[].race_id (JV-Data 生形式・16桁) を返す。
381	    2026-07 修正: 以前は races.id の12桁形式（日付8+場2+R番2、旧・未使用テーブル用）に
382	    変換していたが、参照先を races_v2/race_entries_v2（16桁ネイティブ）に統一したため
383	    変換は不要になった。呼び出し元の互換のため関数自体は残し、恒等関数にしている。
384	    """
385	    return race_id
```

**特に見てほしい箇所**:
- L113-135, L172-176, L378-385: いずれも「旧・未使用テーブル参照」バグの修正跡。同一ファイル内に少なくとも3箇所、修正コメント付きで残っている（他ファイルにも同種の修正があり、合計8箇所とユーザーから報告されている。他7箇所は本ファイルの外）。修正パターンが繰り返されていること自体が、DB参照先の一貫性を保証する仕組み（型やconstでのテーブル名一元管理等）が無いことを示唆している（未確認: 一元管理の仕組みの有無は `KNOWN_ISSUES_AND_HISTORY.md` の分析対象）。
- L644-652 (`compute_confidence`): 本命自信度をS/A/B/Cにラベル化するしきい値がハードコード。

---

## 6. `shared/worker/job_runner.py`（全1306行）

**役割**: ジョブキューワーカー（`fukurou_jvdl.jobs` テーブルをポーリングして非同期処理を実行）。特徴量ストア更新・JVDL同期・予測再計算・tipster評価・実績更新などのジョブハンドラを一元的に処理する。

**重要な関数**:
- `run_worker() -> None` — メインループ。advisory lockで多重起動防止、キューをドレインしてからアイドル時間経過で自動終了。
- `_process_one(conn) -> str | None` — `FOR UPDATE SKIP LOCKED` でジョブを1件取り出し実行。
- `_reset_orphan_jobs(conn) -> None` — 前回クラッシュで`running`のまま残ったジョブを`failed`に戻す。
- `register(job_type)` — デコレータでジョブハンドラを登録。

**抜粋（L44-61, L1044-1071, L1256-1300）**:
```python
44	logger = logging.getLogger(__name__)
49	POLL_INTERVAL = 5          # 秒
53	_HEALTH_CHECK_HOUR_JST = 9
54	_ADVISORY_LOCK_KEY = 42002  # ワーカー起動唯一性保証（batch_predictor の 42001 と別）
56	# ── アイドル自動終了 ─────────────────────────────────────
57	# 常駐させない運用方針: 起動時にキューを一括処理（ドレイン）した後、
58	# この秒数だけ新規ジョブが来なければ自動終了する。
59	# 0 を指定すると無効化（旧来通り常駐し続ける）。
60	_IDLE_EXIT_ENV = "WORKER_IDLE_EXIT_SECONDS"
61	_DEFAULT_IDLE_EXIT_SECONDS = 120
```
```python
1044	_SQL_DEQUEUE = """
1045	SELECT id, job_type, params
1046	FROM   jobs
1047	WHERE  status = 'queued'
1048	ORDER  BY created_at
1049	LIMIT  1
1050	FOR UPDATE SKIP LOCKED
1051	"""
1121	_SQL_RESET_ORPHANS = """
1122	UPDATE jobs
1123	SET    status = 'failed',
1124	       finished_at = now(),
1125	       log_tail = COALESCE(log_tail || E'\\n', '') || '[worker-restart] 起動時に孤児 running ジョブをリセット'
1126	WHERE  status = 'running'
1127	RETURNING id
1128	"""
```
```python
1256	    work_conn = psycopg2.connect(**DB_JVDL)
1257	    _reset_orphan_jobs(work_conn)
1273	                else:
1274	                    idle_for = time.monotonic() - last_activity
1275	                    if idle_exit_seconds > 0 and idle_for >= idle_exit_seconds:
1276	                        logger.info("新規ジョブなしのまま%d秒経過したため終了します。", idle_exit_seconds)
1277	                        break
1278	                    time.sleep(POLL_INTERVAL)
```

**特に見てほしい箇所**:
- L56-61, L1248-1253: ワーカーは「常駐させない運用方針」を明示的にコメントしており、アイドル120秒で自動終了する設計。これは「ワーカー滞留」バグ対策後の設計と推測される（`KNOWN_ISSUES_AND_HISTORY.md` 参照、詳細は未確認＝コミット履歴で裏付け要）。
- L1121-1128: クラッシュ時に`running`のまま残ったジョブを起動時に`failed`へ戻す処理。裏を返すと、ワーカーが起動しない限り「滞留したrunningジョブ」はDB上に残り続ける。
- L615-620（`_handle_recompute_predictions`内コメント）: 意図的なレイヤー違反（`shared/worker` から `api_v2.services` を遅延importしている）についての設計判断メモがコード中に残っている。アーキテクチャ上の既知の妥協点。

---

## 7. `api_v2/routers/tipster.py`（全797行）

**役割**: 予想家(Tipster)系APIエンドポイント群。`tipster/` 配下のロジックには手を入れず、薄いラッパーとして呼び出すだけの設計方針が明記されている。

**主なエンドポイント**（L6-14のdocstring一覧より）:
- `GET /api/v2/tipster/recent-results`, `/cumulative-stats`, `/weekly-overview`
- `POST /log`, `GET /log`（SNS出力用ログ）
- `GET /weekend`, `POST /refresh`（条件ベースpicks）
- `GET /ai-picks`, `POST /ai-refresh`（v1×opponent AI推奨、`generate_ai_picks.py` を subprocess 実行）
- `GET /data-freshness`（データ鮮度チェック、読み取り専用）

**抜粋（L301-337, L674-690, L760-789）**:
```python
301	    # ── jvdl(races_v2) フォールバック ─────────────────────────
302	    # keiba_v2 にまだ入っていない日（JV-Link同期直後・DB同期前の今週末レース等）を
303	    # races_v2 から補完する。races_v2 は fukurou_jvdl 側の改良版スキーマで、
304	    # bulk_ingest_v2 が継続的に書き込んでいる（旧・未使用の races/race_entries
305	    # とは別テーブル）。
306	    covered_dates = {r["race_date"] for r in race_rows}
```
```python
674	@router.get("/data-freshness", response_model=DataFreshnessResponse)
675	def get_data_freshness(
676	    target_dates: str | None = Query(
677	        None, description="カンマ区切り YYYY-MM-DD。省略時は今週末"
678	    ),
679	):
680	    """予想対象レースに対するデータ鮮度をチェックして返す。
684	    呼び、同期を飛ばしたまま古いデータで予想が生成されていないかを表示するための
685	    読み取り専用チェック。
686	    """
```
```python
760	    if total > 0 and unconfirmed > 0:
761	        warnings.append(FreshnessWarning(
762	            level="warning",
763	            code="umaban_unconfirmed",
764	            ...
768	    min_target = min(dates)
769	    770	    if races_max and races_max < min_target and (not results_max or results_max < races_max):
771	        lag_days = (races_max - results_max).days if results_max else None
772	        level = "critical" if (lag_days is None or lag_days >= _SYNC_WARNING_DAYS) else "warning"
```

**特に見てほしい箇所**:
- L163, L196, L278, L377: `psycopg2.connect(**DB_V2)` を各エンドポイントで個別に開閉している（コネクションプーリング不使用）。エンドポイント数が多く、負荷時の挙動は未確認。
- L301-337: `races`（keiba_v2側の本番テーブル）と `races_v2`（jvdl側）を併用する設計。DB間の二重管理・フォールバックロジックが複数箇所に分散している点はレビュー対象。
- L692-725: `get_data_freshness` は複数の `MAX(race_date)` クエリを直列実行しており、テーブルスキャンコストは未確認（インデックス設計はDB定義ファイル側の確認が必要）。
- L587-625 (`post_ai_refresh`): `generate_ai_picks.py` を `subprocess.run(..., timeout=600)` で実行。本番の予測生成トリガーがHTTP POST経由の同期的サブプロセス実行になっている（非同期ジョブキュー化はされていない＝`job_runner.py` の `recompute_predictions` とは別経路）。
