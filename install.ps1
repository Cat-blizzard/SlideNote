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
            $null = & $candidate["Exe"] @($candidate["Args"]) --version 2>$null
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

$python = Find-Python
$venvFullPath = Join-Path $Root $VenvPath
$venvPython = Join-Path $venvFullPath "Scripts\python.exe"

Write-Host "SlideNote setup"
Write-Host "Project: $Root"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment: $VenvPath"
    & $python["Exe"] @($python["Args"]) -m venv $venvFullPath
}
else {
    Write-Host "Using existing virtual environment: $VenvPath"
}

Write-Host "Upgrading pip"
& $venvPython -m pip install --upgrade pip

$extras = if ($NoGui) { ".[dev,llm]" } else { ".[dev,llm,gui]" }
Write-Host "Installing SlideNote: $extras"
& $venvPython -m pip install -e $extras

Write-Host ""
Write-Host "Running environment check"
& $venvPython -m slidenote doctor

Write-Host ""
Write-Host "Setup complete."
if (-not $NoGui) {
    Write-Host "Start the GUI with: .\run_gui.ps1"
}
else {
    Write-Host "Run the CLI with: $venvPython -m slidenote --help"
}
