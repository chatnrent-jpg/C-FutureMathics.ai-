@echo off
title FutureMathics Dashboard Launcher
cd /d C:\FutureMathics.ai
set PYTHONPATH=C:\FutureMathics.ai
set PYTHONUNBUFFERED=1
set FM_IGNORE_MARKET_HOURS=1

tasklist /FI "WINDOWTITLE eq FutureMathics Engine*" 2>nul | find /I "cmd.exe" >nul 2>&1
if errorlevel 1 (
    echo Starting MES orchestrator...
    start "FutureMathics Engine" /MIN cmd /k "cd /d C:\FutureMathics.ai && set PYTHONPATH=C:\FutureMathics.ai && set FM_IGNORE_MARKET_HOURS=1 && python scripts\run_daily_session.py"
)

netstat -an | findstr ":8502" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 goto OPEN_BROWSER

echo Starting FutureMathics dashboard on port 8502...
start "FutureMathics Dashboard" /MIN cmd /k "cd /d C:\FutureMathics.ai && set PYTHONPATH=C:\FutureMathics.ai && python -m streamlit run scripts\sandbox_streamlit.py --server.port 8502 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false"

set /a WAIT=0
:WAIT_LOOP
ping 127.0.0.1 -n 2 >nul
netstat -an | findstr ":8502" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 goto OPEN_BROWSER
set /a WAIT+=1
if %WAIT% LSS 25 goto WAIT_LOOP

echo.
echo Dashboard did not start in time. Check the "FutureMathics Dashboard" window.
pause
exit /b 1

:OPEN_BROWSER
start "" http://127.0.0.1:8502
exit /b 0
