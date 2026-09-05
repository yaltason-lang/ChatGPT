$ErrorActionPreference = 'Stop'

$Root = 'C:\riverwood_revenue_bot'
$Writer = Join-Path $Root 'pms_booking_adapter_v5328.py'
$Launcher = Join-Path $Root 'START_DASHBOARD.bat'
$ExpectedSha = '23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac'
$RequiredMarker = 'RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE'
$LogDir = Join-Path $Root '_writer_recovery_logs'
$ResultFile = Join-Path $LogDir 'LAST_RECOVERY_RESULT.txt'

function Get-Listener8085 {
    Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Write-Result([string[]]$Lines) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $Lines | Set-Content -LiteralPath $ResultFile -Encoding UTF8
    foreach ($line in $Lines) { Write-Host $line }
    Write-Host ('Result file: ' + $ResultFile)
}

Write-Host 'Riverwood HMS Writer :8085 EXACT native recovery V2'
Write-Host 'This does not patch Operations or writer source.'
Write-Host ('Native launcher proven by live capture: ' + $Launcher)

if (-not (Test-Path -LiteralPath $Writer -PathType Leaf)) {
    Write-Result @('RECOVERY FAILED','Writer source missing: ' + $Writer)
    exit 2
}
$sha = (Get-FileHash -LiteralPath $Writer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sha -ne $ExpectedSha) {
    Write-Result @('RECOVERY FAILED','Writer SHA mismatch.','Expected: ' + $ExpectedSha,'Actual:   ' + $sha)
    exit 3
}
$text = [IO.File]::ReadAllText($Writer)
if ($text.IndexOf($RequiredMarker, [StringComparison]::Ordinal) -lt 0) {
    Write-Result @('RECOVERY FAILED','Verified v10 card-budget marker is missing.')
    exit 4
}
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    Write-Result @('RECOVERY FAILED','Native launcher missing: ' + $Launcher)
    exit 5
}

$existing = Get-Listener8085
if ($existing) {
    $pid8085 = [int]$existing.OwningProcess
    $proc = Get-Process -Id $pid8085 -ErrorAction SilentlyContinue
    $name = if ($proc) { $proc.ProcessName } else { 'unknown' }
    try {
        $tcp = New-Object Net.Sockets.TcpClient
        $tcp.Connect('127.0.0.1',8085)
        $tcp.Close()
    } catch {
        Write-Result @('RECOVERY FAILED',('Port 8085 has LISTEN PID ' + $pid8085 + ' but TCP connect failed: ' + $_.Exception.Message))
        exit 6
    }
    Write-Result @('RECOVERY OK',('8085 already LISTEN. PID=' + $pid8085 + ' process=' + $name),('Writer SHA OK: ' + $sha))
    exit 0
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$outLog = Join-Path $LogDir ('START_DASHBOARD_' + $stamp + '.out.txt')
$errLog = Join-Path $LogDir ('START_DASHBOARD_' + $stamp + '.err.txt')
$startedAt = Get-Date

Write-Host '8085 is DOWN. Starting the exact native launcher from the prior working runtime...'
Write-Host ('Launcher stdout: ' + $outLog)
Write-Host ('Launcher stderr: ' + $errLog)

$procLaunch = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/d','/c',('call ' + $Launcher)) -WorkingDirectory $Root -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host ('Native launcher PID: ' + $procLaunch.Id)

$deadline = (Get-Date).AddSeconds(60)
$listener = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $listener = Get-Listener8085
    if ($listener) { break }
    if ($procLaunch.HasExited) {
        Start-Sleep -Milliseconds 300
        break
    }
}

if (-not $listener) {
    $stdout = ''
    $stderr = ''
    if (Test-Path -LiteralPath $outLog) { $stdout = (Get-Content -LiteralPath $outLog -Raw -ErrorAction SilentlyContinue) }
    if (Test-Path -LiteralPath $errLog) { $stderr = (Get-Content -LiteralPath $errLog -Raw -ErrorAction SilentlyContinue) }
    if ([string]::IsNullOrWhiteSpace($stdout)) { $stdout = '<empty>' }
    if ([string]::IsNullOrWhiteSpace($stderr)) { $stderr = '<empty>' }
    Write-Result @(
        'RECOVERY FAILED',
        'Exact native START_DASHBOARD.bat did not restore 8085.',
        ('Launcher PID: ' + $procLaunch.Id),
        ('Launcher exited: ' + $procLaunch.HasExited),
        '--- STDOUT ---',
        $stdout,
        '--- STDERR ---',
        $stderr
    )
    exit 7
}

$pid8085 = [int]$listener.OwningProcess
$writerProc = Get-Process -Id $pid8085 -ErrorAction Stop
if ($writerProc.ProcessName -notlike 'python*') {
    Write-Result @('RECOVERY FAILED',('8085 LISTEN is not Python. PID=' + $pid8085 + ' process=' + $writerProc.ProcessName))
    exit 8
}
if ($writerProc.StartTime -lt $startedAt.AddSeconds(-2)) {
    Write-Result @('RECOVERY FAILED',('8085 listener is stale. PID=' + $pid8085 + ' start=' + $writerProc.StartTime.ToString('s')))
    exit 9
}

try {
    $tcp = New-Object Net.Sockets.TcpClient
    $tcp.Connect('127.0.0.1',8085)
    $tcp.Close()
} catch {
    Write-Result @('RECOVERY FAILED',('8085 LISTEN appeared but TCP connect failed: ' + $_.Exception.Message))
    exit 10
}

$finalSha = (Get-FileHash -LiteralPath $Writer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($finalSha -ne $ExpectedSha) {
    Write-Result @('RECOVERY FAILED','Writer source changed during native launch.','Expected: ' + $ExpectedSha,'Actual:   ' + $finalSha)
    exit 11
}

Write-Result @(
    'RECOVERY OK',
    ('8085 LISTEN PID=' + $pid8085 + ' process=' + $writerProc.ProcessName),
    ('Started: ' + $writerProc.StartTime.ToString('s')),
    ('Writer SHA OK: ' + $finalSha),
    ('Native launcher: ' + $Launcher),
    'Next: restart only Operations 1_START_DASHBOARD.cmd, then click Update live preflight.'
)
exit 0
