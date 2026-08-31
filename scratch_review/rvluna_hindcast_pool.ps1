$ErrorActionPreference = 'Stop'
$doc = Get-Content -Raw -LiteralPath 'tools/hindcast_data/replay_results.json' | ConvertFrom-Json
$scored = @($doc.pairs | Where-Object { $_.n_new_burnable -gt 0 })
$n = ($scored | Measure-Object -Property n_new_burnable -Sum).Sum
$covered = ($scored | Measure-Object -Property covered_possible -Sum).Sum
$late = ($scored | Measure-Object -Property late_miss -Sum).Sum
$early = ($scored | Measure-Object -Property early_miss -Sum).Sum
$scored | ForEach-Object {
    '{0,-8} {1,8} {2,8} {3,8}' -f $_.name, $_.n_new_burnable, $_.covered_possible, $_.late_miss
}
[pscustomobject]@{
    windows = $scored.Count
    burnable_cells = $n
    covered_possible = $covered
    late_miss = $late
    early_miss = $early
    coverage_pct = [math]::Round(100.0 * $covered / $n, 6)
    late_pct = [math]::Round(100.0 * $late / $n, 6)
    early_pct = [math]::Round(100.0 * $early / $n, 6)
} | Format-List

$sweep = Get-Content -Raw -LiteralPath 'scratch_review/gust_sweep.json' | ConvertFrom-Json
$blend = $sweep | Where-Object { $_.blend -eq 0.5 }
[pscustomobject]@{
    artifact = 'scratch_review/gust_sweep.json'
    blend = $blend.blend
    burnable_cells = $blend.n
    covered_cells_derived = [math]::Round($blend.n * $blend.cover / 100.0)
    late_cells_derived = [math]::Round($blend.n * $blend.late / 100.0)
    coverage_pct = $blend.cover
    late_pct = $blend.late
    early_pct = $blend.early
    median_head_ratio = $blend.head
} | Format-List
