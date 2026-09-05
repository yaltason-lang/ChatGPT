$ErrorActionPreference = 'Stop'

$Root = 'C:\riverwood_revenue_bot'
$Writer = Join-Path $Root 'pms_booking_adapter_v5328.py'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$PackageWrapper = Join-Path $PSScriptRoot 'hms_booking_sidecar_8085_v10.py'
$TargetWrapper = Join-Path $Root 'hms_booking_sidecar_8085_v10.py'
$ExpectedSha = '23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac'
$RequiredMarker = 'RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE'
$LogDir = Join-Path $Root '_writer_8085_logs'
$ResultFile = Join-Path $LogDir 'LAST_START_RESULT.txt'

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Set-Content -LiteralPath $ResultFile -Value '' -Encoding UTF8

function Write-Log([string]$Text) {
    Write-Host $Text
    Add-Content -LiteralPath $ResultFile -Value $Text -Encoding UTF8
}

function Get-WriterListener {
    return Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Show-File([string]$Path, [string]$Title) {
    Write-Log ('--- ' + $Title + ' ---')
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $txt = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
        if ([string]::IsNullOrWhiteSpace($txt)) {
            Write-Log '<empty>'
        } else {
            Write-Log $txt
        }
    } else {
        Write-Log '<missing>'
    }
}

function Test-Health {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8085/riverwood-writer-health' -TimeoutSec 3
        if ([int]$resp.StatusCode -ne 200) { return $null }
        return ($resp.Content | ConvertFrom-Json)
    } catch {
        return $null
    }
}

try {
    Write-Log 'Riverwood HMS Writer v10 - dedicated booking sidecar :8085'
    Write-Log 'This does NOT patch Operations and does NOT touch the availability sidecar.'
    Write-Log ('Writer source: ' + $Writer)

    if (-not (Test-Path -LiteralPath $Writer -PathType Leaf)) {
        throw ('Writer source not found: ' + $Writer)
    }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw ('Python not found: ' + $Python)
    }
    if (-not (Test-Path -LiteralPath $PackageWrapper -PathType Leaf)) {
        throw ('Packaged sidecar wrapper not found: ' + $PackageWrapper)
    }

    $actualSha = (Get-FileHash -LiteralPath $Writer -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Log ('Writer SHA256: ' + $actualSha)
    if ($actualSha -ne $ExpectedSha) {
        throw ('Refusing start: writer SHA mismatch. Expected ' + $ExpectedSha + ', actual ' + $actualSha)
    }
    $writerText = [IO.File]::ReadAllText($Writer)
    if ($writerText.IndexOf($RequiredMarker, [StringComparison]::Ordinal) -lt 0) {
        throw 'Refusing start: v10 technical card-budget marker is missing from writer source.'
    }

    $existing = Get-WriterListener
    if ($existing) {
        $health = Test-Health
        if ($health -and $health.ok -eq $true -and [string]$health.writer_sha256 -eq $ExpectedSha) {
            Write-Log ('ALREADY OK: :8085 LISTEN PID=' + [int]$existing.OwningProcess)
            Write-Log ('Runtime writer SHA: ' + [string]$health.writer_sha256)
            Write-Log ('Adapter version: ' + [string]$health.adapter_version)
            Write-Log 'START OK'
            exit 0
        }
        throw ('Port 8085 is already LISTEN PID=' + [int]$existing.OwningProcess + ' but it is not the verified dedicated v10 sidecar. Refusing to stop or replace it.')
    }

    if (Test-Path -LiteralPath $TargetWrapper -PathType Leaf) {
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $backup = Join-Path $LogDir ('hms_booking_sidecar_8085_v10.before_' + $stamp + '.py')
        Copy-Item -LiteralPath $TargetWrapper -Destination $backup -Force
        Write-Log ('Existing wrapper backup: ' + $backup)
    }
    Copy-Item -LiteralPath $PackageWrapper -Destination $TargetWrapper -Force
    Write-Log ('Installed dedicated wrapper: ' + $TargetWrapper)

    Write-Log 'Compiling wrapper...'
    & $Python -m py_compile $TargetWrapper
    if ($LASTEXITCODE -ne 0) { throw ('Wrapper compile failed with code ' + $LASTEXITCODE) }

    Write-Log 'Importing exact writer and registering booking routes before start...'
    $probeCode = "import sys; sys.path.insert(0, r'$Root'); import hms_booking_sidecar_8085_v10 as s; routes=sorted(r.rule for r in s.app.url_map.iter_rules() if r.rule.startswith('/hms-booking/')); print('WRITER_SHA='+s.WRITER_SHA256); print('ADAPTER_VERSION='+str(getattr(s.booking_adapter,'ADAPTER_VERSION',''))); print('BOOKING_ROUTES='+str(len(routes))); print('ROUTES='+'|'.join(routes))"
    $probeOut = & $Python -c $probeCode 2>&1
    $probeCodeExit = $LASTEXITCODE
    foreach ($line in $probeOut) { Write-Log ([string]$line) }
    if ($probeCodeExit -ne 0) { throw ('Writer import/route registration probe failed with code ' + $probeCodeExit) }
    $routeLine = @($probeOut | Where-Object { ([string]$_).StartsWith('BOOKING_ROUTES=') }) | Select-Object -Last 1
    if (-not $routeLine) { throw 'Writer import probe did not report booking routes.' }
    $routeCount = [int](([string]$routeLine).Split('=',2)[1])
    if ($routeCount -lt 3) { throw ('Writer import probe exposed too few booking routes: ' + $routeCount) }

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $stdout = Join-Path $LogDir ('writer_8085_' + $stamp + '.out.txt')
    $stderr = Join-Path $LogDir ('writer_8085_' + $stamp + '.err.txt')
    Write-Log ('Starting dedicated sidecar on 127.0.0.1:8085')
    Write-Log ('stdout: ' + $stdout)
    Write-Log ('stderr: ' + $stderr)

    $proc = Start-Process -FilePath $Python -ArgumentList @('-u', $TargetWrapper) -WorkingDirectory $Root -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Write-Log ('Launcher PID: ' + $proc.Id)

    $listener = $null
    $deadline = (Get-Date).AddSeconds(25)
    do {
        $listener = Get-WriterListener
        if ($listener) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    if (-not $listener) {
        Write-Log 'FAILED: no LISTEN on :8085 after 25 seconds.'
        Show-File $stdout 'writer stdout'
        Show-File $stderr 'writer stderr'
        throw 'Dedicated v10 sidecar did not start.'
    }

    $listenerPid = [int]$listener.OwningProcess
    Write-Log ('8085 LISTEN PID=' + $listenerPid)

    $health = Test-Health
    if (-not $health) {
        Show-File $stdout 'writer stdout'
        Show-File $stderr 'writer stderr'
        throw '8085 is listening but /riverwood-writer-health did not return valid JSON.'
    }
    Write-Log ('Health writer SHA: ' + [string]$health.writer_sha256)
    Write-Log ('Health adapter version: ' + [string]$health.adapter_version)
    if ($health.ok -ne $true) { throw 'Writer health returned ok=false.' }
    if ([string]$health.writer_sha256 -ne $ExpectedSha) {
        throw ('Runtime health SHA mismatch: ' + [string]$health.writer_sha256)
    }

    Write-Log 'RECOVERY OK'
    Write-Log ('8085 LISTEN PID=' + $listenerPid)
    Write-Log ('Writer SHA OK: ' + $ExpectedSha)
    Write-Log 'Dedicated v10 booking sidecar is now running.'
    Write-Log ('Result file: ' + $ResultFile)
    exit 0
}
catch {
    Write-Log ('FAILED: ' + $_.Exception.Message)
    Write-Log ('Result file: ' + $ResultFile)
    exit 7
}
