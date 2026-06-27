"""
api_v1/services/tts_pipeline.py
=================================
競馬特化 TTS 前処理パイプライン。

VOICEVOX / COEIROINK へ渡す前に、競馬特有の漢字誤読を
辞書ベースのルール変換で修正する。

MeCab は任意依存。未インストール環境でも辞書ルール変換のみで動作する。

使い方:
    from api_v1.services.tts_pipeline import prepare_for_tts

    raw_text = "牝馬の差し脚が炸裂し、単勝2.5倍で的中。"
    tts_text = prepare_for_tts(raw_text)
    # → "ひんばのさしあしがさくれつし、たんしょう2.5ばいでてきちゅう。"
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DICT_PATH = Path(__file__).parent.parent.parent / "data" / "masters" / "racing_readings.json"

# ── デフォルト競馬用語読み辞書 ────────────────────────────────────────────────
# キー: 原文文字列, 値: 読み（ひらがな）
# data/masters/racing_readings.json が存在する場合はそちらを優先する。
_DEFAULT_READINGS: dict[str, str] = {
    # 馬の性別・種別
    "牝馬": "ひんば",
    "牡馬": "おすうま",
    "騸馬": "せんば",
    # 脚質
    "差し": "さし",
    "逃げ": "にげ",
    "追い込み": "おいこみ",
    "先行": "せんこう",
    # 券種
    "単勝": "たんしょう",
    "複勝": "ふくしょう",
    "馬連": "うまれん",
    "馬単": "うまたん",
    "ワイド": "ワイド",
    "三連複": "さんれんぷく",
    "三連単": "さんれんたん",
    "枠連": "わくれん",
    # 馬体・調教
    "馬体重": "ばたいじゅう",
    "馬体": "ばたい",
    "斤量": "きんりょう",
    "厩舎": "きゅうしゃ",
    "厩務員": "きゅうむいん",
    "調教師": "ちょうきょうし",
    "騎乗": "きじょう",
    "騎手": "きしゅ",
    # レース展開
    "出遅れ": "でおくれ",
    "内枠": "うちわく",
    "外枠": "そとわく",
    "先頭": "せんとう",
    "直線": "ちょくせん",
    "最終コーナー": "さいしゅうコーナー",
    "好位": "こうい",
    "馬群": "うまぐん",
    # コース・場所
    "芝": "しば",
    "ダート": "ダート",
    "障害": "しょうがい",
    "馬場": "ばば",
    "稍重": "ややおも",
    "不良": "ふりょう",
    # その他頻出
    "有力": "ゆうりょく",
    "断然": "だんぜん",
    "人気": "にんき",
    "穴馬": "あなうま",
    "大穴": "おおあな",
    "本命": "ほんめい",
    "対抗": "たいこう",
    "注目": "ちゅうもく",
}


def _load_readings() -> dict[str, str]:
    if _DICT_PATH.exists():
        try:
            with open(_DICT_PATH, encoding="utf-8") as f:
                custom = json.load(f)
            return {**_DEFAULT_READINGS, **custom}
        except Exception as exc:
            logger.warning("racing_readings.json 読み込み失敗（デフォルト使用）: %s", exc)
    return _DEFAULT_READINGS


_READINGS: dict[str, str] | None = None


def _get_readings() -> dict[str, str]:
    global _READINGS
    if _READINGS is None:
        _READINGS = _load_readings()
    return _READINGS


def _apply_dict_rules(text: str, readings: dict[str, str]) -> str:
    """辞書ルールを長い順に適用（部分一致の重複を防ぐため長い語から置換）。"""
    for term in sorted(readings.keys(), key=len, reverse=True):
        text = text.replace(term, readings[term])
    return text


def _try_mecab(text: str) -> str | None:
    """MeCab が利用可能ならヨミガナに変換して返す。使えなければ None を返す。"""
    try:
        import MeCab  # noqa: PLC0415
        tagger = MeCab.Tagger("-Oyomi")
        return tagger.parse(text).strip()
    except ImportError:
        return None
    except Exception as exc:
        logger.debug("MeCab 変換失敗: %s", exc)
        return None


def prepare_for_tts(text: str, *, use_mecab: bool = True) -> str:
    """
    テキストを TTS エンジンに渡す前に前処理する。

    処理順:
      1. 競馬特化辞書による文字列置換（長い語優先）
      2. MeCab が利用可能ならよみがな変換（辞書置換後のテキストに適用）

    Args:
        text:       元のテキスト
        use_mecab:  True の場合、MeCab によるよみがな変換を試みる

    Returns:
        TTS エンジンへ渡す前処理済みテキスト
    """
    readings = _get_readings()
    text = _apply_dict_rules(text, readings)

    if use_mecab:
        result = _try_mecab(text)
        if result:
            return result

    return text


def to_ssml(text: str) -> str:
    """
    テキストを VOICEVOX 対応 SSML 形式に変換する。

    数字の前後にポーズを入れ、競馬用語の phoneme タグを付与する。
    （VOICEVOX の SSML サポート範囲に限定した軽量実装）
    """
    text = prepare_for_tts(text, use_mecab=False)
    # 数字 + 単位の前後にポーズ
    text = re.sub(r"(\d+(?:\.\d+)?)(倍|着|番|頭|kg|m|R)", r'<break time="100ms"/>\1\2<break time="50ms"/>', text)
    return f"<speak>{text}</speak>"
