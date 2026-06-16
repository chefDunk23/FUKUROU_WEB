"""
jvdl_parser/hook.py
====================
取り込み完了フック: 影響レースの race_detail_cache 再計算ジョブを投入する。

フロー (§5.4 改訂版):
  process_stream() → ProcessResult.affected_race_ids
                   → post_recompute()
                   → POST /jobs {"job_type": "recompute_predictions", "params": {"mode": "ids", "race_ids": [...]}}
                   → api_admin ワーカーが _run_batch() + Redis 無効化を実行

管理 API への変更 (M1-2):
  旧: POST /api/v2/admin/recompute  (api_v2 は公開側なので廃止)
  新: POST /jobs                     (api_admin, port 8003, 127.0.0.1 のみ)
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Collection

logger = logging.getLogger(__name__)

_DEFAULT_ADMIN_URL = "http://127.0.0.1:8003"


def post_recompute(
    race_ids: Collection[str],
    admin_base_url: str = _DEFAULT_ADMIN_URL,
    api_key: str = "",
    timeout: float = 10.0,
) -> dict:
    """指定 race_id の再計算ジョブを api_admin のキューに投入する。

    Args:
        race_ids:       再計算対象の race_id 集合
        admin_base_url: api_admin のベース URL（デフォルト: http://127.0.0.1:8003）
        api_key:        X-API-Key ヘッダーに渡す値
        timeout:        HTTP タイムアウト秒数

    Returns:
        投入されたジョブの JSON dict。空 race_ids の場合は {"skipped": True}。

    Raises:
        RuntimeError: HTTP ステータスが 4xx/5xx の場合
    """
    if not race_ids:
        logger.debug("[Hook] race_ids が空のため recompute スキップ")
        return {"skipped": True}

    url = admin_base_url.rstrip("/") + "/jobs"
    body = json.dumps({
        "job_type": "recompute_predictions",
        "params": {
            "mode": "ids",
            "race_ids": list(race_ids),
        },
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result: dict = json.loads(resp.read())
        logger.info(
            "[Hook] recompute ジョブ投入: job_id=%s race_ids=%d 件",
            result.get("id"), len(race_ids),
        )
        return result
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"recompute ジョブ投入 HTTP {e.code}: {body_text}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"api_admin 接続失敗: {e.reason}") from e
