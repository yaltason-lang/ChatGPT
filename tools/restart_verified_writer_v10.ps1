$ErrorActionPreference = 'Stop'
$Writer = 'C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py'
$ExpectedSha = '23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac'
$RequiredMarker = 'RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE'

Write-Host 'Riverwood HMS Writer v10 - verified runtime restart'
Write-Host ('Writer: ' + $Writer)

if (-not (Test-Path -LiteralPath $Writer -PathType Leaf)) {
    throw ('Writer source not found: ' + $Writer)
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
    throw 'No LISTEN process found on :8085. Refusing to guess a startup command.'
}
$Pid8085 = [int]$Listener.OwningProcess
$Cim = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $Pid8085)
if (-not $Cim) { throw ('Cannot inspect PID ' + $Pid8085) }
$Exe = [string]$Cim.ExecutablePath
$Cmd = [string]$Cim.CommandLine
if ([string]::IsNullOrWhiteSpace($Exe) -or [string]::IsNullOrWhiteSpace($Cmd)) {
    throw ('Cannot resolve executable/command line for PID ' + $Pid8085)
}
if ($Cmd.IndexOf('pms_booking_adapter_v5328.py', [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
    throw ('PID ' + $Pid8085 + ' on :8085 is not the expected Riverwood writer. CommandLine=' + $Cmd)
}

$QuotedExe = '"' + $Exe + '"'
if ($Cmd.StartsWith($QuotedExe, [System.StringComparison]::OrdinalIgnoreCase)) {
    $ArgsText = $Cmd.Substring($QuotedExe.Length).Trim()
} elseif ($Cmd.StartsWith($Exe, [System.StringComparison]::OrdinalIgnoreCase)) {
    $ArgsText = $Cmd.Substring($Exe.Length).Trim()
} else {
    throw ('Cannot safely split writer command line: ' + $Cmd)
}
if ([string]::IsNullOrWhiteSpace($ArgsText)) {
    throw ('Writer command line has no script arguments: ' + $Cmd)
}

$OldStart = (Get-Process -Id $Pid8085).StartTime
Write-Host ('Stopping verified writer PID ' + $Pid8085 + ' started ' + $OldStart.ToString('s'))
Stop-Process -Id $Pid8085 -Force
Start-Sleep -Milliseconds 800

$WorkDir = Split-Path -Parent $Writer
Write-Host ('Starting: ' + $Exe + ' ' + $ArgsText)
$NewProcess = Start-Process -FilePath $Exe -ArgumentList $ArgsText -WorkingDirectory $WorkDir -PassThru

$Deadline = (Get-Date).AddSeconds(20)
$NewListener = $null
while ((Get-Date) -lt $Deadline) {
    Start-Sleep -Milliseconds 500
    $NewListener = Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($NewListener) { break }
}
if (-not $NewListener) {
    throw ('Writer restart failed: no LISTEN on :8085 after 20s. Started PID=' + $NewProcess.Id)
}
$NewPid = [int]$NewListener.OwningProcess
$NewStart = (Get-Process -Id $NewPid).StartTime
$SourceWrite = (Get-Item -LiteralPath $Writer).LastWriteTime
if ($NewStart -lt $SourceWrite.AddSeconds(-2)) {
    throw ('Writer listener is still stale after restart. PID=' + $NewPid)
}
Write-Host ('RESTART OK: :8085 PID ' + $NewPid + ' started ' + $NewStart.ToString('s'))
Write-Host 'Writer v10 source + runtime are now aligned.'
