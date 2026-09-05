$ErrorActionPreference = 'Stop'

$Writer = 'C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py'
$Python = 'C:\riverwood_revenue_bot\.venv\Scripts\python.exe'
$ExpectedSha = '23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac'
$RequiredMarker = 'RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE'

Write-Host 'Riverwood HMS Writer v10 - verified restart without CIM CommandLine'
Write-Host ('Writer: ' + $Writer)
Write-Host ('Python: ' + $Python)

if (-not (Test-Path -LiteralPath $Writer -PathType Leaf)) {
    throw ('Writer source not found: ' + $Writer)
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw ('Riverwood revenue-bot Python not found: ' + $Python)
}

$ActualSha = (Get-FileHash -LiteralPath $Writer -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host ('Writer SHA256: ' + $ActualSha)
if ($ActualSha -ne $ExpectedSha) {
    throw ('Writer source is NOT the verified v10 build. Expected ' + $ExpectedSha + ', actual ' + $ActualSha + '. Nothing was stopped.')
}
$Text = [System.IO.File]::ReadAllText($Writer)
if ($Text.IndexOf($RequiredMarker, [System.StringComparison]::Ordinal) -lt 0) {
    throw 'Verified v10 technical card-budget marker is missing. Nothing was stopped.'
}

$Listener = Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Listener) {
    throw 'No LISTEN process found on :8085. Nothing was stopped.'
}
$OldPid = [int]$Listener.OwningProcess
$OldProc = Get-Process -Id $OldPid -ErrorAction Stop
if ($OldProc.ProcessName -notlike 'python*') {
    throw ('Refusing to stop :8085 because PID ' + $OldPid + ' is not Python. Process=' + $OldProc.ProcessName)
}
$OldStart = $OldProc.StartTime
Write-Host ('Verified :8085 listener PID ' + $OldPid + ' process=' + $OldProc.ProcessName + ' started ' + $OldStart.ToString('s'))
Write-Host 'CIM CommandLine is intentionally NOT required.'

$RestartAt = Get-Date
Write-Host ('Stopping :8085 PID ' + $OldPid)
Stop-Process -Id $OldPid -Force -ErrorAction Stop

$DeadlineStop = (Get-Date).AddSeconds(10)
do {
    Start-Sleep -Milliseconds 250
    $Still = Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
} while ($Still -and (Get-Date) -lt $DeadlineStop)
if ($Still) {
    throw ('Old :8085 listener did not stop. PID=' + $Still.OwningProcess)
}

$WorkDir = Split-Path -Parent $Writer
Write-Host ('Starting verified v10 directly: ' + $Python + ' -u ' + $Writer)
$Started = Start-Process -FilePath $Python -ArgumentList @('-u', $Writer) -WorkingDirectory $WorkDir -PassThru
Write-Host ('Launcher PID: ' + $Started.Id)

$Deadline = (Get-Date).AddSeconds(25)
$NewListener = $null
while ((Get-Date) -lt $Deadline) {
    Start-Sleep -Milliseconds 500
    $NewListener = Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($NewListener) { break }
}
if (-not $NewListener) {
    throw ('Writer restart failed: no LISTEN on :8085 after 25s. Launcher PID=' + $Started.Id)
}

$NewPid = [int]$NewListener.OwningProcess
if ($NewPid -eq $OldPid) {
    throw ('Writer restart did not produce a new listener PID. PID=' + $NewPid)
}
$NewProc = Get-Process -Id $NewPid -ErrorAction Stop
if ($NewProc.ProcessName -notlike 'python*') {
    throw ('New :8085 listener is not Python. PID=' + $NewPid + ' process=' + $NewProc.ProcessName)
}
$NewStart = $NewProc.StartTime
if ($NewStart -lt $RestartAt.AddSeconds(-2)) {
    throw ('New :8085 listener start time is stale. PID=' + $NewPid + ' start=' + $NewStart.ToString('s'))
}

# Verify the exact source has not changed during restart.
$FinalSha = (Get-FileHash -LiteralPath $Writer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($FinalSha -ne $ExpectedSha) {
    throw ('Writer source changed during restart. Expected ' + $ExpectedSha + ', actual ' + $FinalSha)
}

Write-Host ('RESTART OK: :8085 PID ' + $NewPid + ' started ' + $NewStart.ToString('s'))
Write-Host ('WRITER SHA OK: ' + $FinalSha)
Write-Host 'Writer v10 source and runtime are aligned.'
