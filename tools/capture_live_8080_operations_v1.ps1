$ErrorActionPreference = 'Stop'

$Out = Join-Path $PSScriptRoot 'CAPTURED_LIVE_8080_OPERATIONS'
$Report = Join-Path $Out 'CAPTURE_REPORT.txt'
$Target = Join-Path $Out 'LIVE_8080_accommodation_module.py'
$TargetTemplate = Join-Path $Out 'LIVE_8080_accommodation_quote_detail.html'

if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out -Force | Out-Null

function Add-Report([string]$Text) {
    $Text | Tee-Object -FilePath $Report -Append | Write-Host
}

function Is-Historical([string]$Path) {
    $p = $Path.ToLowerInvariant().Replace('/', '\')
    return ($p -like '*\all versions\*' -or
            $p -like '*\old versions\*' -or
            $p -like '*\_backups\*' -or
            $p -like '*\backup\*' -or
            $p -like '*\backups\*' -or
            $p -like '*\before_*' -or
            $p -like '*\baseline*\*' -or
            $p -like '*\.git\*' -or
            $p -like '*\dist\*' -or
            $p -like '*\payload\*')
}

Add-Report 'Riverwood LIVE :8080 Operations capture'
Add-Report 'CAPTURE TOOL 8080 V1 ASCII PS51'
Add-Report ('Captured at: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Add-Report 'READ ONLY: no process or source file will be modified.'
Add-Report ''

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue)
if ($listeners.Count -eq 0) {
    Add-Report 'FAILED: no LISTEN process found on TCP :8080.'
    exit 2
}
$pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
if ($pids.Count -ne 1) {
    Add-Report ('FAILED: expected one owning PID for :8080, got: ' + ($pids -join ', '))
    exit 3
}
$opsPid = [int]$pids[0]
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

$candidates = New-Object System.Collections.Generic.List[string]
$cmd = [string]$proc.CommandLine
$matches = [regex]::Matches($cmd, '(?i)(?:"([^"]+\.py)"|([^\s"]+\.py))')
foreach ($m in $matches) {
    $raw = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
    if ([string]::IsNullOrWhiteSpace($raw)) { continue }
    $tries = @($raw)
    if (-not [System.IO.Path]::IsPathRooted($raw)) {
        $tries += (Join-Path (Get-Location).Path $raw)
        $tries += (Join-Path 'C:\Riverwood_Operations_MVP0_Core_Employees' $raw)
        $tries += (Join-Path 'C:\riverwood_revenue_bot' $raw)
    }
    foreach ($t in $tries) {
        try {
            $resolved = (Resolve-Path $t -ErrorAction Stop).Path
            if ((Test-Path $resolved -PathType Leaf) -and -not (Is-Historical $resolved)) {
                $dir = Split-Path -Parent $resolved
                $near = Join-Path $dir 'accommodation_module.py'
                if ((Test-Path $near -PathType Leaf) -and -not (Is-Historical $near)) {
                    $near = (Resolve-Path $near).Path
                    if (-not $candidates.Contains($near)) { $candidates.Add($near) }
                }
            }
        } catch {}
    }
}

$roots = @('C:\Riverwood_Operations_MVP0_Core_Employees', 'C:\riverwood_revenue_bot')
foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    $files = Get-ChildItem -Path $root -Filter 'accommodation_module.py' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { -not (Is-Historical $_.FullName) }
    foreach ($f in $files) {
        try {
            $text = [System.IO.File]::ReadAllText($f.FullName)
            $markerScore = 0
            if ($text.Contains('def _hms_booking_state')) { $markerScore += 4 }
            if ($text.Contains('def _hms_booking_payload')) { $markerScore += 4 }
            if ($text.Contains('hms_booking_preflight')) { $markerScore += 1 }
            if ($markerScore -ge 8 -and -not $candidates.Contains($f.FullName)) {
                $candidates.Add($f.FullName)
            }
        } catch {}
    }
}

if ($candidates.Count -eq 0) {
    Add-Report 'FAILED: no live accommodation_module.py candidate found.'
    exit 5
}

Add-Report 'Candidate source files:'
$ranked = @()
foreach ($path in $candidates) {
    $score = 0
    $text = ''
    try { $text = [System.IO.File]::ReadAllText($path) } catch {}
    if ($text.Contains('def _hms_booking_state')) { $score += 100 }
    if ($text.Contains('def _hms_booking_payload')) { $score += 100 }
    if ($text.Contains('hms_booking_preflight')) { $score += 10 }
    if ($text.Contains('HMS_COMPAT')) { $score += 10 }
    $parent = Split-Path -Parent $path
    if ($cmd -and $cmd.ToLowerInvariant().Contains($parent.ToLowerInvariant())) { $score += 1000 }
    $ranked += [pscustomobject]@{ Score=$score; Path=$path }
    Add-Report ('  score=' + $score + '  ' + $path)
}

$ranked = @($ranked | Sort-Object Path | Sort-Object Score -Descending)
$topScore = $ranked[0].Score
$top = @($ranked | Where-Object { $_.Score -eq $topScore })
if ($top.Count -ne 1) {
    Add-Report ('FAILED: ambiguous live Operations source at top score ' + $topScore)
    foreach ($item in $top) { Add-Report ('  tied: ' + $item.Path) }
    exit 6
}

$source = [string]$top[0].Path
$sourceDir = Split-Path -Parent $source
$template = Join-Path $sourceDir 'templates\accommodation_quote_detail.html'
if (-not (Test-Path $template -PathType Leaf)) {
    Add-Report ('FAILED: template missing beside selected module: ' + $template)
    exit 7
}

Copy-Item -LiteralPath $source -Destination $Target -Force
Copy-Item -LiteralPath $template -Destination $TargetTemplate -Force
$hash = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant()
$templateHash = (Get-FileHash -LiteralPath $TargetTemplate -Algorithm SHA256).Hash.ToLowerInvariant()

Add-Report ''
Add-Report ('SELECTED LIVE SOURCE: ' + $source)
Add-Report ('SELECTED TEMPLATE: ' + $template)
Add-Report ('MODULE SHA256: ' + $hash)
Add-Report ('TEMPLATE SHA256: ' + $templateHash)
Add-Report ''
Add-Report 'CAPTURE OK. Attach CAPTURE_REPORT.txt and LIVE_8080_accommodation_module.py to ChatGPT.'
exit 0
