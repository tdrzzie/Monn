$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = $PSScriptRoot
$workspace = Split-Path -Parent $PSScriptRoot
& (Join-Path $workspace '.arena-venv\Scripts\python.exe') -m exness_arena.control pause
