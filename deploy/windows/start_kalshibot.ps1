# Start kalshibot on Windows (no Docker). Folder layout: C:\kalshibot\{src, data, secrets\kalshi.pem, config.yaml, .env}
# Run by hand:  powershell -ExecutionPolicy Bypass -File C:\kalshibot\start_kalshibot.ps1
# As a service (auto start at boot, restart on crash): see WINDOWS.md (NSSM).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
# secrets and settings from .env (KEY=VALUE lines)
Get-Content (Join-Path $root ".env") | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process") }
}
$env:KALSHI_PRIVATE_KEY_PATH = Join-Path $root "secrets\kalshi.pem"
$env:PYTHONPATH = Join-Path $root "src"
$env:PYTHONUNBUFFERED = "1"
# openssl.exe (signs Kalshi requests) ships with Git for Windows
$git = "C:\Program Files\Git\usr\bin"
if ((Test-Path $git) -and -not ($env:Path -like "*$git*")) { $env:Path = "$git;$env:Path" }
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) { throw "openssl not found: install Git for Windows (git-scm.com)" }
New-Item -ItemType Directory -Force -Path (Join-Path $root "data"), (Join-Path $root "logs") | Out-Null
python -m kalshibot run --config (Join-Path $root "config.yaml") *>> (Join-Path $root "logs\kalshibot.log")
