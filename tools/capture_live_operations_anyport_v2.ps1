$ErrorActionPreference = 'Stop'

$Out = Join-Path $PSScriptRoot 'CAPTURED_LIVE_OPERATIONS_ANYPORT_V2'
$Report = Join-Path $Out 'CAPTURE_REPORT.txt'
$CandidatesDir = Join-Path $Out 'CANDIDATES'
$Bundle = Join-Path $PSScriptRoot 'CAPTURED_LIVE_OPERATIONS_ANYPORT_V2.zip'

if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
if (Test-Path $Bundle) { Remove-Item $Bundle -Force }
New-Item -ItemType Directory -Path $Out -Force | Out-Null
New-Item -ItemType Directory -Path $CandidatesDir -Force | Out-Null

function Add-Report([string]$Text) {
    $Text | Tee-Object -FilePath $Report -Append | Write-Host
}

function Is-Historical([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $true }
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

function Redact-CommandLine([string]$Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return '' }
    $x = $Text
    $x = [regex]::Replace($x, '(?i)(--token\s+)([^\s]+)', '$1<redacted>')
    $x = [regex]::Replace($x, '(?i)(token=)([^\s]+)', '$1<redacted>')
    $x = [regex]::Replace($x, '(?i)(password=)([^\s]+)', '$1<redacted>')
    $x = [regex]::Replace($x, '(?i)(secret=)([^\s]+)', '$1<redacted>')
    $x = [regex]::Replace($x, '(?i)(api[_-]?key=)([^\s]+)', '$1<redacted>')
    return $x
}

function Add-Candidate([System.Collections.Generic.List[string]]$List, [string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    try {
        $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
        if ((Test-Path -LiteralPath $resolved -PathType Leaf) -and -not (Is-Historical $resolved)) {
            if (-not $List.Contains($resolved)) { $List.Add($resolved) }
        }
    } catch {}
}

Add-Report 'Riverwood Operations live-source capture'
Add-Report 'CAPTURE TOOL ANYPORT V2 ASCII PS51'
Add-Report ('Captured at: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Add-Report 'READ ONLY: no process, service, port, source file, tunnel, or configuration will be modified.'
Add-Report ''

$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)
$listenerByPid = @{}
foreach ($l in $listeners) {
    $pidKey = [string]$l.OwningProcess
    if (-not $listenerByPid.ContainsKey($pidKey)) { $listenerByPid[$pidKey] = @() }
    $listenerByPid[$pidKey] += [int]$l.LocalPort
}

Add-Report 'LISTENING PORTS:'
foreach ($l in @($listeners | Sort-Object LocalPort, OwningProcess)) {
    Add-Report ('  port=' + $l.LocalPort + ' pid=' + $l.OwningProcess + ' addr=' + $l.LocalAddress)
}
Add-Report ''

$allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$appProcesses = @()
foreach ($p in $allProcesses) {
    $name = [string]$p.Name
    $cmd = [string]$p.CommandLine
    $combined = ($name + ' ' + $cmd).ToLowerInvariant()
    if ($combined -match 'python|waitress|flask|uvicorn|riverwood') {
        $appProcesses += $p
    }
}

Add-Report 'PYTHON/APP PROCESS INVENTORY:'
foreach ($p in $appProcesses) {
    $pidKey = [string]$p.ProcessId
    $ports = @()
    if ($listenerByPid.ContainsKey($pidKey)) { $ports = @($listenerByPid[$pidKey] | Sort-Object -Unique) }
    $safeCmd = Redact-CommandLine ([string]$p.CommandLine)
    Add-Report ('  pid=' + $p.ProcessId + ' name=' + $p.Name + ' ports=' + ($ports -join ',') )
    Add-Report ('    exe=' + $p.ExecutablePath)
    Add-Report ('    cmd=' + $safeCmd)
}
Add-Report ''

Add-Report 'CLOUDFLARED PROCESS INVENTORY (tokens redacted):'
foreach ($p in $allProcesses) {
    if (([string]$p.Name).ToLowerInvariant() -like '*cloudflared*') {
        Add-Report ('  pid=' + $p.ProcessId + ' cmd=' + (Redact-CommandLine ([string]$p.CommandLine)))
    }
}
Add-Report ''

Add-Report 'CLOUDFLARED INGRESS HINTS (hostname/service only):'
$configPaths = @()
if ($env:USERPROFILE) {
    $configPaths += (Join-Path $env:USERPROFILE '.cloudflared\config.yml')
    $configPaths += (Join-Path $env:USERPROFILE '.cloudflared\config.yaml')
}
$configPaths += 'C:\Cloudflared\config.yml'
$configPaths += 'C:\Cloudflared\config.yaml'
foreach ($cfg in $configPaths) {
    if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) { continue }
    Add-Report ('  config=' + $cfg)
    try {
        foreach ($line in [System.IO.File]::ReadAllLines($cfg)) {
            $trim = $line.Trim()
            if ($trim -match '^(hostname|service):') { Add-Report ('    ' + $trim) }
        }
    } catch {
        Add-Report ('    read_failed=' + $_.Exception.Message)
    }
}
Add-Report ''

$candidates = New-Object 'System.Collections.Generic.List[string]'

# Candidate 1: explicit Python paths from process command lines.
foreach ($p in $appProcesses) {
    $cmd = [string]$p.CommandLine
    if ([string]::IsNullOrWhiteSpace($cmd)) { continue }
    $matches = [regex]::Matches($cmd, '(?i)(?:"([^"]+\.py)"|([^\s"]+\.py))')
    foreach ($m in $matches) {
        $raw = ''
        if ($m.Groups[1].Success) { $raw = $m.Groups[1].Value } else { $raw = $m.Groups[2].Value }
        if ([string]::IsNullOrWhiteSpace($raw)) { continue }
        try {
            if ([System.IO.Path]::IsPathRooted($raw)) {
                $entry = (Resolve-Path -LiteralPath $raw -ErrorAction Stop).Path
                $dir = Split-Path -Parent $entry
                Add-Candidate $candidates (Join-Path $dir 'accommodation_module.py')
                Add-Candidate $candidates (Join-Path (Split-Path -Parent $dir) 'accommodation_module.py')
            }
        } catch {}
    }
}

# Candidate 2: known live Riverwood trees. Historical copies are excluded.
$roots = @('C:\Riverwood_Operations_MVP0_Core_Employees', 'C:\riverwood_revenue_bot')
foreach ($root in $roots) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
    $files = @(Get-ChildItem -LiteralPath $root -Filter 'accommodation_module.py' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { -not (Is-Historical $_.FullName) })
    foreach ($f in $files) { Add-Candidate $candidates $f.FullName }
}

if ($candidates.Count -eq 0) {
    Add-Report 'FAILED: no non-historical accommodation_module.py candidate found in live Riverwood trees.'
    exit 5
}

Add-Report 'OPERATIONS SOURCE CANDIDATES:'
$ranked = @()
foreach ($path in $candidates) {
    $score = 0
    $text = ''
    try { $text = [System.IO.File]::ReadAllText($path) } catch {}
    if ($text.Contains('def _hms_booking_state')) { $score += 100 }
    if ($text.Contains('def _hms_booking_payload')) { $score += 100 }
    if ($text.Contains('hms_booking_preflight')) { $score += 20 }
    if ($text.Contains('HMS_COMPAT')) { $score += 20 }
    $parent = (Split-Path -Parent $path).ToLowerInvariant()
    foreach ($p in $appProcesses) {
        $cmd = ([string]$p.CommandLine).ToLowerInvariant()
        if ($cmd.Contains($parent)) { $score += 1000 }
    }
    $ranked += [pscustomobject]@{ Score=$score; Path=$path }
}
$ranked = @($ranked | Sort-Object Path | Sort-Object Score -Descending)

$index = 0
foreach ($item in $ranked) {
    $index += 1
    $path = [string]$item.Path
    $sourceDir = Split-Path -Parent $path
    $template = Join-Path $sourceDir 'templates\accommodation_quote_detail.html'
    $hash = ''
    try { $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } catch {}
    Add-Report ('  candidate=' + $index + ' score=' + $item.Score)
    Add-Report ('    module=' + $path)
    Add-Report ('    module_sha256=' + $hash)
    Add-Report ('    template=' + $template)

    $dest = Join-Path $CandidatesDir ('candidate_' + ('{0:D2}' -f $index))
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Copy-Item -LiteralPath $path -Destination (Join-Path $dest 'accommodation_module.py') -Force
    if (Test-Path -LiteralPath $template -PathType Leaf) {
        Copy-Item -LiteralPath $template -Destination (Join-Path $dest 'accommodation_quote_detail.html') -Force
    }
    @(
        'score=' + $item.Score,
        'module=' + $path,
        'module_sha256=' + $hash,
        'template=' + $template
    ) | Set-Content -LiteralPath (Join-Path $dest 'SOURCE_INFO.txt') -Encoding ASCII
}

Add-Report ''
Add-Report ('CANDIDATE_COUNT=' + $ranked.Count)
Add-Report 'No candidate was modified. All candidates were copied for offline comparison.'

Compress-Archive -Path (Join-Path $Out '*') -DestinationPath $Bundle -CompressionLevel Optimal -Force
$bundleHash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash.ToLowerInvariant()
Add-Report ('CAPTURE_ZIP=' + $Bundle)
Add-Report ('CAPTURE_ZIP_SHA256=' + $bundleHash)
Add-Report 'CAPTURE OK. Upload CAPTURED_LIVE_OPERATIONS_ANYPORT_V2.zip to ChatGPT.'
exit 0
