@echo off
setlocal
cd /d "%~dp0"

title Python App Environment

echo ==========================================
echo   Setting up the Python environment
echo ==========================================
echo.

REM Check that Python is installed.
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON=python"
    ) else (
        echo ERROR: Python was not found.
        echo Install Python 3 from https://www.python.org/downloads/
        echo During installation, enable "Add Python to PATH".
        pause
        exit /b 1
    )
)

REM Create the virtual environment if it does not already exist.
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo ERROR: Could not create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Existing virtual environment found.
)

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

echo.
echo Updating pip ...
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 (
    echo WARNING: pip could not be upgraded. Continuing anyway...
)

REM Install project dependencies when requirements.txt is present.
if exist "requirements.txt" (
    echo.
    echo Installing dependencies from requirements.txt ...
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Dependency installation failed.
        pause
        exit /b 1
    )
) else (
    echo WARNING: requirements.txt was not found in:
    echo %~dp0
    echo.
)

REM Start the Streamlit application inside the virtual environment.
if not exist "app.py" (
    echo ERROR: app.py was not found in:
    echo %~dp0
    pause
    exit /b 1
)

echo.
echo Starting Streamlit ...
"%VENV_PYTHON%" -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo The application stopped with an error.
    echo Check that streamlit is listed in requirements.txt.
    pause
    exit /b 1
)

pause
endlocal
