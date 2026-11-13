$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.arena-venv\Scripts\python.exe'
$state = Join-Path $PSScriptRoot '.exness-arena'
$env:PYTHONPATH = $PSScriptRoot
$records = @(Get-Content -LiteralPath (Join-Path $state 'processes.json') -Raw | ConvertFrom-Json)
$trackerRecords = @($records | Where-Object { $_.repo -eq 'tracker' })
if ($trackerRecords.Count -ne 1) { throw 'Expected exactly one recorded tracker.' }
$trackerRecord = $trackerRecords[0]
$procInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($trackerRecord.pid)"
if ($procInfo -and ($procInfo.ExecutablePath -ne $python -or -not $procInfo.CommandLine.Contains('exness_arena.tracker'))) {
    throw 'Tracker process identity does not match; no process was stopped.'
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item -LiteralPath (Join-Path $state 'processes.json') -Destination (Join-Path $state "processes.before-dashboard-$stamp.json")
# The collector cannot submit broker orders. Strategy processes and STOP/flags are untouched.
if ($procInfo) { Stop-Process -Id $trackerRecord.pid }
$out = Join-Path $state "tracker.dashboard-$stamp.out.log"
$err = Join-Path $state "tracker.dashboard-$stamp.err.log"
$newTracker = Start-Process -FilePath $python -ArgumentList '-u','-m','exness_arena.tracker' -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
$trackerRecord.pid = $newTracker.Id
$trackerRecord.stdout = $out
$trackerRecord.stderr = $err
$next = Join-Path $state 'processes.dashboard-next.json'
$records | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $next
Move-Item -LiteralPath $next -Destination (Join-Path $state 'processes.json') -Force
$deadline = (Get-Date).AddSeconds(40)
do {
    $newTracker.Refresh()
    if ($newTracker.HasExited) { throw "New collector exited; inspect $err. Bot processes were not changed." }
    try {
        $snapshot = Invoke-RestMethod 'http://127.0.0.1:8765/api/results'
        $page = Invoke-WebRequest 'http://127.0.0.1:8765/'
        if ($snapshot.account -and $page.Content.Contains('Arena')) {
            Write-Output "Dashboard ready: http://127.0.0.1:8765/ | Collector PID $($newTracker.Id). Bot processes and trading policy unchanged."
            exit 0
        }
    } catch { }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
throw "Collector did not become healthy. Inspect $err; bot processes and loss halts were not changed."
