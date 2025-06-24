# app.py
"""
Remote Control Dashboard – v2
============================
Adds:
- Local + public IP addresses
- Clean hardware specs (CPU model, total RAM GB, GPU name, OS build, manufacturer/model if available)
- Tidier UI (separate "Network & Specs" card)

***Run as Administrator*** for low‑level features.

Dependencies:
```bash
pip install flask psutil pillow pynput opencv-python sounddevice soundfile GPUtil wmi pycaw comtypes requests
```
Then:
```bash
python app.py
# → browse http://localhost:5000
```
"""

from __future__ import annotations
import io, ctypes, subprocess, re, threading, json, os, socket, platform, requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from flask import Flask, jsonify, Response, request, send_file, abort
import psutil
from PIL import ImageGrab
try:
    from pynput import keyboard
except ImportError:
    keyboard = None
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
    import wmi  # for brightness on laptops
except ImportError:
    wmi = None
try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except ImportError:
    AudioUtilities = IAudioEndpointVolume = None  # type: ignore

app = Flask(__name__)

###############################################################################
# Helper functions
###############################################################################

def ping_time(host: str = "8.8.8.8") -> int | None:
    try:
        out = subprocess.check_output(["ping", "-n", "1", host], text=True, timeout=5)
        m = re.search(r"Average = ([0-9]+)ms", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None

def internet_up() -> bool:
    return ping_time() is not None

def is_locked() -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    DESKTOP_SWITCHDESKTOP = 0x0100
    hdesk = user32.OpenDesktopW("Default", 0, False, DESKTOP_SWITCHDESKTOP)
    if not hdesk:
        return False
    res = user32.SwitchDesktop(hdesk)
    user32.CloseDesktop(hdesk)
    return not bool(res)

def gpu_temp() -> int | None:
    if not GPUtil:
        return None
    try:
        gpus = GPUtil.getGPUs()
        return gpus[0].temperature if gpus else None
    except Exception:
        return None

def local_ips() -> Dict[str, str]:
    ips: Dict[str, str] = {}
    for iface, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family == socket.AddressFamily.AF_INET and not a.address.startswith("127."):
                ips[iface] = a.address
    return ips

def public_ip(timeout: float = 3.0) -> str | None:
    try:
        return requests.get("https://api.ipify.org", timeout=timeout).text.strip()
    except Exception:
        return None

def specs() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "cpu": platform.processor() or platform.machine(),
        "ram_gb": round(psutil.virtual_memory().total / (1024 ** 3), 1),
        "os": f"{platform.system()} {platform.release()} (build {platform.version()})",
    }
    # Manufacturer / model via WMI if available
    if wmi:
        try:
            cs = wmi.WMI().Win32_ComputerSystem()[0]
            out["manufacturer"] = cs.Manufacturer
            out["model"] = cs.Model
        except Exception:
            pass
    # GPU name via GPUtil
    if GPUtil:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                out["gpu"] = gpus[0].name
        except Exception:
            pass
    return out

###############################################################################
# Keylogger
###############################################################################
_keybuf: list[str] = []
_key_running = False
_klistener = None  # type: ignore
_lock = threading.Lock()

def _on_press(k):
    with _lock:
        _keybuf.append(f"{datetime.now():%H:%M:%S}  {k}")

def keylog_start():
    global _klistener, _key_running
    if keyboard is None or _key_running:
        return
    _klistener = keyboard.Listener(on_press=_on_press)
    _klistener.start()
    _key_running = True

def keylog_stop():
    global _klistener, _key_running
    if _klistener:
        _klistener.stop()
    _key_running = False

###############################################################################
# Brightness / Volume
###############################################################################

def get_brightness() -> int | None:
    if not wmi:
        return None
    return wmi.WMI(namespace="wmi").WmiMonitorBrightness()[0].CurrentBrightness

def set_brightness(level: int):
    if not wmi:
        raise RuntimeError("WMI not available")
    wmi.WMI(namespace="wmi").WmiMonitorBrightnessMethods()[0].WmiSetBrightness(level, 0)

def get_volume() -> int | None:
    if not AudioUtilities:
        return None
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)  # type: ignore
    vol = cast(interface, POINTER(IAudioEndpointVolume))
    return int(vol.GetMasterVolumeLevelScalar() * 100)

def set_volume(level: int):
    if not AudioUtilities:
        raise RuntimeError("pycaw not available")
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)  # type: ignore
    vol = cast(interface, POINTER(IAudioEndpointVolume))
    vol.SetMasterVolumeLevelScalar(level / 100, None)

###############################################################################
# Flask routes
###############################################################################
@app.route("/")
def root():
    return Response(INDEX_HTML, mimetype="text/html")

@app.route("/status")
def status():
    batt = psutil.sensors_battery()
    disks = {
        p.mountpoint: {
            "total": psutil.disk_usage(p.mountpoint).total,
            "used": psutil.disk_usage(p.mountpoint).used,
            "free": psutil.disk_usage(p.mountpoint).free,
        }
        for p in psutil.disk_partitions() if p.fstype
    }
    return jsonify(
        internet=internet_up(),
        ping=ping_time(),
        locked=is_locked(),
        cpu=psutil.cpu_percent(interval=0.1),
        ram=psutil.virtual_memory().percent,
        gpu_temp=gpu_temp(),
        battery=batt._asdict() if batt else None,
        disk=disks,
        local_ips=local_ips(),
        public_ip=public_ip(),
        specs=specs(),
    )

@app.route("/action/<cmd>", methods=["POST"])
def power(cmd):
    table = {
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
        "restart": ["shutdown", "/r", "/t", "0"],
        "shutdown": ["shutdown", "/s", "/t", "0"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "hibernate": ["shutdown", "/h"],
    }
    if cmd not in table:
        abort(400)
    subprocess.Popen(table[cmd])
    return "", 204

@app.route("/brightness", methods=["GET", "POST"])
def bright():
    if request.method == "POST":
        set_brightness(int(request.json["level"]))
    return jsonify(level=get_brightness())

@app.route("/volume", methods=["GET", "POST"])
def vol():
    if request.method == "POST":
        set_volume(int(request.json["level"]))
    return jsonify(level=get_volume())

@app.route("/screenshot")
def screenshot():
    img = ImageGrab.grab()
    b = io.BytesIO()
    img.save(b, format="PNG"); b.seek(0)
    return send_file(b, mimetype="image/png")

@app.route("/webcam")
def webcam():
    if not cv2:
        abort(501)
    cam = cv2.VideoCapture(0)
    ret, frame = cam.read(); cam.release()
    if not ret:
        abort(500)
    _, buf = cv2.imencode(".jpg", frame)
    return send_file(io.BytesIO(buf.tobytes()), mimetype="image/jpeg")

@app.route("/mic")
def mic():
    if not sd:
        abort(501)
    dur = float(request.args.get("sec", 5))
    fs = 44100
    rec = sd.rec(int(dur*fs), samplerate=fs, channels=1); sd.wait()
    tmp = io.BytesIO(); sf.write(tmp, rec, fs, format="WAV"); tmp.seek(0)
    return send_file(tmp, mimetype="audio/wav")

@app.route("/keylogger/<cmd>", methods=["POST"])
def keylog(cmd):
    if cmd == "start": keylog_start()
    elif cmd == "stop": keylog_stop()
    else: abort(400)
    return "", 204

@app.route("/keylogs")
def keylogs():
    return jsonify(_keybuf[-1000:])

# Process, file browser routes unchanged – omitted for brevity
# … (retain previous /processes, /process/* and /files, /download, /upload code)

###############################################################################
# Front‑end (Bootstrap + Chart.js)
###############################################################################
INDEX_HTML = r"""
<!DOCTYPE html><html lang='en'><head>
<meta charset='UTF-8'><title>Remote Dashboard</title>
<link href='https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css' rel='stylesheet'>
<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js'></script>
<style>body{background:#f8f9fa}.card{margin-bottom:1rem}</style></head>
<body><div class='container py-3'>
<h1 class='mb-3'>Remote Control Dashboard</h1>
<div class='row'>
  <div class='col-lg-6'>
    <div class='card'><div class='card-header'>Status</div><div class='card-body' id='stat'></div></div>
    <div class='card'><div class='card-header'>Network & Specs</div><div class='card-body' id='net'></div></div>
  </div>
  <div class='col-lg-6'>
    <canvas id='cpuC' height='120'></canvas>
    <canvas id='ramC' height='120'></canvas>
  </div>
</div>
<script>
const cpuD={labels:[],datasets:[{label:'CPU %',data:[],tension:.3}]};
const ramD={labels:[],datasets:[{label:'RAM %',data:[],tension:.3}]};
const cpuChart=new Chart(document.getElementById('cpuC'),{type:'line',data:cpuD});
const ramChart=new Chart(document.getElementById('ramC'),{type:'line',data:ramD});
async function tick(){
  const r=await fetch('/status');const s=await r.json();
  const t=new Date().toLocaleTimeString();
  if(cpuD.labels.length>30){cpuD.labels.shift();cpuD.datasets[0].data.shift();ramD.labels.shift();ramD.datasets[0].data.shift();}
  cpuD.labels.push(t);ramD.labels.push(t);
  cpuD.datasets[0].data.push(s.cpu);ramD.datasets[0].data.push(s.ram);
  cpuChart.update();ramChart.update();
  document.getElementById('stat').innerHTML=`<b>Internet:</b> ${s.internet} | ping ${s.ping||'n/a'} ms<br>
  <b>Locked:</b> ${s.locked}<br>
  <b>CPU / RAM:</b> ${s.cpu}% / ${s.ram}%<br>
  <b>GPU Temp:</b> ${s.gpu_temp||'n/a'}°C<br>
  <b>Battery:</b> ${s.battery? s.battery.percent+'% '+(s.battery.power_plugged?'⚡':'🔋'):'n/a'}`;
  const ipList=Object.entries(s.local_ips).map(([i,a])=>`${i}: ${a}`).join(' | ');
  const specs=`${s.specs.manufacturer||''} ${s.specs.model||''}<br>CPU: ${s.specs.cpu}<br>RAM: ${s.specs.ram_gb} GB<br>GPU: ${s.specs.gpu||'—'}<br>OS: ${s.specs.os}`;
  document.getElementById('net').innerHTML=`<b>Local IPs:</b> ${ipList}<br><b>Public IP:</b> ${s.public_ip||'n/a'}<hr>${specs}`;
}
setInterval(tick,3000);tick();
</script>
</div></body></html>
"""
###############################################################################
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
