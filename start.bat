@echo off
cd /d "%~dp0"
if not defined ASTRO_DATA_ROOT set "ASTRO_DATA_ROOT=%CD%\data"
if not exist "%ASTRO_DATA_ROOT%" mkdir "%ASTRO_DATA_ROOT%"
set PYTHONPATH=%CD%
set PYTHONHOME=
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.10+ is required and must be on PATH.
  pause
  exit /b 1
)
echo AstroTranscript  http://127.0.0.1:8787
echo Data: %ASTRO_DATA_ROOT%
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8787
pause
