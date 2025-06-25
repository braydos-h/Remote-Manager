import sys
import types
import importlib.util
import socket
from pathlib import Path
import pytest


def load_app():
    # create stub psutil module
    psutil = types.ModuleType('psutil')

    class Battery:
        percent = 50
        power_plugged = True

        def _asdict(self):
            return {'percent': self.percent, 'power_plugged': self.power_plugged}

    def sensors_battery():
        return Battery()

    def disk_partitions():
        part = types.SimpleNamespace(mountpoint='/', fstype='ext4')
        return [part]

    def disk_usage(path):
        return types.SimpleNamespace(total=100, used=50, free=50)

    def cpu_percent(interval=None):
        return 10

    def virtual_memory():
        return types.SimpleNamespace(percent=20, total=1024**3)

    def net_if_addrs():
        addr = types.SimpleNamespace(family=socket.AddressFamily.AF_INET, address='192.168.0.2')
        return {'eth0': [addr]}

    def process_iter(attrs):
        proc = types.SimpleNamespace(info={'pid': 1, 'name': 'proc', 'username': 'u',
                                           'cpu_percent': 0.0, 'memory_percent': 0.1})
        return [proc]

    class NoSuchProcess(Exception):
        pass

    psutil.sensors_battery = sensors_battery
    psutil.disk_partitions = disk_partitions
    psutil.disk_usage = disk_usage
    psutil.cpu_percent = cpu_percent
    psutil.virtual_memory = virtual_memory
    psutil.net_if_addrs = net_if_addrs
    psutil.process_iter = process_iter
    psutil.NoSuchProcess = NoSuchProcess

    # stub requests module
    requests = types.ModuleType('requests')

    class Resp:
        text = '8.8.8.8'

    def get(url, timeout=3.0):
        return Resp()

    requests.get = get

    # stub Pillow ImageGrab
    pil = types.ModuleType('PIL')
    imagegrab = types.ModuleType('ImageGrab')
    imagegrab.grab = lambda: None
    pil.ImageGrab = imagegrab

    # stub minimal Flask
    flask = types.ModuleType('flask')

    class Flask:
        def __init__(self, name):
            self.name = name

        def route(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator

    def jsonify(obj=None, **kwargs):
        return obj if obj is not None else kwargs

    flask.Flask = Flask
    flask.jsonify = jsonify
    flask.Response = dict
    flask.request = types.SimpleNamespace(args={})

    def abort(code):
        raise RuntimeError(code)

    flask.abort = abort
    flask.send_file = lambda *a, **k: {}

    # stub werkzeug secure_filename
    werkzeug = types.ModuleType('werkzeug')
    utils = types.ModuleType('werkzeug.utils')
    def secure_filename(name):
        return name
    utils.secure_filename = secure_filename
    werkzeug.utils = utils

    sys.modules['psutil'] = psutil
    sys.modules['requests'] = requests
    sys.modules['PIL'] = pil
    sys.modules['PIL.ImageGrab'] = imagegrab
    sys.modules['flask'] = flask
    sys.modules['werkzeug'] = werkzeug
    sys.modules['werkzeug.utils'] = utils

    if 'app_module' in sys.modules:
        del sys.modules['app_module']
    spec = importlib.util.spec_from_file_location(
        'app_module',
        Path(__file__).resolve().parents[1] / 'main' / 'main.py'
    )
    app_mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(app_mod)
    # override platform-specific helpers
    app_mod.is_locked = lambda: False
    return app_mod


@pytest.fixture()
def app_module():
    return load_app()


def test_status(app_module):
    data = app_module.status()
    assert 'cpu' in data
    assert 'ram' in data


def test_processes(app_module):
    data = app_module.processes()
    assert isinstance(data, list)
    assert data and data[0]['pid'] == 1


def test_files(app_module, tmp_path):
    f = tmp_path / 'test.txt'
    f.write_text('hi')
    # patch request args for the listing function
    app_module.request.args = {'path': str(tmp_path)}
    listing = app_module.listing()
    assert listing['path'] == str(tmp_path)
    assert any(item['name'] == 'test.txt' for item in listing['items'])
