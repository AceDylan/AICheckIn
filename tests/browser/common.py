# -*- coding: utf-8 -*-
"""真实浏览器回归的公共部分：自己起一个隔离实例，只访问它，其余请求一律掐掉。

- 服务：用当前工作树里的 app.py，在 127.0.0.1 的空闲端口上起一个子进程；数据目录是临时目录，
  内容从 seed/ 里的合成数据拷过去，和任何真实部署无关。
- 管理密码：每次运行现生成，只活在本进程和它起的子进程的环境变量里，不落盘、不打印。
- 图标：/api/favicon 在浏览器侧用本地现画的 PNG 顶替，服务端因此也不会替页面去抓外站。

依赖只有 playwright（`pip install playwright`，浏览器用 `playwright install chromium`，
或用 BH_CHROME 指到一份现成的 Chromium）。可选环境变量：
  BH_CHROME          Chromium 可执行文件路径
  BH_VERIFY_SHOTS    截图目录（默认在临时目录里，跑完即删）
  BH_VERIFY_RESULTS  verify.py 把逐项结果另存成 JSON 的路径
"""
import atexit
import glob
import hashlib
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

from playwright.sync_api import sync_playwright  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SEED = os.path.join(HERE, "seed")
WORK = tempfile.mkdtemp(prefix="bh-verify-")
DATA = os.path.join(WORK, "data")
SHOTS = os.environ.get("BH_VERIFY_SHOTS") or os.path.join(WORK, "shots")
PASSWORD = secrets.token_urlsafe(18)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
BASE = "http://127.0.0.1:%d" % PORT
_SERVERS = []


def _stop_servers():
    for proc in _SERVERS:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    shutil.rmtree(WORK, ignore_errors=True)


atexit.register(_stop_servers)


def seed_data(data_dir=DATA):
    """把数据目录还原成 seed/ 里那份合成数据（服务每次请求都读盘，不用重启）。"""
    # 下面会清空目录里的文件：只许动本次运行自己的临时目录，传错路径宁可直接报错。
    if os.path.commonpath([os.path.realpath(data_dir), os.path.realpath(WORK)]) != os.path.realpath(WORK):
        raise ValueError("seed_data 只能用在本次运行的临时目录里：%s" % data_dir)
    os.makedirs(data_dir, exist_ok=True)
    for name in os.listdir(data_dir):
        path = os.path.join(data_dir, name)
        if os.path.isfile(path):
            os.remove(path)
    shutil.copy(os.path.join(SEED, "config.json"), os.path.join(data_dir, "config.json"))
    shutil.copy(os.path.join(SEED, "todos.json"), os.path.join(data_dir, "todos.json"))


def start_server(port=PORT, data_dir=DATA, password=PASSWORD):
    """起一个隔离实例并等它能应答。password 传空串 = 不设管理密码的实例。"""
    seed_data(data_dir)
    os.makedirs(SHOTS, exist_ok=True)
    env = dict(os.environ, GYQD_CONFIG_FILE=os.path.join(data_dir, "config.json"), GYQD_ADMIN_PASSWORD=password, GYQD_SCHEDULER="0")
    boot = ("import app; app.app.config['TEMPLATES_AUTO_RELOAD'] = True; app.app.jinja_env.auto_reload = True; "
            "app.app.run(host='127.0.0.1', port=%d, threaded=True)" % port)
    log = open(os.path.join(WORK, "server-%d.log" % port), "w")
    proc = subprocess.Popen([sys.executable, "-c", boot], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    _SERVERS.append(proc)
    url = "http://127.0.0.1:%d/" % port
    for _ in range(100):
        if proc.poll() is not None:
            break
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return url
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("测试服务没有起来，日志：\n" + open(log.name).read()[-2000:])


def launch(p):
    """优先用 BH_CHROME；没给就用 playwright 自带的；版本对不上时退到本机缓存里任意一份 Chromium。"""
    path = os.environ.get("BH_CHROME")
    if path:
        return p.chromium.launch(executable_path=path, args=["--no-sandbox"])
    try:
        return p.chromium.launch(args=["--no-sandbox"])
    except Exception:
        found = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
        if not found:
            raise
        return p.chromium.launch(executable_path=found[-1], args=["--no-sandbox"])


def _png(size, pixel):
    """最小的 RGBA PNG 编码器：pixel(x, y) → (r, g, b, a)。免得为了几张假图标再多装一个图像库。"""
    raw = b"".join(b"\x00" + bytes(c for x in range(size) for c in pixel(x, y)) for y in range(size))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


_ICONS = {}


def fake_icon(url):
    """按网址哈希出一张 64px 图标：多数是彩色实底，少数是透明底的白 / 黑 logo（覆盖图标底板的三种情况）。"""
    h = hashlib.sha256(url.encode()).digest()
    kind = h[0] % 8
    key = (min(kind, 2), h[1:4] if kind > 1 else b"")
    if key not in _ICONS:
        clear, white, dark = (0, 0, 0, 0), (255, 255, 255, 255), (17, 20, 28, 255)
        color = tuple(60 + b % 170 for b in h[1:4]) + (255,)

        def disc(x, y, r):
            return (x - 31.5) ** 2 + (y - 31.5) ** 2 <= r * r
        if kind == 0:
            _ICONS[key] = _png(64, lambda x, y: white if disc(x, y, 21) else clear)
        elif kind == 1:
            _ICONS[key] = _png(64, lambda x, y: dark if 13 <= x <= 50 and 13 <= y <= 50 else clear)
        else:
            _ICONS[key] = _png(64, lambda x, y: white if disc(x, y, 13) else color)
    return _ICONS[key]


def new_context(browser, width, height, mobile=False, unlocked=True, base=BASE, **kw):
    ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2 if mobile else 1,
                              is_mobile=mobile, has_touch=mobile, **kw)
    ctx.route("**/api/favicon*", lambda route: route.fulfill(status=200, content_type="image/png", body=fake_icon(route.request.url)))
    # 兜底：任何离开本机测试实例的请求一律掐掉（页面本来也不该发）。
    ctx.route(lambda url: not url.startswith("http://127.0.0.1:") and not url.startswith("data:"), lambda route: route.abort())
    if unlocked:
        resp = ctx.request.post(base + "/api/auth", headers={"X-Admin-Password": PASSWORD})
        assert resp.ok, resp.status
    return ctx


def api(ctx, method, path, data=None, base=BASE):
    resp = ctx.request.fetch(base + path, method=method, data=data, headers={"Content-Type": "application/json"})
    return resp.status, resp.json()
