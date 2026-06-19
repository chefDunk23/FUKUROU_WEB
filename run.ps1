#!/usr/bin/env pwsh
# run.ps1 - AI Fukuro ClassicVideo pipeline (Windows)
#
# Usage:
#   .\run.ps1 prompt -DATE 2026-05-17          # all venues -> 1 JSON
#   .\run.ps1 prompt -DATE 2026-05-17 -VENUE 08 # Kyoto only
#   .\run.ps1 render
#   .\run.ps1 render -DRY_RUN

param(
    [Parameter(Position=0, Mandatory=$true)]
    [ValidateSet("prompt","render","help")]
    [string]$Target,

    [string]$DATE        = "",
    [string]$VENUE       = "",
    [string]$DRAFT_INPUT = "data/input/draft_classic_video.json",
    [string]$TTS_OUTPUT  = "data/output/classic_video_data.json",
    [string]$VIDEO_OUT   = "owl_video/out/classic_video.mp4",
    [switch]$DRY_RUN
)

Set-Location $PSScriptRoot

switch ($Target) {

    "help" {
        Write-Host ""
        Write-Host "  AI Fukuro - ClassicVideo pipeline"
        Write-Host ""
        Write-Host "  .\run.ps1 prompt -DATE YYYY-MM-DD"
        Write-Host "      -> ALL venues that day, combined into 1 JSON"
        Write-Host ""
        Write-Host "  .\run.ps1 prompt -DATE YYYY-MM-DD -VENUE 08"
        Write-Host "      -> Kyoto only"
        Write-Host ""
        Write-Host "  .\run.ps1 render [-DRY_RUN]"
        Write-Host "      Phase 3: TTS + Remotion MP4 render"
        Write-Host "      input:  data/input/draft_classic_video.json"
        Write-Host "      output: owl_video/out/classic_video.mp4"
        Write-Host ""
    }

    "prompt" {
        if (-not $DATE) {
            Write-Error "DATE is required. e.g.: .\run.ps1 prompt -DATE 2026-05-17"
            exit 1
        }
        Write-Host ""
        Write-Host "=== Phase 1: generate prompt JSON ==="

        $cmd = @("scripts/generate_prompt.py", "--date", $DATE)
        if ($VENUE) {
            Write-Host "  venue: $VENUE only"
            $cmd += "--venue", $VENUE
        } else {
            Write-Host "  venue: all venues for $DATE (combined into 1 JSON)"
        }

        & py -3.13 @cmd
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        # output file を特定して表示
        $dateCompact = $DATE -replace "-", ""
        $suffix = if ($VENUE) { $VENUE } else { "all" }
        $outFile = "data/output/raw_race_data_${dateCompact}_${suffix}.json"
        Write-Host ""
        Write-Host "============================================================"
        Write-Host "  [Next] Give this file to LLM:"
        Write-Host "  $outFile"
        Write-Host ""
        Write-Host "  Ask LLM to fill in:"
        Write-Host "    picks[].evaluation_reason  (15 chars each)"
        Write-Host "    picks[].concern            (15 chars each)"
        Write-Host "    speech_text                (no 'NAME: [' labels)"
        Write-Host "    telop                      (20 chars)"
        Write-Host ""
        Write-Host "  Then save LLM output as:"
        Write-Host "  $DRAFT_INPUT"
        Write-Host ""
        Write-Host "  Then run:  .\run.ps1 render"
        Write-Host "============================================================"
        Write-Host ""

        # output JSON を自動で DRAFT_INPUT にコピーするか確認
        $ans = Read-Host "Auto-copy output to $DRAFT_INPUT now? (y/N)"
        if ($ans -eq "y" -or $ans -eq "Y") {
            if (Test-Path $outFile) {
                Copy-Item -Path $outFile -Destination $DRAFT_INPUT -Force
                Write-Host "  Copied. Open $DRAFT_INPUT, fill with LLM, then run .\run.ps1 render"
            } else {
                Write-Host "  File not found: $outFile"
            }
        }
    }

    "render" {
        if (-not (Test-Path $DRAFT_INPUT)) {
            Write-Error "ERROR: $DRAFT_INPUT not found. Save LLM output first."
            exit 1
        }

        # Phase 3a: TTS
        Write-Host ""
        Write-Host "=== Phase 3a: VOICEVOX TTS ==="
        $tts = @("scripts/generate_tts_classic.py", "--input", $DRAFT_INPUT, "--output", $TTS_OUTPUT)
        if ($DRY_RUN) { $tts += "--dry-run" }
        & py -3.13 @tts
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        # Phase 3b: copy JSON to Remotion public/
        Write-Host ""
        Write-Host "=== Phase 3b: copy JSON to Remotion public/ ==="
        $dst = "owl_video/public/data/classic_video_data.json"
        New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
        Copy-Item -Path $TTS_OUTPUT -Destination $dst -Force
        Write-Host "  copied: $TTS_OUTPUT -> $dst"

        # Phase 3c: Remotion render
        Write-Host ""
        Write-Host "=== Phase 3c: Remotion render ==="
        New-Item -ItemType Directory -Force -Path "owl_video/out" | Out-Null
        Push-Location owl_video
        npx remotion render ClassicVideo "../$VIDEO_OUT"
        $code = $LASTEXITCODE
        Pop-Location
        if ($code -ne 0) { exit $code }

        Write-Host ""
        Write-Host "  done: $VIDEO_OUT"
        Write-Host ""
    }
}
