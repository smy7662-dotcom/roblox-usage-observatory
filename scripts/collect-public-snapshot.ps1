param(
    [ValidateSet('top100', 'top1000', 'all')]
    [string]$Tier = 'all',
    [string]$UniverseIdsFile = '',
    [string]$SourceFile = 'C:\Users\mdymr\Documents\roblox-usage-dashboard\public\data\game_history_daily.json',
    [string]$OutputFile = 'C:\Users\mdymr\Documents\roblox-usage-dashboard\public\data\live_games.json',
    [string]$HistoryFile = 'C:\Users\mdymr\Documents\roblox-usage-dashboard\public\data\live_game_history.json',
    [int]$BatchSize = 50,
    [int]$DelayMilliseconds = 250,
    [int]$HistoryDays = 30,
    [string]$ObservedAt = ''
)

$ErrorActionPreference = 'Stop'

function Get-HeaderValue {
    param([object]$Headers, [string]$Name)
    if ($null -eq $Headers) { return $null }
    try { return [string]$Headers[$Name] } catch { return $null }
}

function Read-UniverseIds {
    param([string]$Path, [string]$SelectionTier, [string]$FallbackSource)

    if ($Path) {
        if (-not (Test-Path -LiteralPath $Path)) { throw "Universe ID source not found: $Path" }
        if ($Path.EndsWith('.json')) {
            $rows = @(Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json)
            return @($rows | ForEach-Object { [string]$_.universeId } | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique)
        }
        return @(Get-Content -LiteralPath $Path | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique)
    }

    if (-not (Test-Path -LiteralPath $FallbackSource)) { throw "Historical source not found: $FallbackSource" }
    $sourceRows = @(Get-Content -Raw -LiteralPath $FallbackSource | ConvertFrom-Json)
    $latestById = @{}
    foreach ($row in $sourceRows) {
        $id = [string]$row.universeId
        if ($id -notmatch '^\d+$') { continue }
        $date = [string]$row.date
        if (-not $latestById.ContainsKey($id) -or $date -gt [string]$latestById[$id].date) {
            $latestById[$id] = $row
        }
    }

    $ranked = @($latestById.Values | Sort-Object @{ Expression = { if ($null -eq $_.avg) { -1 } else { [double]$_.avg } }; Descending = $true })
    if ($SelectionTier -eq 'top100') { $ranked = @($ranked | Select-Object -First 100) }
    if ($SelectionTier -eq 'top1000') { $ranked = @($ranked | Select-Object -First 1000) }
    return @($ranked | ForEach-Object { [string]$_.universeId } | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique)
}

$universeIds = @(Read-UniverseIds -Path $UniverseIdsFile -SelectionTier $Tier -FallbackSource $SourceFile)
if ($universeIds.Count -eq 0) { throw 'No universe IDs found.' }

if ($ObservedAt) {
    try { $observedDate = [DateTime]::Parse($ObservedAt).ToUniversalTime() }
    catch { throw "ObservedAt must be an ISO timestamp: $ObservedAt" }
} else {
    $observedDate = [DateTime]::UtcNow
}
$observedIso = $observedDate.ToString('o')
$results = New-Object System.Collections.Generic.List[object]
$rateLimit = $null
$rateRemaining = $null
$rateReset = $null

for ($i = 0; $i -lt $universeIds.Count; $i += $BatchSize) {
    $end = [Math]::Min($i + $BatchSize - 1, $universeIds.Count - 1)
    $batch = @($universeIds[$i..$end])
    $query = [Uri]::EscapeDataString(($batch -join ','))
    $response = $null
    $request = $null
    for ($attempt = 0; $attempt -lt 6 -and $null -eq $response; $attempt++) {
        try {
            $request = Invoke-WebRequest -Method Get -Uri "https://games.roblox.com/v1/games?universeIds=$query" -UseBasicParsing
            $response = $request.Content | ConvertFrom-Json
            $rateLimit = Get-HeaderValue $request.Headers 'x-ratelimit-limit'
            $rateRemaining = Get-HeaderValue $request.Headers 'x-ratelimit-remaining'
            $rateReset = Get-HeaderValue $request.Headers 'x-ratelimit-reset'
        } catch {
            if ($attempt -eq 5) { throw }
            $retryAfter = $null
            try { $retryAfter = [int]$_.Exception.Response.Headers['Retry-After'] } catch { $retryAfter = $null }
            $backoff = if ($retryAfter -and $retryAfter -gt 0) { $retryAfter * 1000 } else { [Math]::Min(60000, 10000 * [Math]::Pow(2, $attempt)) }
            Write-Warning "Roblox 공개 API 제한/일시 오류. $backoff ms 후 재시도합니다."
            Start-Sleep -Milliseconds $backoff
        }
    }

    foreach ($row in @($response.data)) {
        $results.Add([pscustomobject]@{
            observedAt = $observedIso
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
            tier = $Tier
        })
    }

    if ($DelayMilliseconds -gt 0 -and $end -lt ($universeIds.Count - 1)) {
        Start-Sleep -Milliseconds ([Math]::Max($DelayMilliseconds, 100))
    }
    Write-Progress -Activity "Roblox 게임 스냅샷 수집 ($Tier)" -Status "$($end + 1) / $($universeIds.Count)" -PercentComplete ((($end + 1) / $universeIds.Count) * 100)
}

if ($results.Count -eq 0) { throw "Roblox API returned no rows for tier $Tier." }

$parent = Split-Path -Parent $OutputFile
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$payload = [pscustomobject]@{
    collectedAt = $observedIso
    count = $results.Count
    selected = $universeIds.Count
    tier = $Tier
    rateLimit = $rateLimit
    rateRemaining = $rateRemaining
    rateReset = $rateReset
    policy = '공개 API 원시 관측값. DAU 환산·보간 없음.'
    data = @($results.ToArray())
}
$payload | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath $OutputFile -Encoding utf8

$historyRows = New-Object System.Collections.Generic.List[object]
if (Test-Path -LiteralPath $HistoryFile) {
    try {
        $historyPayload = Get-Content -Raw -LiteralPath $HistoryFile | ConvertFrom-Json
        foreach ($item in @($historyPayload.data)) { $historyRows.Add($item) }
    } catch {
        Write-Warning "기존 live_game_history.json을 읽지 못해 새 관측값만 사용합니다."
    }
}

$byKey = @{}
foreach ($item in $historyRows) {
    $key = "$([string]$item.universeId)|$([string]$item.observedAt)"
    if ($item.universeId -and $item.observedAt) { $byKey[$key] = $item }
}
foreach ($item in $results) {
    $byKey["$($item.universeId)|$($item.observedAt)"] = [pscustomobject]@{
        date = $item.observedAt
        observedAt = $item.observedAt
        universeId = $item.universeId
        name = $item.name
        genre = $item.genre
        avg = $item.ccu
        peak = $item.ccu
        visitsDelta = $null
        favorites = $item.favorites
        creator = $item.creator
        source = 'roblox_public_games_api'
        sourceUrl = 'https://games.roblox.com/v1/games'
        tier = $Tier
    }
}

$cutoff = [DateTime]::UtcNow.AddDays(-[Math]::Max($HistoryDays, 1))
$trimmed = @($byKey.Values | Where-Object {
    try { [DateTime]::Parse([string]$_.observedAt).ToUniversalTime() -ge $cutoff } catch { $false }
} | Sort-Object @{ Expression = { [string]$_.observedAt } }, @{ Expression = { [string]$_.universeId } })

$historyParent = Split-Path -Parent $HistoryFile
New-Item -ItemType Directory -Force -Path $historyParent | Out-Null
[pscustomobject]@{
    updatedAt = [DateTime]::UtcNow.ToString('o')
    retentionDays = $HistoryDays
    policy = 'Roblox 공개 게임 API의 실제 playing 관측값만 보존. DAU 환산·보간 없음.'
    data = $trimmed
} | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath $HistoryFile -Encoding utf8

Write-Host "Collected $($results.Count) / selected $($universeIds.Count) game snapshots at $observedIso ($Tier); retained $($trimmed.Count) live history rows."
