@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "utils\start_gradio_data_browser.ps1" -World default -HostName 127.0.0.1 -Port 7860
if errorlevel 1 (
  pause
  exit /b 1
)

endlocal
