$ErrorActionPreference = 'Stop'

$Out = Join-Path $PSScriptRoot 'CAPTURED_LIVE_8080_OPERATIONS'
$Report = Join-Path $Out 'CAPTURE_REPORT.txt'
$TargetModule = Join-Path $Out 'LIVE_8080_accommodation_module.py'
$TargetTemplate = Join-Path $Out 'LIVE_8080_accommodation_quote_detail.html'
$TargetEntry = Join-Path $Out 'LIVE_8080_ENTRYPOINT.py'

if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out -Force | Out-Null

function Add-Report([string]$Text) {
    $Text | Tee-Object -FilePath $Report -Append | Write-Host
}

function Is-Historical([string]$Path) {
    $p = $Path.ToLowerInvariant().Replace('/', '\')
    if ($p -like '*\all versions\*') { return $true }
    if ($p -like '*\old versions\*') { return $true }
    if ($p -like '*\_backups\*') { return $true }
    if ($p -like '*\backups\*') { return $true }
    if ($p -like '*\backup\*') { return $true }
    if ($p -like '*\before_*') { return $true }
    if ($p -like '*\baseline*\*') { return $true }
    if ($p -like '*\.git\*') { return $true }
    if ($p -like '*\dist\*') { return $true }
    if ($p -like '*\payload\*') { return $true }
    return $false
}

Add-Report 'Riverwood LIVE :8080 Operations capture'
Add-Report 'CAPTURE TOOL 8080 V1 - READ ONLY - PowerShell 5.1 compatible'
Add-Report ('Captured at: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Add-Report 'READ-ONLY: this tool does not stop/start/restart/modify any process or source file.'
Add-Report ''

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue)
if ($listeners.Count -eq 0) {
    Add-Report 'FAILED: no LISTEN process found on TCP :8080.'
    exit 2
}
$owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
if ($owners.Count -ne 1) {
    Add-Report ('FAILED: expected exactly one owning PID for :8080, got: ' + ($owners -join ', '))
    exit 3
}
$opsPid = [int]$owners[0]
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$opsPid"
if (-not $proc) {
    Add-Report ('FAILED: cannot read Win32_Process for PID ' + $opsPid)
    exit 4
}
Add-Report ('PID: ' + $opsPid)
Add-Report ('Name: ' + $proc.Name)
Add-Report ('ExecutablePath: ' + $proc.ExecutablePath)
Add-Report ('CommandLine: ' + $proc.CommandLine)
Add-Report ''

$cmd = [string]$proc.CommandLine
$entryCandidates = @()
$matches = [regex]::Matches($cmd, '(?i)(?:"([^"]+\.py)"|([^\s"]+\.py))')
foreach ($m in $matches) {
    if ($m.Groups[1].Success) { $raw = $m.Groups[1].Value } else { $raw = $m.Groups[2].Value }
    if ([string]::IsNullOrWhiteSpace($raw)) { continue }
    $tries = @($raw)
    if (-not [System.IO.Path]::IsPathRooted($raw)) {
        $tries += (Join-Path ((Get-Location).Path) $raw)
        $tries += (Join-Path 'C:\Riverwood_Operations_MVP0_Core_Employees' $raw)
        $tries += (Join-Path 'C:\riverwood_revenue_bot' $raw)
    }
    foreach ($t in $tries) {
        try {
            $resolved = (Resolve-Path $t -ErrorAction Stop).Path
            if ((Test-Path $resolved -PathType Leaf) -and -not (Is-Historical $resolved)) {
                if ($entryCandidates -notcontains $resolved) { $entryCandidates += $resolved }
            }
        } catch {}
    }
}

$entry = ''
if ($entryCandidates.Count -gt 0) {
    $entry = [string]$entryCandidates[0]
    Copy-Item -LiteralPath $entry -Destination $TargetEntry -Force
    Add-Report ('ENTRYPOINT CANDIDATE: ' + $entry)
    Add-Report ('ENTRYPOINT SHA256: ' + ((Get-FileHash -LiteralPath $TargetEntry -Algorithm SHA256).Hash.ToLowerInvariant()))
} else {
    Add-Report 'ENTRYPOINT CANDIDATE: none resolved from command line'
}
Add-Report ''

$roots = @()
if ($entry -ne '') {
    try {
        $entryDir = Split-Path -Parent $entry
        if ($entryDir -and $roots -notcontains $entryDir) { $roots += $entryDir }
        $entryParent = Split-Path -Parent $entryDir
        if ($entryParent -and $roots -notcontains $entryParent) { $roots += $entryParent }
    } catch {}
}
foreach ($known in @('C:\Riverwood_Operations_MVP0_Core_Employees','C:\riverwood_revenue_bot')) {
    if ((Test-Path $known) -and $roots -notcontains $known) { $roots += $known }
}

$pairs = @()
foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    $modules = @(Get-ChildItem -Path $root -Filter 'accommodation_module.py' -File -Recurse -ErrorAction SilentlyContinue | Where-Object { -not (Is-Historical $_.FullName) })
    foreach ($mf in $modules) {
        $module = $mf.FullName
        $dir = Split-Path -Parent $module
        $template = ''
        $t1 = Join-Path $dir 'templates\accommodation_quote_detail.html'
        $t2 = Join-Path $dir 'accommodation_quote_detail.html'
        if ((Test-Path $t1 -PathType Leaf) -and -not (Is-Historical $t1)) { $template = (Resolve-Path $t1).Path }
        elseif ((Test-Path $t2 -PathType Leaf) -and -not (Is-Historical $t2)) { $template = (Resolve-Path $t2).Path }
        if ($template -eq '') { continue }
        try { $text = [System.IO.File]::ReadAllText($module) } catch { continue }
        $score = 0
        if ($text.Contains('def _hms_booking_state')) { $score += 100 }
        if ($text.Contains('def _hms_booking_payload')) { $score += 100 }
        if ($text.Contains('hms_booking_preflight')) { $score += 30 }
        if ($text.Contains('HMS_COMPAT')) { $score += 20 }
        if ($entry -ne '') {
            $entryDir2 = Split-Path -Parent $entry
            if ($dir -eq $entryDir2) { $score += 1000 }
            if ($module.ToLowerInvariant().StartsWith($entryDir2.ToLowerInvariant())) { $score += 500 }
            if ($cmd.ToLowerInvariant().Contains($dir.ToLowerInvariant())) { $score += 300 }
        }
        $pairs += [pscustomobject]@{ Score=$score; Module=$module; Template=$template }
    }
}

if ($pairs.Count -eq 0) {
    Add-Report 'FAILED: no non-archive accommodation_module.py + template pair found near live :8080 process.'
    exit 5
}

$dedup = @()
$groups = @($pairs | Group-Object Module)
foreach ($g in $groups) {
    $best = @($g.Group | Sort-Object Score -Descending)[0]
    $dedup += $best
}
$ranked = @($dedup | Sort-Object Module | Sort-Object Score -Descending)
Add-Report 'Candidate Operations pairs:'
foreach ($item in $ranked) {
    Add-Report ('  score=' + $item.Score + ' module=' + $item.Module)
    Add-Report ('             template=' + $item.Template)
}

$topScore = $ranked[0].Score
$top = @($ranked | Where-Object { $_.Score -eq $topScore })
if ($top.Count -ne 1) {
    Add-Report ('FAILED: ambiguous live Operations source at top score ' + $topScore + '. Nothing copied as authoritative.')
    foreach ($item in $top) { Add-Report ('  tied: ' + $item.Module) }
    exit 6
}

$selected = $top[0]
Copy-Item -LiteralPath $selected.Module -Destination $TargetModule -Force
Copy-Item -LiteralPath $selected.Template -Destination $TargetTemplate -Force
$moduleHash = (Get-FileHash -LiteralPath $TargetModule -Algorithm SHA256).Hash.ToLowerInvariant()
$templateHash = (Get-FileHash -LiteralPath $TargetTemplate -Algorithm SHA256).Hash.ToLowerInvariant()
$text = [System.IO.File]::ReadAllText($selected.Module)
$stateCount = ([regex]::Matches($text, '(?m)^def\s+_hms_booking_state\s*\(')).Count
$payloadCount = ([regex]::Matches($text, '(?m)^def\s+_hms_booking_payload\s*\(')).Count
$obsoleteText = 'Автоматичний запис раннього заїзду/пізнього виїзду в HMS ще не зіставлений з полями GroupCard.'
$obsoleteCount = ([regex]::Matches($text, [regex]::Escape($obsoleteText))).Count

Add-Report ''
Add-Report ('SELECTED LIVE MODULE  : ' + $selected.Module)
Add-Report ('SELECTED LIVE TEMPLATE: ' + $selected.Template)
Add-Report ('MODULE SHA256  : ' + $moduleHash)
Add-Report ('TEMPLATE SHA256: ' + $templateHash)
Add-Report ('_hms_booking_state definitions : ' + $stateCount)
Add-Report ('_hms_booking_payload definitions: ' + $payloadCount)
Add-Report ('obsolete early/late blocker copies: ' + $obsoleteCount)
Add-Report ''
Add-Report 'CAPTURE OK. Attach CAPTURE_REPORT.txt and LIVE_8080_accommodation_module.py to ChatGPT.'
exit 0
