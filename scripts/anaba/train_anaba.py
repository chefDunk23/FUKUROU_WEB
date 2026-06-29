"""
scripts/anaba/train_anaba.py
=============================
穴馬AI フルパイプライン: 特徴量生成 → サブモデル学習 → メタモデル学習 → 検証。

Usage:
    py -3.13 scripts/anaba/train_anaba.py
    py -3.13 scripts/anaba/train_anaba.py --parquet outputs/bloodline_features_v1_2022plus.parquet
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd

from anaba_ai.config import MODEL_DIR, RESULTS_DIR, SUBMODEL_DEFS
from anaba_ai.evaluate import full_evaluation_report
from anaba_ai.models.meta_model import (
    get_submodel_importances,
    predict_anaba_score,
    train_meta_model,
)
from anaba_ai.models.sub_models import predict_submodels, train_all_submodels
from anaba_ai.pipeline import load_and_prepare, split_periods

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _summarize_importances(imps: dict) -> dict:
    """fold 間で平均した特徴量重要度を返す。"""
    # imps = {submodel_name: [fold1_dict, fold2_dict, ...]}
    summary = {}
    for name, fold_imps in imps.items():
        if not fold_imps:
            continue
        all_feats = set()
        for fi in fold_imps:
            all_feats.update(fi.keys())
        avg = {}
        for feat in all_feats:
            vals = [fi.get(feat, 0.0) for fi in fold_imps]
            avg[feat] = float(np.mean(vals))
        # 上位 10 件
        top10 = sorted(avg.items(), key=lambda x: -x[1])[:10]
        summary[name] = top10
    return summary


def _write_results(
    results: dict,
    imp_summary: dict,
    meta_imps: dict,
    parquet_path: str,
) -> None:
    """ANABA_AI_RESULTS.md を生成する。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_md = _ROOT / "ANABA_AI_RESULTS.md"

    lines = ["# 穴馬AI v1 検証結果", "", f"> 入力Parquet: `{parquet_path}`", ""]

    # ── サブモデル特徴量重要度 ────────────────────────────────────────────────
    lines += ["## 1. サブモデル別 特徴量重要度 TOP 10", ""]
    for name, top10 in imp_summary.items():
        lines.append(f"### {name}")
        lines.append("| Rank | 特徴量 | 相対重要度 |")
        lines.append("|------|--------|-----------|")
        for rank, (feat, val) in enumerate(top10, 1):
            lines.append(f"| {rank} | `{feat}` | {val:.1%} |")
        lines.append("")

    # ── メタモデルのサブモデル寄与度 ─────────────────────────────────────────
    lines += ["## 2. メタモデル サブモデル寄与度", ""]
    lines.append("| サブモデル | 寄与度 |")
    lines.append("|-----------|--------|")
    for feat, share in sorted(meta_imps.items(), key=lambda x: -x[1]):
        lines.append(f"| `{feat}` | {share:.1%} |")
    lines.append("")

    # ── C 期間検証結果 ────────────────────────────────────────────────────────
    overall   = results.get("overall", [])
    anaba_only = results.get("anaba_only", [])
    by_ninki  = results.get("by_ninki", [])
    natural   = results.get("natural_rates", {})

    lines += ["## 3. C 期間検証結果（2024-07〜）", ""]
    lines.append("### 全馬対象（TOP N 推奨）")
    lines.append("| TOP N | 賭数 | 単勝的中率 | 単勝ROI | 複勝的中率 | 平均人気 |")
    lines.append("|-------|------|-----------|---------|-----------|---------|")
    for r in overall:
        lines.append(
            f"| TOP{r['top_n']} | {r['total_bets']:,} | "
            f"{r['win_hit_rate']:.1%} | {r['win_roi']:.1f}% | "
            f"{r['place_hit_rate']:.1%} | {r['avg_ninki']:.1f} |"
        )
    lines.append("")

    lines.append("### 4番人気以降限定（穴馬フィルタ）")
    lines.append("| TOP N | 賭数 | 単勝的中率 | 単勝ROI | 複勝的中率 | 平均人気 |")
    lines.append("|-------|------|-----------|---------|-----------|---------|")
    for r in anaba_only:
        lines.append(
            f"| TOP{r['top_n']} | {r['total_bets']:,} | "
            f"{r['win_hit_rate']:.1%} | {r['win_roi']:.1f}% | "
            f"{r['place_hit_rate']:.1%} | {r['avg_ninki']:.1f} |"
        )
    lines.append("")

    lines.append("### 人気帯別 精度（TOP1推奨）")
    lines.append("| 人気帯 | 賭数 | AI的中率 | 自然確率 | 単勝ROI |")
    lines.append("|--------|------|---------|---------|---------|")
    for r in by_ninki:
        nat = natural.get(r["ninki_bucket"], 0.0)
        exceed = "✅" if r["win_hit_rate"] > nat else "❌"
        lines.append(
            f"| {r['ninki_bucket']} | {r['bets']:,} | "
            f"{r['win_hit_rate']:.1%} | {nat:.1%} {exceed} | {r['win_roi']:.1f}% |"
        )
    lines.append("")

    # ── 穴馬発掘力評価 ────────────────────────────────────────────────────────
    lines += ["## 4. 穴馬発掘力の評価", ""]
    anaba_r = next((r for r in anaba_only if r["top_n"] == 1), None)
    if anaba_r:
        # 自然確率: 4番人気以降全体の自然勝率
        nat_anaba = natural.get("4-6番人気", 0.0)
        ai_rate   = anaba_r["win_hit_rate"]
        if ai_rate > nat_anaba * 1.2:
            assessment = "✅ 穴馬発掘力あり（AI的中率 > 自然確率 × 1.2）"
        elif ai_rate > nat_anaba:
            assessment = "⚠️ 軽微な優位あり（AI的中率 > 自然確率）"
        else:
            assessment = "❌ 穴馬発掘力なし（AI的中率 ≤ 自然確率）"
        lines.append(f"- 4番人気以降TOP1: AI的中率 {ai_rate:.1%} / 自然確率 {nat_anaba:.1%}")
        lines.append(f"- **評価: {assessment}**")
    lines.append("")

    # ── フェーズ2推奨 ─────────────────────────────────────────────────────────
    lines += [
        "## 5. フェーズ2への推奨事項",
        "",
        "- [ ] 前日オッズ取得機能の実装（JV-Link リアルタイム → `odds_history` テーブル）",
        "- [ ] 残差ターゲットを前日オッズベースに切り替えて再学習",
        "- [ ] 複勝ROI の正確な計算（payouts テーブルから複勝払戻を取得）",
        "- [ ] テン3F（馬個別）の取得（現状: 全体ラップのみ）",
        "- [ ] 2022年以前の過去データ追加（データ量増加 → 精度向上の可能性）",
        "- [ ] 騎手乗り替わりフラグの明示的な特徴量化",
        "",
    ]

    results_md.write_text("\n".join(lines), encoding="utf-8")
    log.info("結果ファイル出力: %s", results_md)

    # JSON形式でも保存（プログラムからの参照用）
    json_path = RESULTS_DIR / "anaba_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info("JSON結果出力: %s", json_path)


def main(parquet_path: str | None = None) -> None:
    # ── Step 1: データ読み込み ─────────────────────────────────────────────────
    log.info("==== Step 1: データ読み込み ====")
    df = load_and_prepare(parquet_path)
    df_A, df_B, df_C = split_periods(df)

    # ── Step 2: サブモデル学習（A 期間）────────────────────────────────────────
    log.info("==== Step 2: サブモデル学習（A 期間: %d 行）====", len(df_A))
    sub_dir = MODEL_DIR / "submodels"
    df_A_scored = train_all_submodels(df_A, save_dir=sub_dir)
    imp_raw = df_A_scored.attrs.get("submodel_importances", {})
    imp_summary = _summarize_importances(imp_raw)

    # ── Step 3: B 期間にサブモデルを推論 ─────────────────────────────────────
    log.info("==== Step 3: B 期間 サブモデル推論（%d 行）====", len(df_B))
    df_B_scored = predict_submodels(df_B, model_dir=sub_dir, fold=1)

    # ── Step 4: メタモデル学習（B 期間）─────────────────────────────────────────
    log.info("==== Step 4: メタモデル学習（B 期間）====")
    meta_model = train_meta_model(df_B_scored, save_dir=MODEL_DIR / "meta")
    meta_imps  = get_submodel_importances(model_dir=MODEL_DIR / "meta")

    # ── Step 5: C 期間でホールドアウト検証 ─────────────────────────────────────
    log.info("==== Step 5: C 期間 検証（%d 行）====", len(df_C))
    df_C_scored = predict_submodels(df_C, model_dir=sub_dir, fold=1)
    df_C_scored["anaba_score"] = predict_anaba_score(df_C_scored, model_dir=MODEL_DIR / "meta")

    results = full_evaluation_report(df_C_scored)

    # ── Step 6: 結果記録 ──────────────────────────────────────────────────────
    log.info("==== Step 6: 結果記録 ====")
    _write_results(results, imp_summary, meta_imps, str(parquet_path or "default"))

    log.info("==== 穴馬AI v1 学習・検証 完了 ====")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="穴馬AI v1 フルパイプライン")
    p.add_argument("--parquet", default=None, help="入力 Parquet のパス")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(parquet_path=args.parquet)
