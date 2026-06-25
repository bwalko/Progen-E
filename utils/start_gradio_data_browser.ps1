param(
  [string]$World = "default",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 7860,
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TempDir = Join-Path $ProjectRoot "temp"
$VenvDir = Join-Path $TempDir "gradio_browser_venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$SetupLog = Join-Path $TempDir "gradio_setup_current.log"
$AppLog = Join-Path $TempDir "gradio_data_browser.log"
$PidPath = Join-Path $TempDir "gradio_data_browser.pid"

New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

function Invoke-LoggedPython {
  param(
    [string]$Python,
    [string[]]$Arguments,
    [string]$LogPath
  )

  Push-Location $ProjectRoot
  try {
    & $Python @Arguments 2>&1 | Tee-Object -FilePath $LogPath -Append
    if ($LASTEXITCODE -ne 0) {
      throw "Python command failed with exit code ${LASTEXITCODE}: $Python $($Arguments -join ' ')"
    }
  } finally {
    Pop-Location
  }
}

function Test-BrowserPython {
  param([string]$Python)

  if ($Python -ne "python" -and -not (Test-Path $Python)) {
    return $false
  }

  Push-Location $ProjectRoot
  try {
    & $Python -c "import gradio, numpy; import utils.gradio_data_browser; print('ok')" *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  } finally {
    Pop-Location
  }
}

function Test-VenvBootstrapPython {
  param([string]$Python)

  if ($Python -ne "python" -and -not (Test-Path $Python)) {
    return $false
  }

  try {
    & $Python -c "import venv, ensurepip; print('ok')" *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Get-BootstrapPython {
  $BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  $MinicondaPython = Join-Path $env:USERPROFILE "miniconda3\python.exe"
  $Candidates = @($BundledPython, $MinicondaPython, "python")

  foreach ($Candidate in $Candidates) {
    if (Test-VenvBootstrapPython $Candidate) {
      return $Candidate
    }
  }

  throw "Could not find a Python able to create a virtual environment. Tried: $($Candidates -join ', ')"
}

function Ensure-BrowserEnvironment {
  if (Test-BrowserPython $VenvPython) {
    return $VenvPython
  }

  $BootstrapPython = Get-BootstrapPython

  if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Gradio browser environment in temp\gradio_browser_venv..."
    Invoke-LoggedPython $BootstrapPython @("-m", "venv", $VenvDir) $SetupLog
  }

  Write-Host "Installing Gradio browser dependencies from requirements.txt..."
  Invoke-LoggedPython $VenvPython @("-m", "pip", "install", "-r", $Requirements) $SetupLog

  if (-not (Test-BrowserPython $VenvPython)) {
    throw "The Gradio browser environment was created, but the app dependencies still do not import. See $SetupLog"
  }

  return $VenvPython
}

try {
  $PythonExe = Ensure-BrowserEnvironment

  & (Join-Path $PSScriptRoot "stop_gradio_data_browser_on_port.ps1") -Port $Port
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }

  $Arguments = @(
    "utils\gradio_data_browser.py",
    "--world", $World,
    "--host", $HostName,
    "--port", [string]$Port
  )

  Write-Host "Starting History Project Data Browser on http://${HostName}:$Port ..."
  $Process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $Arguments `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Minimized `
    -PassThru

  Set-Content -Path $PidPath -Value $Process.Id

  $Url = "http://${HostName}:$Port"
  for ($Attempt = 0; $Attempt -lt 45; $Attempt++) {
    Start-Sleep -Seconds 1

    if ($Process.HasExited) {
      Write-Host "Gradio exited before the service was ready. App log tail:"
      if (Test-Path $AppLog) {
        Get-Content $AppLog -Tail 40
      }
      exit 1
    }

    try {
      Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1 | Out-Null
      if (-not $NoBrowser) {
        Start-Process $Url
      }
      Write-Host "Gradio Data Browser is running at $Url"
      exit 0
    } catch {
    }
  }

  Write-Host "Timed out waiting for Gradio at $Url. App log tail:"
  if (Test-Path $AppLog) {
    Get-Content $AppLog -Tail 40
  }
  exit 1
} catch {
  Write-Host $_.Exception.Message
  if (Test-Path $SetupLog) {
    Write-Host "Setup log tail:"
    Get-Content $SetupLog -Tail 40
  }
  exit 1
}
