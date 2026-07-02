# ══════════════════════════════════════════════════════════════════════════════
# AI Fukuro 動画自動生成パイプライン
# ══════════════════════════════════════════════════════════════════════════════
#
# 使い方:
#   make prompt DATE=2026-05-17 VENUE=08   # Phase 1: プロンプト生成
#   make render                             # Phase 3: TTS + MP4 書き出し
#   make render DRY_RUN=1                   # Phase 3: VOICEVOXなしダミー音声で実行
#
# 前提:
#   GNU Make がインストール済み（Chocolatey: choco install make、または Git for Windows）
#   Python 3.13: py -3.13 で呼び出せること
#   Remotion: owl_video/ で npm install 済みであること
#   VOICEVOX: make render 実行時に http://localhost:50021 で起動済みであること

PYTHON    := py -3.13
SCORES    ?= outputs/v2_stacked_features.parquet
DATE      ?=
VENUE     ?=
INPUT     ?= data/input/draft_video_data.json
OUTPUT    ?= data/output/final_video_data.json
VIDEO_OUT ?= owl_video/out/video.mp4
DRY_RUN   ?=

# Remotion コンポジション ID（Root.tsx で定義した ID）
COMPOSITION := FukuroLongVideo

.PHONY: prompt render help

# ── デフォルトターゲット ────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  AI Fukuro 動画生成パイプライン"
	@echo ""
	@echo "  make prompt DATE=YYYY-MM-DD VENUE=XX"
	@echo "      Phase 1: 指定日・会場のプロンプトテキストを生成する"
	@echo "      出力: data/output/prompt_YYYYMMDD_venue.txt"
	@echo "      例:  make prompt DATE=2026-05-17 VENUE=08"
	@echo ""
	@echo "  make render [DRY_RUN=1]"
	@echo "      Phase 3: TTS 合成 + Remotion MP4 書き出し"
	@echo "      入力: data/input/draft_video_data.json  (Phase 2 で手動保存)"
	@echo "      出力: owl_video/out/video.mp4"
	@echo "      DRY_RUN=1 を付けると VOICEVOX なしでダミー音声で実行"
	@echo ""

# ── Phase 1: プロンプト生成 ────────────────────────────────────────────────────

prompt:
ifndef DATE
	$(error DATE が未設定です。例: make prompt DATE=2026-05-17 VENUE=08)
endif
	@echo ""
	@echo "=== Phase 1: プロンプト生成 ==="
	$(PYTHON) scripts/generate_prompt.py \
		--date "$(DATE)" \
		$(if $(VENUE),--venue "$(VENUE)",) \
		$(if $(filter-out $(SCORES),outputs/v2_stacked_features.parquet),--parquet "$(SCORES)",)
	@echo ""
	@echo "  -> data/output/ 以下のテキストファイルを LLM チャットにコピペしてください"
	@echo "  -> LLM の出力 JSON を data/input/draft_video_data.json として保存後、"
	@echo "     make render を実行してください"
	@echo ""

# ── Phase 3: TTS 合成 + Remotion レンダリング ─────────────────────────────────

render: _check_input _tts _copy_json _remotion_render

_check_input:
	@$(PYTHON) -c "\
import sys, pathlib; \
p = pathlib.Path('$(INPUT)'); \
sys.exit(0) if p.exists() else (print(f'ERROR: 入力 JSON が見つかりません: {p}'), sys.exit(1))"

_tts:
	@echo ""
	@echo "=== Phase 3a: VOICEVOX TTS 合成 ==="
	$(PYTHON) scripts/generate_tts_assets.py \
		--input  "$(INPUT)" \
		--output "$(OUTPUT)" \
		$(if $(DRY_RUN),--dry-run,)

_copy_json:
	@echo ""
	@echo "=== Phase 3b: final_video_data.json を Remotion public/ へコピー ==="
	$(PYTHON) -c "\
import shutil, pathlib; \
src = pathlib.Path('$(OUTPUT)'); \
dst = pathlib.Path('owl_video/public/data/final_video_data.json'); \
dst.parent.mkdir(parents=True, exist_ok=True); \
shutil.copy2(src, dst); \
print(f'  コピー完了: {src} -> {dst}')"

_remotion_render:
	@echo ""
	@echo "=== Phase 3c: Remotion MP4 レンダリング ==="
	$(PYTHON) -c "\
import pathlib; \
pathlib.Path('owl_video/out').mkdir(parents=True, exist_ok=True)"
	cd owl_video && npx remotion render $(COMPOSITION) ../$(VIDEO_OUT)
	@echo ""
	@echo "  MP4 書き出し完了: $(VIDEO_OUT)"
	@echo ""
