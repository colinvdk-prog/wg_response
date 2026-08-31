$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repo = Split-Path -Parent $PSScriptRoot

function Read-Json([string]$RelativePath) {
    $path = Join-Path $Repo $RelativePath
    return Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
}

function Sum-Field($Rows, [string]$Field) {
    $m = @($Rows) | Measure-Object -Property $Field -Sum
    if ($null -eq $m -or $null -eq $m.Sum) { return 0.0 }
    return [double]$m.Sum
}

function Mean-Values($Values) {
    $a = @($Values)
    if ($a.Count -eq 0) { return $null }
    return [double](($a | Measure-Object -Average).Average)
}

function Median-Values($Values) {
    $a = @($Values | Sort-Object)
    if ($a.Count -eq 0) { return $null }
    $mid = [int][math]::Floor($a.Count / 2.0)
    if (($a.Count % 2) -eq 1) { return [double]$a[$mid] }
    return ([double]$a[$mid - 1] + [double]$a[$mid]) / 2.0
}

# Linear interpolation with h=(n-1)p, equivalent to the usual type-7
# percentile definition used by NumPy's default linear method.
function Percentile-Type7($Values, [double]$P) {
    $a = @($Values | Sort-Object)
    if ($a.Count -eq 0) { return $null }
    if ($a.Count -eq 1) { return [double]$a[0] }
    $h = ($a.Count - 1) * $P
    $lo = [int][math]::Floor($h)
    $hi = [int][math]::Ceiling($h)
    if ($lo -eq $hi) { return [double]$a[$lo] }
    $w = $h - $lo
    return ([double]$a[$lo] * (1.0 - $w)) + ([double]$a[$hi] * $w)
}

function Pool-Coverage($Rows) {
    $rowsArray = @($Rows)
    $n = Sum-Field $rowsArray 'n_new_burnable'
    $cover = Sum-Field $rowsArray 'covered_possible'
    $late = Sum-Field $rowsArray 'late_miss'
    return [ordered]@{
        pairs = $rowsArray.Count
        cells = [int]$n
        covered = [int]$cover
        late = [int]$late
        coverage_pct = if ($n -gt 0) { 100.0 * $cover / $n } else { $null }
        late_pct = if ($n -gt 0) { 100.0 * $late / $n } else { $null }
    }
}

function Concentration($Rows) {
    $rowsArray = @($Rows)
    $total = Sum-Field $rowsArray 'n_new_burnable'
    $shares = @($rowsArray | ForEach-Object { [double]$_.n_new_burnable / $total } | Sort-Object -Descending)
    $hhi = [double](($shares | ForEach-Object { $_ * $_ } | Measure-Object -Sum).Sum)
    return [ordered]@{
        hhi = $hhi
        effective_n_inverse_hhi = 1.0 / $hhi
        largest_share_pct = 100.0 * $shares[0]
        top3_share_pct = 100.0 * (($shares | Select-Object -First 3 | Measure-Object -Sum).Sum)
    }
}

function Incident-Concentration($Rows) {
    $rowsArray = @($Rows)
    $total = Sum-Field $rowsArray 'n_new_burnable'
    $groups = @($rowsArray | Group-Object incident | ForEach-Object {
        $cells = Sum-Field $_.Group 'n_new_burnable'
        [pscustomobject]@{ incident = $_.Name; name = $_.Group[0].name; cells = [int]$cells; share = $cells / $total }
    } | Sort-Object cells -Descending)
    $hhi = [double](($groups | ForEach-Object { $_.share * $_.share } | Measure-Object -Sum).Sum)
    return [ordered]@{
        incidents = $groups.Count
        hhi = $hhi
        effective_n_inverse_hhi = 1.0 / $hhi
        largest = $groups[0]
        shares = $groups
    }
}

function Belt-Stats($Rows) {
    $rowsArray = @($Rows)
    $homes = @($rowsArray | ForEach-Object { $_.homes })
    $belt = @($homes | Where-Object {
        $_.front -or (($null -ne $_.dist) -and ([double]$_.dist -le 1000.0))
    })
    $gelled = @($belt | Where-Object { $_.gelled }).Count
    return [ordered]@{
        scenarios = $rowsArray.Count
        homes = $homes.Count
        belt_total = $belt.Count
        belt_gelled = $gelled
        belt_pct = if ($belt.Count -gt 0) { 100.0 * $gelled / $belt.Count } else { $null }
        violations = [int](($rowsArray | Measure-Object -Property viol -Sum).Sum)
        overrun_ticks = [int](($rowsArray | Measure-Object -Property ov_ticks -Sum).Sum)
    }
}

$aug = Read-Json 'scratch_review/hindcast_aug_data/aug_replay.json'
$augDeployedAll = @($aug.rows | Where-Object { $_.variant -eq 'as-deployed' })
$augDeployed = @($augDeployedAll | Where-Object { [int]$_.n_new_burnable -gt 0 })
$augTotalCells = Sum-Field $augDeployed 'n_new_burnable'

$headRatios = @($augDeployed | Where-Object { [double]$_.obs_head_m -gt 0.0 } |
    ForEach-Object { [double]$_.head_m_p50 / [double]$_.obs_head_m })
$tartar48 = $augDeployed | Where-Object { $_.name -eq 'Tartar-ID' -and [double]$_.dt_h -gt 48.0 -and [double]$_.dt_h -lt 49.0 }
$bug1297 = $augDeployed | Where-Object { $_.name -eq 'Bug-CA' -and [double]$_.dt_h -gt 12.9 -and [double]$_.dt_h -lt 13.1 }
$bug9486 = $augDeployed | Where-Object { $_.name -eq 'Bug-CA' -and [double]$_.dt_h -gt 9.4 -and [double]$_.dt_h -lt 9.6 }

$a1 = [ordered]@{
    scored_pairs = $augDeployed.Count
    all_variant_rows_including_zero_cell = $augDeployedAll.Count
    early_miss_sum = [int](Sum-Field $augDeployed 'early_miss')
    p10_before_window_sum = [int](Sum-Field $augDeployed 'p10_before_window')
    covered_certain_sum = [int](Sum-Field $augDeployed 'covered_certain')
    rows_cover_ne_p10_not_after = @($augDeployedAll | Where-Object { [int]$_.covered_possible -ne [int]$_.p10_not_after_window }).Count
    rows_late_plus_cover_ne_n = @($augDeployedAll | Where-Object { ([int]$_.late_miss + [int]$_.covered_possible) -ne [int]$_.n_new_burnable }).Count
    pooled_cover_eq_p10_not_after = ((Sum-Field $augDeployed 'covered_possible') -eq (Sum-Field $augDeployed 'p10_not_after_window'))
    pooled_late_plus_cover_eq_n = (((Sum-Field $augDeployed 'late_miss') + (Sum-Field $augDeployed 'covered_possible')) -eq $augTotalCells)
    head_ratio_n = $headRatios.Count
    mean_head_ratio = Mean-Values $headRatios
    true_median_head_ratio = Median-Values $headRatios
    tartar_48h = [ordered]@{
        name = $tartar48.name
        dt_h = [double]$tartar48.dt_h
        cells = [int]$tartar48.n_new_burnable
        head_ratio = [double]$tartar48.head_m_p50 / [double]$tartar48.obs_head_m
        observed_area_ac = [double]$tartar48.obs_new_area_ac
        modeled_area_ac = [double]$tartar48.modeled_new_area_ac_p50
        area_ratio = [double]$tartar48.modeled_new_area_ac_p50 / [double]$tartar48.obs_new_area_ac
    }
    bug_12_97h = [ordered]@{
        name = $bug1297.name
        dt_h = [double]$bug1297.dt_h
        cells = [int]$bug1297.n_new_burnable
        head_ratio = [double]$bug1297.head_m_p50 / [double]$bug1297.obs_head_m
        observed_area_ac = [double]$bug1297.obs_new_area_ac
        modeled_area_ac = [double]$bug1297.modeled_new_area_ac_p50
        area_ratio = [double]$bug1297.modeled_new_area_ac_p50 / [double]$bug1297.obs_new_area_ac
    }
}

$binDefs = @(
    [pscustomobject]@{ label = '(0,6]'; min = 0.0; max = 6.0 },
    [pscustomobject]@{ label = '(6,12]'; min = 6.0; max = 12.0 },
    [pscustomobject]@{ label = '(12,24]'; min = 12.0; max = 24.0 },
    [pscustomobject]@{ label = '(24,48]'; min = 24.0; max = 48.0 },
    [pscustomobject]@{ label = '(48,96]'; min = 48.0; max = 96.0 }
)
$bins = [ordered]@{}
foreach ($b in $binDefs) {
    $rows = @($augDeployed | Where-Object { [double]$_.dt_h -gt $b.min -and [double]$_.dt_h -le $b.max })
    $pool = Pool-Coverage $rows
    $pool['cell_share_pct'] = if ($augTotalCells -gt 0) { 100.0 * $pool.cells / $augTotalCells } else { $null }
    $bins[$b.label] = $pool
}
$over12 = @($augDeployed | Where-Object { [double]$_.dt_h -gt 12.0 })
$shortWithoutBugPair = @($augDeployed | Where-Object {
    [double]$_.dt_h -gt 6.0 -and [double]$_.dt_h -le 12.0 -and
    -not ($_.incident -eq $bug9486.incident -and [double]$_.t0_s -eq [double]$bug9486.t0_s)
})
$shortLate = Sum-Field @($augDeployed | Where-Object { [double]$_.dt_h -gt 6.0 -and [double]$_.dt_h -le 12.0 }) 'late_miss'

$a2 = [ordered]@{
    bins = $bins
    over_12h_combined = Pool-Coverage $over12
    no_pair_at_or_under_6h = (@($augDeployed | Where-Object { [double]$_.dt_h -le 6.0 }).Count -eq 0)
    pair_concentration = Concentration $augDeployed
    incident_concentration = Incident-Concentration $augDeployed
    drop_bug_9_486h_pair = Pool-Coverage @($augDeployed | Where-Object {
        -not ($_.incident -eq $bug9486.incident -and [double]$_.t0_s -eq [double]$bug9486.t0_s)
    })
    drop_all_bug_incident_pairs = Pool-Coverage @($augDeployed | Where-Object { $_.incident -ne $bug9486.incident })
    drop_all_tartar_incident_pairs = Pool-Coverage @($augDeployed | Where-Object { $_.name -ne 'Tartar-ID' })
    short_6_12_without_bug_pair = Pool-Coverage $shortWithoutBugPair
    bug_share_of_6_12_late_pct = 100.0 * [double]$bug9486.late_miss / $shortLate
}

$oos = Read-Json 'scratch_review/hindcast_oos_data/oos_realism.json'
$july = @($oos | Where-Object { $_.variant -eq 'grass_floor' -and [int]$_.n_new_burnable -gt 0 })
$julyPairCover = @($july | ForEach-Object { 100.0 * [double]$_.covered_possible / [double]$_.n_new_burnable })
$julyPairLate = @($july | ForEach-Object { 100.0 * [double]$_.late_miss / [double]$_.n_new_burnable })
$julyConcentration = Concentration $july
$julyLargest = $july | Sort-Object n_new_burnable -Descending | Select-Object -First 1
$julyIncidents = @($july | Group-Object incident | ForEach-Object {
    $n = Sum-Field $_.Group 'n_new_burnable'
    $cover = Sum-Field $_.Group 'covered_possible'
    $late = Sum-Field $_.Group 'late_miss'
    [pscustomobject]@{
        incident = $_.Name
        name = $_.Group[0].name
        pairs = $_.Count
        cells = [int]$n
        cover_pct = 100.0 * $cover / $n
        late_pct = 100.0 * $late / $n
    }
})
$a3 = [ordered]@{
    pool = Pool-Coverage $july
    pair_mean_cover_pct = Mean-Values $julyPairCover
    pair_mean_late_pct = Mean-Values $julyPairLate
    median_pair_cover_pct = Median-Values $julyPairCover
    distinct_incidents = $julyIncidents.Count
    incident_mean_cover_pct = Mean-Values @($julyIncidents | ForEach-Object { $_.cover_pct })
    incident_mean_late_pct = Mean-Values @($julyIncidents | ForEach-Object { $_.late_pct })
    incident_rows = $julyIncidents
    pair_concentration = $julyConcentration
    largest_pair = [ordered]@{
        name = $julyLargest.name
        cells = [int]$julyLargest.n_new_burnable
        share_pct = 100.0 * [double]$julyLargest.n_new_burnable / (Sum-Field $july 'n_new_burnable')
        coverage_pct = 100.0 * [double]$julyLargest.covered_possible / [double]$julyLargest.n_new_burnable
    }
    incident_concentration = Incident-Concentration $july
}

$discover = Read-Json 'scratch_review/aug_hindcast_discover.json'
$dropped = @($discover.all | Where-Object { -not $_.kept })
$bigGrass = $discover.all | Where-Object { $_.name -eq 'BIG GRASS' }
$selectionRuleMismatches = @($discover.all | Where-Object {
    [bool]$_.kept -ne (([int]$_.n_rows -ge 3) -and ([double]$_.span_h -ge 6.0))
})
$a4 = [ordered]@{
    candidates_all = @($discover.all).Count
    kept_array_count = @($discover.kept).Count
    kept_true_count = @($discover.all | Where-Object { $_.kept }).Count
    dropped_count = $dropped.Count
    dropped_n_rows_le_1 = @($dropped | Where-Object { [int]$_.n_rows -le 1 }).Count
    dropped_n_rows_gt_1_and_span_zero = @($dropped | Where-Object { [int]$_.n_rows -gt 1 -and [double]$_.span_h -eq 0.0 }).Count
    dropped_other = @($dropped | Where-Object { -not (([int]$_.n_rows -le 1) -or ([int]$_.n_rows -gt 1 -and [double]$_.span_h -eq 0.0)) }).Count
    rule_n_rows_ge_3_and_span_ge_6_mismatches = $selectionRuleMismatches.Count
    big_grass = [ordered]@{
        kept = [bool]$bigGrass.kept
        state = $bigGrass.state
        n_rows = [int]$bigGrass.n_rows
        max_acres = [double]$bigGrass.max_acres
        span_h = [double]$bigGrass.span_h
        t_min = [double]$bigGrass.t_min
        t_max = [double]$bigGrass.t_max
    }
}

$lead = Read-Json 'scratch_review/probe_leadtime_lead_c8d10.json'
$leadHomes = @($lead.rows | ForEach-Object { $_.homes })
$truthReached = @($leadHomes | Where-Object { $null -ne $_.arr_h })
$gelled = @($leadHomes | Where-Object { $_.gelled })
$gelledReached = @($leadHomes | Where-Object { $_.gelled -and $null -ne $_.arr_h })
$gelledNever = @($leadHomes | Where-Object { $_.gelled -and $null -eq $_.arr_h })
$arrivals = @($gelledReached | ForEach-Object { [double]$_.arr_h })

function Gel-Life-HandTrace([double]$TempC, [double]$RhPct, [double]$WindMps) {
    $rh = [math]::Max($RhPct, 1e-9)
    $wind = [math]::Max($WindMps, 0.0)
    $raw = 6.0 * [math]::Exp(-0.08 * ($TempC - 25.0)) * [math]::Pow($rh / 50.0, 0.3) / (1.0 + 0.05 * $wind)
    return [ordered]@{ temp_c = $TempC; rh_pct = $RhPct; wind_mps = $WindMps; raw_h = $raw; returned_h = [math]::Max(1.0, $raw) }
}

$a5 = [ordered]@{
    scenarios = @($lead.rows).Count
    homes_total = $leadHomes.Count
    truth_reached = $truthReached.Count
    gelled = $gelled.Count
    gelled_and_truth_reached = $gelledReached.Count
    gelled_and_never_truth_reached = $gelledNever.Count
    gelled_never_reached_share_of_gelled_pct = 100.0 * $gelledNever.Count / $gelled.Count
    thresholds = [ordered]@{
        gt_6h = [ordered]@{ n = @($arrivals | Where-Object { $_ -gt 6.0 }).Count; pct = 100.0 * @($arrivals | Where-Object { $_ -gt 6.0 }).Count / $arrivals.Count }
        gt_24h = [ordered]@{ n = @($arrivals | Where-Object { $_ -gt 24.0 }).Count; pct = 100.0 * @($arrivals | Where-Object { $_ -gt 24.0 }).Count / $arrivals.Count }
        gt_72h = [ordered]@{ n = @($arrivals | Where-Object { $_ -gt 72.0 }).Count; pct = 100.0 * @($arrivals | Where-Object { $_ -gt 72.0 }).Count / $arrivals.Count }
    }
    arrival_h = [ordered]@{
        min = [double](($arrivals | Measure-Object -Minimum).Minimum)
        p10_type7 = Percentile-Type7 $arrivals 0.10
        p50_type7 = Percentile-Type7 $arrivals 0.50
        p90_type7 = Percentile-Type7 $arrivals 0.90
        mean = Mean-Values $arrivals
        max = [double](($arrivals | Measure-Object -Maximum).Maximum)
    }
    gel_life_hand_trace_due_to_python_unavailable = @(
        (Gel-Life-HandTrace 25 50 0),
        (Gel-Life-HandTrace 35 20 5),
        (Gel-Life-HandTrace 33 12 8),
        (Gel-Life-HandTrace 38 8 15)
    )
}

$windFiles = [ordered]@{
    low_c4 = 'scratch_review/census_wind_low_c4.json'
    low_c8 = 'scratch_review/census_wind_low_c8.json'
    moderate_c4 = 'scratch_review/census_wind_moderate_c4.json'
    moderate_c8 = 'scratch_review/census_wind_moderate_c8.json'
}
$windCurve = [ordered]@{}
foreach ($key in $windFiles.Keys) {
    $d = Read-Json $windFiles[$key]
    $windCurve[$key] = Belt-Stats $d.rows
}
foreach ($crews in 4, 8) {
    $d = Read-Json ("scratch_review/opt_ember_flip_c{0}.json" -f $crews)
    foreach ($inten in 'high', 'extreme') {
        $windCurve[("{0}_c{1}" -f $inten, $crews)] = Belt-Stats @($d.rows | Where-Object { $_.inten -eq $inten })
    }
}

$rsf = [ordered]@{}
foreach ($crews in 8, 10, 12) {
    $d = Read-Json ("scratch_review/rsf_city_c{0}_scatter.json" -f $crews)
    $raw = Belt-Stats $d.rows
    $raw['params'] = $d.params
    $raw['recorded_stats_e1000'] = $d.stats.e1000
    $rsf[("c{0}" -f $crews)] = $raw
}

$a6 = [ordered]@{
    wind_curve_raw_row_rederivation = $windCurve
    rsf_raw_row_rederivation = $rsf
}

$out = [ordered]@{
    derivation_engine = 'PowerShell ConvertFrom-Json; Python unavailable (py -3 exit 112)'
    A1 = $a1
    A2 = $a2
    A3 = $a3
    A4 = $a4
    A5 = $a5
    A6 = $a6
}

$json = $out | ConvertTo-Json -Depth 20
$outPath = Join-Path $PSScriptRoot 'rv2stats_derive_out.json'
[System.IO.File]::WriteAllText($outPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
Write-Output $json
