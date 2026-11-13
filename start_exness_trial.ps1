$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.arena-venv\Scripts\python.exe'
$state = Join-Path $PSScriptRoot '.exness-arena'
$env:PYTHONPATH = $PSScriptRoot
$settings = Get-Content -LiteralPath (Join-Path $state 'settings.json') -Raw | ConvertFrom-Json
$enabledBots = @($settings.enabled_bots)
if (Test-Path (Join-Path $state 'STOP')) { throw 'Trial is paused. Inspect state before manually resuming.' }
foreach ($repo in $enabledBots) {
    & $python (Join-Path $workspace "$repo\exness_trial.py") --preflight
    if ($LASTEXITCODE -ne 0) { throw "Preflight failed: $repo" }
}
$configPath = Join-Path $state 'settings.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($config.live) { throw 'Live is already enabled; inspect existing processes before restarting.' }
$config.live = $true
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $configPath -Encoding utf8NoBOM
$tracker = Start-Process -FilePath $python -ArgumentList '-u','-m','exness_arena.tracker' -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $state 'tracker.out.log') -RedirectStandardError (Join-Path $state 'tracker.err.log') -PassThru
try {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        if ($tracker.HasExited) { throw 'Tracker exited during startup' }
        try { $snapshot = Invoke-RestMethod 'http://127.0.0.1:8765/api/results'; break } catch { Start-Sleep -Milliseconds 500 }
    } while ((Get-Date) -lt $deadline)
    if (-not $snapshot) { throw 'Tracker did not become healthy' }
    $records = @(@{repo='tracker'; pid=$tracker.Id})
    foreach ($repo in $enabledBots) {
        $process = Start-Process -FilePath $python -ArgumentList '-u',(Join-Path $workspace "$repo\exness_trial.py") -WorkingDirectory (Join-Path $workspace $repo) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $state "$repo.out.log") -RedirectStandardError (Join-Path $state "$repo.err.log") -PassThru
        $records += @{repo=$repo; pid=$process.Id}
    }
    $records | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'processes.json')
    $records | Format-Table
    Write-Output 'Dashboard: http://127.0.0.1:8765'
} catch {
    $config.live = $false
    $config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $configPath -Encoding utf8NoBOM
    throw
}
