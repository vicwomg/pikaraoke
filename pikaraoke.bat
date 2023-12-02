@echo off

:: Activate the virtual environment
call .venv\Scripts\activate

:: Pass remaining arguments to the command
shift

:: Run the command with the remaining arguments
python app.py %*