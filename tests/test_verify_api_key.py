"""
tests/test_verify_api_key.py
==============================
verify_api_key の4ケーステスト。
- ヘッダーなし         → 401
- 不正キー             → 401（タイミング攻撃防止: compare_digest 使用）
- 正しいキー           → キー文字列を返す
- API_KEY 空 + dev     → "dev" を返す（認証スキップ）
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi import HTTPException


_REAL_KEY = "test-secret-key-abcdef1234567890"


def _call(api_key_header: str | None, env_key: str = _REAL_KEY) -> str:
    """verify_api_key をモジュール再ロードして env_key で検証する。"""
    with patch.dict("os.environ", {"API_KEY": env_key}):
        import importlib
        import shared.config as _cfg
        importlib.reload(_cfg)

        import api_v2.deps as _deps
        importlib.reload(_deps)
        return _deps.verify_api_key(api_key=api_key_header)


class TestVerifyApiKey:
    def test_no_header_returns_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _call(api_key_header=None)
        assert exc_info.value.status_code == 401

    def test_wrong_key_returns_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _call(api_key_header="wrong-key")
        assert exc_info.value.status_code == 401

    def test_correct_key_returns_key(self):
        result = _call(api_key_header=_REAL_KEY)
        assert result == _REAL_KEY

    def test_empty_api_key_dev_mode(self):
        result = _call(api_key_header=None, env_key="")
        assert result == "dev"

    def test_none_key_does_not_raise_type_error(self):
        """None を compare_digest に渡しても TypeError でなく 401 になること。"""
        with pytest.raises(HTTPException) as exc_info:
            _call(api_key_header=None)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid or missing API key"
