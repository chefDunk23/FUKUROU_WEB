"""
shared/services/model_version.py
==================================
モデルファイル群のハッシュから不変なバージョン文字列を生成する。

用途:
  - race_predictions / race_detail_cache のキャッシュキーの一部
  - モデル更新後の古いキャッシュを自動的にミスとして扱う

ハッシュ対象:
  - models/v2/ensemble/lgbm_rank_fold*.lgb (芝アンサンブル)
  - models/v2/ensemble_dirt/lgbm_rank_fold*.lgb (ダートアンサンブル)
  の各ファイルの mtime_ns + size を MD5 で集約する。

サーバー再起動時に1回だけ計算し @lru_cache でメモリに保持する。
モデルファイルを差し替えた場合は再起動が必要（通常デプロイはこれを伴う）。
"""
from __future__ import annotations

import hashlib
import logging
from functools import lru_cache

from shared.config import PATHS

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_model_version() -> str:
    """芝・ダート fold ファイル群の mtime+size ハッシュ（12 文字）を返す。
    モデルが存在しない場合は "no_model" を返す（起動時エラーを出さない）。
    """
    h = hashlib.md5()
    found = False
    for model_dir in (PATHS.model_dir_v2, PATHS.model_dir_v2_dirt):
        for f in sorted(model_dir.glob("lgbm_rank_fold*.lgb")):
            st = f.stat()
            h.update(f"{f.name}:{st.st_mtime_ns}:{st.st_size}".encode())
            found = True
    if not found:
        logger.warning("[ModelVersion] モデルファイルが見つかりません — version=no_model")
        return "no_model"
    version = h.hexdigest()[:12]
    logger.info("[ModelVersion] version=%s", version)
    return version
