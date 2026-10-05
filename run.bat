@echo off
REM LinkedIn Job Search & Easy Apply Runner
REM Searches for jobs at target companies and auto-applies via Easy Apply

echo ========================================
echo LinkedIn Job Automation
echo ========================================
echo.

REM Check if virtual environment exists
if not exist "venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found.
    echo Run setup first:
    echo   python -m venv venv
    echo   venv\Scripts\python.exe -m pip install -r requirements.txt
    echo   venv\Scripts\python.exe -m playwright install chromium
    pause
    exit /b 1
)

REM Check if login state exists
if not exist "linkedin_state.json" (
    echo ERROR: linkedin_state.json not found.
    echo Run login first:
    echo   venv\Scripts\python.exe linkedin_login.py
    pause
    exit /b 1
)

REM Check if resume profile exists (will be created if missing)
if not exist "resume_profile.json" (
    echo Resume profile not found, will be created from resume PDF...
)

echo Starting job search for target companies...
echo.

REM Run the search - you can override role, applicants, max from command line
REM Example: run.bat "React Developer" 50 --max 5
venv\Scripts\python.exe linkedin_search.py %*

echo.
echo ========================================
echo Done. Check logs above for results.
echo ========================================
pause