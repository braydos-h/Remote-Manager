# Windows Remote Checkup

A Flask-based dashboard providing remote control and monitoring features for Windows systems.

## Features
- Keylogger (start/stop, fetch logs)
- Webcam snapshot and screenshots
- Microphone recordings
- CPU and RAM charts
- Disk usage overview
- Process manager (list, kill, start)
- File browser (download, upload)
- Power actions: lock, sleep, hibernate, restart, shutdown
- Brightness and volume controls

## Installation
1. Ensure Python 3.10+ is installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   python app.py
   ```
   Then browse to `http://localhost:5000`.

Administrator privileges may be required for some functionality.
