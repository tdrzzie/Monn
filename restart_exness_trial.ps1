param([switch]$RemoveCombinedOpenRiskCap, [switch]$EnableIndexMinimumLots)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.arena-venv\Scripts\python.exe'
$state = Join-Path $PSScriptRoot '.exness-arena'
$env:PYTHONPATH = $PSScriptRoot
$settings = Get-Content -LiteralPath (Join-Path $state 'settings.json') -Raw | ConvertFrom-Json
$enabledBots = @($settings.enabled_bots)
$marker = Join-Path $state 'STOP'
if (Test-Path -LiteralPath $marker) { throw 'Trial is already paused. Restart will not override an existing pause.' }
$records = @(Get-Content -LiteralPath (Join-Path $state 'processes.json') -Raw | ConvertFrom-Json)
foreach ($record in $records) {
    $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)"
    if (-not $procInfo) { continue }
    $expected = if ($record.repo -eq 'tracker') { 'exness_arena.tracker' } else { "$($record.repo)\exness_trial.py" }
    if ($procInfo.ExecutablePath -ne $python -or -not $procInfo.CommandLine.Contains($expected)) { throw "Process identity mismatch: $($record.repo)" }
}
$token = [guid]::NewGuid().ToString()
Set-Content -LiteralPath $marker -Value $token -NoNewline
$deadline = (Get-Date).AddSeconds(45)
do {
    $stillRunning = @($records | Where-Object { $_.repo -ne 'tracker' } | Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue })
    if (-not $stillRunning.Count) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if ($stillRunning.Count) { throw 'Runners have not exited cleanly. Trial remains paused.' }
if ($EnableIndexMinimumLots) {
    & $python -m exness_arena.control enable-index-minimum-lots
    if ($LASTEXITCODE -ne 0) { throw 'Index lot update failed. Trial remains paused.' }
}
# Old workers must exit before new discovery code updates the shared watchlist.
# Otherwise an old inventory refresh can overwrite the expanded universe mid-preflight.
foreach ($repo in $enabledBots) {
    & $python (Join-Path $workspace "$repo\exness_trial.py") --preflight
    if ($LASTEXITCODE -ne 0) { throw "Preflight failed: $repo. Trial remains paused." }
}
if ($RemoveCombinedOpenRiskCap) {
    & $python -m exness_arena.control remove-combined-cap
    if ($LASTEXITCODE -ne 0) { throw 'Combined-cap update failed. Trial remains paused.' }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item -LiteralPath (Join-Path $state 'processes.json') -Destination (Join-Path $state "processes.$stamp.json")
foreach ($record in $records | Where-Object { $_.repo -eq 'tracker' }) {
    # Identity was checked above. This collector is read-only; stopping it never changes positions.
    Stop-Process -Id $record.pid -ErrorAction SilentlyContinue
}
$trackerOut = Join-Path $state "tracker.$stamp.out.log"
$trackerErr = Join-Path $state "tracker.$stamp.err.log"
$tracker = Start-Process -FilePath $python -ArgumentList '-u','-m','exness_arena.tracker' -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput $trackerOut -RedirectStandardError $trackerErr -PassThru
$deadline = (Get-Date).AddSeconds(30)
$healthy = $false
do {
    $tracker.Refresh()
    if ($tracker.HasExited) { throw 'Tracker exited. Trial remains paused.' }
    try {
        $snapshot = Invoke-RestMethod 'http://127.0.0.1:8765/api/results'
        if ($snapshot.watchlist.Count -gt 0) { $healthy = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if (-not $healthy) { throw 'New tracker did not become healthy. Trial remains paused.' }
if ((Get-Content -LiteralPath $marker -Raw) -ne $token) { throw 'Pause marker changed; preserving the pause.' }
Remove-Item -LiteralPath $marker
$newRecords = @(@{repo='tracker';pid=$tracker.Id;stdout=$trackerOut;stderr=$trackerErr})
foreach ($repo in $enabledBots) {
    $out = Join-Path $state "$repo.$stamp.out.log"
    $err = Join-Path $state "$repo.$stamp.err.log"
    $process = Start-Process -FilePath $python -ArgumentList '-u',(Join-Path $workspace "$repo\exness_trial.py") -WorkingDirectory (Join-Path $workspace $repo) -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    $newRecords += @{repo=$repo;pid=$process.Id;stdout=$out;stderr=$err}
}
$newRecords | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'processes.json')
$newRecords | Select-Object repo,pid | Format-Table
Write-Output "Restarted with $($snapshot.watchlist.Count) monitored pairs. Existing positions and journal preserved."
