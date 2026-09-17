param(
    [string]$UniverseIdsFile = '',
    [string]$OutputFile = 'C:\Users\mdymr\Documents\roblox-usage-dashboard\public\data\live_games.json',
    [int]$BatchSize = 50,
    [int]$DelayMilliseconds = 1000
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($UniverseIdsFile)) {
    $UniverseIdsFile = 'C:\Users\mdymr\Documents\roblox-usage-dashboard\public\data\games_daily.json'
}

if (-not (Test-Path -LiteralPath $UniverseIdsFile)) {
    throw "Universe ID source not found: $UniverseIdsFile"
}

if ($UniverseIdsFile.EndsWith('.json')) {
    $sourceRows = @(Get-Content -Raw -LiteralPath $UniverseIdsFile | ConvertFrom-Json)
    $universeIds = @($sourceRows | ForEach-Object { [string]$_.universeId } | Where-Object { $_ } | Sort-Object -Unique)
} else {
    $universeIds = @(Get-Content -LiteralPath $UniverseIdsFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique)
}

if ($universeIds.Count -eq 0) { throw 'No universe IDs found.' }

$collectedAt = [DateTime]::UtcNow.ToString('o')
$results = New-Object System.Collections.Generic.List[object]
for ($i = 0; $i -lt $universeIds.Count; $i += $BatchSize) {
    $end = [Math]::Min($i + $BatchSize - 1, $universeIds.Count - 1)
    $batch = @($universeIds[$i..$end])
    $query = [Uri]::EscapeDataString(($batch -join ','))
    $response = $null
    for ($attempt = 0; $attempt -lt 5 -and $null -eq $response; $attempt++) {
        try {
            $response = Invoke-RestMethod -Method Get -Uri "https://games.roblox.com/v1/games?universeIds=$query"
        } catch {
            if ($attempt -eq 4) { throw }
            $backoff = 10000 * ($attempt + 1)
            Write-Warning "공개 API 제한 응답. $backoff ms 후 재시도합니다."
            Start-Sleep -Milliseconds $backoff
        }
    }
    foreach ($row in @($response.data)) {
        $results.Add([pscustomobject]@{
            observedAt = $collectedAt
            universeId = [string]$row.id
            name = $row.name
            genre = if ($row.genre) { $row.genre } else { '미분류' }
            ccu = if ($null -ne $row.playing) { [double]$row.playing } else { $null }
            visits = if ($null -ne $row.visits) { [double]$row.visits } else { $null }
            favorites = if ($null -ne $row.favoritedCount) { [double]$row.favoritedCount } else { $null }
            maxPlayers = if ($null -ne $row.maxPlayers) { [int]$row.maxPlayers } else { $null }
            creator = if ($row.creator) { $row.creator.name } else { $null }
            updatedAt = $row.updated
            source = 'Roblox public games API'
        })
    }
    if ($DelayMilliseconds -gt 0 -and $end -lt ($universeIds.Count - 1)) { Start-Sleep -Milliseconds ([Math]::Max($DelayMilliseconds, 1000)) }
    Write-Progress -Activity 'Roblox 게임 스냅샷 수집' -Status "$($end + 1) / $($universeIds.Count)" -PercentComplete ((($end + 1) / $universeIds.Count) * 100)
}

$parent = Split-Path -Parent $OutputFile
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$payload = [pscustomobject]@{
    collectedAt = $collectedAt
    count = $results.Count
    policy = '공개 API 원시 관측값. DAU 환산·보간 없음.'
    data = @($results)
}
$payload | ConvertTo-Json -Depth 6 -Compress | Set-Content -LiteralPath $OutputFile -Encoding utf8
Write-Host "Collected $($results.Count) game snapshots at $collectedAt"
