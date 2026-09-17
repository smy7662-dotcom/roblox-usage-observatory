param(
    [string]$SourceRoot = '',
    [string]$OutputRoot = 'C:\Users\mdymr\Documents\roblox-usage-dashboard\public\data'
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $candidate = Get-ChildItem -LiteralPath 'C:\Users\mdymr\Documents' -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'data\roblox_historical\platform_ccu_daily_combined.csv') } |
        Select-Object -First 1
    if ($candidate) { $SourceRoot = Join-Path $candidate.FullName 'data\roblox_historical' }
}

function Read-CsvOrFail([string]$Name) {
    $path = Join-Path $SourceRoot $Name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing source file: $path"
    }
    return @(Import-Csv -LiteralPath $path)
}

function Read-OptionalCsv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    return @(Import-Csv -LiteralPath $Path)
}

function NumberOrNull($value) {
    if ([string]::IsNullOrWhiteSpace([string]$value)) { return $null }
    return [double]::Parse([string]$value, [Globalization.CultureInfo]::InvariantCulture)
}

function DateRange($values) {
    $sorted = @($values | Sort-Object)
    if ($sorted.Count -eq 0) { return [pscustomobject]@{ start = $null; end = $null } }
    return [pscustomobject]@{ start = $sorted[0]; end = $sorted[$sorted.Count - 1] }
}

$daily = Read-CsvOrFail 'platform_ccu_daily_combined.csv' | ForEach-Object {
    [pscustomobject]@{
        date = $_.date_utc
        avg = NumberOrNull $_.avg_ccu
        peak = NumberOrNull $_.peak_ccu
        source = $_.source
        sourceUrl = $_.source_url
    }
}

$hourly = Read-CsvOrFail 'platform_ccu_hourly_combined.csv' | ForEach-Object {
    [pscustomobject]@{
        timestamp = $_.timestamp_utc
        ccu = NumberOrNull $_.ccu
        peak = NumberOrNull $_.peak_ccu
        source = $_.source
        sourceUrl = $_.source_url
    }
}

$reported = Read-CsvOrFail 'platform_ccu_reported_points.csv' |
    Where-Object { $_.source_type -notmatch 'estimate' -and $_.note -notmatch '(?i)approximate' } |
    ForEach-Object {
    [pscustomobject]@{
        date = $_.date_or_period_utc
        ccu = NumberOrNull $_.ccu
        gameCcu = NumberOrNull $_.game_ccu
        scope = $_.metric_scope
        sourceType = $_.source_type
        confidence = $_.confidence
        note = $_.note
        sourceUrl = $_.source_url
    }
}

$games = Read-CsvOrFail 'bloxscout_daily_game_metrics.csv' | ForEach-Object {
    [pscustomobject]@{
        date = $_.date_utc
        universeId = $_.universe_id
        name = $_.name
        genre = $_.genre
        avg = NumberOrNull $_.avg_ccu
        peak = NumberOrNull $_.peak_ccu
        visitsDelta = NumberOrNull $_.visits_delta
        favorites = NumberOrNull $_.favorites_count
        source = 'bloxscout_daily'
        sourceUrl = $_.source_url
    }
}

$earlyShiftPath = Join-Path (Join-Path $SourceRoot 'early-shift') 'ccu_daily_by_game.csv'
$earlyShiftGames = Read-OptionalCsv $earlyShiftPath | ForEach-Object {
    $name = [string]$_.name
    if ([string]::IsNullOrWhiteSpace($name)) { $name = "게임 $($_.universe_id)" }
    [pscustomobject]@{
        date = $_.date_utc
        universeId = $_.universe_id
        name = $name
        genre = '미분류'
        avg = NumberOrNull $_.avg_ccu
        peak = NumberOrNull $_.peak_ccu
        visitsDelta = $null
        favorites = $null
        source = 'early_shift_daily'
        sourceUrl = 'local archive: early-shift'
    }
}

$gameHistory = @($earlyShiftGames) + @($games)

$meta = [pscustomobject]@{
    generatedAt = (Get-Date).ToUniversalTime().ToString('o')
    sourceRoot = $SourceRoot
    policy = 'No modeled DAU, no gap interpolation. Values are raw observations or explicitly reported points.'
    coverage = [pscustomobject]@{
        platformDaily = DateRange $daily.date
        platformHourly = DateRange $hourly.timestamp
        gameDaily = DateRange $games.date
        gameHistoryDaily = DateRange $gameHistory.date
    }
}

@($daily) | ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath (Join-Path $OutputRoot 'platform_daily.json') -Encoding utf8
@($hourly) | ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath (Join-Path $OutputRoot 'platform_hourly.json') -Encoding utf8
@($reported) | ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath (Join-Path $OutputRoot 'reported_points.json') -Encoding utf8
@($games) | ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath (Join-Path $OutputRoot 'games_daily.json') -Encoding utf8
@($gameHistory) | ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath (Join-Path $OutputRoot 'game_history_daily.json') -Encoding utf8
$meta | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath (Join-Path $OutputRoot 'meta.json') -Encoding utf8

Write-Host "Built $($daily.Count) daily rows, $($hourly.Count) hourly rows, $($games.Count) current game rows, $($gameHistory.Count) historical game rows."
