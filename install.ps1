[CmdletBinding()]
param(
    [string]$VenvPath = ".venv",
    [switch]$NoGui
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        try {
            # Accept the interpreter only if it is Python 3.10 or newer.
            $null = & $candidate["Exe"] @($candidate["Args"]) -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
            continue
        }
    }
    throw "Python 3.10+ was not found. Install Python, then run this script again."
}

# $ErrorActionPreference does not stop on failing native commands, so check exit codes.
function Invoke-Checked {
    param([string]$Description, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

$python = Find-Python
$venvFullPath = Join-Path $Root $VenvPath
$venvPython = Join-Path $venvFullPath "Scripts\python.exe"

Write-Host "SlideNote setup"
Write-Host "Project: $Root"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment: $VenvPath"
    Invoke-Checked "Creating the virtual environment" { & $python["Exe"] @($python["Args"]) -m venv $venvFullPath }
}
else {
    Write-Host "Using existing virtual environment: $VenvPath"
}

Write-Host "Upgrading pip"
Invoke-Checked "Upgrading pip" { & $venvPython -m pip install --upgrade pip }

$extras = if ($NoGui) { ".[dev,llm]" } else { ".[dev,llm,gui]" }
Write-Host "Installing SlideNote: $extras"
Invoke-Checked "Installing SlideNote" { & $venvPython -m pip install -e $extras }

Write-Host ""
Write-Host "Running environment check"
& $venvPython -m slidenote doctor
if ($LASTEXITCODE -ne 0) {
    Write-Warning "The environment check reported problems; see the output above."
}

Write-Host ""
Write-Host "Setup complete."
if (-not $NoGui) {
    Write-Host "Start the GUI with: .\run_gui.ps1"
}
else {
    Write-Host "Run the CLI with: $venvPython -m slidenote --help"
}
