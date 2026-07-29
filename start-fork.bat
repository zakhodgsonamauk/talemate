@echo off
REM ============================================================
REM  tell-me-a-story - launch everything
REM ============================================================
REM  Starts, in order:
REM    1. Ollama            (text)   - localhost:11434
REM    2. KoboldCpp         (images) - localhost:5001, image-only
REM    3. Talemate backend           - localhost:5050
REM    4. Talemate frontend          - localhost:8082
REM  then waits for the frontend and opens Chrome.
REM
REM  Each service gets its own window. Close a window to stop
REM  that service. Anything already listening is left alone, so
REM  re-running this is safe.
REM
REM  Override any of these before calling if you need to:
REM    TALEMATE_BACKEND_PORT   (default 5050)
REM    TALEMATE_FRONTEND_PORT  (default 8082)
REM    KCPP_PORT               (default 5001)
REM    KCPP_DIR                (default %LOCALAPPDATA%\koboldcpp)
REM    KCPP_MODEL              (default <KCPP_DIR>\models\sd_xl_turbo_1.0_fp16.safetensors)
REM    SKIP_IMAGES=1           don't start KoboldCpp
REM    NO_BROWSER=1            don't open Chrome
REM ------------------------------------------------------------

setlocal EnableDelayedExpansion

REM Work from the repo root. Paths here contain spaces and
REM parentheses, so everything below stays quoted and relative.
pushd "%~dp0"

echo.
echo ===============================================
echo   tell-me-a-story - starting services
echo ===============================================
echo.

REM ---------[ Defaults ]---------
if "%TALEMATE_BACKEND_PORT%"==""  set "TALEMATE_BACKEND_PORT=5050"
if "%TALEMATE_FRONTEND_PORT%"=="" set "TALEMATE_FRONTEND_PORT=8082"
if "%KCPP_PORT%"==""              set "KCPP_PORT=5001"
if "%KCPP_DIR%"==""               set "KCPP_DIR=%LOCALAPPDATA%\koboldcpp"
if "%KCPP_MODEL%"==""             set "KCPP_MODEL=%KCPP_DIR%\models\sd_xl_turbo_1.0_fp16.safetensors"

set "TALEMATE_DEBUG=1"
set "COREPACK_ENABLE_DOWNLOAD_PROMPT=0"

REM Machine-specific overrides (model paths, ports, OLLAMA_MODELS) live in
REM start-fork.local.bat, which is untracked. Keeps this file generic.
REM The .\ prefix is required: cmd fails to resolve a bare command name
REM containing more than one dot.
if exist "start-fork.local.bat" call .\start-fork.local.bat

REM Prefer the embedded Node runtime when install.bat provisioned one.
if exist "embedded_node\node.exe" set "PATH=%CD%\embedded_node;%PATH%"

REM ---------[ Preflight ]---------
if not exist ".venv\Scripts\python.exe" (
    echo [FATAL] .venv not found.
    echo         Run:  uv sync --python 3.11
    goto :fail
)

where node >nul 2>&1
if errorlevel 1 (
    echo [FATAL] node not found on PATH. Install Node.js, or run install.bat
    echo         to provision the embedded runtime.
    goto :fail
)

if not exist "config.yaml" (
    echo [setup] config.yaml missing - creating from config.example.yaml
    copy /Y "config.example.yaml" "config.yaml" >nul
)

REM The Memory agent dies without this shim - torchcodec cannot resolve
REM its DLLs, which breaks sentence_transformers and blocks scene loading.
REM It lives in .venv, so recreating .venv silently removes it.
if not exist ".venv\Lib\site-packages\zzz_torchcodec_dll_fix.pth" (
    echo [WARN] torchcodec DLL shim is missing from .venv.
    echo        Scene loading will fail with:
    echo          "Memory Agent - Failed to set up the database"
    echo        Fix: docs\fork\images-without-comfyui.md ^(Troubleshooting^)
    echo.
)

REM ---------[ 1. Ollama ]---------
call :is_listening 11434
if "%LISTENING%"=="1" (
    echo [ok]    Ollama already running on 11434
    goto :after_ollama
)

where ollama >nul 2>&1
if errorlevel 1 (
    echo [WARN]  Ollama not running and 'ollama' not on PATH.
    echo         Text generation will not work until you start it.
    goto :after_ollama
)

REM Ollama only sees models in OLLAMA_MODELS. If that is unset it falls back to
REM %USERPROFILE%\.ollama\models - which on this machine is nearly empty, because
REM the real library lives elsewhere. Starting Ollama without it makes every
REM model appear to vanish, so refuse to start blind.
if not defined OLLAMA_MODELS (
    echo [WARN]  Ollama is not running and OLLAMA_MODELS is not set.
    echo         Not starting it: it would use %%USERPROFILE%%\.ollama\models and
    echo         your model library would appear empty.
    echo         Set OLLAMA_MODELS in start-fork.local.bat, or start Ollama
    echo         yourself the usual way, then re-run this script.
    goto :after_ollama
)

echo [start] Ollama on 11434
echo         OLLAMA_MODELS=%OLLAMA_MODELS%
start "Ollama :11434" /min cmd /k "ollama serve"
call :wait_for 11434 30 Ollama

:after_ollama

REM ---------[ 2. KoboldCpp - images ]---------
if "%SKIP_IMAGES%"=="1" (
    echo [skip]  KoboldCpp skipped ^(SKIP_IMAGES=1^) - image generation unavailable
    goto :after_images
)

call :is_listening %KCPP_PORT%
if "%LISTENING%"=="1" (
    echo [ok]    KoboldCpp already running on %KCPP_PORT%
    goto :after_images
)

if not exist "%KCPP_DIR%\koboldcpp.exe" (
    echo [WARN]  koboldcpp.exe not found at "%KCPP_DIR%"
    echo         Image generation will be unavailable. Text still works.
    echo         Setup: docs\fork\images-without-comfyui.md
    goto :after_images
)
if not exist "%KCPP_MODEL%" (
    echo [WARN]  image model not found:
    echo         "%KCPP_MODEL%"
    echo         Image generation will be unavailable. Text still works.
    goto :after_images
)

echo [start] KoboldCpp ^(image-only^) on %KCPP_PORT%
REM No --model on purpose: image-only, so Ollama keeps serving text and
REM the two never compete for VRAM.
start "KoboldCpp :%KCPP_PORT% (images)" cmd /k ""%KCPP_DIR%\koboldcpp.exe" --sdmodel "%KCPP_MODEL%" --usecuda --port %KCPP_PORT% --skiplauncher"
call :wait_for %KCPP_PORT% 120 KoboldCpp

:after_images

REM ---------[ 3. Talemate backend ]---------
call :is_listening %TALEMATE_BACKEND_PORT%
if "%LISTENING%"=="1" (
    echo [ok]    backend already running on %TALEMATE_BACKEND_PORT%
) else (
    echo [start] Talemate backend on %TALEMATE_BACKEND_PORT%
    REM Relative paths on purpose. The repo path contains spaces AND
    REM parentheses, which break cmd's nested-quote parsing; the new
    REM window inherits this directory from start, so relative is safe.
    start "Talemate backend :%TALEMATE_BACKEND_PORT%" cmd /k ".venv\Scripts\python.exe src\talemate\server\run.py runserver --host 0.0.0.0 --port %TALEMATE_BACKEND_PORT% --backend-only"
    call :wait_for %TALEMATE_BACKEND_PORT% 180 backend
)

REM ---------[ 4. Talemate frontend ]---------
call :is_listening %TALEMATE_FRONTEND_PORT%
if "%LISTENING%"=="1" (
    echo [ok]    frontend already running on %TALEMATE_FRONTEND_PORT%
) else (
    echo [start] Talemate frontend on %TALEMATE_FRONTEND_PORT%
    start "Talemate frontend :%TALEMATE_FRONTEND_PORT%" cmd /k "cd talemate_frontend && corepack pnpm run serve --host 0.0.0.0 --port %TALEMATE_FRONTEND_PORT%"
    call :wait_for %TALEMATE_FRONTEND_PORT% 120 frontend
)

REM ---------[ 5. Browser ]---------
set "TM_URL=http://localhost:%TALEMATE_FRONTEND_PORT%"

if "%NO_BROWSER%"=="1" (
    echo.
    echo [skip]  browser not opened ^(NO_BROWSER=1^)
    goto :done
)

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe"      set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"      set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if defined CHROME (
    echo [open]  Chrome -^> %TM_URL%
    start "" "%CHROME%" --new-window "%TM_URL%"
) else (
    echo [open]  Chrome not found - using default browser
    start "" "%TM_URL%"
)

:done
echo.
echo ===============================================
echo   ready
echo ===============================================
echo   frontend   %TM_URL%
echo   backend    http://localhost:%TALEMATE_BACKEND_PORT%
if not "%SKIP_IMAGES%"=="1" echo   images     http://localhost:%KCPP_PORT%/sdui/
echo.
echo   Each service runs in its own window.
echo   Close a window to stop that service.
echo ===============================================
echo.
popd
endlocal
exit /b 0

:fail
echo.
popd
endlocal
pause
exit /b 1


REM ============================================================
REM  :is_listening <port>   -> sets LISTENING to 1 or 0
REM ============================================================
:is_listening
set "LISTENING=0"
for /f "tokens=*" %%L in ('netstat -an -p TCP ^| findstr /R /C:"LISTENING" ^| findstr /C:":%~1 "') do set "LISTENING=1"
exit /b 0

REM ============================================================
REM  :wait_for <port> <seconds> <label>
REM ============================================================
:wait_for
set /a "_left=%~2"
<nul set /p "=        waiting for %~3 "
:wait_loop
call :is_listening %~1
if "%LISTENING%"=="1" (
    echo  up.
    exit /b 0
)
if %_left% LEQ 0 (
    echo  TIMEOUT.
    echo         %~3 did not come up on port %~1 - check its window.
    exit /b 1
)
<nul set /p "=."
REM 1s tick; >nul keeps ping quiet
ping -n 2 127.0.0.1 >nul
set /a "_left=_left-1"
goto :wait_loop
