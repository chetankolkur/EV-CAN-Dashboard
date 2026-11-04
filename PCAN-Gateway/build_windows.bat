@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Run run_windows.bat once first, or create the environment.
    pause
    exit /b 1
)
call .venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --noconfirm --clean --windowed --name EV-CAN-PCAN-Gateway src\gateway.py
echo.
echo Build finished. Output: dist\EV-CAN-PCAN-Gateway\
pause
