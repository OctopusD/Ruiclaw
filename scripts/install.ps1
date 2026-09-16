param(
    [switch]$Dev,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$Package = "ruiclaw-ai"
$InstallTarget = $Package
$InstallSource = "PyPI"
$script:RuiClawRunner = $null
$script:RuiClawPython = $null
$script:LastInstallSucceeded = $false

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Fail {
    param([string]$Message)
    throw "Error: $Message"
}

function Show-InstallFailureHint {
    [Console]::Error.WriteLine("Error: could not install ruiclaw from $InstallSource.")
    [Console]::Error.WriteLine("If pip mentioned externally-managed-environment, use uv, pipx, or a virtual environment instead of system pip.")
    [Console]::Error.WriteLine("You can also run manually:")
    [Console]::Error.WriteLine("  uv tool install --force --upgrade $InstallTarget")
    [Console]::Error.WriteLine("  $Python -m venv `$HOME\.ruiclaw\venv")
    [Console]::Error.WriteLine("  `$HOME\.ruiclaw\venv\Scripts\python.exe -m pip install --upgrade $InstallTarget")
    [Console]::Error.WriteLine("Then start setup with:")
    [Console]::Error.WriteLine("  ruiclaw onboard --wizard")
    throw "could not install ruiclaw from $InstallSource"
}

function Show-Usage {
    Write-Host "Usage: install.ps1 [-DryRun|--dry-run]"
    Write-Host ""
    Write-Host "By default this installs or upgrades ruiclaw-ai from PyPI."
    Write-Host "Use --dry-run to print what would happen without installing or starting setup."
    Write-Host ""
    Write-Host "For current main, clone the repository and run 'python -m pip install -e .'."
}

function Test-Python {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-Python {
    if ($env:PYTHON) {
        if (Get-Command $env:PYTHON -ErrorAction SilentlyContinue) {
            if (Test-Python $env:PYTHON) {
                return $env:PYTHON
            }
            Fail "PYTHON=$env:PYTHON is not Python 3.11 or newer."
        }
        Fail "PYTHON=$env:PYTHON was not found."
    }

    foreach ($Candidate in @("python", "py")) {
        if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
            if (Test-Python $Candidate) {
                return $Candidate
            }
        }
    }

    Fail "Python 3.11 or newer was not found. Install Python first, then rerun this command."
}

function Test-VirtualEnv {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Ensure-Pip {
    param([string]$Command)

    try {
        & $Command -m pip --version *> $null
    } catch {}

    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Info "pip was not found for $Command. Trying ensurepip..."
    & $Command -m ensurepip --upgrade *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "pip is not available. Install pip for $Command, then rerun this command."
    }
}

function Invoke-RuiClaw {
    param([string[]]$RuiClawArgs)

    switch ($script:RuiClawRunner) {
        "uv" {
            & uv tool run --from $InstallTarget ruiclaw @RuiClawArgs
        }
        "pipx" {
            & pipx run --spec $InstallTarget ruiclaw @RuiClawArgs
        }
        "python" {
            & $script:RuiClawPython -m ruiclaw @RuiClawArgs
        }
        default {
            Fail "ruiclaw was installed, but no runner was configured."
        }
    }
}

function Get-RuiClawCommand {
    switch ($script:RuiClawRunner) {
        "uv" { return "uv tool run --from $InstallTarget ruiclaw" }
        "pipx" { return "pipx run --spec $InstallTarget ruiclaw" }
        "python" { return "$script:RuiClawPython -m ruiclaw" }
        default { return "ruiclaw" }
    }
}

function Test-FreshRuiClawInstall {
    $HomeDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if (-not $HomeDir) {
        return $false
    }
    return -not (Test-Path -LiteralPath (Join-Path $HomeDir ".ruiclaw\config.json"))
}

function Test-BrowserSession {
    if ($env:SSH_CONNECTION -or $env:SSH_TTY -or -not [Environment]::UserInteractive) {
        return $false
    }

    $CurrentSessionId = (Get-Process -Id $PID).SessionId
    return @(
        Get-Process -Name explorer -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq $CurrentSessionId }
    ).Count -gt 0
}

function Install-WithActivePython {
    Write-Info "Detected an active virtual environment. Installing into it..."
    Ensure-Pip $Python
    & $Python -m pip install --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }
    $script:RuiClawRunner = "python"
    $script:RuiClawPython = $Python
}

function Install-WithUv {
    $script:LastInstallSucceeded = $false
    Write-Info "Installing or upgrading ruiclaw from $InstallSource with uv tool..."
    & uv tool install --python $Python --force --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $script:RuiClawRunner = "uv"
    $script:LastInstallSucceeded = $true
}

function Install-WithPipx {
    $script:LastInstallSucceeded = $false
    Write-Info "Installing or upgrading ruiclaw from $InstallSource with pipx..."
    & pipx install --python $Python --force $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $script:RuiClawRunner = "pipx"
    $script:LastInstallSucceeded = $true
}

function Install-WithManagedVenv {
    $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { $null }
    if (-not $HomeDir) {
        Fail "HOME is not set; cannot create a managed virtual environment."
    }

    $VenvDir = if ($env:RUICLAW_VENV) { $env:RUICLAW_VENV } else { Join-Path $HomeDir ".ruiclaw\venv" }
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

    if (-not (Test-Path $VenvPython)) {
        Write-Info "Creating a dedicated virtual environment at $VenvDir..."
        $Parent = Split-Path -Parent $VenvDir
        if ($Parent) {
            New-Item -ItemType Directory -Force -Path $Parent *> $null
        }
        & $Python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Show-InstallFailureHint
        }
    }

    if (-not (Test-Python $VenvPython)) {
        Fail "The managed venv uses Python older than 3.11. Remove it or set RUICLAW_VENV to a new path."
    }

    Write-Info "Installing or upgrading ruiclaw from $InstallSource in $VenvDir..."
    Ensure-Pip $VenvPython
    & $VenvPython -m pip install --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }

    $script:RuiClawRunner = "python"
    $script:RuiClawPython = $VenvPython
}

foreach ($Arg in $RemainingArgs) {
    switch ($Arg) {
        "--dev" {
            $Dev = $true
        }
        "--dry-run" {
            $DryRun = $true
        }
        "-h" {
            Show-Usage
            return
        }
        "--help" {
            Show-Usage
            return
        }
        default {
            Fail "Unknown option: $Arg"
        }
    }
}

if ($Dev) {
    Fail "--dev installed an untracked main snapshot and is no longer supported; clone the repository and run 'python -m pip install -e .' instead."
}

$Python = Find-Python
$Version = & $Python --version
Write-Info "Using Python: $Version"

if ($DryRun) {
    Write-Info "Dry run: would install or upgrade ruiclaw from $InstallSource."
    if (Test-VirtualEnv $Python) {
        Write-Info "Dry run: active virtual environment detected; would run: $Python -m pip install --upgrade $InstallTarget"
        Write-Info "Dry run: would run ruiclaw as: $Python -m ruiclaw"
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Info "Dry run: would run: uv tool install --python $Python --force --upgrade $InstallTarget"
        Write-Info "Dry run: would run ruiclaw as: uv tool run --from $InstallTarget ruiclaw"
    } elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
        Write-Info "Dry run: would run: pipx install --python $Python --force $InstallTarget"
        Write-Info "Dry run: would run ruiclaw as: pipx run --spec $InstallTarget ruiclaw"
    } else {
        $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { "~" }
        $VenvDir = if ($env:RUICLAW_VENV) { $env:RUICLAW_VENV } else { Join-Path $HomeDir ".ruiclaw\venv" }
        Write-Info "Dry run: would create or reuse a dedicated virtual environment: $VenvDir"
        Write-Info "Dry run: would run: $VenvDir\Scripts\python.exe -m pip install --upgrade $InstallTarget"
        Write-Info "Dry run: would run ruiclaw as: $VenvDir\Scripts\python.exe -m ruiclaw"
    }
    if ($env:RUICLAW_SKIP_WIZARD -eq "1") {
        Write-Info "Dry run: would skip automatic setup because RUICLAW_SKIP_WIZARD=1."
    } elseif ((Test-FreshRuiClawInstall) -and (Test-BrowserSession)) {
        Write-Info "Dry run: would start the WebUI for this fresh desktop install."
        Write-Info "Dry run: would fall back to the setup wizard for older releases."
    } else {
        Write-Info "Dry run: would run the setup wizard."
    }
    Write-Info "Dry run: no changes made."
    return
}

if (Test-VirtualEnv $Python) {
    Install-WithActivePython
} else {
    $Installed = $false

    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Install-WithUv
        $Installed = $script:LastInstallSucceeded
        if (-not $Installed) {
            Write-Info "uv tool install failed. Trying the next isolated install method..."
        }
    }

    if (-not $Installed -and (Get-Command pipx -ErrorAction SilentlyContinue)) {
        Install-WithPipx
        $Installed = $script:LastInstallSucceeded
        if (-not $Installed) {
            Write-Info "pipx install failed. Trying the managed virtual environment..."
        }
    }

    if (-not $Installed) {
        Write-Info "Using a dedicated virtual environment to avoid system pip."
        Install-WithManagedVenv
    }
}

Write-Info "Installed ruiclaw:"
Invoke-RuiClaw @("--version")
if ($LASTEXITCODE -ne 0) {
    Fail "ruiclaw was installed, but the command could not be started."
}

if ($env:RUICLAW_SKIP_WIZARD -eq "1") {
    Write-Info "Skipping automatic setup because RUICLAW_SKIP_WIZARD=1."
    Write-Info "Run this later: $(Get-RuiClawCommand) webui"
    return
}

if ((Test-FreshRuiClawInstall) -and (Test-BrowserSession)) {
    Invoke-RuiClaw @("webui", "--help") *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Starting ruiclaw WebUI..."
        Write-Info "Configure your first provider and model in Settings > Models."
        Write-Info "Run this later: $(Get-RuiClawCommand) webui"
        Invoke-RuiClaw @("webui", "--yes")
        if ($LASTEXITCODE -ne 0) {
            Fail "WebUI did not start."
        }
        return
    }
    Write-Info "The installed release does not support ruiclaw webui yet."
    Write-Info "Falling back to the setup wizard..."
}

Write-Info "Starting setup wizard..."
Invoke-RuiClaw @("onboard", "--wizard")
if ($LASTEXITCODE -ne 0) {
    Fail "Setup wizard did not complete."
}

Write-Info "Done. Try: $(Get-RuiClawCommand) agent -m `"Hello!`""
