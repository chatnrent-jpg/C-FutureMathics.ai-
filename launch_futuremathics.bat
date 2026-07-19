@echo off
title FutureMathics — MES Paper Session
cd /d C:\FutureMathics.ai
set PYTHONPATH=C:\FutureMathics.ai
set PYTHONUNBUFFERED=1
set FM_IGNORE_MARKET_HOURS=1
echo Starting FutureMathics MES orchestrator (paper, 24/7 cycles)...
python scripts\run_daily_session.py
pause
