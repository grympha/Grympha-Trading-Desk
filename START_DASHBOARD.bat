@echo off
cd /d "%~dp0"
echo Starting independent BBMA + Confluence analyses and local dashboard...
echo Open http://127.0.0.1:8765 in your browser after startup.
py -3.11 bbma_bot.py --dashboard
pause
