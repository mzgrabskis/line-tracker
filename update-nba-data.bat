@echo off
REM NBA.com often blocks GitHub's servers. Run this on your own PC to build fresh NBA data,
REM then upload docs\data\nba.json to your GitHub repo (Add file > Upload files).
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -m pip install -r requirements.txt
%PY% build_data.py nbacom
echo.
echo Done. Now upload the file docs\data\nba.json to your GitHub repository (inside docs\data).
explorer docs\data
pause
