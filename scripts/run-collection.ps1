param(
    [ValidateSet('', 'top100', 'top1000', 'all')]
    [string]$ForceTier = '',
    [int]$HistoryDays = 30
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$collector = Join-Path $PSScriptRoot 'collect-public-snapshot.ps1'
$slotDate = [DateTime]::UtcNow.Date.AddHours([DateTime]::UtcNow.Hour)
$slot = [DateTime]::SpecifyKind($slotDate, [DateTimeKind]::Utc).ToString('o')
$hour = [DateTime]::UtcNow.Hour

if ($ForceTier) {
    $tiers = @($ForceTier)
} else {
    $tiers = New-Object System.Collections.Generic.List[string]
    $tiers.Add('top100')
    if (($hour % 4) -eq 0) { $tiers.Add('top1000') }
    if ($hour -eq 0) { $tiers.Add('all') }
}

Write-Host "예약 수집 슬롯: $slot / 대상: $($tiers -join ', ')"
foreach ($tier in $tiers) {
    & $collector -Tier $tier -ObservedAt $slot -HistoryDays $HistoryDays -DelayMilliseconds 250
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "수집기 종료 코드 $LASTEXITCODE ($tier)" }
}

$manifest = [pscustomobject]@{
    ranAt = [DateTime]::UtcNow.ToString('o')
    scheduledSlot = $slot
    tiers = @($tiers)
    policy = '시간별 상위 100, 4시간별 상위 1,000, 일별 현재 추적 목록. 실제 공개 API 관측값만 저장.'
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'public\data\collection_status.json') -Encoding utf8
Write-Host "수집 완료: $($tiers -join ', ')"
