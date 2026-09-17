$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
powershell -ExecutionPolicy Bypass -File .\scripts\build-data.ps1
python -m http.server 8000
