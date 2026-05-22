"""
src/models/submodel_registry.py
================================
サブモデルの保存・読み込みを規格化する管理クラス。

保存パッケージ構造（ディレクトリ単位）:
    {model_dir}/
        model.txt        — LightGBM Booster（テキスト形式）
        features.json    — 学習に使用した特徴量リスト（順序保証）
        metadata.json    — 名前・バージョン・説明・学習日・評価指標 etc.

使い方:
    # 保存
    manager = SubmodelManager("models/submodels/team_v1")
    manager.save(booster, feature_cols, metadata={"auc": 0.71, "description": "..."})

    # 読み込み
    manager = SubmodelManager("models/submodels/team_v1")
    booster, feature_cols, meta = manager.load()
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm as lgb

_logger = logging.getLogger(__name__)

_MODEL_FILE    = "model.txt"
_FEATURES_FILE = "features.json"
_METADATA_FILE = "metadata.json"


class SubmodelManager:
    """
    LightGBM サブモデルのパッケージ保存・読み込みを担うマネージャー。

    Args:
        model_dir: モデルパッケージを格納するディレクトリパス。
                   保存時は自動作成される。
    """

    def __init__(self, model_dir: str | Path) -> None:
        self.model_dir = Path(model_dir)

    # ──────────────────────────────────────────────────────────────────────────
    # 保存
    # ──────────────────────────────────────────────────────────────────────────

    def save(
        self,
        booster: lgb.Booster,
        feature_cols: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """
        モデル・特徴量リスト・メタデータを一括保存する。

        Args:
            booster:      学習済み LightGBM Booster
            feature_cols: 学習に使用した特徴量名リスト（順序保証必須）
            metadata:     任意の付加情報（auc, description, version etc.）

        Returns:
            保存先ディレクトリの Path
        """
        self.model_dir.mkdir(parents=True, exist_ok=True)

        # モデル本体
        model_path = self.model_dir / _MODEL_FILE
        booster.save_model(str(model_path))
        _logger.info("[SubmodelManager] model saved → %s", model_path)

        # 特徴量リスト
        features_path = self.model_dir / _FEATURES_FILE
        features_path.write_text(
            json.dumps(feature_cols, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _logger.info("[SubmodelManager] features saved (%d cols) → %s",
                     len(feature_cols), features_path)

        # メタデータ（渡されたものに saved_at を自動付与）
        meta = dict(metadata or {})
        meta.setdefault("saved_at", datetime.now(timezone.utc).isoformat())
        meta.setdefault("n_features", len(feature_cols))
        meta_path = self.model_dir / _METADATA_FILE
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _logger.info("[SubmodelManager] metadata saved → %s", meta_path)

        return self.model_dir

    # ──────────────────────────────────────────────────────────────────────────
    # 読み込み
    # ──────────────────────────────────────────────────────────────────────────

    def load(self) -> tuple[lgb.Booster, list[str], dict[str, Any]]:
        """
        保存済みパッケージからモデル・特徴量・メタデータを一括ロードする。

        Returns:
            (booster, feature_cols, metadata)

        Raises:
            FileNotFoundError: 必須ファイルが存在しない場合
        """
        model_path    = self.model_dir / _MODEL_FILE
        features_path = self.model_dir / _FEATURES_FILE
        meta_path     = self.model_dir / _METADATA_FILE

        for p in (model_path, features_path, meta_path):
            if not p.exists():
                raise FileNotFoundError(
                    f"[SubmodelManager] 必須ファイルが見つかりません: {p}\n"
                    f"  model_dir={self.model_dir}"
                )

        booster      = lgb.Booster(model_file=str(model_path))
        feature_cols = json.loads(features_path.read_text(encoding="utf-8"))
        metadata     = json.loads(meta_path.read_text(encoding="utf-8"))

        _logger.info("[SubmodelManager] loaded from %s (features=%d)",
                     self.model_dir, len(feature_cols))
        return booster, feature_cols, metadata

    # ──────────────────────────────────────────────────────────────────────────
    # ユーティリティ
    # ──────────────────────────────────────────────────────────────────────────

    def exists(self) -> bool:
        """3ファイルすべてが揃っているか確認する。"""
        return all(
            (self.model_dir / f).exists()
            for f in (_MODEL_FILE, _FEATURES_FILE, _METADATA_FILE)
        )

    def metadata(self) -> dict[str, Any]:
        """モデルをロードせずメタデータだけ読む（高速確認用）。"""
        meta_path = self.model_dir / _METADATA_FILE
        if not meta_path.exists():
            return {}
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def __repr__(self) -> str:
        status = "ready" if self.exists() else "not saved"
        return f"<SubmodelManager dir={self.model_dir} status={status}>"
