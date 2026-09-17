param(
    [switch]$Commit
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$distPublic = Join-Path $root 'dist\public'
$distData = Join-Path $distPublic 'data'

New-Item -ItemType Directory -Force -Path $distData | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'index.html') -Destination (Join-Path $root 'dist\index.html') -Force
Copy-Item -LiteralPath (Join-Path $root 'public\app.js') -Destination (Join-Path $distPublic 'app.js') -Force
Copy-Item -LiteralPath (Join-Path $root 'public\styles.css') -Destination (Join-Path $distPublic 'styles.css') -Force
Copy-Item -LiteralPath (Join-Path $root 'public\overrides.css') -Destination (Join-Path $distPublic 'overrides.css') -Force
Copy-Item -Path (Join-Path $root 'public\data\*.json') -Destination $distData -Force

if ($Commit) {
    Push-Location $root
    try {
        git add -- 'public/data' 'public/app.js' 'scripts' 'dist'
        $pending = git diff --cached --name-only
        if ($pending) {
            git commit -m "chore: update Roblox usage observations"
        } else {
            Write-Host '변경사항이 없어 커밋하지 않았습니다.'
        }
    } finally {
        Pop-Location
    }
}

Write-Host "정적 배포 묶음 동기화 완료: $distPublic"
