@echo off
echo ========================================================
echo Starting EcoTwin Campus Smart Waste Digital Twin Server
echo ========================================================
call "D:\DSU-hackathon\.venv\Scripts\activate.bat"
cd /d "D:\DSU-hackathon\ecotwin\src"
python app.py
pause
