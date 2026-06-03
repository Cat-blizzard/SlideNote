[CmdletBinding()]
param(
    [int]$Port = 8501,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Virtual environment not found. Running setup first."
    & (Join-Path $Root "install.ps1")
}

& $venvPython -m streamlit --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "GUI dependency missing. Installing GUI extras."
    & $venvPython -m pip install -e ".[dev,llm,gui]"
}

$args = @("-m", "streamlit", "run", "gui/app.py", "--server.port", "$Port")
if ($NoBrowser) {
    $args += @("--server.headless", "true")
}

Write-Host "Starting SlideNote Studio..."
& $venvPython @args
