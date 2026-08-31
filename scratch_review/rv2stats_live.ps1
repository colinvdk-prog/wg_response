$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-ExactlyOneUnauthedGet([string]$Url) {
    $requestedAt = [DateTimeOffset]::UtcNow
    $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $Url -MaximumRedirection 0 -TimeoutSec 30
    $receivedAt = [DateTimeOffset]::UtcNow
    return [ordered]@{
        url = $Url
        requested_at_utc = $requestedAt.ToString('o')
        received_at_utc = $receivedAt.ToString('o')
        received_epoch_s = $receivedAt.ToUnixTimeMilliseconds() / 1000.0
        http_status = [int]$response.StatusCode
        body = [string]$response.Content
    }
}

function Find-NamedFields($Object, [string]$Path = '$') {
    $found = [System.Collections.Generic.List[object]]::new()
    function Walk($Value, [string]$At) {
        if ($null -eq $Value) { return }
        if ($Value -is [System.Management.Automation.PSCustomObject]) {
            foreach ($prop in $Value.PSObject.Properties) {
                $next = "$At.$($prop.Name)"
                if ($prop.Name -match '(?i)cycle|cadence|period|stale|started|restored|solver|degraded|uptime') {
                    $found.Add([pscustomobject]@{ path = $next; value = $prop.Value })
                }
                Walk $prop.Value $next
            }
        } elseif ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
            $i = 0
            foreach ($item in $Value) {
                Walk $item "$At[$i]"
                $i++
            }
        }
    }
    Walk $Object $Path
    return @($found)
}

# The only three network operations in this script and in this review.
$watchHealth = Invoke-ExactlyOneUnauthedGet 'https://y372zmpfuj.us-west-2.awsapprunner.com/healthz'
$simHealth = Invoke-ExactlyOneUnauthedGet 'https://m897yqgsmt.us-west-2.awsapprunner.com/healthz'
$watchState = Invoke-ExactlyOneUnauthedGet 'https://y372zmpfuj.us-west-2.awsapprunner.com/api/state'

$watchHealthJson = $watchHealth.body | ConvertFrom-Json
$simHealthJson = $simHealth.body | ConvertFrom-Json
$watchStateJson = $watchState.body | ConvertFrom-Json

$lastCycleAge = $null
if ($null -ne $watchHealthJson.last_cycle_t_s) {
    $lastCycleAge = [double]$watchHealth.received_epoch_s - [double]$watchHealthJson.last_cycle_t_s
}

$summary = [ordered]@{
    watch_health = [ordered]@{
        http_status = $watchHealth.http_status
        requested_at_utc = $watchHealth.requested_at_utc
        received_at_utc = $watchHealth.received_at_utc
        json = $watchHealthJson
        derived_last_cycle_age_s_at_receipt = $lastCycleAge
        last_cycle_utc = if ($null -ne $watchHealthJson.last_cycle_t_s) { [DateTimeOffset]::FromUnixTimeMilliseconds([int64]([double]$watchHealthJson.last_cycle_t_s * 1000.0)).UtcDateTime.ToString('o') } else { $null }
    }
    simulator_health = [ordered]@{
        http_status = $simHealth.http_status
        requested_at_utc = $simHealth.requested_at_utc
        received_at_utc = $simHealth.received_at_utc
        json = $simHealthJson
    }
    watcher_state_unauthenticated = [ordered]@{
        http_status = $watchState.http_status
        requested_at_utc = $watchState.requested_at_utc
        received_at_utc = $watchState.received_at_utc
        body_bytes_utf8 = [Text.Encoding]::UTF8.GetByteCount($watchState.body)
        top_level_fields = @($watchStateJson.PSObject.Properties.Name)
        cadence_staleness_related_fields = @(Find-NamedFields $watchStateJson)
    }
}

$saved = [ordered]@{
    summary = $summary
    raw_responses = [ordered]@{
        watcher_health = $watchHealth
        simulator_health = $simHealth
        watcher_state = $watchState
    }
}

$json = $saved | ConvertTo-Json -Depth 30
$outPath = Join-Path $PSScriptRoot 'rv2stats_live_out.json'
[System.IO.File]::WriteAllText($outPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
$summary | ConvertTo-Json -Depth 20
