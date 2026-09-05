$ErrorActionPreference = 'Stop'

$OpsModule = 'C:\Riverwood_Operations_MVP0_Core_Employees\accommodation_module.py'
$OpsPython = 'C:\Riverwood_Operations_MVP0_Core_Employees\.venv\Scripts\python.exe'
$GuardMarker = 'RIVERWOOD_HMS_WRITER_V10_RUNTIME_PREFLIGHT_GUARD_V1'
$PatchScript = Join-Path $PSScriptRoot 'patch_operations_writer_runtime_guard_v1.py'
$RestartScript = Join-Path $PSScriptRoot 'restart_verified_writer_v10_no_cim.ps1'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host 'Riverwood HMS Writer v10 - Runtime Preflight Guard V1.2 SELF-ELEVATING'
Write-Host ''

if (-not (Test-IsAdministrator)) {
    Write-Host 'Administrator rights are required to restart the existing :8085 writer process.'
    Write-Host 'Opening one UAC prompt now...'
    $quotedScript = '"' + $PSCommandPath + '"'
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $quotedScript)
    try {
        $elevated = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -Verb RunAs -Wait -PassThru
    } catch {
        throw ('Administrator elevation was cancelled or failed: ' + $_.Exception.Message)
    }
    if ($elevated.ExitCode -ne 0) {
        throw ('Elevated installer failed with exit code ' + $elevated.ExitCode)
    }
    Write-Host 'Elevated installer completed successfully.'
    exit 0
}

Write-Host 'ADMIN RIGHTS: OK'

if (-not (Test-Path -LiteralPath $OpsModule -PathType Leaf)) {
    throw ('Operations module not found: ' + $OpsModule)
}
if (-not (Test-Path -LiteralPath $RestartScript -PathType Leaf)) {
    throw ('Restart script not found: ' + $RestartScript)
}

$opsText = [System.IO.File]::ReadAllText($OpsModule)
if ($opsText.IndexOf($GuardMarker, [System.StringComparison]::Ordinal) -ge 0) {
    Write-Host 'Operations preflight guard: ALREADY INSTALLED - no rewrite needed.'
} else {
    if (-not (Test-Path -LiteralPath $PatchScript -PathType Leaf)) {
        throw ('Operations guard patch script not found: ' + $PatchScript)
    }
    if (-not (Test-Path -LiteralPath $OpsPython -PathType Leaf)) {
        throw ('Operations Python not found: ' + $OpsPython)
    }
    Write-Host 'Installing Operations preflight guard...'
    & $OpsPython $PatchScript
    if ($LASTEXITCODE -ne 0) {
        throw ('Operations guard patch failed with exit code ' + $LASTEXITCODE)
    }
    $opsText = [System.IO.File]::ReadAllText($OpsModule)
    if ($opsText.IndexOf($GuardMarker, [System.StringComparison]::Ordinal) -lt 0) {
        throw 'Operations guard marker is still missing after patch.'
    }
    Write-Host 'Operations preflight guard: INSTALLED.'
}

Write-Host ''
Write-Host 'Restarting ONLY verified writer :8085 with administrator rights...'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RestartScript
if ($LASTEXITCODE -ne 0) {
    throw ('Verified writer restart failed with exit code ' + $LASTEXITCODE)
}

Write-Host ''
Write-Host 'INSTALL OK'
Write-Host 'Writer :8085 is now restarted from the verified v10 source.'
Write-Host 'Operations guard is installed/verified.'
Write-Host ':8082 was not touched.'
Write-Host ''
Write-Host 'NEXT: restart ONLY Operations using:'
Write-Host 'C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd'
Write-Host 'Then open the quote and click Update live preflight BEFORE booking.'
