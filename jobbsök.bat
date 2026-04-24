@echo off
chcp 65001 >nul
title AMS JobbSök

cd /d "%~dp0"
python run.py
pause
