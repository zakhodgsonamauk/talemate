@echo off
REM ============================================================
REM  Machine-specific overrides for start-fork.bat
REM
REM  TRACKED as of 2026-07-31 (previously in .git/info/exclude).
REM  The paths below are therefore shared, not private - they describe this
REM  machine, so anyone else cloning this fork will need to change them rather
REM  than assume they work. Nothing secret lives here, but do not add secrets.
REM
REM  start-fork.bat calls this file if it exists, after its own defaults, so
REM  anything set here wins.
REM ============================================================

REM ---- Ollama model library --------------------------------
REM As of 2026-07-29 this is ALSO persisted in the user environment via:
REM   setx OLLAMA_MODELS "I:\Dropbox (Personal)\ZakHodgson\Projects\ai-powered-programming\Ollama_models"
REM so Ollama picks it up on login without this file. Kept here as a belt-and-
REM braces default, and so the launcher's "refuse to start Ollama blind" guard
REM is satisfied even if the user env is ever cleared.
REM
REM Why it matters: Ollama only reads models from OLLAMA_MODELS. Without it,
REM it falls back to %USERPROFILE%\.ollama\models - 1 manifest / 0.6 GB - and
REM the real 254 GB / 40-model library becomes invisible.
set "OLLAMA_MODELS=I:\Dropbox (Personal)\ZakHodgson\Projects\ai-powered-programming\Ollama_models"

REM ---- ComfyUI (DEFAULT image backend) --------------------
REM As of 2026-07-31 the launcher starts ComfyUI by default - it is the
REM reference-conditioned path that keeps characters looking like themselves.
REM Nothing needs setting here: COMFYUI_DIR defaults to C:\ComfyUI-talemate
REM (verified present) and COMFYUI_ARGS to "--reserve-vram 5".
REM set "COMFYUI_DIR=C:\ComfyUI-talemate"
REM set "COMFYUI_PORT=8188"
REM Raise the reservation if ComfyUI and the text model fight over the 16GB.
REM set "COMFYUI_ARGS=--reserve-vram 6"

REM ---- KoboldCpp (fallback only) --------------------------
REM Only used when USE_KOBOLDCPP=1 below. Faster, but no reference
REM conditioning, so character identity drifts between images.
REM CyberRealistic PonySemi V5 - uncensored, semi-realistic
REM (painterly finish over realistic anatomy). Pony-based, so it needs Talemate's
REM a1111 settings at steps 30 / cfg 5 / DPM++ 2M / Karras, plus the
REM "Semi-Real (Pony)" art style for the score_9/score_8_up tags.
set "KCPP_DIR=%LOCALAPPDATA%\koboldcpp"
set "KCPP_MODEL=%KCPP_DIR%\models\CyberRealistic_PonySemi_V5.safetensors"
REM Photoreal alternative. Same sampler settings, but switch the art style to
REM "Photoreal (Pony)" (fork_styles__photoreal_pony) to match.
REM set "KCPP_MODEL=%KCPP_DIR%\models\CyberRealisticPony_V9.0_FP16.safetensors"
REM Previous model - SDXL Turbo. Much faster (6 steps) but censored, and needs
REM Talemate switched back to steps 6 / cfg 1 / Euler a + the "Digital Art" style.
REM set "KCPP_MODEL=%KCPP_DIR%\models\sd_xl_turbo_1.0_fp16.safetensors"
REM set "KCPP_PORT=5001"

REM ---- Ports ----------------------------------------------
REM set "TALEMATE_BACKEND_PORT=5050"
REM set "TALEMATE_FRONTEND_PORT=8082"

REM ---- Switches -------------------------------------------
REM Fall back to KoboldCpp instead of ComfyUI. Read after this file is called,
REM so setting it here works.
REM set "USE_KOBOLDCPP=1"
REM set "SKIP_IMAGES=1"
REM set "NO_BROWSER=1"
