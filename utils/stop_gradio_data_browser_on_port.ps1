param(
    [int]$Port = 7860
)

$ErrorActionPreference = "Stop"

try {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
} catch {
    Write-Warning "Could not inspect port $Port. Continuing without cleanup. $($_.Exception.Message)"
    exit 0
}

$processIds = @{}
foreach ($listener in $listeners) {
    if ($listener.OwningProcess -and $listener.OwningProcess -ne 0) {
        $processIds[$listener.OwningProcess] = $true
    }
}

foreach ($processId in $processIds.Keys) {
    Write-Host "Stopping existing process $processId on port $Port."
    Stop-Process -Id $processId -Force
}

if ($processIds.Count -gt 0) {
    Start-Sleep -Milliseconds 750
}

$remaining = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($remaining.Count -gt 0) {
    $ids = ($remaining | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique) -join ", "
    Write-Error "Port $Port is already in use by process id(s): $ids. Close that app or change PORT in the launcher."
    exit 1
}

exit 0
