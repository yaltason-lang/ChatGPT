$ErrorActionPreference = 'Stop'

$Root = 'C:\Riverwood_Operations_MVP0_Core_Employees'
$Out = Join-Path $PSScriptRoot 'CAPTURED_LIVE_8085_WRITER'
$Report = Join-Path $Out 'CAPTURE_REPORT.txt'
$Target = Join-Path $Out 'LIVE_8085_WRITER.py'

if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out -Force | Out-Null

function Add-Report([string]$Text) {
    $Text | Tee-Object -FilePath $Report -Append | Write-Host
}

function Is-Historical([string]$Path) {
    $p = $Path.ToLowerInvariant().Replace('/', '\')
    return ($p -like '*\all versions\*' -or
            $p -like '*\_backups\*' -or
            $p -like '*\backup\*' -or
            $p -like '*\backups\*' -or
            $p -like '*\before_*' -or
            $p -like '*\baseline*\*' -or
            $p -like '*\.git\*' -or
            $p -like '*\dist\*' -or
            $p -like '*\payload\*')
}

Add-Report 'Riverwood LIVE :8085 writer capture'
Add-Report ('Captured at: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Add-Report 'READ-ONLY: this tool does not stop/start/restart/modify any process or source file.'
Add-Report ''

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8085 -ErrorAction SilentlyContinue)
if ($listeners.Count -eq 0) {
    Add-Report 'FAILED: no LISTEN process found on TCP :8085.'
    exit 2
}
$pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
if ($pids.Count -ne 1) {
    Add-Report ('FAILED: expected exactly one owning PID for :8085, got: ' + ($pids -join ', '))
    exit 3
}
$writerPid = [int]$pids[0]
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$writerPid"
if (-not $proc) {
    Add-Report ('FAILED: cannot read Win32_Process for PID ' + $writerPid)
    exit 4
}
Add-Report ('PID: ' + $writerPid)
Add-Report ('Name: ' + $proc.Name)
Add-Report ('ExecutablePath: ' + $proc.ExecutablePath)
Add-Report ('CommandLine: ' + $proc.CommandLine)
Add-Report ''

$candidates = New-Object System.Collections.Generic.List[string]

# First: explicit .py paths from the live process command line.
$cmd = [string]$proc.CommandLine
$matches = [regex]::Matches($cmd, '(?i)(?:"([^"]+\.py)"|([^\s"]+\.py))')
foreach ($m in $matches) {
    $raw = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
    if ([string]::IsNullOrWhiteSpace($raw)) { continue }
    $tries = @($raw)
    if (-not [System.IO.Path]::IsPathRooted($raw)) {
        $tries += (Join-Path $Root $raw)
    }
    foreach ($t in $tries) {
        try {
            $resolved = (Resolve-Path $t -ErrorAction Stop).Path
            if ((Test-Path $resolved -PathType Leaf) -and -not (Is-Historical $resolved)) {
                if (-not $candidates.Contains($resolved)) { $candidates.Add($resolved) }
            }
        } catch {}
    }
}

# Second: exact live implementation markers. We deliberately ignore archives/backups.
if (Test-Path $Root) {
    $pyFiles = Get-ChildItem -Path $Root -Filter '*.py' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { -not (Is-Historical $_.FullName) }
    foreach ($f in $pyFiles) {
        try {
            $text = [System.IO.File]::ReadAllText($f.FullName)
            $markerScore = 0
            if ($text.Contains('prepare_group_header_and_cards')) { $markerScore += 4 }
            if ($text.Contains('HMS_GUEST_SLOT_COUNT_MISMATCH')) { $markerScore += 4 }
            if ($text.Contains('ReserveGroupThirdStep')) { $markerScore += 1 }
            if ($text.Contains('ReserveGroupSecondStep')) { $markerScore += 1 }
            if ($text.Contains('ReserveGroupFirstStep')) { $markerScore += 1 }
            if ($markerScore -ge 8 -and -not $candidates.Contains($f.FullName)) {
                $candidates.Add($f.FullName)
            }
        } catch {}
    }
}

if ($candidates.Count -eq 0) {
    Add-Report 'FAILED: no non-archive Python source matched the live :8085 process / writer error markers.'
    exit 5
}

Add-Report 'Candidate source files:'
$ranked = @()
foreach ($path in $candidates) {
    $score = 0
    $text = ''
    try { $text = [System.IO.File]::ReadAllText($path) } catch {}
    if ($text.Contains('prepare_group_header_and_cards')) { $score += 100 }
    if ($text.Contains('HMS_GUEST_SLOT_COUNT_MISMATCH')) { $score += 100 }
    if ($text.Contains('stay {')) { $score += 10 }
    if ($text.Contains('ReserveGroupThirdStep')) { $score += 5 }
    if ($cmd -and $cmd.Contains($path)) { $score += 1000 }
    $ranked += [pscustomobject]@{ Score=$score; Path=$path }
    Add-Report ('  score=' + $score + '  ' + $path)
}
# Windows PowerShell 5.1 compatible multi-key sort.
$ranked = @($ranked | Sort-Object -Property @{Expression={$_.Score}; Descending=$true}, @{Expression={$_.Path}; Descending=$false})
$topScore = $ranked[0].Score
$top = @($ranked | Where-Object { $_.Score -eq $topScore })
if ($top.Count -ne 1) {
    Add-Report ('FAILED: ambiguous live writer source at top score ' + $topScore + '. Nothing copied as authoritative.')
    exit 6
}

$source = [string]$top[0].Path
Copy-Item -LiteralPath $source -Destination $Target -Force
$hash = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant()
Add-Report ''
Add-Report ('SELECTED LIVE SOURCE: ' + $source)
Add-Report ('COPY: ' + $Target)
Add-Report ('SHA256: ' + $hash)

# Include exact marker excerpts with line numbers, useful even before source review.
$lines = [System.IO.File]::ReadAllLines($source)
$needles = @('prepare_group_header_and_cards','HMS_GUEST_SLOT_COUNT_MISMATCH','ReserveGroupFirstStep','ReserveGroupSecondStep','ReserveGroupThirdStep')
foreach ($needle in $needles) {
    for ($i=0; $i -lt $lines.Length; $i++) {
        if ($lines[$i].Contains($needle)) {
            Add-Report (('MARKER {0} line {1}: {2}' -f $needle, ($i+1), $lines[$i].Trim()))
            break
        }
    }
}

Add-Report ''
Add-Report 'CAPTURE OK. Attach LIVE_8085_WRITER.py to ChatGPT or upload it to the GitHub repository.'
exit 0
