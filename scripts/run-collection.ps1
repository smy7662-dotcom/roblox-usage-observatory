# 로컬 수동 수집: GitHub Actions 와 같은 수집기(scripts/collect.py)를 이 폴더의 public/data 에 돌린다.
# 운영 수집·배포는 GitHub Actions(.github/workflows/collect-deploy.yml)가 data 브랜치에서 한다.
param(
    [ValidateSet('', 'top100', 'top1000', 'all')]
    [string]$ForceTier = '',
    # 예전 Codex 자동화 프롬프트가 넘기는 인자 — 보존 기간은 collect.py 에 30일로 고정.
    [int]$HistoryDays = 30
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$collectorArgs = @((Join-Path $PSScriptRoot 'collect.py'), '--data-root', $projectRoot)
if ($ForceTier) { $collectorArgs += @('--force-tier', $ForceTier) }
$env:PYTHONIOENCODING = 'utf-8'
python @collectorArgs
if ($LASTEXITCODE -ne 0) { throw "수집기 종료 코드 $LASTEXITCODE" }
