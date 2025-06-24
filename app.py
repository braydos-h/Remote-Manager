# app.py
"""
Extended local Flask app for remote device control, monitoring, and multimedia capture.

⚠️  Windows‑only proof‑of‑concept.  *Run as Administrator.*

### Features
- Keylogger (toggle start/stop, view logs)
- Webcam snapshot
- 5‑second microphone recording
- CPU/RAM live graphs (Chart.js)
- Disk space overview
- Process manager (list, kill, launch)
- File browser (list dir, download, upload)
- Sleep/Hibernate
- Brightness + Volume controls

### Quick start
```bash
pip install flask psutil pillow pynput opencv-python sounddevice soundfile GPUtil wmi pycaw comtypes
python app.py
``` 
Then open <http://localhost:5000> in your browser.
"""

import os, io, ctypes, subprocess, re, threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, Response, request, send_file, abort
import psutil
from PIL import ImageGrab
from pynput import keyboard

# Optional deps
try:
    import cv2
except ImportError:
    cv2 = None
try:
    import sounddevice as sd, soundfile as sf
except ImportError:
    sd = sf = None
try:
    import GPUtil
except ImportError:
    GPUtil = None
try:
    import wmi  # for brightness
except ImportError:
    wmi = None

app = Flask(__name__)

######################################################################
# Utility helpers
######################################################################

def ping_time(host='8.8.8.8'):
    try:
        proc = subprocess.run(['ping', '-n', '1', host], capture_output=True, text=True, timeout=5)
        m = re.search(r'Average = ([0-9]+)ms', proc.stdout)
        return int(m.group(1)) if m else None
    except Exception:
        return None

def internet_up():
    return ping_time() is not None

def is_locked():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    DESKTOP_SWITCHDESKTOP = 0x0100
    hdesk = user32.OpenDesktopW('Default', 0, False, DESKTOP_SWITCHDESKTOP)
    if not hdesk:
        return False
    locked = not bool(user32.SwitchDesktop(hdesk))
    user32.CloseDesktop(hdesk)
    return locked

def gpu_temp():
    if not GPUtil:
        return None
    try:
        gpus = GPUtil.getGPUs()
        return gpus[0].temperature if gpus else None
    except Exception:
        return None

######################################################################
# Keylogger
######################################################################

keylog_buffer = []
keylog_running = False
logger_lock = threading.Lock()
listener = None

def _on_key_press(key):
    with logger_lock:
        keylog_buffer.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}: {key}")

def start_keylogger():
    global listener, keylog_running
    if keylog_running:
        return
    listener = keyboard.Listener(on_press=_on_key_press)
    listener.start()
    keylog_running = True

def stop_keylogger():
    global listener, keylog_running
    if listener:
        listener.stop()
    keylog_running = False

######################################################################
# Flask routes
######################################################################

@app.route('/')
def index():
    return Response(index_html, mimetype='text/html')

@app.route('/status')
def status():
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory().percent
    disks = {p.mountpoint: psutil.disk_usage(p.mountpoint)._asdict() for p in psutil.disk_partitions() if p.fstype}
    return jsonify({
        'internet': internet_up(),
        'locked': is_locked(),
        'battery': psutil.sensors_battery()._asdict() if psutil.sensors_battery() else None,
        'ping': ping_time(),
        'gpu_temp': gpu_temp(),
        'cpu': cpu,
        'ram': mem,
        'disk': disks
    })

# Power actions
@app.route('/action/<cmd>', methods=['POST'])
def action(cmd):
    if cmd == 'lock':
        ctypes.windll.user32.LockWorkStation()
    elif cmd in ('restart', 'reboot'):
        subprocess.Popen(['shutdown', '/r', '/t', '0'])
    elif cmd == 'shutdown':
        subprocess.Popen(['shutdown', '/s', '/t', '0'])
    elif cmd == 'sleep':
        subprocess.Popen(['rundll32.exe', 'powrprof.dll,SetSuspendState', '0,1,0'])
    elif cmd == 'hibernate':
        subprocess.Popen(['shutdown', '/h'])
    else:
        return 'Bad command', 400
    return '', 204

# Brightness & volume
@app.route('/brightness', methods=['GET', 'POST'])
def brightness():
    if not wmi:
        abort(501)
    c = wmi.WMI(namespace='wmi')
    methods = c.WmiMonitorBrightnessMethods()[0]
    current = c.WmiMonitorBrightness()[0].CurrentBrightness
    if request.method == 'POST':
        level = int(request.json['level'])
        methods.WmiSetBrightness(level, 0)
        current = level
    return jsonify({'level': current})

@app.route('/volume', methods=['GET', 'POST'])
def volume():
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume = cast(interface, POINTER(IAudioEndpointVolume))
    if request.method == 'POST':
        volume.SetMasterVolumeLevelScalar(float(request.json['level'])/100, None)
    level = int(volume.GetMasterVolumeLevelScalar()*100)
    return jsonify({'level': level})

# Multimedia capture
@app.route('/screenshot')
def screenshot():
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name='screenshot.png')

@app.route('/webcam')
def webcam():
    if not cv2:
        abort(501)
    cam = cv2.VideoCapture(0)
    ret, frame = cam.read()
    cam.release()
    if not ret:
        abort(500)
    _, buf = cv2.imencode('.jpg', frame)
    return send_file(io.BytesIO(buf.tobytes()), mimetype='image/jpeg')

@app.route('/mic')
def mic():
    if not sd:
        abort(501)
    duration = float(request.args.get('sec', 5))
    fs = 44100
    rec = sd.rec(int(duration*fs), samplerate=fs, channels=1)
    sd.wait()
    tmp = io.BytesIO()
    sf.write(tmp, rec, fs, format='WAV')
    tmp.seek(0)
    return send_file(tmp, mimetype='audio/wav', download_name=f'rec_{datetime.now():%Y%m%d_%H%M%S}.wav')

# Keylogger endpoints
@app.route('/keylogger/<cmd>', methods=['POST'])
def keylogger_ctrl(cmd):
    if cmd == 'start':
        start_keylogger()
    elif cmd == 'stop':
        stop_keylogger()
    else:
        return 'Bad cmd', 400
    return '', 204

@app.route('/keylogs')
def get_keylogs():
    with logger_lock:
        return jsonify({'logs': keylog_buffer[-1000:]})

# Processes
@app.route('/processes')
def processes():
    procs = [{'pid': p.pid, 'name': p.name(), 'cpu': p.cpu_percent(None), 'mem': p.memory_percent()} for p in psutil.process_iter(['name'])]
    return jsonify(procs)

@app.route('/process/<int:pid>/kill', methods=['POST'])
def kill_proc(pid):
    try:
        psutil.Process(pid).kill()
        return '', 204
    except psutil.NoSuchProcess:
        abort(404)

@app.route('/process/launch', methods=['POST'])
def launch_proc():
    cmd = request.json['cmd']
    subprocess.Popen(cmd, shell=True)
    return '', 204

# File browser
ROOT = Path.home()

@app.route('/files')
def list_files():
    path = ROOT / request.args.get('path', '')
    if not path.exists() or not path.resolve().is_relative_to(ROOT):
        abort(403)
    items = [{'name': p.name, 'dir': p.is_dir(), 'size': p.stat().st_size, 'mtime': p.stat().st_mtime} for p in path.iterdir()]
    return jsonify(items)

@app.route('/download')
def download():
    path = ROOT / request.args.get('path', '')
    if not path.exists() or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True)

@app.route('/upload', methods=['POST'])
def upload():
    path = ROOT / request.form['path']
    if not path.resolve().is_relative_to(ROOT):
        abort(403)
    file = request.files['file']
    path.parent.mkdir(parents=True, exist_ok=True)
    file.save(path)
    return '', 204

######################################################################
# Inline HTML + JS
######################################################################

index_html = """
<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<title>Remote Control</title>
<link href='https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css' rel='stylesheet'>
<script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
<style>body{background:#f8f9fa}</style>
</head>
<body class='p-4'>
<h1 class='mb-4'>Remote Control Dashboard</h1>
<div class='row'>
  <div class='col-md-4'>
    <div class='card mb-4'>
      <div class='card-header'>System</div>
      <div class='card-body'>
        <p>Internet: <span id='internet'>...</span></p>
        <p>Locked: <span id='locked'>...</span></p>
        <p>Battery: <span id='battery'>...</span></p>
        <p>Ping: <span id='ping'>...</span> ms</p>
        <p>GPU °C: <span id='gpu_temp'>...</span></p>
      </div>
    </div>
    <div class='card mb-4'>
      <div class='card-header'>Controls</div>
      <div class='card-body'>
        <button class='btn btn-sm btn-primary me-1' onclick="act('lock')">Lock</button>
        <button class='btn btn-sm btn-warning me-1' onclick="act
        
