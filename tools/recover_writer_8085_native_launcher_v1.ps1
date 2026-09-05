$ErrorActionPreference = 'Stop'

$Root = 'C:\riverwood_revenue_bot'
$Writer = Join-Path $Root 'pms_booking_adapter_v5328.py'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$ExpectedSha = '23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac'
$RequiredMarker = 'RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE'
$Self = $MyInvocation.MyCommand.Path

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host 'Requesting Administrator rights for writer recovery...'
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $Self + '"'))
    exit 0
}

function Get-Listener {
    return Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Wait-Listener([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        $l = Get-Listener
        if ($l) { return $l }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Show-TextFile([string]$Path, [string]$Label) {
    Write-Host ('--- ' + $Label + ' ---')
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $txt = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
        if ([string]::IsNullOrWhiteSpace($txt)) { Write-Host '<empty>' } else { Write-Host $txt }
    } else {
        Write-Host '<missing>'
    }
}

Write-Host 'Riverwood HMS Writer :8085 native-launcher recovery V1'
Write-Host 'NO Operations patch. Other services are not touched.'
Write-Host ('Writer: ' + $Writer)

if (-not (Test-Path -LiteralPath $Writer -PathType Leaf)) { throw ('Writer not found: ' + $Writer) }
$sha = (Get-FileHash -LiteralPath $Writer -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host ('Writer SHA256: ' + $sha)
if ($sha -ne $ExpectedSha) { throw ('Refusing recovery: writer SHA mismatch. Expected ' + $ExpectedSha + ', actual ' + $sha) }
$text = [IO.File]::ReadAllText($Writer)
if ($text.IndexOf($RequiredMarker, [StringComparison]::Ordinal) -lt 0) { throw 'Refusing recovery: v10 card-budget marker missing.' }

$existing = Get-Listener
if ($existing) {
    $pid8085 = [int]$existing.OwningProcess
    $proc = Get-Process -Id $pid8085 -ErrorAction SilentlyContinue
    if ($proc) { $procName = $proc.ProcessName } else { $procName = 'unknown' }
    Write-Host ('RECOVERY NOT NEEDED: :8085 already LISTEN PID=' + $pid8085 + ' process=' + $procName)
    exit 0
}

Write-Host 'Current state: :8085 is DOWN. Searching native launchers...'
$skipRegex = '(?i)\\Old VERSIONS\\|\\All versions\\|\\_backups\\|\\backup\\|\\archive\\|\\__pycache__\\|\\.git\\'
$launchers = @()
$files = Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
    $_.Extension -in @('.bat','.cmd','.ps1') -and $_.FullName -notmatch $skipRegex
}
foreach ($f in $files) {
    try { $c = Get-Content -LiteralPath $f.FullName -Raw -ErrorAction Stop } catch { continue }
    if ($c.IndexOf('pms_booking_adapter_v5328.py', [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $score = 100
        if ($c.IndexOf('8085', [StringComparison]::OrdinalIgnoreCase) -ge 0) { $score += 20 }
        if ($c.IndexOf('HMS_BOOKING', [StringComparison]::OrdinalIgnoreCase) -ge 0) { $score += 10 }
        $launchers += [PSCustomObject]@{ Path=$f.FullName; Extension=$f.Extension.ToLowerInvariant(); Score=$score }
    }
}
$launchers = @($launchers | Sort-Object -Property @{Expression='Score';Descending=$true}, @{Expression='Path';Descending=$false})

if ($launchers.Count -gt 0) {
    Write-Host ('Exact writer launcher candidates found: ' + $launchers.Count)
    foreach ($x in $launchers) { Write-Host ('  score=' + $x.Score + '  ' + $x.Path) }
}

if ($launchers.Count -eq 1) {
    $candidate = $launchers[0]
    $logDir = Join-Path $Root '_writer_recovery_logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $outLog = Join-Path $logDir ('native_' + $stamp + '.out.txt')
    $errLog = Join-Path $logDir ('native_' + $stamp + '.err.txt')
    Write-Host ('Launching UNIQUE native launcher: ' + $candidate.Path)
    if ($candidate.Extension -eq '.ps1') {
        $p = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $candidate.Path + '"')) -WorkingDirectory (Split-Path -Parent $candidate.Path) -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    } else {
        $p = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c',('"' + $candidate.Path + '"')) -WorkingDirectory (Split-Path -Parent $candidate.Path) -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    }
    Write-Host ('Native launcher PID: ' + $p.Id)
    $l = Wait-Listener 30
    if ($l) {
        $newPid = [int]$l.OwningProcess
        Write-Host ('RECOVERY OK: :8085 LISTEN PID=' + $newPid)
        Write-Host ('Native launcher used: ' + $candidate.Path)
        exit 0
    }
    Write-Host 'Native launcher did not restore :8085 within 30 seconds.'
    Show-TextFile $outLog 'native stdout'
    Show-TextFile $errLog 'native stderr'
    throw 'RECOVERY FAILED: unique native launcher did not restore :8085. Exact output is shown above.'
}

if ($launchers.Count -gt 1) {
    throw 'RECOVERY STOPPED SAFELY: more than one exact native launcher exists. Nothing was started. Send the candidate list above.'
}

Write-Host 'No exact native launcher file found. Running one diagnostic direct start with captured stdout/stderr.'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw ('Python not found: ' + $Python) }
$logDir = Join-Path $Root '_writer_recovery_logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$outLog = Join-Path $logDir ('direct_' + $stamp + '.out.txt')
$errLog = Join-Path $logDir ('direct_' + $stamp + '.err.txt')
$p = Start-Process -FilePath $Python -ArgumentList @('-u',$Writer) -WorkingDirectory $Root -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host ('Diagnostic launcher PID: ' + $p.Id)
$l = Wait-Listener 12
if ($l) {
    Write-Host ('RECOVERY OK: direct start produced :8085 LISTEN PID=' + [int]$l.OwningProcess)
    exit 0
}
Show-TextFile $outLog 'direct stdout'
Show-TextFile $errLog 'direct stderr'
throw 'RECOVERY DIAGNOSTIC COMPLETE: :8085 did not start. The exact startup error is printed above. Nothing else was modified.'
