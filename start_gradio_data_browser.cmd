@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "URL=http://127.0.0.1:7860"
set "BUNDLED_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "MINICONDA_PYTHON=%USERPROFILE%\miniconda3\python.exe"

cd /d "%PROJECT_ROOT%"

for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$candidates = @('python', '%MINICONDA_PYTHON%', '%BUNDLED_PYTHON%'); foreach ($candidate in $candidates) { try { $resolved = & $candidate -c 'import sys, gradio; print(sys.executable)' 2>$null; if ($LASTEXITCODE -eq 0 -and $resolved) { $resolved; exit 0 } } catch {} }; exit 1"`) do set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
  echo Could not find a Python environment with Gradio installed.
  echo Tried: python, %MINICONDA_PYTHON%, %BUNDLED_PYTHON%
  echo Install dependencies with: python -m pip install -r requirements.txt
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "utils\stop_gradio_data_browser_on_port.ps1" -Port 7860
if errorlevel 1 (
  pause
  exit /b 1
)

if not exist "temp" mkdir "temp"
echo.>>"temp\gradio_data_browser_console.log"
echo ===== Starting History Project Data Browser %DATE% %TIME% =====>>"temp\gradio_data_browser_console.log"
start "History Project Data Browser" /min cmd /c ""%PYTHON_EXE%" "utils\gradio_data_browser.py" --world default --host 127.0.0.1 --port 7860 1>>"temp\gradio_data_browser_console.log" 2>>&1"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url = '%URL%';" ^
  "for ($i = 0; $i -lt 30; $i++) {" ^
  "  try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1 | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Seconds 1 }" ^
  "};" ^
  "Start-Process $url"

endlocal
