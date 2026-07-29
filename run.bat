@echo off
call venv\Scripts\activate.bat 2>nul
:loop
python main.py
goto loop
