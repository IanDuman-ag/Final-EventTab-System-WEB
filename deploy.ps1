[CmdletBinding(DefaultParameterSetName = 'Deploy')]
param(
    [Parameter(ParameterSetName = 'DryRun')][switch]$DryRun,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [Parameter(ParameterSetName = 'Rollback')][switch]$Rollback,
    [Parameter(ParameterSetName = 'Deploy')]
    [Parameter(ParameterSetName = 'DryRun')]
    [string[]]$Tests = @('core', 'events', 'accounts')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Target = 'eventtab@103.125.217.125'
$RemoteHelper = '/usr/local/lib/eventtab-deploy/deploy_server.py'
$RemoteInbox = '/srv/eventtab-deploy/incoming'
$SshOptions = @('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15')
$TranscriptStarted = $false

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable. Deployment stopped; see the output above." }
}

try {
    $LogDirectory = Join-Path $ProjectRoot '.tmp\deploy\logs'
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    $LogPath = Join-Path $LogDirectory ("windows-{0}.log" -f (Get-Date -Format 'yyyyMMddTHHmmssfff'))
    Start-Transcript -LiteralPath $LogPath | Out-Null
    $TranscriptStarted = $true
    foreach ($Tool in @('git', 'ssh', 'scp')) {
        if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) { throw "Install $Tool and add it to PATH first." }
    }
    $Python = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Project venv\Scripts\python.exe is missing. Use the existing local virtual environment.' }
    foreach ($File in @('manage.py', 'execution\deploy_release.py', 'execution\deploy_checks.py', 'execution\local_test_database.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $File) -PathType Leaf)) { throw "Missing project file: $File" }
    }
    Invoke-Checked $Python @('--version')
    if ($Status) {
        Invoke-Checked 'ssh' ($SshOptions + @($Target, "python3 $RemoteHelper status"))
        exit 0
    }
    if ($Rollback) {
        Write-Host 'Verifying server and rollback checkpoint'
        & ssh @SshOptions $Target "python3 $RemoteHelper status"
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'Status could not pass. Recovery can still be attempted; the rollback helper independently validates the target and checkpoint.'
        }
        $Answer = Read-Host 'Type ROLLBACK to restore the latest checkpoint (no database migrations are reversed)'
        if ($Answer -cne 'ROLLBACK') { throw 'Rollback cancelled.' }
        # A remote TTY is required as a second safeguard; helper asks for the release ID.
        Invoke-Checked 'ssh' ($SshOptions + @('-t', $Target, "python3 $RemoteHelper rollback"))
        exit 0
    }
    Push-Location $ProjectRoot
    try {
        $OutputDirectory = Join-Path $ProjectRoot '.tmp\deploy'
        $PackageArgs = @((Join-Path $ProjectRoot 'execution\deploy_release.py'), '--root', $ProjectRoot, '--output', $OutputDirectory)
        Write-Host 'Inspecting Git branch, changes and allowed release files'
        Invoke-Checked $Python ($PackageArgs + @('--dry-run'))
        if (-not $DryRun) {
            $Dirty = & git status --porcelain --untracked-files=normal
            if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Commit reviewed changes first. Deployment requires a clean Git working tree.' }
        }
        Write-Host 'Running deployment safety tests'
        Invoke-Checked $Python @('-m', 'unittest', 'discover', '-s', 'execution', '-p', 'test_deploy*.py')
        Write-Host 'Running local Django checks and tests in an isolated database'
        Invoke-Checked $Python (@((Join-Path $ProjectRoot 'execution\deploy_checks.py')) + $Tests)
        if ($DryRun) {
            Write-Host 'Dry run complete. No SSH connection, upload or server changes. Remote checks have not run.'
            exit 0
        }
        Invoke-Checked $Python $PackageArgs
        $Release = Get-Content -LiteralPath (Join-Path $OutputDirectory 'release.json') -Raw | ConvertFrom-Json
        if ($Release.release -notmatch '^\d{8}T\d{6}Z-[0-9a-f]{12}$' -or $Release.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Invalid release metadata.' }
        $ActualHash = (Get-FileHash -LiteralPath $Release.archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ActualHash -cne $Release.sha256) { throw 'Local archive SHA256 mismatch.' }
        Write-Host 'Verifying server'
        Invoke-Checked 'ssh' ($SshOptions + @($Target, "python3 $RemoteHelper status"))
        Write-Host "Release: $($Release.release) | SHA256: $ActualHash"
        $Answer = Read-Host 'Review the summary above. Type DEPLOY to upload and deploy this committed release'
        if ($Answer -cne 'DEPLOY') { throw 'Deployment cancelled.' }
        Write-Host 'Uploading release'
        Invoke-Checked 'scp' ($SshOptions + @($Release.archive, "${Target}:$RemoteInbox/$($Release.release).tar.gz"))
        Invoke-Checked 'ssh' ($SshOptions + @($Target, "python3 $RemoteHelper deploy $($Release.release) $ActualHash"))
        Write-Host 'Deployment successful. Check the website and Judge app manually.'
    } finally { Pop-Location }
} catch {
    Write-Host "Deployment blocked: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($TranscriptStarted) { Stop-Transcript | Out-Null }
}
