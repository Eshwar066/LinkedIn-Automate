# LinkedIn Apply - one-time installer for Windows
param(
    [string]$InstallPath = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

# --- Check Python -----------------------------------------------------------
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "Python is not installed or not on PATH." -ForegroundColor Red
    Write-Host "Install Python 3.10 or newer from https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

$pyVersion = & python --version 2>&1
if ($pyVersion -notmatch "Python (\d+)\.(\d+)") {
    Write-Host "Could not detect Python version." -ForegroundColor Red
    exit 1
}

$major = [int]$Matches[1]
$minor = [int]$Matches[2]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    Write-Host "Python $major.$minor is too old. Install Python 3.10 or newer." -ForegroundColor Red
    exit 1
}

Write-Host "Found $pyVersion" -ForegroundColor Green
Set-Location $InstallPath

# --- Create virtual environment ---------------------------------------------
$venvPath = Join-Path $InstallPath "venv"
if (-not (Test-Path $venvPath)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv venv
} else {
    Write-Host "Virtual environment already exists." -ForegroundColor Cyan
}

$pip = Join-Path $venvPath "Scripts\pip.exe"
$pythonVenv = Join-Path $venvPath "Scripts\python.exe"

# --- Install Python dependencies --------------------------------------------
Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
& $pip install --upgrade pip setuptools wheel
& $pip install -r (Join-Path $InstallPath "requirements.txt")

# --- Install Playwright Chromium browser -------------------------------------
Write-Host "Installing Playwright Chromium browser..." -ForegroundColor Cyan
& $pythonVenv -m playwright install chromium

# --- Create .env from example -----------------------------------------------
$envExample = Join-Path $InstallPath ".env.example"
$envFile = Join-Path $InstallPath ".env"
if ((Test-Path $envExample) -and -not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "Created .env from .env.example." -ForegroundColor Yellow
    Write-Host "Edit it to add your GEMINI_API_KEY before running." -ForegroundColor Yellow
}

# --- Create config.json from example ----------------------------------------
$configExample = Join-Path $InstallPath "config.example.json"
$configFile = Join-Path $InstallPath "config.json"
if ((Test-Path $configExample) -and -not (Test-Path $configFile)) {
    Copy-Item $configExample $configFile
    Write-Host "Created config.json from config.example.json." -ForegroundColor Yellow
    Write-Host "Edit it to set your role, location, and resume path." -ForegroundColor Yellow
}

# --- Create resume_profile.json from example --------------------------------
$resumeExample = Join-Path $InstallPath "resume_profile.example.json"
$resumeFile = Join-Path $InstallPath "resume_profile.json"
if ((Test-Path $resumeExample) -and -not (Test-Path $resumeFile)) {
    Copy-Item $resumeExample $resumeFile
    Write-Host "Created resume_profile.json from resume_profile.example.json." -ForegroundColor Yellow
    Write-Host "Replace the example values with your own profile before running." -ForegroundColor Yellow
}

# --- Create run.bat ---------------------------------------------------------
$runBat = Join-Path $InstallPath "run.bat"
$batContent = @"
@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
python main.py
"@
Set-Content -Path $runBat -Value $batContent -Encoding ASCII -Force

# --- Create Desktop shortcut ------------------------------------------------
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "LinkedIn Apply.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = $runBat
$Shortcut.WorkingDirectory = $InstallPath
$Shortcut.IconLocation = "shell32.dll,14"
$Shortcut.Save()

Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "A 'LinkedIn Apply' shortcut was added to your Desktop." -ForegroundColor Green
if (-not (Test-Path $envFile)) {
    Write-Host "Remember to add your GEMINI_API_KEY to .env before running." -ForegroundColor Yellow
}
