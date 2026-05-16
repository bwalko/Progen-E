@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "URL=http://127.0.0.1:7860"
set "PYTHON_EXE=python"
set "BUNDLED_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

cd /d "%PROJECT_ROOT%"

where python >nul 2>nul
if errorlevel 1 (
  if exist "%BUNDLED_PYTHON%" (
    set "PYTHON_EXE=%BUNDLED_PYTHON%"
  ) else (
    echo Python was not found on PATH, and bundled Python was not found at:
    echo %BUNDLED_PYTHON%
    pause
    exit /b 1
  )
)

start "History Project Data Browser" /min "%PYTHON_EXE%" "utils\gradio_data_browser.py" --world default --host 127.0.0.1 --port 7860

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url = '%URL%';" ^
  "for ($i = 0; $i -lt 30; $i++) {" ^
  "  try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1 | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Seconds 1 }" ^
  "};" ^
  "Start-Process $url"

endlocal
