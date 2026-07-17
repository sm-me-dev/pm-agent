<#
.SYNOPSIS
    pm-agent installer — Windows
.DESCRIPTION
    Installs pm-agent via pipx into an isolated Python environment.
    Run from PowerShell:
      irm https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.ps1 | iex
    To pin a version:
      $env:PM_AGENT_VERSION = "0.3.0"; irm ... | iex
.NOTES
    Security: piping from the web to your shell is convenient but skips
    normal verification. Review the script first:
      irm https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.ps1
#>

$ErrorActionPreference = "Stop"
$RepoOwner = "sm-me-uwe"
$RepoName = "pm-agent"
$PipxDir = Join-Path $env:USERPROFILE ".local\bin"

# ---- Python check ----

function Get-PythonPath {
    $candidates = @(
        (Get-Command "python3" -ErrorAction SilentlyContinue).Source,
        (Get-Command "python" -ErrorAction SilentlyContinue).Source,
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) {
            return $c
        }
    }
    return $null
}

$python = Get-PythonPath
if (-not $python) {
    Write-Host "error: Python 3.12+ is required but not found." -ForegroundColor Red
    Write-Host "  Download from: https://www.python.org/downloads/"
    exit 1
}

# Check Python version
$pyver = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
try {
    $major, $minor = $pyver.Split('.') | ForEach-Object { [int]$_ }
} catch {
    Write-Host "error: could not determine Python version." -ForegroundColor Red
    exit 1
}
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) {
    Write-Host "error: Python 3.12+ required (found $pyver)" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Python $pyver found at $python" -ForegroundColor Green

# ---- pipx ----

$pipx = Get-Command "pipx" -ErrorAction SilentlyContinue
if (-not $pipx) {
    Write-Host "pipx not found — installing via pip..."

    $pipCheck = & $python -m pip --version 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host "error: pip is not available." -ForegroundColor Red
        exit 1
    }

    & $python -m pip install --user pipx 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "error: pip install pipx failed." -ForegroundColor Red
        exit 1
    }

    $pipx = Get-Command "pipx" -ErrorAction SilentlyContinue
    if (-not $pipx) {
        $pipxPath = Join-Path $env:USERPROFILE ".local\bin\pipx.exe"
        if (-not (Test-Path $pipxPath)) {
            $pipxPath = Join-Path $env:USERPROFILE "AppData\Roaming\Python\Scripts\pipx.exe"
        }
        if (Test-Path $pipxPath) {
            $pipx = $pipxPath
        } else {
            Write-Host ""
            Write-Host "  pipx was installed but is not in your PATH." -ForegroundColor Yellow
            Write-Host "  Add this directory to your PATH:" -ForegroundColor Yellow
            Write-Host "    $PipxDir" -ForegroundColor Yellow
            Write-Host "  Or run the following command in this session:" -ForegroundColor Yellow
            Write-Host "    `$env:Path += `";$PipxDir`"" -ForegroundColor Yellow
            Write-Host ""
        }
    }
}

if ($pipx -is [System.Management.Automation.CommandInfo]) {
    $pipxExe = $pipx.Source
} else {
    $pipxExe = $pipx
}

# ---- Install / upgrade pm-agent ----

$version = $env:PM_AGENT_VERSION
if ($version) {
    $installSpec = "pm-agent==$version"
} else {
    $installSpec = "pm-agent"
}

Write-Host "Installing pm-agent via pipx ..."

if ($pipxExe) {
    # Check if already installed
    $listOutput = & $pipxExe list --short 2>&1 | Out-String
    $alreadyInstalled = $listOutput -match "^pm-agent "

    if ($alreadyInstalled) {
        if ($version) {
            & $pipxExe install $installSpec --force 2>&1
        } else {
            & $pipxExe upgrade pm-agent 2>&1
            if ($LASTEXITCODE -ne 0) {
                & $pipxExe install $installSpec --force 2>&1
            }
        }
    } else {
        & $pipxExe install $installSpec 2>&1
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "error: pipx install failed." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "error: pipx is not available in PATH. Add it and re-run." -ForegroundColor Red
    exit 1
}

# ---- Verify ----

Write-Host ""
$pmAgent = Get-Command "pm-agent" -ErrorAction SilentlyContinue
if ($pmAgent) {
    Write-Host "✓ pm-agent installed successfully" -ForegroundColor Green
    & $pmAgent.Source --help 2>&1 | Select-Object -First 1
    Write-Host ""
    Write-Host "Get started:" -ForegroundColor Cyan
    Write-Host "  cd C:\path\to\your\project"
    Write-Host "  pm-agent init"
    Write-Host "  pm-agent status"
} else {
    $pipxBin = Split-Path $pipxExe -Parent
    Write-Host "✓ pm-agent installed" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Ensure $pipxBin is in your PATH, then run:" -ForegroundColor Yellow
    Write-Host "    pm-agent --help"
}
Write-Host ""
Write-Host "  Documentation: https://github.com/$RepoOwner/$RepoName" -ForegroundColor Cyan
