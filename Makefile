# ══════════════════════════════════════════════════════════════════════════════
# AI Fukuro — ClassicVideo 横型動画生成パイプライン
# ══════════════════════════════════════════════════════════════════════════════
#
# 使い方:
#   make prompt DATE=2026-05-31 VENUE=08   # Phase 1: LLM 指示文生成
#   make render                             # Phase 3: TTS + MP4 書き出し
#   make render DRY_RUN=1                   # Phase 3: VOICEVOX なしドライラン
#
# 前提:
#   GNU Make（Git for Windows / Chocolatey / MSYS2 経由）
#   py -3.13 が使えること
#   owl_video/ で npm install 済み
#   VOICEVOX が http://localhost:50021 で起動済み（make render 時）

PYTHON      := py -3.13
PARQUET     ?= outputs/v2_stacked_features.parquet
DATE        ?=
VENUE       ?=
DRAFT_INPUT ?= data/input/draft_classic_video.json
TTS_OUTPUT  ?= data/output/classic_video_data.json
VIDEO_OUT   ?= owl_video/out/classic_video.mp4
DRY_RUN     ?=

COMPOSITION := ClassicVideo

.PHONY: prompt render help

help:
	@echo ""
	@echo "  AI Fukuro — ClassicVideo 横型動画パイプライン"
	@echo ""
	@echo "  make prompt DATE=YYYY-MM-DD VENUE=XX"
	@echo "      Phase 1: 指定日・会場の LLM プロンプトを生成"
	@echo "      出力: data/output/prompt_YYYYMMDD_venue.txt"
	@echo "      例:  make prompt DATE=2026-05-31 VENUE=08"
	@echo ""
	@echo "  make render [DRY_RUN=1]"
	@echo "      Phase 3: TTS 合成 + Remotion MP4 書き出し"
	@echo "      入力: data/input/draft_classic_video.json"
	@echo "      出力: owl_video/out/classic_video.mp4"
	@echo ""

# ── Phase 1: プロンプト生成 ────────────────────────────────────────────────────

prompt:
ifndef DATE
	$(error DATE が未設定です。例: make prompt DATE=2026-05-31 VENUE=08)
endif
	@echo ""
	@echo "=== Phase 1: LLM プロンプト生成 ==="
	$(PYTHON) scripts/generate_prompt.py \
		--date  "$(DATE)" \
		$(if $(VENUE),--venue "$(VENUE)",) \
		$(if $(filter-out $(PARQUET),outputs/v2_stacked_features.parquet),--parquet "$(PARQUET)",)

# ── Phase 3: TTS + Remotion レンダリング ─────────────────────────────────────

render: _check_draft _tts _copy_json _remotion

_check_draft:
	@$(PYTHON) -c "\
import sys, pathlib; p = pathlib.Path('$(DRAFT_INPUT)'); \
sys.exit(0) if p.exists() else (print(f'ERROR: {p} が見つかりません。Step 2 で JSON を保存してください。'), sys.exit(1))"

_tts:
	@echo ""
	@echo "=== Phase 3a: VOICEVOX TTS 合成 ==="
	$(PYTHON) scripts/generate_tts_classic.py \
		--input  "$(DRAFT_INPUT)" \
		--output "$(TTS_OUTPUT)" \
		$(if $(DRY_RUN),--dry-run,)

_copy_json:
	@echo ""
	@echo "=== Phase 3b: JSON を Remotion public/ へコピー ==="
	$(PYTHON) -c "\
import shutil, pathlib; \
src = pathlib.Path('$(TTS_OUTPUT)'); \
dst = pathlib.Path('owl_video/public/data/classic_video_data.json'); \
dst.parent.mkdir(parents=True, exist_ok=True); \
shutil.copy2(src, dst); \
print(f'  コピー完了: {src} -> {dst}')"

_remotion:
	@echo ""
	@echo "=== Phase 3c: Remotion MP4 レンダリング ($(COMPOSITION)) ==="
	$(PYTHON) -c "import pathlib; pathlib.Path('owl_video/out').mkdir(parents=True, exist_ok=True)"
	cd owl_video; npx remotion render $(COMPOSITION) ../$(VIDEO_OUT)
	@echo ""
	@echo "  完了: $(VIDEO_OUT)"
	@echo ""
