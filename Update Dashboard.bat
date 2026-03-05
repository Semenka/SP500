@echo off
REM Windows: Double-click this file to update the S&P 500 Dashboard
cd /d "%~dp0"
echo Updating S&P 500 Dashboard...
echo This will take ~10-15 minutes for 503 companies.
echo.
python update_sp500_dashboard.py
echo.
echo Done!
pause
