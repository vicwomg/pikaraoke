@echo off

:start
echo Are you sure you want to setup PiKaraoke? (y/n):
set /p confirm=

if /i "%confirm%" == "y" goto setup
if /i "%confirm%" == "n" goto end

:setup

echo
echo "*** PULLING LATEST PIKARAOKE CODE ***."
git pull

echo
echo "*** CREATING PYTHON VIRTUAL ENVIRONMENT ***"
python3 -m venv .venv
call .venv\Scripts\activate

echo
echo "*** INSTALLING PYTHON DEPENDENCIES ***"
pip install -r requirements.txt

echo
echo "*** DONE ***"
echo "Run PiKaraoke with: ./pikaraoke.bat <args>"
echo

:end


