# Windows Remote Control Dashboard

This project is a self-hosted dashboard for remotely monitoring and controlling a Windows machine. It exposes a simple web interface for common administrative tasks such as taking screenshots, adjusting system settings, viewing live performance data and browsing files.

**Administrator privileges are recommended** when running the server to enable hardware controls like brightness and volume adjustment.

## Setup

1. Ensure Python 3.10+ is installed on the host machine.
2. Install dependencies:

```bash
pip install flask psutil pillow pynput opencv-python sounddevice soundfile GPUtil wmi pycaw comtypes requests werkzeug
```

3. Start the application:

```bash
python app.py
```

Then open `http://localhost:5000` in a browser to access the dashboard.

## Available Endpoints

These HTTP endpoints provide the dashboard functionality and can also be called programmatically:

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET`  | `/`  | Dashboard HTML interface |
| `GET`  | `/status` | JSON with CPU, RAM, battery, disk usage, IP information and more |
| `POST` | `/action/<cmd>` | Power actions (`lock`, `restart`, `shutdown`, `sleep`, `hibernate`) |
| `GET/POST` | `/brightness` | Get or set display brightness |
| `GET/POST` | `/volume` | Get or set master volume |
| `GET` | `/screenshot` | Current screen capture (PNG) |
| `GET` | `/webcam` | Snapshot from default webcam (JPEG) |
| `GET` | `/mic` | Capture audio for 5 seconds (WAV) |
| `POST` | `/keylogger/<start|stop>` | Control the keylogger |
| `GET` | `/keylogs` | Retrieve recent keystrokes |
| `GET` | `/processes` | List running processes |
| `POST` | `/process/<pid>/kill` | Terminate a process |
| `POST` | `/process/start` | Start a new process (JSON body with `cmd`) |
| `GET` | `/files?path=<path>` | List files or download a file |
| `GET` | `/download?path=<path>` | Download the specified file |
| `POST` | `/upload` | Upload a file (multipart form) |

## Security Considerations

The server exposes powerful system controls. Run it only on trusted networks and restrict access with a firewall or VPN. Administrator privileges allow deeper integration (brightness control, volume control, process management) but also increase risk if the service is exposed. Use strong passwords or additional authentication if deploying beyond a local test environment.

