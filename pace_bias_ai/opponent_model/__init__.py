"""
pace_bias_ai/opponent_model — 前走メンバーレベル（相手関係×クラス）AIサブモデル。

v1（展開×バイアスAI）とは完全に独立した別モデル。
「前走の相手がどれだけ強かったか」を評価する。

主要モジュール:
  features.py                  — 特徴量生成
  model.py                     — LightGBM lambdarank ラッパー
  condition_mapper_opponent.py — 日本語説明文生成
"""
