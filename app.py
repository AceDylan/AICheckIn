#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gyqd 签到逻辑的 Web 封装。

设计原则：
- 复用 gyqd.py 的 run_all / run_one / 格式化函数，不重写签到逻辑（DRY）。
- 配置持久化到挂载的 config.json，支持页面增删改查。
- 签到动作开放（一键直签）；配置写入 / 查看真实 token / 定时设置受可选管理密码保护。
- 内置每日定时自动签到 + 运行历史，做到无人值守。
"""

import datetime
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import stat
import string
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse
from pathlib import Path

from flask import Flask, jsonify, render_template, request

# 镜像内 gyqd.py 与 app.py 同级；本地开发时回退到仓库根目录。
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import gyqd  # noqa: E402

app = Flask(__name__)

# 配置文件路径：默认指向容器内可写数据目录的挂载点。
CONFIG_FILE = os.environ.get("GYQD_CONFIG_FILE", "/app/data/config.json")
# 数据目录（历史记录与配置同目录），需对容器运行用户可写。
DATA_DIR = Path(CONFIG_FILE).resolve().parent
HISTORY_FILE = str(DATA_DIR / "history.json")
# 历史记录保留条数上限。
HISTORY_CAP = 50
# 配置文件按天留档的保留天数（0 = 只保留 .bak，不留每日快照）。
CONFIG_BACKUP_DAYS = max(0, int(os.environ.get("GYQD_BACKUP_DAYS", "7")))
# 每日备份文件名里的日期段，用于识别与清理归档（避免误删同目录下其它 .bak）。
_STAMP_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 指标快照文件：持久化每组配置上一次获取到的签到奖励/钱包余额/已用额度/请求数。
# 以 base_url|user_id 为键，独立于 configs 数组索引，避免增删改导入打乱对齐。
METRICS_FILE = str(DATA_DIR / "metrics.json")

# 管理密码：保护配置写入 / 查看真实 token / 定时设置。留空表示完全开放（公网部署强烈建议设置）。
ADMIN_PASSWORD = os.environ.get("GYQD_ADMIN_PASSWORD", "").strip()
# 是否启用后台定时调度线程。
SCHEDULER_ENABLED = os.environ.get("GYQD_SCHEDULER", "1") == "1"

# 私密模式：连「只读浏览」也要先解锁。默认关闭——首页本来就是设计给人直接打开的
# 收藏导航页。公网部署若不想让路人看到自建服务的地址与站点清单，设 GYQD_PRIVATE=1。
PRIVATE_MODE = os.environ.get("GYQD_PRIVATE", "0") == "1"

# 定时签到的当日补签：失败的账号隔一段时间再试几次。设 0 关闭。
SCHEDULE_RETRY_LIMIT = max(0, int(os.environ.get("GYQD_RETRY_LIMIT", "3")))
SCHEDULE_RETRY_DELAY_MIN = max(1, int(os.environ.get("GYQD_RETRY_DELAY_MINUTES", "30")))

# 请求体上限：没有上限时，一个几百 MB 的 JSON 就能把单 worker 的内存吃光。
# 导入整份配置是这里最大的合法载荷，4 MB 绰绰有余。
MAX_REQUEST_BYTES = max(64 * 1024, int(os.environ.get("GYQD_MAX_REQUEST_BYTES", str(4 * 1024 * 1024))))
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

# 建议的最短管理密码长度：低于此值在启动时告警（不阻止启动，内网/本地部署仍可自便）。
MIN_ADMIN_PASSWORD_LEN = 12

if not ADMIN_PASSWORD:
    sys.stderr.write(
        "[gyqd-web] 警告：未设置 GYQD_ADMIN_PASSWORD。"
        "任何访问者都能编辑配置、查看真实 token、执行签到并读取运行历史。"
        "公网部署请立即设置（见 SECURITY.md）\n"
    )
    if PRIVATE_MODE:
        # 私密模式靠管理密码兜底，没有密码就无从校验，只能当作未开启。
        sys.stderr.write("[gyqd-web] 警告：GYQD_PRIVATE=1 但未设置管理密码，私密模式不生效\n")
elif len(ADMIN_PASSWORD) < MIN_ADMIN_PASSWORD_LEN:
    sys.stderr.write(
        "[gyqd-web] 警告：GYQD_ADMIN_PASSWORD 短于 {0} 位，公网上容易被爆破。"
        "建议改用 python3 -c \"import secrets; print(secrets.token_urlsafe(24))\" 生成的随机口令\n"
        .format(MIN_ADMIN_PASSWORD_LEN)
    )

# 并发保护：配置写、历史写、签到执行各一把锁。
_store_lock = threading.Lock()
_history_lock = threading.Lock()
_run_lock = threading.Lock()
_metrics_lock = threading.Lock()
# 定时刷新用独立的锁：它和签到互不相干，没必要互相等待。
_refresh_lock = threading.Lock()
_sched_thread = None

# =========================
# 配置存取（持久化层）
# =========================

# 站点看板自动刷新：可选的间隔（分钟）。给固定档位而不是任意数字，
# 避免有人填个 1 分钟把被监控的站点打爆。
REFRESH_INTERVALS = (15, 30, 60, 120, 360, 720, 1440)
DEFAULT_REFRESH_INTERVAL = 60


def normalize_refresh(raw):
    """归一化 refresh 配置：{enabled, interval_minutes, last_run_time, last_run_ts}。"""
    cfg = dict(raw or {}) if isinstance(raw, dict) else {}
    try:
        interval = int(cfg.get("interval_minutes") or DEFAULT_REFRESH_INTERVAL)
    except (TypeError, ValueError):
        interval = DEFAULT_REFRESH_INTERVAL
    if interval not in REFRESH_INTERVALS:
        interval = DEFAULT_REFRESH_INTERVAL
    try:
        last_ts = float(cfg.get("last_run_ts") or 0)
    except (TypeError, ValueError):
        last_ts = 0.0
    return {
        "enabled": bool(cfg.get("enabled")),
        "interval_minutes": interval,
        "last_run_time": str(cfg.get("last_run_time") or ""),
        "last_run_ts": last_ts,
    }


def read_store():
    """读取完整配置存储，归一化为 {configs, proxy_url, schedule, refresh}。

    config.json 兼容两种历史格式：数组 或 {proxy_url, configs}。
    文件缺失时回退到 gyqd.CONFIGS（仅占位，token 为假）。
    """
    path = Path(CONFIG_FILE)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise RuntimeError("读取配置文件失败：{0}".format(exc))
        if isinstance(raw, list):
            store = {"configs": list(raw), "proxy_url": "", "schedule": {}, "refresh": {},
                     "bookmarks": [], "link_groups": None}
        elif isinstance(raw, dict):
            store = {
                "configs": list(raw.get("configs") or []),
                "proxy_url": str(raw.get("proxy_url") or "").strip(),
                "schedule": dict(raw.get("schedule") or {}),
                "refresh": dict(raw.get("refresh") or {}),
                "bookmarks": list(raw.get("bookmarks") or []),
                # None 表示旧数据尚无此键（读取时合成默认分组）；[] 表示用户已清空。
                "link_groups": raw.get("link_groups"),
            }
        else:
            raise RuntimeError("config.json 格式应为数组或对象")
    else:
        store = {"configs": list(gyqd.CONFIGS), "proxy_url": "", "schedule": {}, "refresh": {},
                 "bookmarks": [], "link_groups": None}
    store.setdefault("configs", [])
    store.setdefault("proxy_url", "")
    store.setdefault("schedule", {})
    store["refresh"] = normalize_refresh(store.get("refresh"))
    store.setdefault("bookmarks", [])  # 仅收藏不签到的站点：[{name, url, fields}]
    # 统一为多字段结构：旧数据的单一 balance_config 合成为「余额」金额字段，并维持旧键镜像。
    store["bookmarks"] = [normalize_bookmark(b) for b in store["bookmarks"] if isinstance(b, dict)]
    # 收藏库子页面（自建服务 / 常用网站 / AI 服务 …）：键缺失时合成默认分组，写入时才落盘。
    store["link_groups"] = normalize_link_groups(store.get("link_groups"))
    return store


def _write_text_atomic(path, text):
    """先写同目录临时文件再原子替换；替换不可用时退回就地写入。

    config.json 装着全部凭据，就地截断写入一旦中途失败（磁盘写满、容器被杀），
    留下的就是半截 JSON，下次启动整份配置都读不出来。同一文件系统内的 rename
    要么全成要么全不成，配合 fsync 才能保证「要么是旧的完整内容，要么是新的完整内容」。

    少数部署把 config.json 本身做成 bind mount（而不是挂它所在的目录），这时
    rename 会失败（EBUSY / EXDEV / EINVAL），回退到就地写入，行为与从前一致。
    """
    # 新建时用 0600（里面是凭据）；文件已存在则沿用它的权限，不擅自改变部署现状。
    try:
        mode = stat.S_IMODE(os.stat(str(path)).st_mode)
    except OSError:
        mode = 0o600
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, str(path))
        tmp_path = None
    except OSError:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        path.write_text(text, encoding="utf-8")  # 失败则由调用方转成 RuntimeError
        return
    # 目录项也刷一次，确保 rename 本身落盘（掉电后新文件名才真的存在）。
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _daily_backup_name(path, day):
    return path.with_name("{0}.{1}.bak".format(path.name, day))


def _rotate_daily_backup(path, previous_text):
    """每天第一次写入时，把「今天改动之前」的内容另存一份，最多保留 N 天。

    `.bak` 只保留上一次写入前的内容：误删一个分组之后又随手改了两下，
    好数据就被冲掉了，而这类问题往往隔天才发现。按天的回溯点才救得回来。
    """
    if CONFIG_BACKUP_DAYS <= 0:
        return
    today = _daily_backup_name(path, _today_str())
    if today.exists():
        return  # 今天已经留过快照，后续写入不再覆盖它
    _write_text_atomic(today, previous_text)
    # 只保留最近 N 份，其余删掉；文件名里带日期，按名字排序即按时间排序。
    prefix, suffix = path.name + ".", ".bak"
    snapshots = sorted(
        p for p in path.parent.glob(path.name + ".*.bak")
        if _STAMP_DAY_RE.match(p.name[len(prefix):-len(suffix)] or "")
    )
    for stale in snapshots[:-CONFIG_BACKUP_DAYS]:
        try:
            stale.unlink()
        except OSError:
            pass


def write_store(store):
    """持久化配置：写前自检 JSON、留备份，再原子替换目标文件。"""
    text = json.dumps(store, ensure_ascii=False, indent=2)
    json.loads(text)  # 写前自检，确保可往返。
    path = Path(CONFIG_FILE)
    with _store_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                try:
                    previous = path.read_text(encoding="utf-8")
                    _write_text_atomic(path.with_name(path.name + ".bak"), previous)
                    _rotate_daily_backup(path, previous)
                except OSError:
                    pass  # 备份失败不该挡住正常保存
            _write_text_atomic(path, text)
        except OSError as exc:
            raise RuntimeError(
                "配置写入失败（请检查挂载是否只读、容器用户对数据目录是否有写权限）：{0}".format(exc)
            )


def clean_config(payload, existing=None):
    """校验并规整单组配置；只保留允许字段。token 留空且有 existing 时沿用旧值。"""
    name = str(payload.get("name") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    token = str(payload.get("access_token") or "").strip()
    if not token and existing:
        token = str(existing.get("access_token") or "")
    turnstile = str(payload.get("turnstile") or "").strip()
    enabled = bool(payload.get("enabled", True))

    errors = []
    if not name:
        errors.append("name 必填")
    if not base_url:
        errors.append("base_url 必填")
    elif not base_url.startswith(("http://", "https://")):
        errors.append("base_url 需以 http:// 或 https:// 开头")
    if not user_id:
        errors.append("user_id 必填")
    if not token:
        errors.append("access_token 必填")
    if errors:
        raise ValueError("；".join(errors))

    return {
        "name": name,
        "base_url": base_url,
        "user_id": user_id,
        "access_token": token,
        "enabled": enabled,
        "turnstile": turnstile,
    }


def _extract_by_path(obj, path):
    """按 . 分割的路径从嵌套 dict/list 中提取值，如 data.items.0.user.balance。"""
    keys = [k for k in path.split(".") if k]
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
            if obj is None:
                return None
        elif isinstance(obj, list):
            if not key.isdigit():
                return None
            idx = int(key)
            if idx >= len(obj):
                return None
            obj = obj[idx]
            if obj is None:
                return None
        else:
            return None
    return obj


def _fetch_url(method, url, headers, body, proxy_url="", timeout=15):
    """单次 HTTP 请求，支持 GET/POST 带 body。proxy_url 显式传入，不依赖全局态。

    返回 (status, body_str, error_msg)。注意：urllib 的 ProxyHandler 不支持 socks，
    socks 代理需走 GET 分支的 gyqd 客户端（curl_cffi）。
    """
    import urllib.error
    import urllib.request

    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)

    proxy = (proxy_url or "").strip()
    # urllib 的 ProxyHandler 不支持 socks，POST 走此路径时显式报错而非静默直连。
    if proxy.lower().startswith("socks"):
        return 0, "", "SOCKS 代理下暂不支持 POST 余额接口，请改用 http(s) 代理或留空"
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        opener = urllib.request.build_opener()

    # build_opener 已自带验证型 HTTPSHandler；OpenerDirector.open 不接受 context 参数。
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", resp.getcode())
            resp_body = resp.read().decode("utf-8", errors="replace")
            return int(status), resp_body, None
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8", errors="replace")
        return exc.code, resp_body, "HTTP {0}".format(exc.code)
    except Exception as exc:
        return 0, "", "网络请求失败：{0}".format(exc)


# 重放时会坏事、必须剥掉的请求头（小写）：
#  - accept-encoding：客户端不解压，保留会拿到 gzip/br 乱码。
#  - if-none-match / if-modified-since：条件请求，命中缓存时服务器回 304 空 body。
_DROP_REQUEST_HEADERS = {"accept-encoding", "if-none-match", "if-modified-since"}


def parse_curl(curl_text):
    """解析浏览器「Copy as cURL (bash)」命令为 {method, url, headers, body}。

    支持 -X/--request、-H/--header、-b/--cookie、-d/--data*（含 --data-raw）及反斜杠换行续行；
    单/双引号由 shlex 处理。未识别的标志（--compressed/-L/-k 等无参数标志）一律忽略。
    解析失败抛 ValueError。
    """
    import shlex

    text = (curl_text or "").strip()
    if not text:
        raise ValueError("curl 命令为空")
    # 去掉 shell 续行符（反斜杠+换行），否则 shlex 会把换行当字面量
    text = re.sub(r"\\\r?\n", " ", text)
    # Chrome 偶尔用 ANSI-C 引用 $'...' 包裹含特殊字符的值，shlex 不识别 $，先剥掉前缀 $
    text = text.replace("$'", "'")

    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError("引号未闭合或语法错误：{0}".format(exc))
    if not tokens:
        raise ValueError("未解析出任何参数")
    if tokens[0] == "curl":
        tokens = tokens[1:]

    method = ""
    url = ""
    headers = {}
    body = None
    data_flags = (
        "-d", "--data", "--data-raw", "--data-binary",
        "--data-ascii", "--data-urlencode",
    )

    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in ("-X", "--request") and i + 1 < n:
            method = tokens[i + 1].upper()
            i += 2
        elif tok in ("-H", "--header") and i + 1 < n:
            line = tokens[i + 1]
            ci = line.find(":")
            if ci > 0:
                key = line[:ci].strip()
                # 丢弃重放有害头（压缩头 / 条件请求头），见 _DROP_REQUEST_HEADERS。
                if key and key.lower() not in _DROP_REQUEST_HEADERS:
                    headers[key] = line[ci + 1:].strip()
            i += 2
        elif tok in ("-b", "--cookie") and i + 1 < n:
            headers["cookie"] = tokens[i + 1].strip()
            i += 2
        elif tok in data_flags and i + 1 < n:
            body = tokens[i + 1]
            if not method:
                method = "POST"
            i += 2
        elif tok.startswith(("http://", "https://")):
            url = tok
            i += 1
        elif tok.startswith("-"):
            # 未识别标志：当作无参开关跳过（Chrome 输出里常见 --compressed 等）
            i += 1
        else:
            # 裸 token：可能是被引号包裹但未带协议头的 URL
            if not url:
                url = tok
            i += 1

    if not url:
        raise ValueError("未找到 URL")
    if not method:
        method = "GET"
    return {"method": method, "url": url, "headers": headers, "body": body}


def _sign_nekocode(url, ts, nonce):
    """nekocode.ai 的请求签名：SHA256(ts + nonce + path + 密钥) 取 hex 前 16 位。

    path 为去掉 axios baseURL(/api)前缀、去 query 的相对路径，例如
    https://nekocode.ai/api/user/self → /user/self。密钥常量见前端 bundle。
    """
    path = urlparse(url).path
    if path.startswith("/api"):
        path = path[len("/api"):] or "/"
    raw = "{0}{1}{2}{3}".format(ts, nonce, path, "nekoneko")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# 需要动态请求签名的站点：host -> 签名函数。命中后每次请求实时重算，
# 覆盖 curl 抓到的过期 X-Sign，解决“签名几分钟后失效”问题。
_BALANCE_SIGNERS = {
    "nekocode.ai": _sign_nekocode,
}


def _apply_dynamic_signature(url, headers):
    """若 host 命中已知动态签名站点，就地刷新 X-Timestamp/X-Nonce/X-Sign。"""
    host = (urlparse(url).hostname or "").lower()
    signer = _BALANCE_SIGNERS.get(host)
    if not signer:
        return
    ts = str(int(time.time()))
    nonce = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    sign = signer(url, ts, nonce)
    # 删掉原有同名头(大小写不敏感)再写入新值，避免重复头。
    for hk in [k for k in headers if k.lower() in ("x-timestamp", "x-nonce", "x-sign")]:
        headers.pop(hk)
    headers["X-Timestamp"] = ts
    headers["X-Nonce"] = nonce
    headers["X-Sign"] = sign


def _fetch_json_value(cfg, proxy_url):
    """按接口配置请求并按 json_path 取值。返回 (value, error_msg)；error_msg 非空表示失败。"""
    method = str(cfg.get("method") or "GET").upper()
    url = cfg["url"]
    headers = dict(cfg.get("headers") or {})
    body = cfg.get("body")
    json_path = cfg.get("json_path") or ""

    # 剥掉重放有害头：accept-encoding（拿到压缩乱码）、if-none-match /
    # if-modified-since（命中缓存回 304 空 body）。覆盖 curl 抓来的旧配置。
    for hk in [k for k in headers if k.lower() in _DROP_REQUEST_HEADERS]:
        headers.pop(hk)

    # 动态签名站点（如 nekocode）：实时重算签名头，覆盖 curl 里的过期值。
    _apply_dynamic_signature(url, headers)

    if method == "POST":
        if body:
            headers.setdefault("content-type", "application/json")
        status, resp_body, err = _fetch_url(method, url, headers, body, proxy_url)
        if err:
            return None, err
        if status >= 400:
            return None, "HTTP {0}".format(status)
    else:
        client = _build_client(proxy_url)
        headers.pop("content-type", None)
        try:
            status, resp_body, _ = client._fetch_raw(method, url, headers, 15)
        except Exception as exc:
            return None, "请求失败：{0}".format(exc)
        if int(status) >= 400:
            return None, "HTTP {0}".format(status)

    # 去掉可能的 UTF-8 BOM 和首尾空白，避免合法 JSON 因 BOM 被判非法。
    text = resp_body.lstrip("﻿").strip() if isinstance(resp_body, str) else resp_body
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        # 附带响应片段，便于区分压缩乱码 / HTML 错误页 / 真正的非 JSON。
        snippet = str(resp_body)[:80].replace("\n", " ")
        return None, "响应不是合法 JSON：{0}（响应开头：{1}）".format(exc, snippet)

    value = _extract_by_path(parsed, json_path)
    if value is None:
        return None, "未找到路径 {0}".format(json_path)

    return value, None


# =========================
# 收藏接口字段：多字段 + 类型换算（金额 / 时间 / 原值）
# =========================

FIELD_TYPES = ("amount", "time", "raw")
TS_UNITS = ("auto", "s", "ms")
DEFAULT_TZ = "Asia/Shanghai"
# 旧数据单一 balance_config 迁移后的字段 id，前端据此标识「由旧余额配置迁移」。
LEGACY_FIELD_ID = "balance"
MAX_FIELDS_PER_BOOKMARK = 20
_SNAPSHOT_KEYS = ("value", "raw", "updated_at", "error")
_FIELD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _gen_field_id(taken=()):
    while True:
        fid = "f_" + "".join(random.choice("0123456789abcdef") for _ in range(8))
        if fid not in taken:
            return fid


def _resolve_tz(name):
    """解析时区名为 tzinfo；镜像缺少 tzdata 时对中国常用时区回退为固定 +08:00。"""
    name = str(name or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError / ImportError 等统一回退。
        if name in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin", "Asia/Hong_Kong",
                    "Asia/Macau", "Asia/Taipei", "PRC"):
            return datetime.timezone(datetime.timedelta(hours=8), "UTC+8")
        if name.upper() == "UTC":
            return datetime.timezone.utc
        raise ValueError("无法识别的时区：{0}".format(name))


def _epoch_from_value(value, ts_unit="auto"):
    """把接口原始值解析为 epoch 秒（float）或 datetime。

    - 数字 / 数字字符串：按 ts_unit 换算；auto 按数量级识别秒(<1e11)/毫秒(<1e14)/微秒(<1e17)/纳秒。
    - ISO 8601 字符串（含 Z / 偏移）：返回 datetime；无时区信息时由调用方按目标时区解释。
    """
    if isinstance(value, bool):
        raise ValueError("布尔值不是时间")
    if isinstance(value, (int, float)):
        num = float(value)
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("空值")
        try:
            num = float(text)
        except ValueError:
            iso = text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text
            try:
                return datetime.datetime.fromisoformat(iso)
            except ValueError:
                raise ValueError("无法识别的时间格式：{0}".format(text[:40]))
    unit = str(ts_unit or "auto").lower()
    if unit == "auto":
        mag = abs(num)
        if mag < 1e11:
            unit = "s"
        elif mag < 1e14:
            unit = "ms"
        elif mag < 1e17:
            unit = "us"
        else:
            unit = "ns"
    return num / {"s": 1.0, "ms": 1e3, "us": 1e6, "ns": 1e9}[unit]


def convert_value(value, field):
    """按字段类型换算接口原始值。返回 (display_str, raw, error)；error 非空表示失败。

    - amount：沿用旧余额逻辑（可选除数，保留两位小数；字符串仅在配置除数时数值化）。
    - time：秒/毫秒时间戳或 ISO 字符串 → 目标时区（默认 Asia/Shanghai）的 YYYY-MM-DD HH:MM:SS，raw 为 epoch 秒。
    - raw：原样文本（对象/数组转 JSON），最长 200 字。
    """
    ftype = str(field.get("type") or "amount")
    if ftype == "time":
        try:
            tz = _resolve_tz(field.get("tz") or DEFAULT_TZ)
            parsed = _epoch_from_value(value, field.get("ts_unit") or "auto")
            if isinstance(parsed, datetime.datetime):
                dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
                dt = dt.astimezone(tz)
            else:
                dt = datetime.datetime.fromtimestamp(parsed, tz)
        except (ValueError, OverflowError, OSError) as exc:
            return None, None, "时间换算失败：{0}".format(exc)
        return dt.strftime("%Y-%m-%d %H:%M:%S"), round(dt.timestamp(), 3), ""
    if ftype == "raw":
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value).strip()
        return text[:200], None, ""
    # amount
    if isinstance(value, bool):
        return None, None, "布尔值不是金额"
    try:
        divisor = float(field.get("divisor"))
        if divisor <= 0:
            divisor = 1.0
    except (TypeError, ValueError):
        divisor = 1.0
    if isinstance(value, (int, float)):
        return "{0:.2f}".format(float(value) / divisor), float(value), ""
    text = str(value).strip()
    if divisor != 1.0:
        try:
            num = float(text)
        except (TypeError, ValueError):
            pass
        else:
            return "{0:.2f}".format(num / divisor), num, ""
    return text, None, ""


def _fetch_balance(balance_cfg, proxy_url):
    """兼容旧调用：按金额类型获取并换算。返回 (balance_str_or_None, error_msg)。"""
    value, err = _fetch_json_value(balance_cfg, proxy_url)
    if err:
        return None, err
    display, _raw, cerr = convert_value(value, {"type": "amount", "divisor": balance_cfg.get("divisor")})
    if cerr:
        return None, cerr
    return display, ""


def _request_config(src):
    """从字段或旧 balance_config 中抽取请求配置部分（method/url/headers/body/json_path/curl/divisor）。"""
    cfg = {
        "method": str(src.get("method") or "GET").strip().upper(),
        "url": str(src.get("url") or "").strip(),
        "headers": dict(src.get("headers") or {}),
        "body": src.get("body"),
        "json_path": str(src.get("json_path") or "").strip(),
    }
    if src.get("curl"):
        cfg["curl"] = str(src["curl"])
    divisor = src.get("divisor")
    if divisor not in (None, ""):
        try:
            cfg["divisor"] = float(divisor)
        except (TypeError, ValueError):
            pass
    return cfg


def legacy_field_from_balance(bookmark):
    """把旧的单一 balance_config（含 balance 快照）表示为一个标签为「余额」的金额字段。"""
    cfg = bookmark.get("balance_config") or {}
    field = {"id": LEGACY_FIELD_ID, "label": "余额", "type": "amount", "enabled": True}
    field.update(_request_config(cfg))
    if bookmark.get("balance") is not None:
        field["value"] = bookmark.get("balance")
    if bookmark.get("balance_updated_at"):
        field["updated_at"] = bookmark["balance_updated_at"]
    if bookmark.get("balance_error"):
        field["error"] = bookmark["balance_error"]
    return field


def sync_legacy_balance(bookmark):
    """把首个启用的金额字段镜像回旧键 balance_config/balance 等，使回滚到旧版本仍可用；没有则清除旧键。"""
    primary = None
    for f in bookmark.get("fields") or []:
        if f.get("enabled", True) and f.get("type") == "amount":
            primary = f
            break
    if primary is None:
        for k in ("balance_config", "balance", "balance_updated_at", "balance_error"):
            bookmark.pop(k, None)
        return bookmark
    bookmark["balance_config"] = _request_config(primary)
    for src_key, dst_key in (("value", "balance"), ("updated_at", "balance_updated_at"), ("error", "balance_error")):
        if primary.get(src_key) not in (None, ""):
            bookmark[dst_key] = primary[src_key]
        else:
            bookmark.pop(dst_key, None)
    return bookmark


def normalize_bookmark(bookmark):
    """读取时统一为多字段结构：无 fields 的旧数据由 balance_config 合成；补齐 id/type/enabled 并同步旧键镜像。"""
    if not isinstance(bookmark, dict):
        return bookmark
    fields = bookmark.get("fields")
    if not isinstance(fields, list):
        fields = [legacy_field_from_balance(bookmark)] if bookmark.get("balance_config") else []
    cleaned, taken = [], set()
    for f in fields:
        if not isinstance(f, dict):
            continue
        f = dict(f)
        fid = str(f.get("id") or "").strip()
        if not _FIELD_ID_RE.match(fid) or fid in taken:
            fid = _gen_field_id(taken)
        f["id"] = fid
        taken.add(fid)
        if not str(f.get("label") or "").strip():
            f["label"] = "余额" if fid == LEGACY_FIELD_ID else "字段"
        if f.get("type") not in FIELD_TYPES:
            f["type"] = "amount"
        f["enabled"] = bool(f.get("enabled", True))
        cleaned.append(f)
    bookmark["fields"] = cleaned
    return sync_legacy_balance(bookmark)


def _field_signature(field):
    """影响取值结果的配置签名；变化时丢弃旧快照，避免把旧值当新配置的结果展示。"""
    return (
        field.get("type"), field.get("method"), field.get("url"), field.get("json_path"),
        json.dumps(field.get("headers") or {}, sort_keys=True), field.get("body"),
        field.get("divisor"), field.get("ts_unit"), field.get("tz"),
    )


def clean_field(raw, existing=None, taken=()):
    """校验并规整单个接口字段配置。

    - 请求部分与旧 balance_config 同规则：优先解析 curl，否则接受分字段 method/url/headers/body。
    - 类型专属：amount 可选 divisor(>0)/unit；time 可选 ts_unit(auto/s/ms)/tz（默认 Asia/Shanghai）。
    - 快照（value/raw/updated_at/error）：优先沿用同 id 旧字段（配置签名未变时），否则接受 payload 自带（导入）。
    """
    if not isinstance(raw, dict):
        raise ValueError("字段需为对象")
    errors = []
    label = str(raw.get("label") or "").strip()
    if not label:
        errors.append("字段标签必填")
    elif len(label) > 40:
        errors.append("字段标签过长（最多 40 字）")
    ftype = str(raw.get("type") or "amount").strip().lower()
    if ftype not in FIELD_TYPES:
        errors.append("转换类型无效（amount / time / raw）")
    enabled = bool(raw.get("enabled", True))

    json_path = str(raw.get("json_path") or "").strip()
    curl_text = str(raw.get("curl") or "").strip()
    raw_url = str(raw.get("url") or "").strip()
    # 「不传即沿用」：payload 既没给 curl 也没给 url 时，整段请求配置从同 id 的旧字段继承。
    # /api/configs 下发的是脱敏视图（没有 curl / url / headers / json_path），
    # 前端只改标签或启用开关时因此可以原样回存，不必重新粘贴 curl。
    if existing and not curl_text and not raw_url:
        method = str(existing.get("method") or "").strip().upper()
        api_url = str(existing.get("url") or "").strip()
        headers = dict(existing.get("headers") or {})
        body = existing.get("body")
        curl_text = str(existing.get("curl") or "").strip()
        json_path = json_path or str(existing.get("json_path") or "").strip()
    elif curl_text:
        try:
            parsed = parse_curl(curl_text)
            method, api_url, headers, body = parsed["method"], parsed["url"], parsed["headers"], parsed["body"]
        except ValueError as exc:
            errors.append("curl 解析失败：{0}".format(exc))
            method, api_url, headers, body = "", "", {}, None
    else:
        method = str(raw.get("method") or "").strip().upper()
        api_url = raw_url
        headers = raw.get("headers")
        body = raw.get("body")
    if method not in ("GET", "POST"):
        errors.append("method 需为 GET 或 POST")
    if not api_url:
        errors.append("接口 URL 必填（粘贴 curl 命令）")
    elif not api_url.startswith(("http://", "https://")):
        errors.append("接口 URL 需以 http:// 或 https:// 开头")
    if not isinstance(headers, dict):
        errors.append("headers 需为对象")
    if body is not None and not isinstance(body, str):
        errors.append("body 需为字符串或 null")
    if not json_path:
        errors.append("JSON 取值路径必填")

    field = {
        "id": "", "label": label, "type": ftype, "enabled": enabled,
        "method": method, "url": api_url, "headers": dict(headers or {}), "body": body, "json_path": json_path,
    }
    if curl_text:
        field["curl"] = curl_text

    if ftype == "amount":
        divisor_raw = raw.get("divisor")
        if divisor_raw is not None and str(divisor_raw).strip() != "":
            try:
                divisor = float(divisor_raw)
            except (TypeError, ValueError):
                errors.append("金额换算除数需为数字")
            else:
                if divisor <= 0:
                    errors.append("金额换算除数需大于 0")
                elif divisor != 1:
                    field["divisor"] = divisor
        unit = str(raw.get("unit") or "").strip()
        if len(unit) > 12:
            errors.append("单位过长（最多 12 字）")
        elif unit:
            field["unit"] = unit
    elif ftype == "time":
        ts_unit = str(raw.get("ts_unit") or "auto").strip().lower()
        if ts_unit not in TS_UNITS:
            errors.append("时间戳单位需为 auto / s / ms")
        field["ts_unit"] = ts_unit
        tz = str(raw.get("tz") or DEFAULT_TZ).strip() or DEFAULT_TZ
        try:
            _resolve_tz(tz)
        except ValueError as exc:
            errors.append(str(exc))
        field["tz"] = tz

    if errors:
        raise ValueError("；".join(errors))

    fid = str(raw.get("id") or "").strip()
    if not _FIELD_ID_RE.match(fid) or fid in taken:
        fid = _gen_field_id(taken)
    field["id"] = fid

    # 快照沿用：同 id 旧字段且配置签名未变 → 沿用旧快照；无旧字段（导入）→ 接受 payload 自带。
    snapshot_src = None
    if existing and existing.get("id") == fid:
        if _field_signature(existing) == _field_signature(field):
            snapshot_src = existing
    else:
        snapshot_src = raw
    if snapshot_src:
        for k in _SNAPSHOT_KEYS:
            v = snapshot_src.get(k)
            if v not in (None, "") and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                field[k] = v
    return field


def clean_bookmark(payload, existing=None):
    """校验并规整单条收藏站点；保留 name/url 与多字段配置 fields（含快照）。

    字段来源优先级：
      1. payload.fields（数组，null 视为清空）→ 逐条校验，同 id 旧字段沿用快照
      2. payload.balance_config（旧客户端/旧导出）→ 合成「余额」金额字段替换旧的迁移字段；null 表示删除该字段
      3. 都不传 → 沿用 existing 的字段
    始终把首个启用的金额字段镜像回 balance_config/balance 旧键，保证旧版本可回滚读取。
    """
    name = str(payload.get("name") or "").strip()
    url = str(payload.get("url") or "").strip()

    errors = []
    if not name:
        errors.append("name 必填")
    if not url:
        errors.append("url 必填")
    elif not url.startswith(("http://", "https://")):
        errors.append("url 需以 http:// 或 https:// 开头")

    existing_norm = normalize_bookmark(json.loads(json.dumps(existing))) if isinstance(existing, dict) else None
    existing_fields = list((existing_norm or {}).get("fields") or [])
    existing_by_id = {f["id"]: f for f in existing_fields}

    fields = existing_fields
    if "fields" in payload:
        raw_fields = payload["fields"]
        if raw_fields is None:
            raw_fields = []
        if not isinstance(raw_fields, list):
            errors.append("fields 需为数组")
        elif len(raw_fields) > MAX_FIELDS_PER_BOOKMARK:
            errors.append("字段数量过多（最多 {0} 个）".format(MAX_FIELDS_PER_BOOKMARK))
        else:
            fields, taken = [], set()
            for i, rf in enumerate(raw_fields):
                rid = str(rf.get("id") or "").strip() if isinstance(rf, dict) else ""
                try:
                    f = clean_field(rf, existing_by_id.get(rid), taken)
                except ValueError as exc:
                    errors.append("字段 {0}：{1}".format(i + 1, exc))
                    continue
                taken.add(f["id"])
                fields.append(f)
    elif "balance_config" in payload:
        bc = payload["balance_config"]
        others = [f for f in existing_fields if f["id"] != LEGACY_FIELD_ID]
        if bc is None:
            fields = others
        elif not isinstance(bc, dict):
            errors.append("balance_config 需为对象或 null")
        else:
            raw_field = legacy_field_from_balance(payload)
            try:
                f = clean_field(raw_field, existing_by_id.get(LEGACY_FIELD_ID), {o["id"] for o in others})
            except ValueError as exc:
                errors.append("balance_config：{0}".format(exc))
            else:
                pos = next((i for i, o in enumerate(existing_fields) if o["id"] == LEGACY_FIELD_ID), 0)
                fields = others[:pos] + [f] + others[pos:]

    if errors:
        raise ValueError("；".join(errors))

    item = {"name": name, "url": url, "fields": fields}
    return sync_legacy_balance(item)


def _apply_field(field, proxy_url):
    """拉取并就地写回单个字段的快照。返回 (ok, value_or_errmsg)。

    成功：写 value（+raw）与 updated_at，清除 error。失败：写 error + updated_at，保留上次 value 不动。
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    display, raw, err = None, None, None
    try:
        value, err = _fetch_json_value(field, proxy_url)
        if not err:
            display, raw, err = convert_value(value, field)
    except Exception as exc:  # noqa: BLE001 - 单个字段异常不应影响批量流程。
        err = "刷新失败：{0}".format(exc)
    field["updated_at"] = now
    if err:
        field["error"] = err
        return False, err
    field["value"] = display
    if raw is not None:
        field["raw"] = raw
    else:
        field.pop("raw", None)
    field.pop("error", None)
    return True, display


def refresh_bookmark_fields(bookmark, proxy_url, field_id=None):
    """刷新收藏的全部启用字段（或 field_id 指定的单个字段，不论启用与否），并同步旧键镜像。

    返回 [{id, label, ok, value|error}]；field_id 不存在时返回空列表。
    """
    normalize_bookmark(bookmark)
    results = []
    for f in bookmark.get("fields") or []:
        if field_id is not None:
            if f.get("id") != field_id:
                continue
        elif not f.get("enabled", True):
            continue
        ok, msg = _apply_field(f, proxy_url)
        entry = {"id": f["id"], "label": f.get("label"), "ok": ok}
        entry["value" if ok else "error"] = msg
        results.append(entry)
    sync_legacy_balance(bookmark)
    return results


def bookmark_snapshot_key(bookmark):
    """收藏的稳定标识：网址 + 名称。独立于数组下标，增删改导入都不会错位。"""
    return "{0}|{1}".format(
        str(bookmark.get("url") or "").strip(),
        str(bookmark.get("name") or "").strip(),
    )


def collect_field_snapshots(bookmark):
    """取出该收藏各字段的取值快照：{field_id: {value/raw/updated_at/error}}。"""
    out = {}
    for field in bookmark.get("fields") or []:
        fid = field.get("id")
        if not fid:
            continue
        out[fid] = {k: field[k] for k in _SNAPSHOT_KEYS if field.get(k) not in (None, "")}
    return out


def persist_field_snapshots(snapshots, schedule_run=None, refresh_run=False):
    """把后台刷新拿到的取值快照合并回**最新的** store 并落盘。

    后台任务（定时签到、定时刷新）可能跑几十秒，其间用户完全可能保存过配置。
    如果把任务开始时读到的那份 store 整个写回去，这些编辑就被静默冲掉了。
    所以这里重新读一次，只按 (收藏标识, 字段 id) 把快照贴回去；期间被删掉、
    改了名或换了接口配置的字段找不到对应项，直接丢弃这份快照。
    """
    try:
        store = read_store()
    except RuntimeError:
        return
    for bookmark in store.get("bookmarks") or []:
        snap = snapshots.get(bookmark_snapshot_key(bookmark))
        if not snap:
            continue
        for field in bookmark.get("fields") or []:
            values = snap.get(field.get("id"))
            if values is None:
                continue
            for key in _SNAPSHOT_KEYS:
                if key in values:
                    field[key] = values[key]
                else:
                    field.pop(key, None)
        sync_legacy_balance(bookmark)
    if schedule_run:
        schedule = dict(store.get("schedule") or {})
        schedule.update(schedule_run)
        store["schedule"] = schedule
    if refresh_run:
        refresh = dict(store.get("refresh") or {})
        refresh["last_run_time"] = _now_str()
        refresh["last_run_ts"] = time.time()
        store["refresh"] = refresh
    try:
        write_store(store)
    except RuntimeError:
        pass


def refresh_all_bookmarks(store):
    """刷新 store 里所有配置了接口字段的收藏，返回可合并的快照映射。"""
    snapshots = {}
    for bookmark in store.get("bookmarks") or []:
        if not bookmark.get("fields"):
            continue
        try:
            refresh_bookmark_fields(bookmark, store.get("proxy_url", ""))
        except Exception:  # noqa: BLE001 - 单条收藏失败不影响整体调度。
            continue
        snapshots[bookmark_snapshot_key(bookmark)] = collect_field_snapshots(bookmark)
    return snapshots


def _apply_balance_to_bookmark(bookmark, proxy_url):
    """兼容旧调用：刷新全部启用字段。返回 (all_ok, balance_or_errmsg)。"""
    results = refresh_bookmark_fields(bookmark, proxy_url)
    if not results:
        return False, "未配置接口字段"
    failed = [r for r in results if not r["ok"]]
    if failed:
        return False, "；".join("{0}：{1}".format(r["label"], r["error"]) for r in failed)
    return True, bookmark.get("balance") or results[0].get("value")


# 完全不透露内容的占位掩码：长度也固定，免得从掩码长度反推出 token 长度。
OPAQUE_TOKEN_MASK = "•" * 8


def mask_token(token, reveal=False):
    """token 脱敏。

    reveal=True（已通过管理鉴权）时保留首尾各 4 位，便于在多组配置里认出是哪一个。
    未解锁的访客只拿到固定长度的占位符——给路人看 8 个真实字符没有任何必要，
    而且首尾片段对撞库是有用的信息。
    """
    token = str(token or "")
    if not token:
        return ""
    if not reveal:
        return OPAQUE_TOKEN_MASK
    if len(token) <= 8:
        return "•" * len(token)
    return "{0}…{1}".format(token[:4], token[-4:])


def public_config(item, reveal=False):
    """对外暴露的脱敏配置视图（不含真实 token）。

    reveal=True 仅在已通过管理鉴权时使用：额外带上 turnstile 原文，供编辑弹窗回填。
    """
    out = {
        "name": item.get("name", ""),
        "base_url": item.get("base_url", ""),
        "user_id": item.get("user_id", ""),
        "enabled": bool(item.get("enabled", True)),
        "token_masked": mask_token(item.get("access_token", ""), reveal=reveal),
        "has_token": bool(item.get("access_token")),
        "has_turnstile": bool(str(item.get("turnstile") or "").strip()),
    }
    out["turnstile"] = item.get("turnstile", "") if reveal else ""
    return out


# 收藏字段里承载凭据的键：请求头（Authorization / Cookie）、原始 curl 命令、
# 请求体、接口 URL（常带 key / token 查询参数）。这些一律不出现在开放接口里。
_FIELD_SECRET_KEYS = ("headers", "curl", "body", "url", "method")
# 展示用的非敏感元信息（缺失则不输出，保持响应精简）。
_FIELD_PUBLIC_META = ("unit", "divisor", "ts_unit", "tz")


def public_error(message):
    """错误文案脱敏：去掉附在末尾的上游响应片段，只保留可公开的失败原因。"""
    text = str(message or "")
    cut = text.find("（响应开头：")
    return text[:cut] if cut >= 0 else text


def public_field(field):
    """字段的对外视图：只留标签 / 类型 / 取值快照，整段请求配置与凭据一律剥离。"""
    out = {
        "id": field.get("id", ""),
        "label": field.get("label", ""),
        "type": field.get("type", "amount"),
        "enabled": bool(field.get("enabled", True)),
        # 前端据此判断「已配置接口但还没刷新过」与「压根没配接口」。
        "has_request": bool(field.get("url")),
    }
    for key in _FIELD_PUBLIC_META:
        if field.get(key) not in (None, ""):
            out[key] = field[key]
    for key in _SNAPSHOT_KEYS:  # value / raw / updated_at / error
        if field.get(key) not in (None, ""):
            out[key] = public_error(field[key]) if key == "error" else field[key]
    return out


def public_bookmark(bookmark):
    """收藏站点的对外视图：名称 / 网址 / 字段展示值；不含 fields 的请求配置，也不含 balance_config。"""
    return {
        "name": bookmark.get("name", ""),
        "url": bookmark.get("url", ""),
        "fields": [public_field(f) for f in bookmark.get("fields") or [] if isinstance(f, dict)],
        # 旧键镜像仅用于展示，本身不含凭据；balance_config（含请求头）刻意不下发。
        "balance": bookmark.get("balance", ""),
        "balance_updated_at": bookmark.get("balance_updated_at", ""),
    }


# =========================
# 签到执行（复用 gyqd）
# =========================

def _build_client(proxy_url):
    gyqd.PROXY_URL = proxy_url or ""
    client = gyqd.HttpClient()
    gyqd.ensure_client_ready(client)
    return client


def run_checkin(configs, proxy_url):
    return gyqd.run_all(configs, client=_build_client(proxy_url))


def run_single(config, proxy_url):
    return gyqd.run_one(config, _build_client(proxy_url))


def test_single(config, proxy_url):
    """仅查钱包额度，作为「测试连接」，不执行签到。"""
    return gyqd.get_wallet(config, _build_client(proxy_url))


def serialize(item):
    """把 gyqd 结果字典转成前端友好的 JSON（含格式化后的额度字段）。"""
    label, color = gyqd.STATUS_STYLE.get(item.get("status"), (item.get("status"), "gray"))
    wallet = item.get("wallet") or {}
    return {
        "name": item.get("name"),
        "status": item.get("status"),
        "status_label": label,
        "color": color,
        "message": gyqd.result_note(item),
        "quota_awarded": gyqd.format_quota(item.get("quota")),
        "wallet_balance": gyqd.format_quota(wallet.get("quota")),
        "used_quota": gyqd.format_quota(wallet.get("used_quota")),
        "request_count": gyqd.format_count(wallet.get("request_count")),
        "wallet_status": item.get("wallet_status"),
    }


def summarize(results):
    """汇总统计，复用 gyqd 的合计格式化逻辑。"""
    return {
        "total": len(results),
        "signed": sum(1 for r in results if r.get("status") == "signed"),
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "disabled": sum(1 for r in results if r.get("status") == "disabled"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
        "quota_total": gyqd.format_quota_total([r.get("quota") for r in results]),
        "wallet_total": gyqd.format_quota_total([gyqd.wallet_value(r, "quota") for r in results]),
    }


# =========================
# 历史记录
# =========================

def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def read_history():
    path = Path(HISTORY_FILE)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write_history_entry(entry):
    with _history_lock:
        data = read_history()
        data.insert(0, entry)
        data = data[:HISTORY_CAP]
        try:
            Path(HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(HISTORY_FILE).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # 历史写失败不影响主流程。


def record_history(trigger, results=None, error=None):
    entry = {
        "time": _now_str(),
        "trigger": trigger,
        "summary": summarize(results) if results is not None else None,
        "results": [serialize(r) for r in results] if results is not None else [],
        "error": error,
    }
    _write_history_entry(entry)


# =========================
# 指标快照（持久化每组配置上一次获取到的额度数据）
# =========================

def metrics_key(config):
    """以 base_url|user_id 作为稳定键，独立于配置数组索引，增删改导入均不丢失。"""
    return "{0}|{1}".format(
        str(config.get("base_url") or "").strip(),
        str(config.get("user_id") or "").strip(),
    )


def read_metrics():
    """读取指标快照映射 {key: {quota_awarded, wallet_balance, used_quota, request_count, updated_at}}。"""
    path = Path(METRICS_FILE)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def update_metric(config, serialized, mark_signed=False):
    """从一次签到/测试结果提取四项指标，合并写入快照（仅在成功取到数据时调用）。

    - 钱包三项（余额/已用/请求数）：只要本次取到有效值就刷新。
    - 签到奖励：仅签到成功时有值，测试不产生；无有效值时保留旧值，不覆盖为 '-'。
    - mark_signed=True：额外记录今日已签到日期（last_checkin_date），供「待签到」统计；
      测试连接不传此参数，故不会把站点标记为已签到。
    失败/禁用项不调用本函数，从而保留上一次的有效快照。
    """
    key = metrics_key(config)
    if not key.strip("|"):
        return
    with _metrics_lock:
        metrics = read_metrics()
        snap = dict(metrics.get(key) or {})
        for field in ("wallet_balance", "used_quota", "request_count"):
            value = serialized.get(field)
            if value not in (None, "", "-"):
                snap[field] = value
        awarded = serialized.get("quota_awarded")
        if awarded not in (None, "", "-"):
            snap["quota_awarded"] = awarded
        if mark_signed:
            snap["last_checkin_date"] = _today_str()
        snap["updated_at"] = _now_str()
        metrics[key] = snap
        try:
            Path(METRICS_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(METRICS_FILE).write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # 指标写失败不影响主流程。


# =========================
# 鉴权（仅管理操作）
# =========================

# 会话 Cookie：解锁成功后由服务端签发一个带有效期的 HMAC 令牌，写入 HttpOnly Cookie。
# 这样浏览器关闭再打开仍保持解锁，而浏览器端不保存任何明文管理密码。
SESSION_COOKIE = "gyqd_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 令牌有效期 30 天。
SESSION_SECRET_FILE = DATA_DIR / ".session_secret"

_secret_lock = threading.Lock()
_session_secret_cache = None


def _session_secret():
    """持久化的随机密钥；落盘失败时退回进程内随机值（仅表现为重启后需重新解锁）。"""
    global _session_secret_cache
    with _secret_lock:
        if _session_secret_cache:
            return _session_secret_cache
        try:
            if SESSION_SECRET_FILE.is_file():
                raw = SESSION_SECRET_FILE.read_text(encoding="utf-8").strip()
                if raw:
                    _session_secret_cache = raw
                    return raw
        except OSError:
            pass
        raw = secrets.token_hex(32)
        try:
            # O_EXCL 独占创建：多个 gunicorn worker 同时首启时只有一个写入成功，
            # 其余读回同一份密钥，避免各 worker 拿着不同密钥互相判定令牌无效。
            fd = os.open(str(SESSION_SECRET_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw)
        except FileExistsError:
            try:
                raw = SESSION_SECRET_FILE.read_text(encoding="utf-8").strip() or raw
            except OSError:
                pass
        except OSError:
            sys.stderr.write("[gyqd-web] 警告：会话密钥无法写入数据目录，服务重启后需重新解锁\n")
        _session_secret_cache = raw
        return raw


def _session_key():
    """签名密钥 = 持久随机密钥 + 当前管理密码；改密码即让全部旧会话立刻失效。"""
    return hashlib.sha256((_session_secret() + "|" + ADMIN_PASSWORD).encode("utf-8")).digest()


def _issue_session_token():
    exp = str(int(time.time()) + SESSION_MAX_AGE)
    return exp + "." + hmac.new(_session_key(), exp.encode("utf-8"), hashlib.sha256).hexdigest()


def _session_token_ok(token):
    exp, sep, sig = str(token or "").partition(".")
    if not sep or not exp.isdigit():
        return False
    expected = hmac.new(_session_key(), exp.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return int(exp) > time.time()


def _request_is_https():
    """反代（nginx）转发的是明文 http，真实协议看 X-Forwarded-Proto。"""
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    return proto == "https" if proto else bool(request.is_secure)


def _set_session_cookie(resp):
    # HttpOnly：JS 读不到，避免 XSS 直接窃取；SameSite=Lax：阻断跨站写操作携带该 Cookie（CSRF）。
    resp.set_cookie(
        SESSION_COOKIE,
        _issue_session_token(),
        max_age=SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="Lax",
        secure=_request_is_https(),
    )
    return resp


def _clear_session_cookie(resp):
    resp.set_cookie(
        SESSION_COOKIE,
        "",
        max_age=0,
        path="/",
        httponly=True,
        samesite="Lax",
        secure=_request_is_https(),
    )
    return resp


def admin_ok():
    """管理鉴权：请求头带管理密码（兼容命令行脚本），或持有有效会话 Cookie。未设密码则完全放行。"""
    if not ADMIN_PASSWORD:
        return True
    header = request.headers.get("X-Admin-Password", "")
    if header and hmac.compare_digest(header, ADMIN_PASSWORD):
        return True
    return _session_token_ok(request.cookies.get(SESSION_COOKIE, ""))


# =========================
# 管理密码防爆破
# =========================
#
# 公网部署下 /api/auth 与任何带 X-Admin-Password 的请求都是在线爆破入口。
# 这里按「客户端 IP + 全局」双维度计数：窗口内失败次数超限即锁定并回 429。
# 全局维度是兜底——X-Forwarded-For 可伪造，只按 IP 计数挡不住换头重试。

LOGIN_MAX_FAILS = max(1, int(os.environ.get("GYQD_LOGIN_MAX_FAILS", "8")))
LOGIN_WINDOW = max(30, int(os.environ.get("GYQD_LOGIN_WINDOW", "900")))
LOGIN_GLOBAL_MAX_FAILS = max(LOGIN_MAX_FAILS, int(os.environ.get("GYQD_LOGIN_GLOBAL_MAX_FAILS", "40")))
_LOGIN_GLOBAL_KEY = "*"
_LOGIN_MAX_KEYS = 4096  # 计数表上限，防止伪造 IP 把内存撑爆

_login_lock = threading.Lock()
_login_fails = {}  # key -> [失败时间戳]（升序）


def client_ip():
    """反代后的真实来源 IP；取 X-Forwarded-For 最左一跳，缺失时用 remote_addr。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    return str(request.remote_addr or "?")[:64]


def _login_prune(now):
    """就地清掉过期时间戳与空桶；表过大时整体丢弃（等价于放宽一次窗口，可接受）。"""
    for key in list(_login_fails):
        kept = [t for t in _login_fails[key] if now - t < LOGIN_WINDOW]
        if kept:
            _login_fails[key] = kept
        else:
            del _login_fails[key]
    if len(_login_fails) > _LOGIN_MAX_KEYS:
        _login_fails.clear()


def login_retry_after(key=None):
    """当前是否被锁定：返回还需等待的秒数，0 表示可以尝试。"""
    now = time.time()
    key = key or client_ip()
    with _login_lock:
        _login_prune(now)
        for bucket, limit in ((key, LOGIN_MAX_FAILS), (_LOGIN_GLOBAL_KEY, LOGIN_GLOBAL_MAX_FAILS)):
            stamps = _login_fails.get(bucket) or []
            if len(stamps) >= limit:
                wait = int(LOGIN_WINDOW - (now - stamps[-1])) + 1
                if wait > 0:
                    return wait
    return 0


def record_login_failure(key=None):
    now = time.time()
    key = key or client_ip()
    with _login_lock:
        _login_prune(now)
        for bucket, limit in ((key, LOGIN_MAX_FAILS), (_LOGIN_GLOBAL_KEY, LOGIN_GLOBAL_MAX_FAILS)):
            stamps = _login_fails.get(bucket) or []
            stamps.append(now)
            _login_fails[bucket] = stamps[-(limit + 1):]


def clear_login_failures(key=None):
    """本次鉴权成功：清掉该来源的失败计数（全局桶不清，避免一次成功抹掉爆破痕迹）。"""
    with _login_lock:
        _login_fails.pop(key or client_ip(), None)


def _credential_presented():
    """请求是否真的带了凭据。没带就只是「未解锁」，不该计入爆破失败。"""
    return bool(request.headers.get("X-Admin-Password") or request.cookies.get(SESSION_COOKIE))


def _locked_response(wait):
    resp = jsonify({"ok": False, "error": "管理密码尝试过于频繁，请 {0} 秒后再试".format(wait)})
    resp.headers["Retry-After"] = str(wait)
    return resp, 429


def _guard_admin():
    """管理接口统一入口：未解锁回 403，爆破中回 429。返回 None 表示放行。"""
    if admin_ok():
        return None
    if not ADMIN_PASSWORD:  # 理论上到不了这里（未设密码时 admin_ok 恒真），保守处理。
        return None
    wait = login_retry_after()
    if wait:
        return _locked_response(wait)
    if _credential_presented():
        record_login_failure()
    return jsonify({"ok": False, "error": "需要管理密码"}), 403


# =========================
# 响应安全头
# =========================
#
# 页面内联了全部脚本与样式（单文件模板），因此 script-src / style-src 必须放行
# 'unsafe-inline'；其余方向一律收紧到同源，并禁止被嵌进 iframe。
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)
# HSTS 会把整个域名（含其它端口的服务）锁到 https，默认不开，由部署方按需打开。
HSTS_ENABLED = os.environ.get("GYQD_HSTS", "0") == "1"


# 私密模式下仍然开放的端点：
#  - index / static / service_worker：应用外壳本身，不含任何数据，解锁界面要靠它渲染；
#  - health：容器 HEALTHCHECK 在调，堵掉会让容器被判定为不健康；
#  - api_auth / api_logout：解锁与锁定的入口，堵掉就没法解锁了；
#  - api_configs：自己会在未解锁时返回空壳（见函数内），不走这里的拦截。
_PRIVATE_OPEN_ENDPOINTS = frozenset({
    "index", "static", "service_worker", "health", "api_auth", "api_logout", "api_configs",
})


def private_mode_active():
    """私密模式是否真的生效：没有管理密码时无从校验，视为未开启。"""
    return PRIVATE_MODE and bool(ADMIN_PASSWORD)


@app.before_request
def enforce_private_mode():
    """私密模式：未解锁时连只读接口也不给，避免公网路人看到站点清单与自建服务地址。"""
    if not private_mode_active():
        return None
    if request.endpoint in _PRIVATE_OPEN_ENDPOINTS:
        return None
    if admin_ok():
        return None
    return _guard_admin()


def _wants_json():
    """/api/* 一律回 JSON；页面路由保持 Flask 默认的 HTML 错误页。"""
    return request.path.startswith("/api/")


@app.errorhandler(404)
def _handle_not_found(exc):
    if _wants_json():
        return jsonify({"ok": False, "error": "接口不存在"}), 404
    return exc


@app.errorhandler(405)
def _handle_method_not_allowed(exc):
    if _wants_json():
        return jsonify({"ok": False, "error": "请求方法不被允许"}), 405
    return exc


@app.errorhandler(413)
def _handle_too_large(exc):
    limit_mb = MAX_REQUEST_BYTES / 1024.0 / 1024.0
    message = "请求体过大（上限 {0:.1f} MB）".format(limit_mb)
    if _wants_json():
        return jsonify({"ok": False, "error": message}), 413
    return message, 413


@app.errorhandler(500)
def _handle_server_error(exc):
    # 细节留在服务端日志里；回给前端的只有一句话，避免堆栈泄漏内部路径与配置。
    if _wants_json():
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
    return exc


@app.after_request
def apply_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if HSTS_ENABLED and _request_is_https():
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # 接口响应可能含配置与运行数据，不允许中间层或浏览器留存；
    # /api/favicon 自带长缓存头，这里不覆盖已显式设置的值。
    if request.path.startswith("/api/") and "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    return resp


# =========================
# 路由：页面
# =========================

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/sw.js")
def service_worker():
    """Service Worker 必须从根路径提供：放在 /static/ 下作用域只有 /static/，
    盖不住首页。文件本体仍在 static/ 里，这里只是换个路径吐出去。"""
    resp = app.send_static_file("sw.js")
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    # SW 自身不缓存，否则改了缓存策略却要等旧 SW 过期才生效。
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/api/health")
def health():
    """容器 HEALTHCHECK 用的轻量探针。

    永远只回 {ok: true}，不带任何状态——它是未鉴权可达的（私密模式下也是），
    多一个字段就多一分信息泄漏。需要细节看 /api/diagnostics（需管理密码）。
    """
    return jsonify({"ok": True})


def _check(status, label, detail):
    return {"status": status, "label": label, "detail": detail}


def _data_dir_writable():
    """真的试着写一下，而不是看权限位——只读挂载、磁盘写满都只有写了才知道。"""
    try:
        fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".healthcheck-", suffix=".tmp")
        os.close(fd)
        os.unlink(tmp)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def collect_diagnostics(request_is_https=None):
    """部署后自检：把「装好了没有、会不会按时跑」这些问题一次性答清楚。

    只输出名称、计数与布尔值——不含任何 token、Cookie、密码或完整网址。

    request_is_https 由调用方显式传入（而不是在这里读 request），这样本函数不依赖
    请求上下文，可以在任何地方调用；传 None 表示「无从判断」，跳过传输安全那一项。
    """
    checks = []

    # 1. 管理密码
    if not ADMIN_PASSWORD:
        checks.append(_check("error", "管理密码", "未设置：任何访问者都能编辑配置、查看真实 token、执行签到"))
    elif len(ADMIN_PASSWORD) < MIN_ADMIN_PASSWORD_LEN:
        checks.append(_check("warn", "管理密码", "已设置，但短于 {0} 位，公网上容易被爆破".format(MIN_ADMIN_PASSWORD_LEN)))
    else:
        checks.append(_check("ok", "管理密码", "已设置（{0} 位）".format(len(ADMIN_PASSWORD))))

    # 2. 私密模式
    if PRIVATE_MODE and not ADMIN_PASSWORD:
        checks.append(_check("error", "私密模式", "GYQD_PRIVATE=1 但没有管理密码，未生效"))
    elif private_mode_active():
        checks.append(_check("ok", "私密模式", "已开启：未解锁时整站不可浏览"))
    else:
        checks.append(_check("ok", "私密模式", "未开启：收藏库可被公开浏览（凭据始终不下发）"))

    # 3. 传输安全
    if request_is_https is True:
        checks.append(_check("ok", "传输", "https" + ("（已下发 HSTS）" if HSTS_ENABLED else "")))
    elif request_is_https is False:
        checks.append(_check("warn", "传输", "当前请求不是 https：反代需转发 X-Forwarded-Proto，否则会话 Cookie 不带 Secure"))

    # 4. 数据目录
    writable, why = _data_dir_writable()
    checks.append(_check("ok", "数据目录", "可写") if writable
                  else _check("error", "数据目录", "不可写，所有编辑都会失败：{0}".format(why)))

    # 5. 配置文件
    try:
        store = read_store()
    except RuntimeError as exc:
        store = None
        checks.append(_check("error", "配置文件", "读取失败：{0}".format(exc)))
    if store is not None:
        links = sum(len(g.get("links") or []) for g in store.get("link_groups") or [])
        checks.append(_check("ok", "配置文件", "签到 {0} 组 · 看板 {1} 个 · 分组 {2} 个 · 网址 {3} 条".format(
            len(store.get("configs") or []), len(store.get("bookmarks") or []),
            len(store.get("link_groups") or []), links)))

    # 6. 配置备份
    if CONFIG_BACKUP_DAYS <= 0:
        checks.append(_check("warn", "配置备份", "每日留档已关闭（GYQD_BACKUP_DAYS=0），只有一份 .bak"))
    else:
        try:
            snaps = sorted(Path(CONFIG_FILE).parent.glob(Path(CONFIG_FILE).name + ".*.bak"))
            days = [p for p in snaps if _STAMP_DAY_RE.match(
                p.name[len(Path(CONFIG_FILE).name) + 1:-len(".bak")] or "")]
        except OSError:
            days = []
        checks.append(_check("ok", "配置备份", "保留 {0} 天，现有 {1} 份每日留档".format(
            CONFIG_BACKUP_DAYS, len(days))))

    # 7. 会话密钥
    try:
        exists = SESSION_SECRET_FILE.is_file()
        mode = stat.S_IMODE(os.stat(str(SESSION_SECRET_FILE)).st_mode) if exists else None
    except OSError:
        exists, mode = False, None
    if not exists:
        checks.append(_check("warn", "会话密钥", "尚未落盘（还没人解锁过）；重启后需重新解锁"))
    elif mode & 0o077:
        checks.append(_check("warn", "会话密钥", "权限为 {0}，建议 600".format(oct(mode))))
    else:
        checks.append(_check("ok", "会话密钥", "已持久化，权限 600"))

    # 8. 后台调度
    alive = bool(_sched_thread and _sched_thread.is_alive())
    schedule = (store or {}).get("schedule") or {}
    refresh = (store or {}).get("refresh") or {}
    wants_background = bool(schedule.get("enabled") or refresh.get("enabled"))
    if not SCHEDULER_ENABLED:
        checks.append(_check("error" if wants_background else "ok", "后台调度",
                             "已被 GYQD_SCHEDULER=0 关闭" + ("，但定时签到 / 自动刷新是开着的，不会执行" if wants_background else "")))
    elif not alive:
        checks.append(_check("error", "后台调度", "线程未在运行，定时任务不会执行"))
    else:
        checks.append(_check("ok", "后台调度", "运行中"))

    # 9. 定时签到 / 补签
    if store is not None:
        if not schedule.get("enabled"):
            checks.append(_check("ok", "定时签到", "未启用"))
        else:
            detail = "每天 {0}".format(schedule.get("time") or "--:--")
            if schedule.get("last_run_time"):
                detail += " · 上次 {0}".format(schedule["last_run_time"])
            ran_today = schedule.get("last_run_date") == _today_str()
            pending = len(pending_configs(store))
            if not ran_today:
                # 今天的主轮次还没到点。此时 pending 只是「还没轮到」，不是失败。
                checks.append(_check("ok", "定时签到", detail + " · 今日尚未执行"))
            elif pending:
                left = max(0, SCHEDULE_RETRY_LIMIT - int(schedule.get("retry_count") or 0))
                detail += " · 今日 {0} 个未签成（剩 {1} 次补签）".format(pending, left)
                checks.append(_check("warn" if left else "error", "定时签到", detail))
            else:
                checks.append(_check("ok", "定时签到", detail + " · 今日已全部签成"))

        # 10. 自动刷新
        if refresh.get("enabled"):
            detail = "每 {0} 分钟".format(refresh.get("interval_minutes"))
            if refresh.get("last_run_time"):
                detail += " · 上次 {0}".format(refresh["last_run_time"])
            checks.append(_check("ok", "站点数据自动刷新", detail))
        else:
            checks.append(_check("ok", "站点数据自动刷新", "未启用"))

    return {
        "ok": not any(c["status"] == "error" for c in checks),
        "checks": checks,
        "generated_at": _now_str(),
    }


@app.get("/api/diagnostics")
def api_diagnostics():
    """部署自检明细；需管理密码（内容虽不含凭据，但足以描摹部署形态）。"""
    guard = _guard_admin()
    if guard:
        return guard
    return jsonify(collect_diagnostics(request_is_https=_request_is_https()))


# =========================
# 路由：签到（需管理密码）
# =========================
#
# 签到会带着账号凭据请求第三方平台，并把结果写进历史，属于代表账号主人执行的动作。
# 未设管理密码时 _guard_admin 恒放行，本地/内网部署的行为不变。

@app.post("/api/checkin")
def api_checkin():
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    configs = store["configs"]
    if not configs:
        return jsonify({"ok": False, "error": "未配置任何平台，请先在「配置管理」中添加"}), 400

    with _run_lock:
        try:
            results = run_checkin(configs, store["proxy_url"])
        except gyqd.CheckinError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": "签到执行异常：{0}".format(exc)}), 500
        record_history("manual", results)
        # 成功取到钱包数据的项（签到成功/今日已签）刷新指标快照；禁用/失败保留旧值。
        for cfg, r in zip(configs, results):
            if r.get("status") in ("signed", "skipped"):
                update_metric(cfg, serialize(r), mark_signed=True)

    return jsonify({
        "ok": True,
        "results": [serialize(r) for r in results],
        "summary": summarize(results),
        "time": _now_str(),
    })


@app.post("/api/checkin/<int:idx>")
def api_checkin_one(idx):
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    configs = store["configs"]
    if idx < 0 or idx >= len(configs):
        return jsonify({"ok": False, "error": "配置不存在"}), 404

    with _run_lock:
        try:
            result = run_single(configs[idx], store["proxy_url"])
        except gyqd.CheckinError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": "签到执行异常：{0}".format(exc)}), 500
        record_history("manual-single", [result])
        if result.get("status") in ("signed", "skipped"):
            update_metric(configs[idx], serialize(result), mark_signed=True)

    return jsonify({"ok": True, "result": serialize(result), "time": _now_str()})


@app.post("/api/test/<int:idx>")
def api_test_one(idx):
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    configs = store["configs"]
    if idx < 0 or idx >= len(configs):
        return jsonify({"ok": False, "error": "配置不存在"}), 404

    try:
        wallet = test_single(configs[idx], store["proxy_url"])
    except gyqd.CheckinError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": "测试异常：{0}".format(exc)}), 200

    balance = gyqd.format_quota(wallet.get("quota"))
    used = gyqd.format_quota(wallet.get("used_quota"))
    requests_made = gyqd.format_count(wallet.get("request_count"))
    # 测试连接也能拿到钱包三项，顺带刷新快照（不含签到奖励，保留旧值）。
    update_metric(configs[idx], {
        "wallet_balance": balance,
        "used_quota": used,
        "request_count": requests_made,
    })

    return jsonify({
        "ok": True,
        "wallet_balance": balance,
        "used_quota": used,
        "request_count": requests_made,
    })


# =========================
# 路由：配置查询
# =========================

@app.get("/api/configs")
def api_configs():
    """页面初始数据：脱敏配置列表 + 收藏展示值 + 全局设置 + 鉴权状态。开放访问。

    这是首页唯一的开放读接口，因此凭据一律不出现在这里：
    - 签到配置只给 token 掩码；turnstile 仅在已解锁时回填。
    - 收藏站点走 public_bookmark：剥掉 fields 的 headers / curl / body / url 与 balance_config，
      编辑态需要的原文另走 admin-only 的 /api/bookmarks/<idx>/secret。
    - proxy_url 可能形如 http://user:pass@host，未解锁时只回是否已配置。
    """
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    unlocked = admin_ok()
    # 私密模式未解锁：回一个不含任何数据的空壳，让页面能渲染出解锁面板，
    # 而不是把用户怼到一个 403 的白屏上。
    if private_mode_active() and not unlocked:
        return jsonify({
            "ok": True, "private": True, "locked": True,
            "configs": [], "bookmarks": [], "link_groups": [],
            "proxy_url": "", "proxy_configured": False,
            "schedule": {"enabled": False, "time": "08:30", "last_run_time": None, "last_run_date": None},
            "refresh": {"enabled": False, "interval_minutes": DEFAULT_REFRESH_INTERVAL,
                        "last_run_time": "", "intervals": list(REFRESH_INTERVALS)},
            "admin_required": True, "admin_unlocked": False, "scheduler_running": SCHEDULER_ENABLED,
        })
    schedule = store.get("schedule") or {}
    refresh = store.get("refresh") or {}
    metrics = read_metrics()
    today = _today_str()
    configs_out = []
    for c in store["configs"]:
        pc = public_config(c, reveal=unlocked)
        snap = metrics.get(metrics_key(c)) or None
        pc["metrics"] = snap  # 上一次获取的指标快照，供刷新后填充。
        # 今日是否已签到（手动/定时签到成功或已签到跳过时写入），供前端「待签到」跨刷新统计。
        pc["checked_in_today"] = bool(snap and snap.get("last_checkin_date") == today)
        configs_out.append(pc)
    proxy_url = str(store.get("proxy_url") or "")
    return jsonify({
        "ok": True,
        "configs": configs_out,
        # 仅收藏不签到的站点：只下发展示所需字段，接口配置与凭据不出现在开放接口里。
        "bookmarks": [public_bookmark(b) for b in store.get("bookmarks") or []],
        "link_groups": list(store.get("link_groups", [])),  # 收藏库子页面（网址分组）。
        "proxy_url": proxy_url if unlocked else "",
        "proxy_configured": bool(proxy_url.strip()),
        "schedule": {
            "enabled": bool(schedule.get("enabled")),
            "time": schedule.get("time", "08:30"),
            "last_run_time": schedule.get("last_run_time"),
            "last_run_date": schedule.get("last_run_date"),
            "retry_count": schedule.get("retry_count") or 0,
            "retry_limit": SCHEDULE_RETRY_LIMIT,
            "retry_delay_minutes": SCHEDULE_RETRY_DELAY_MIN,
            # 今天还没签成功的启用配置数：>0 且未用完重试次数时，后台还会自动补签。
            "pending_today": len(pending_configs(store, metrics)) if schedule.get("enabled") else 0,
        },
        "refresh": {
            "enabled": bool(refresh.get("enabled")),
            "interval_minutes": refresh.get("interval_minutes", DEFAULT_REFRESH_INTERVAL),
            "last_run_time": refresh.get("last_run_time") or "",
            "intervals": list(REFRESH_INTERVALS),
        },
        "admin_required": bool(ADMIN_PASSWORD),
        "admin_unlocked": unlocked,
        "scheduler_running": SCHEDULER_ENABLED,
        "private": private_mode_active(),
        "locked": False,
    })


@app.get("/api/configs/<int:idx>/secret")
def api_config_secret(idx):
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    configs = store["configs"]
    if idx < 0 or idx >= len(configs):
        return jsonify({"ok": False, "error": "配置不存在"}), 404
    return jsonify({"ok": True, "access_token": configs[idx].get("access_token", "")})


# =========================
# 路由：配置增删改（管理）
# =========================

@app.post("/api/configs")
def api_config_create():
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        cleaned = clean_config(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        store = read_store()
        store["configs"].append(cleaned)
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "index": len(store["configs"]) - 1})


@app.put("/api/configs/<int:idx>")
def api_config_update(idx):
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    configs = store["configs"]
    if idx < 0 or idx >= len(configs):
        return jsonify({"ok": False, "error": "配置不存在"}), 404
    try:
        configs[idx] = clean_config(payload, existing=configs[idx])
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.delete("/api/configs/<int:idx>")
def api_config_delete(idx):
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    configs = store["configs"]
    if idx < 0 or idx >= len(configs):
        return jsonify({"ok": False, "error": "配置不存在"}), 404
    configs.pop(idx)
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


# =========================
# 路由：收藏站点增删改（管理）
# =========================

@app.post("/api/bookmarks")
def api_bookmark_create():
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        cleaned = clean_bookmark(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        store = read_store()
        store["bookmarks"].append(cleaned)
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "index": len(store["bookmarks"]) - 1})


@app.put("/api/bookmarks/<int:idx>")
def api_bookmark_update(idx):
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    bookmarks = store["bookmarks"]
    if idx < 0 or idx >= len(bookmarks):
        return jsonify({"ok": False, "error": "收藏不存在"}), 404
    try:
        bookmarks[idx] = clean_bookmark(payload, existing=bookmarks[idx])
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.delete("/api/bookmarks/<int:idx>")
def api_bookmark_delete(idx):
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    bookmarks = store["bookmarks"]
    if idx < 0 or idx >= len(bookmarks):
        return jsonify({"ok": False, "error": "收藏不存在"}), 404
    bookmarks.pop(idx)
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.post("/api/bookmarks/reorder")
def api_bookmark_reorder():
    """重排收藏顺序：接收旧下标的新排列 order，须为 0..n-1 的一个全排列。"""
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    bookmarks = store["bookmarks"]
    n = len(bookmarks)
    if not isinstance(order, list) or sorted(order) != list(range(n)):
        return jsonify({"ok": False, "error": "排序参数无效"}), 400
    store["bookmarks"] = [bookmarks[i] for i in order]
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


def _bookmark_refresh_response(store, bookmark, results):
    """刷新结果统一响应：ok 表示所请求字段全部成功；附带最新收藏对象与旧键 balance 供前端就地更新。"""
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    failed = [r for r in results if not r["ok"]]
    resp = {
        "ok": bool(results) and not failed,
        "results": results,
        # 前端拿到后直接替换 STATE.bookmarks[i]，因此这里也用公开视图，
        # 免得凭据经由刷新响应重新回到页面内存里。
        "bookmark": public_bookmark(bookmark),
        "balance": bookmark.get("balance"),
        "balance_updated_at": bookmark.get("balance_updated_at"),
    }
    if not results:
        resp["error"] = "没有可刷新的字段"
    elif failed:
        resp["error"] = "；".join("{0}：{1}".format(r["label"], r["error"]) for r in failed)
    return jsonify(resp)


@app.get("/api/bookmarks/<int:idx>/secret")
def api_bookmark_secret(idx):
    """编辑弹窗专用：返回该收藏的完整字段配置（含 curl / 请求头）；需管理密码。

    /api/configs 已把这些剥干净，编辑态按需单条拉取，凭据不再随列表广播。
    """
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    bookmarks = store["bookmarks"]
    if idx < 0 or idx >= len(bookmarks):
        return jsonify({"ok": False, "error": "收藏不存在"}), 404
    return jsonify({"ok": True, "bookmark": bookmarks[idx]})


@app.post("/api/bookmarks/<int:idx>/refresh_balance")
def api_bookmark_refresh_balance(idx):
    """刷新指定收藏的全部启用字段（旧路径名保留以兼容）；需管理密码。"""
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    bookmarks = store["bookmarks"]
    if idx < 0 or idx >= len(bookmarks):
        return jsonify({"ok": False, "error": "收藏不存在"}), 404
    bookmark = bookmarks[idx]
    if not [f for f in bookmark.get("fields") or [] if f.get("enabled", True)]:
        return jsonify({"ok": False, "error": "该收藏没有启用的接口字段"}), 400
    results = refresh_bookmark_fields(bookmark, store.get("proxy_url", ""))
    return _bookmark_refresh_response(store, bookmark, results)


@app.post("/api/bookmarks/<int:idx>/fields/<field_id>/refresh")
def api_bookmark_refresh_field(idx, field_id):
    """刷新指定收藏的单个字段（即使已禁用也可手动刷新）；需管理密码。"""
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    bookmarks = store["bookmarks"]
    if idx < 0 or idx >= len(bookmarks):
        return jsonify({"ok": False, "error": "收藏不存在"}), 404
    bookmark = bookmarks[idx]
    if not any(f.get("id") == field_id for f in bookmark.get("fields") or []):
        return jsonify({"ok": False, "error": "字段不存在"}), 404
    results = refresh_bookmark_fields(bookmark, store.get("proxy_url", ""), field_id=field_id)
    return _bookmark_refresh_response(store, bookmark, results)


@app.post("/api/bookmarks/field_preview")
def api_bookmark_field_preview():
    """保存前测试单个字段配置：校验 + 请求 + 换算，不落盘；需管理密码。"""
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        field = clean_field(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    for k in _SNAPSHOT_KEYS:
        field.pop(k, None)
    ok, msg = _apply_field(field, store.get("proxy_url", ""))
    resp = {"ok": ok, "type": field["type"], "label": field["label"], "updated_at": field.get("updated_at")}
    if ok:
        resp["value"] = msg
        if field.get("raw") is not None:
            resp["raw"] = field["raw"]
    else:
        resp["error"] = msg
    return jsonify(resp)


# =========================
# 路由：导入 / 导出（管理）
# =========================

# =========================
# 链接分组：收藏库下的网址收藏页面（自建服务 / 常用网站 / AI 服务 / 自定义分组）
# =========================
# 数据结构（config.json 顶层 link_groups）：
#   [{id, name, icon, color, desc, links: [{id, name, url, desc, icon, tags[], pinned, created_at, updated_at}]}]
# 与 bookmarks（带接口字段监控的站点看板）相互独立；键缺失时合成三个默认空分组，但不落盘，
# 直到发生任意一次写入。用户清空全部分组会持久化为 []，不再重新合成默认分组。

LINK_GROUP_COLORS = ("mint", "sky", "violet", "amber", "rose", "slate")
LINK_GROUP_ICONS = (
    "folder", "server", "globe", "sparkles", "code", "book", "play", "chat", "wrench", "star", "cloud", "shield",
)
MAX_LINK_GROUPS = 30
MAX_LINKS_PER_GROUP = 300
MAX_LINK_TAGS = 8
_LINK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

DEFAULT_LINK_GROUPS = (
    {"id": "self-hosted", "name": "自建服务", "icon": "server", "color": "sky",
     "desc": "自己部署的面板、工具与内网服务入口。"},
    {"id": "daily", "name": "常用网站", "icon": "globe", "color": "mint",
     "desc": "每天都会打开的站点。"},
    {"id": "ai", "name": "AI 服务", "icon": "sparkles", "color": "violet",
     "desc": "模型控制台、对话工具与 API 平台。"},
)


def default_link_groups():
    return [dict(g, links=[]) for g in DEFAULT_LINK_GROUPS]


def _gen_link_id(taken=()):
    while True:
        cand = secrets.token_hex(4)
        if cand not in taken:
            return cand


def _squash_text(value, limit):
    text = re.sub(r"\s+", " ", str(value if value is not None else "").strip())
    return text[:limit]


def _clean_text(value, limit, label, required=False):
    text = re.sub(r"\s+", " ", str(value if value is not None else "").strip())
    if required and not text:
        raise ValueError("{0}必填".format(label))
    if len(text) > limit:
        raise ValueError("{0}过长（最多 {1} 字）".format(label, limit))
    return text


def _name_from_url(url):
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        host = ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or url[:60]


def _clean_link_url(value):
    url = str(value if value is not None else "").strip()
    if not url:
        raise ValueError("网址必填")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("网址需以 http:// 或 https:// 开头")
    if len(url) > 2048:
        raise ValueError("网址过长")
    if any(ch.isspace() for ch in url):
        raise ValueError("网址不能包含空白字符")
    return url


def _clean_tags(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = re.split(r"[,，;；\s]+", raw)
    if not isinstance(raw, list):
        raise ValueError("标签需为数组")
    tags, seen = [], set()
    for t in raw:
        t = str(t if t is not None else "").strip()
        if not t:
            continue
        if len(t) > 20:
            raise ValueError("标签过长（最多 20 字）")
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(t)
    if len(tags) > MAX_LINK_TAGS:
        raise ValueError("标签最多 {0} 个".format(MAX_LINK_TAGS))
    return tags


def clean_link(payload, existing=None, taken=()):
    """校验并规整单条链接。existing 存在时沿用 id / created_at；否则分配新 id。"""
    if not isinstance(payload, dict):
        raise ValueError("链接需为对象")
    url = _clean_link_url(payload.get("url"))
    name = _clean_text(payload.get("name"), 60, "名称") or _name_from_url(url)
    desc = _clean_text(payload.get("desc"), 160, "描述")
    icon = _clean_text(payload.get("icon"), 8, "图标")
    tags = _clean_tags(payload.get("tags")) if "tags" in payload else list((existing or {}).get("tags") or [])
    if "pinned" in payload:
        pinned = bool(payload.get("pinned"))
    else:
        pinned = bool((existing or {}).get("pinned"))
    if existing:
        lid = existing["id"]
    else:
        lid = str(payload.get("id") or "").strip()
        if not _LINK_ID_RE.match(lid) or lid in taken:
            lid = _gen_link_id(taken)
    now = _now_str()
    created = (existing or {}).get("created_at") or ""
    if not created:
        raw_created = str(payload.get("created_at") or "").strip()
        created = raw_created if _STAMP_RE.match(raw_created) else now
    return {
        "id": lid, "name": name, "url": url, "desc": desc, "icon": icon,
        "tags": tags, "pinned": pinned, "created_at": created, "updated_at": now,
    }


def clean_link_group(payload, existing=None, taken=(), with_links=False):
    """校验并规整分组元信息；with_links=True 时（导入路径）连同 links 数组一起校验替换。"""
    if not isinstance(payload, dict):
        raise ValueError("分组需为对象")
    name = _clean_text(payload.get("name"), 40, "分组名称", required=True)
    desc = _clean_text(payload.get("desc"), 120, "分组描述") if "desc" in payload else str((existing or {}).get("desc") or "")
    icon = str(payload.get("icon") or (existing or {}).get("icon") or "folder").strip()
    if icon not in LINK_GROUP_ICONS:
        raise ValueError("图标不受支持")
    color = str(payload.get("color") or (existing or {}).get("color") or "mint").strip()
    if color not in LINK_GROUP_COLORS:
        raise ValueError("颜色不受支持")
    if existing:
        gid = existing["id"]
    else:
        gid = str(payload.get("id") or "").strip()
        if not _LINK_ID_RE.match(gid) or gid in taken:
            gid = _gen_link_id(taken)
    links = list((existing or {}).get("links") or [])
    if with_links and isinstance(payload.get("links"), list):
        links, used = [], set()
        for i, raw in enumerate(payload["links"]):
            try:
                link = clean_link(raw, None, used)
            except ValueError as exc:
                raise ValueError("第 {0} 条链接：{1}".format(i + 1, exc))
            used.add(link["id"])
            links.append(link)
        if len(links) > MAX_LINKS_PER_GROUP:
            raise ValueError("单个分组最多 {0} 条链接".format(MAX_LINKS_PER_GROUP))
    return {"id": gid, "name": name, "icon": icon, "color": color, "desc": desc, "links": links}


def _coerce_link_group(raw, taken):
    """读取路径的宽松规整：不抛错，缺失/非法字段补默认值，仅丢弃没有合法网址的链接。"""
    gid = str(raw.get("id") or "").strip()
    if not _LINK_ID_RE.match(gid) or gid in taken:
        gid = _gen_link_id(taken)
    icon = raw.get("icon") if raw.get("icon") in LINK_GROUP_ICONS else "folder"
    color = raw.get("color") if raw.get("color") in LINK_GROUP_COLORS else "mint"
    links, used = [], set()
    for item in (raw.get("links") if isinstance(raw.get("links"), list) else []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        lid = str(item.get("id") or "").strip()
        if not _LINK_ID_RE.match(lid) or lid in used:
            lid = _gen_link_id(used)
        used.add(lid)
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        links.append({
            "id": lid,
            "name": _squash_text(item.get("name"), 60) or _name_from_url(url),
            "url": url[:2048],
            "desc": _squash_text(item.get("desc"), 160),
            "icon": _squash_text(item.get("icon"), 8),
            "tags": [str(t).strip() for t in tags if isinstance(t, str) and str(t).strip()][:MAX_LINK_TAGS],
            "pinned": bool(item.get("pinned")),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        })
        if len(links) >= MAX_LINKS_PER_GROUP:
            break
    return {
        "id": gid,
        "name": _squash_text(raw.get("name"), 40) or "未命名分组",
        "icon": icon, "color": color,
        "desc": _squash_text(raw.get("desc"), 120),
        "links": links,
    }


def normalize_link_groups(raw):
    """读取时规整 link_groups：键缺失（None）→ 默认三组；空数组保持为空；非法项跳过。"""
    if raw is None:
        return default_link_groups()
    if not isinstance(raw, list):
        return default_link_groups()
    groups, taken = [], set()
    for g in raw:
        if not isinstance(g, dict):
            continue
        cleaned = _coerce_link_group(g, taken)
        taken.add(cleaned["id"])
        groups.append(cleaned)
        if len(groups) >= MAX_LINK_GROUPS:
            break
    return groups


def link_dedupe_key(url):
    """网址查重用的归一化键。

    忽略协议、`www.` 前缀、默认端口与结尾斜杠——同一个站点被记成两条多半是因为
    抄来的链接协议或尾斜杠不同。查询串保留（`?tab=a` 与 `?tab=b` 是两个页面），
    片段（#...）丢弃。无法解析时回落到原串，宁可漏判也不误判成重复。
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return raw.lower()
    if not host:
        return raw.lower()
    if host.startswith("www."):
        host = host[4:]
    default_port = 80 if parsed.scheme == "http" else (443 if parsed.scheme == "https" else None)
    key = host if port in (None, default_port) else "{0}:{1}".format(host, port)
    key += (parsed.path or "").rstrip("/")
    if parsed.query:
        key += "?" + parsed.query
    return key


def find_duplicate_links(store, url, skip_id=None):
    """返回 store 里与 url 同指一处的既有链接：[{group_id, group_name, link_id, name, url}]。"""
    key = link_dedupe_key(url)
    if not key:
        return []
    hits = []
    for group in store.get("link_groups") or []:
        for link in group.get("links") or []:
            if skip_id and link.get("id") == skip_id:
                continue
            if link_dedupe_key(link.get("url")) == key:
                hits.append({
                    "group_id": group.get("id"), "group_name": group.get("name"),
                    "link_id": link.get("id"), "name": link.get("name"), "url": link.get("url"),
                })
    return hits


def _find_group(store, gid):
    for i, g in enumerate(store.get("link_groups") or []):
        if g.get("id") == gid:
            return i, g
    return -1, None


def _find_link(group, lid):
    for i, link in enumerate(group.get("links") or []):
        if link.get("id") == lid:
            return i, link
    return -1, None


def _link_groups_response(store, **extra):
    body = {"ok": True, "link_groups": store["link_groups"]}
    body.update(extra)
    return jsonify(body)


def _load_store_or_error():
    try:
        return read_store(), None
    except RuntimeError as exc:
        return None, (jsonify({"ok": False, "error": str(exc)}), 500)


def _save_store_or_error(store):
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return None


@app.post("/api/link_groups")
def api_link_group_create():
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    store, err = _load_store_or_error()
    if err:
        return err
    if len(store["link_groups"]) >= MAX_LINK_GROUPS:
        return jsonify({"ok": False, "error": "分组数量已达上限（{0}）".format(MAX_LINK_GROUPS)}), 400
    try:
        group = clean_link_group(payload, None, {g["id"] for g in store["link_groups"]})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    store["link_groups"].append(group)
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store, group=group)


@app.put("/api/link_groups/<gid>")
def api_link_group_update(gid):
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    store, err = _load_store_or_error()
    if err:
        return err
    pos, group = _find_group(store, gid)
    if group is None:
        return jsonify({"ok": False, "error": "分组不存在"}), 404
    try:
        store["link_groups"][pos] = clean_link_group(payload, existing=group)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store, group=store["link_groups"][pos])


@app.delete("/api/link_groups/<gid>")
def api_link_group_delete(gid):
    guard = _guard_admin()
    if guard:
        return guard
    store, err = _load_store_or_error()
    if err:
        return err
    pos, group = _find_group(store, gid)
    if group is None:
        return jsonify({"ok": False, "error": "分组不存在"}), 404
    store["link_groups"].pop(pos)
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store)


@app.post("/api/link_groups/reorder")
def api_link_group_reorder():
    """按分组 id 列表重排；order 须恰好为现有全部分组 id 的一个排列。"""
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")
    store, err = _load_store_or_error()
    if err:
        return err
    by_id = {g["id"]: g for g in store["link_groups"]}
    if not isinstance(order, list) or sorted(map(str, order)) != sorted(by_id):
        return jsonify({"ok": False, "error": "排序参数无效"}), 400
    store["link_groups"] = [by_id[str(gid)] for gid in order]
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store)


@app.post("/api/link_groups/<gid>/links")
def api_link_create(gid):
    """新增链接：请求体为单条链接对象，或 {links: [...]} 批量新增（逐条校验，任一失败整体不写入）。"""
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    store, err = _load_store_or_error()
    if err:
        return err
    pos, group = _find_group(store, gid)
    if group is None:
        return jsonify({"ok": False, "error": "分组不存在"}), 404
    raw_links = payload.get("links") if isinstance(payload, dict) and "links" in payload else [payload]
    if not isinstance(raw_links, list) or not raw_links:
        return jsonify({"ok": False, "error": "links 需为非空数组"}), 400
    if len(group["links"]) + len(raw_links) > MAX_LINKS_PER_GROUP:
        return jsonify({"ok": False, "error": "单个分组最多 {0} 条链接".format(MAX_LINKS_PER_GROUP)}), 400
    taken = {l["id"] for l in group["links"]}
    created = []
    # 重复提示在写入前算，否则新加的这几条会跟自己撞上。不拦截——同一网址收进
    # 两个分组有时是刻意的，只把事实告诉前端，由用户决定。
    duplicates = []
    for i, raw in enumerate(raw_links):
        try:
            link = clean_link(raw, None, taken)
        except ValueError as exc:
            prefix = "第 {0} 条：".format(i + 1) if len(raw_links) > 1 else ""
            return jsonify({"ok": False, "error": prefix + str(exc)}), 400
        hits = find_duplicate_links(store, link["url"])
        if hits:
            duplicates.append({"url": link["url"], "name": link["name"], "existing": hits})
        taken.add(link["id"])
        created.append(link)
    group["links"].extend(created)
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store, links=created, group_id=gid, duplicates=duplicates)


@app.put("/api/link_groups/<gid>/links/<lid>")
def api_link_update(gid, lid):
    """更新链接；请求体可带 group 指定目标分组 id 以移动链接（追加到目标分组末尾）。"""
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    store, err = _load_store_or_error()
    if err:
        return err
    _, group = _find_group(store, gid)
    if group is None:
        return jsonify({"ok": False, "error": "分组不存在"}), 404
    lpos, link = _find_link(group, lid)
    if link is None:
        return jsonify({"ok": False, "error": "链接不存在"}), 404
    target_gid = str(payload.get("group") or gid)
    _, target = _find_group(store, target_gid)
    if target is None:
        return jsonify({"ok": False, "error": "目标分组不存在"}), 404
    merged = dict(link)
    merged.update({k: v for k, v in payload.items() if k in ("name", "url", "desc", "icon", "tags", "pinned")})
    try:
        updated = clean_link(merged, existing=link)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    # 改网址后可能撞上别处已有的同一站点；同样只提示不拦截（排除自己）。
    duplicates = find_duplicate_links(store, updated["url"], skip_id=lid)
    if target is group:
        group["links"][lpos] = updated
    else:
        if len(target["links"]) >= MAX_LINKS_PER_GROUP:
            return jsonify({"ok": False, "error": "目标分组链接已达上限"}), 400
        if any(l["id"] == updated["id"] for l in target["links"]):
            updated["id"] = _gen_link_id({l["id"] for l in target["links"]})
        group["links"].pop(lpos)
        target["links"].append(updated)
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store, link=updated, group_id=target_gid, duplicates=duplicates)


@app.delete("/api/link_groups/<gid>/links/<lid>")
def api_link_delete(gid, lid):
    guard = _guard_admin()
    if guard:
        return guard
    store, err = _load_store_or_error()
    if err:
        return err
    _, group = _find_group(store, gid)
    if group is None:
        return jsonify({"ok": False, "error": "分组不存在"}), 404
    lpos, link = _find_link(group, lid)
    if link is None:
        return jsonify({"ok": False, "error": "链接不存在"}), 404
    group["links"].pop(lpos)
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store)


@app.post("/api/link_groups/<gid>/links/reorder")
def api_link_reorder(gid):
    """按链接 id 列表重排分组内顺序；order 须恰好为该分组全部链接 id 的一个排列。"""
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")
    store, err = _load_store_or_error()
    if err:
        return err
    _, group = _find_group(store, gid)
    if group is None:
        return jsonify({"ok": False, "error": "分组不存在"}), 404
    by_id = {l["id"]: l for l in group["links"]}
    if not isinstance(order, list) or sorted(map(str, order)) != sorted(by_id):
        return jsonify({"ok": False, "error": "排序参数无效"}), 400
    group["links"] = [by_id[str(lid)] for lid in order]
    err = _save_store_or_error(store)
    if err:
        return err
    return _link_groups_response(store)


# =========================
# 站点图标（favicon）：抓取 → 本地缓存 → 失败由前端回退首字母
# =========================
# 只为「已经出现在配置里」的 origin 抓取图标：本接口必须开放访问（未解锁的只读访客也要看到
# 图标），若允许任意 URL 就等于对外开放一个转发器，因此用 store 里的 origin 白名单兜住。
# 白名单里的地址都是管理员自己添加的（含内网自建服务），故不额外拦私有网段。

FAVICON_DIR = DATA_DIR / "favicons"
FAVICON_OK_TTL = 7 * 24 * 3600        # 抓到图标后的缓存有效期
FAVICON_FAIL_TTL = 6 * 3600           # 失败的负缓存有效期，避免反复抓死站
# 单个图标体积上限：不少站点直接拿几百 KB 的 Logo.png 当 favicon，卡太死会白白丢图标。
FAVICON_MAX_BYTES = 512 * 1024
FAVICON_HTML_MAX_BYTES = 256 * 1024   # 首页 HTML 只读前若干字节用于找 <link rel=icon>
FAVICON_TIMEOUT = 5                   # 单次请求超时（秒）
FAVICON_BUDGET = 12                   # 单个 origin 的总抓取时间预算（秒）
FAVICON_MAX_CANDIDATES = 4
FAVICON_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
# Cloudflare 一类 WAF 会因为 urllib 的 TLS 指纹直接挡掉；这些状态码才值得换 curl_cffi 重试，
# 连不上 / 超时不重试，免得把死站的等待时间翻倍。
_FAVICON_WAF_STATUSES = frozenset({403, 405, 406, 409, 429, 503})
# 同时向外抓取的上限：gunicorn 线程数有限，抓图标不能把线程全占满。
_favicon_fetch_slots = threading.BoundedSemaphore(3)
_favicon_curl = {"mod": None, "tried": False}
_favicon_locks = {}
_favicon_locks_guard = threading.Lock()
# origin 白名单按 config.json 的 mtime 缓存，避免每个图标请求都解析一遍配置。
_favicon_ctx = {"stamp": object(), "origins": frozenset(), "proxy": ""}

_FAVICON_LINK_RE = re.compile(r"<link\b([^>]*)>", re.I)
_FAVICON_BASE_RE = re.compile(r"<base\b([^>]*)>", re.I)
_FAVICON_ATTR_RE = re.compile(r"""([a-zA-Z][\w:.-]*)\s*=\s*("[^"]*"|'[^']*'|[^\s"'`=<>]+)""")
_FAVICON_SIZE_RE = re.compile(r"(\d+)\s*[xX]\s*\d+")


class _FaviconRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只跟随 http(s) 跳转，挡掉 ftp/file 等协议。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme not in ("http", "https"):
            return None
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )


def favicon_origin(url):
    """把任意站点地址归一化为 scheme://host[:port]；非 http(s) 或无主机名返回空串。"""
    try:
        parts = urlparse(str(url if url is not None else "").strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    netloc = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return ""
    if port and port != (443 if parts.scheme == "https" else 80):
        netloc = "{0}:{1}".format(netloc, port)
    return "{0}://{1}".format(parts.scheme, netloc)


def _favicon_context():
    """返回 (允许抓取的 origin 集合, 代理地址)，按 config.json 的 mtime 缓存。"""
    try:
        stamp = Path(CONFIG_FILE).stat().st_mtime_ns
    except OSError:
        stamp = None
    if stamp is not None and _favicon_ctx["stamp"] == stamp:
        return _favicon_ctx["origins"], _favicon_ctx["proxy"]
    try:
        store = read_store()
    except RuntimeError:
        return frozenset(), ""
    origins = set()
    for bookmark in store.get("bookmarks") or []:
        origins.add(favicon_origin(bookmark.get("url")))
    for group in store.get("link_groups") or []:
        for link in group.get("links") or []:
            origins.add(favicon_origin(link.get("url")))
    for cfg in store.get("configs") or []:
        origins.add(favicon_origin(cfg.get("base_url")))
    origins.discard("")
    proxy = str(store.get("proxy_url") or "").strip()
    _favicon_ctx.update({"stamp": stamp, "origins": frozenset(origins), "proxy": proxy})
    return _favicon_ctx["origins"], proxy


def sniff_image_mime(data):
    """按魔数判断图片类型；不是已知图片（例如站点用 200 返回了一个 HTML 错误页）返回空串。"""
    if not data:
        return ""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"):
        return "image/x-icon"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<!doctype svg") or b"<svg" in head:
        return "image/svg+xml"
    return ""


_FAVICON_HEADERS = {
    "User-Agent": FAVICON_UA,
    "Accept": "text/html,image/avif,image/webp,image/apng,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _favicon_curl_requests():
    """按需加载 curl_cffi.requests（本仓库已依赖它做浏览器 TLS 指纹）。

    不走 gyqd.load_curl_requests：那条路径在缺依赖时会触发 pip 自动安装，不能放在 Web 请求里。
    """
    if not _favicon_curl["tried"]:
        _favicon_curl["tried"] = True
        try:
            import curl_cffi.requests as curl_requests  # noqa: PLC0415 - 可选依赖，按需导入
            _favicon_curl["mod"] = curl_requests
        except Exception:  # noqa: BLE001 - 缺失或加载失败都只是退回 urllib
            _favicon_curl["mod"] = None
    return _favicon_curl["mod"]


def _favicon_urllib_get(url, proxy, timeout, max_bytes):
    """标准库抓取。返回 (data, content_type, final_url, status)，status=0 表示根本没连上。"""
    req = urllib.request.Request(url, headers=dict(_FAVICON_HEADERS))
    handlers = [_FaviconRedirectHandler()]
    # urllib 的 ProxyHandler 不支持 socks；配的是 socks 时交给下面的 curl_cffi 分支。
    if proxy and not proxy.lower().startswith("socks"):
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode() or 0)
            if status != 200:
                return None, "", "", status
            data = resp.read(max_bytes + 1)
            if not data or len(data) > max_bytes:
                return None, "", "", status
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return data, ctype, resp.geturl() or url, status
    except urllib.error.HTTPError as exc:
        try:
            exc.read()
        except Exception:  # noqa: BLE001 - 只是为了尽快释放连接
            pass
        return None, "", "", int(getattr(exc, "code", 0) or 0)
    except Exception:  # noqa: BLE001 - DNS / 超时 / TLS 失败都只意味着这个候选不可用
        return None, "", "", 0


def _favicon_curl_get(url, proxy, timeout, max_bytes):
    """用 curl_cffi 的 Chrome 指纹重试；同时是 socks 代理下唯一能走通的路径。"""
    curl_requests = _favicon_curl_requests()
    if curl_requests is None:
        return None, "", "", 0
    kwargs = {
        "method": "GET", "url": url, "headers": dict(_FAVICON_HEADERS),
        "timeout": timeout, "impersonate": gyqd.CURL_IMPERSONATE_BROWSER,
    }
    proxies = gyqd.proxy_mapping(proxy)
    if proxies:
        kwargs["proxies"] = proxies
    try:
        resp = curl_requests.request(**kwargs)
        status = int(resp.status_code)
        if status != 200:
            return None, "", "", status
        data = resp.content or b""
        if not data or len(data) > max_bytes:
            return None, "", "", status
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return data, ctype, str(resp.url or url), status
    except Exception:  # noqa: BLE001 - 可选路径，失败即放弃该候选
        return None, "", "", 0


def _favicon_http_get(url, proxy, timeout, max_bytes):
    """抓取单个 URL，最多读 max_bytes 字节。

    返回 (data, content_type, final_url, status)；status=0 表示连都没连上（DNS / 超时 / TLS）。
    """
    socks = bool(proxy) and proxy.lower().startswith("socks")
    if not socks:
        data, ctype, final_url, status = _favicon_urllib_get(url, proxy, timeout, max_bytes)
        if data is not None or status not in _FAVICON_WAF_STATUSES:
            return data, ctype, final_url, status
    return _favicon_curl_get(url, proxy, timeout, max_bytes)


def _favicon_attrs(chunk):
    attrs = {}
    for name, value in _FAVICON_ATTR_RE.findall(chunk or ""):
        attrs[name.lower()] = value.strip("\"'").strip()
    return attrs


def _favicon_link_score(attrs):
    """rel / sizes / type 综合打分：优先常规 icon，其次 apple-touch-icon；尺寸偏好 32~192px 与矢量图。"""
    rel = (attrs.get("rel") or "").lower()
    if "apple-touch-icon" in rel:
        score = 30
    elif "mask-icon" in rel:
        score = 5
    else:
        score = 40
    sizes = (attrs.get("sizes") or "").lower()
    if sizes == "any" or (attrs.get("type") or "").lower() == "image/svg+xml":
        score += 25
    else:
        nums = [int(n) for n in _FAVICON_SIZE_RE.findall(sizes)]
        if nums:
            best = max(nums)
            score += 20 if 32 <= best <= 192 else (12 if best > 192 else 6)
    return score


def favicon_candidates(html, page_url):
    """从首页 HTML 抽出图标候选，按可用性排序。相对路径 / 协议相对路径 / <base href> 都会解析为绝对地址。"""
    head = html.split("</head>", 1)[0] if "</head>" in html else html
    base = page_url
    base_tag = _FAVICON_BASE_RE.search(head)
    if base_tag:
        href = _favicon_attrs(base_tag.group(1)).get("href")
        if href:
            try:
                base = urljoin(page_url, href)
            except ValueError:
                base = page_url
    found = []
    for match in _FAVICON_LINK_RE.finditer(head):
        attrs = _favicon_attrs(match.group(1))
        if "icon" not in (attrs.get("rel") or "").lower():
            continue
        href = attrs.get("href") or ""
        if not href or href.lower().startswith(("javascript:", "about:", "data:")):
            continue
        try:
            url = urljoin(base, href)
        except ValueError:
            continue
        if urlparse(url).scheme not in ("http", "https"):
            continue
        found.append((_favicon_link_score(attrs), len(found), url))
    found.sort(key=lambda item: (-item[0], item[1]))
    urls, seen = [], set()
    for _, _, url in found:
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _favicon_fetch(origin, proxy):
    """按候选顺序抓取图标，返回 (data, mime)；全部失败返回 (None, "")。"""
    deadline = time.monotonic() + FAVICON_BUDGET
    candidates = []
    page, ctype, final_url, status = _favicon_http_get(origin + "/", proxy, FAVICON_TIMEOUT, FAVICON_HTML_MAX_BYTES)
    # 首页连都连不上（内网地址、域名不存在、端口关闭）时，同 origin 的 /favicon.ico 也一定连不上，
    # 再试一次只是让整个请求多等一个超时；HTTP 错误码（403/404…）则说明服务活着，值得继续试。
    if page is None and status == 0:
        return None, ""
    if page and (not ctype or "html" in ctype or "xml" in ctype):
        candidates = favicon_candidates(page.decode("utf-8", errors="replace"), final_url or origin + "/")
    # 站点没有声明 <link rel=icon>（或首页就抓不到）时回落到约定俗成的 /favicon.ico。
    fallback = origin + "/favicon.ico"
    if fallback not in candidates:
        candidates.append(fallback)
    for url in candidates[:FAVICON_MAX_CANDIDATES]:
        if time.monotonic() >= deadline:
            break
        data = _favicon_http_get(url, proxy, FAVICON_TIMEOUT, FAVICON_MAX_BYTES)[0]
        mime = sniff_image_mime(data)
        if mime:
            return data, mime
    return None, ""


def _favicon_key(origin):
    return hashlib.sha1(origin.encode("utf-8")).hexdigest()


def _favicon_cache_read(origin):
    """命中且未过期返回 {'ok': bool, 'data':, 'mime':}；未命中/过期返回 None。"""
    key = _favicon_key(origin)
    try:
        meta = json.loads((FAVICON_DIR / (key + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("origin") != origin:
        return None
    try:
        age = time.time() - float(meta.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return None
    ok = bool(meta.get("ok"))
    if age < 0 or age > (FAVICON_OK_TTL if ok else FAVICON_FAIL_TTL):
        return None
    if not ok:
        return {"ok": False, "data": None, "mime": ""}
    try:
        data = (FAVICON_DIR / (key + ".bin")).read_bytes()
    except OSError:
        return None
    return {"ok": True, "data": data, "mime": str(meta.get("mime") or "image/png")}


def _favicon_cache_write(origin, data, mime):
    """写入缓存；失败（例如数据目录只读）只影响命中率，不影响接口可用性。"""
    key = _favicon_key(origin)
    try:
        FAVICON_DIR.mkdir(parents=True, exist_ok=True)
        if data:
            (FAVICON_DIR / (key + ".bin")).write_bytes(data)
        (FAVICON_DIR / (key + ".json")).write_text(
            json.dumps({"origin": origin, "ok": bool(data), "mime": mime, "fetched_at": time.time()},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _favicon_origin_lock(key):
    with _favicon_locks_guard:
        if len(_favicon_locks) > 512:
            _favicon_locks.clear()
        lock = _favicon_locks.get(key)
        if lock is None:
            lock = _favicon_locks[key] = threading.Lock()
        return lock


def _favicon_missing():
    """抓不到图标：返回 404（带短期缓存），前端据此稳定回退到首字母。"""
    resp = jsonify({"ok": False, "error": "未找到站点图标"})
    resp.headers["Cache-Control"] = "public, max-age=1800"
    return resp, 404


@app.get("/api/favicon")
def api_favicon():
    """返回站点图标（磁盘缓存优先）。开放访问，但只接受配置里已存在的 origin。"""
    origin = favicon_origin(request.args.get("u", ""))
    if not origin:
        return jsonify({"ok": False, "error": "网址无效"}), 400
    allowed, proxy = _favicon_context()
    if origin not in allowed:
        return _favicon_missing()
    hit = _favicon_cache_read(origin)
    if hit is None:
        with _favicon_origin_lock(_favicon_key(origin)):
            hit = _favicon_cache_read(origin)  # 等锁期间可能已被同批请求抓好
            if hit is None:
                with _favicon_fetch_slots:
                    data, mime = _favicon_fetch(origin, proxy)
                _favicon_cache_write(origin, data, mime)
                hit = {"ok": bool(data), "data": data, "mime": mime}
    if not hit.get("ok"):
        return _favicon_missing()
    resp = app.response_class(hit["data"], mimetype=hit["mime"])
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # 内容来自第三方（SVG 可内嵌脚本）：直接访问该地址时用 CSP 关死脚本与外部加载。
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@app.get("/api/configs/export")
def api_export():
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify(store)


@app.post("/api/configs/import")
def api_import():
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"ok": False, "error": "请求体不是合法 JSON"}), 400

    if isinstance(payload, list):
        raw_configs, proxy_url, schedule, bookmarks_raw, groups_raw = payload, None, None, None, None
    elif isinstance(payload, dict):
        raw_configs = payload.get("configs")
        proxy_url = payload.get("proxy_url")
        schedule = payload.get("schedule")
        bookmarks_raw = payload.get("bookmarks")
        groups_raw = payload.get("link_groups")
    else:
        return jsonify({"ok": False, "error": "格式应为数组或对象"}), 400

    if not isinstance(raw_configs, list):
        return jsonify({"ok": False, "error": "缺少 configs 数组"}), 400

    cleaned = []
    for i, c in enumerate(raw_configs):
        try:
            cleaned.append(clean_config(c))
        except ValueError as exc:
            return jsonify({"ok": False, "error": "第 {0} 组配置无效：{1}".format(i + 1, exc)}), 400

    # 收藏站点为可选项：仅当导入数据提供 bookmarks 数组时才覆盖，否则保留现有。
    cleaned_bookmarks = None
    if isinstance(bookmarks_raw, list):
        cleaned_bookmarks = []
        for i, b in enumerate(bookmarks_raw):
            try:
                cleaned_bookmarks.append(clean_bookmark(b))
            except ValueError as exc:
                return jsonify({"ok": False, "error": "第 {0} 条收藏无效：{1}".format(i + 1, exc)}), 400

    # 链接分组同样为可选项：仅当导入数据提供 link_groups 数组时才覆盖。
    cleaned_groups = None
    if isinstance(groups_raw, list):
        cleaned_groups, taken = [], set()
        if len(groups_raw) > MAX_LINK_GROUPS:
            return jsonify({"ok": False, "error": "分组数量过多（最多 {0} 个）".format(MAX_LINK_GROUPS)}), 400
        for i, g in enumerate(groups_raw):
            try:
                cg = clean_link_group(g, None, taken, with_links=True)
            except ValueError as exc:
                return jsonify({"ok": False, "error": "第 {0} 个分组无效：{1}".format(i + 1, exc)}), 400
            taken.add(cg["id"])
            cleaned_groups.append(cg)

    try:
        store = read_store()
        store["configs"] = cleaned
        if proxy_url is not None:
            store["proxy_url"] = str(proxy_url or "").strip()
        if isinstance(schedule, dict):
            store["schedule"] = _clean_schedule(schedule, store.get("schedule") or {})
        if isinstance(payload, dict) and isinstance(payload.get("refresh"), dict):
            store["refresh"] = _clean_refresh(payload["refresh"], store.get("refresh") or {})
        if cleaned_bookmarks is not None:
            store["bookmarks"] = cleaned_bookmarks
        if cleaned_groups is not None:
            store["link_groups"] = cleaned_groups
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "count": len(cleaned)})


# =========================
# 路由：全局设置（管理）
# =========================

def _clean_refresh(payload, existing):
    cfg = normalize_refresh(existing)
    if "enabled" in payload:
        cfg["enabled"] = bool(payload.get("enabled"))
    if "interval_minutes" in payload:
        try:
            minutes = int(payload.get("interval_minutes"))
        except (TypeError, ValueError):
            raise ValueError("刷新间隔需为数字")
        if minutes not in REFRESH_INTERVALS:
            raise ValueError("刷新间隔需为 {0} 分钟之一".format(
                " / ".join(str(x) for x in REFRESH_INTERVALS)))
        # 换了间隔就重新计时，避免「从 24 小时改成 15 分钟」还要等到下一个 24 小时。
        if minutes != cfg["interval_minutes"]:
            cfg["interval_minutes"] = minutes
            cfg["last_run_ts"] = 0.0
    return cfg


def _clean_schedule(payload, existing):
    schedule = dict(existing or {})
    if "enabled" in payload:
        schedule["enabled"] = bool(payload.get("enabled"))
    if "time" in payload:
        t = str(payload.get("time") or "").strip()
        if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", t):
            raise ValueError("time 需为 HH:MM 格式")
        # 规整为两位小时。
        hh, mm = t.split(":")
        schedule["time"] = "{0:02d}:{1}".format(int(hh), mm)
    return schedule


@app.put("/api/settings")
def api_settings():
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    if "proxy_url" in payload:
        store["proxy_url"] = str(payload.get("proxy_url") or "").strip()
    if "schedule" in payload and isinstance(payload["schedule"], dict):
        try:
            store["schedule"] = _clean_schedule(payload["schedule"], store.get("schedule") or {})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    if "refresh" in payload and isinstance(payload["refresh"], dict):
        try:
            store["refresh"] = _clean_refresh(payload["refresh"], store.get("refresh") or {})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.get("/api/history")
def api_history():
    """运行历史含各账号的签到结果与额度，属于账号主人的私有数据；需管理密码。"""
    guard = _guard_admin()
    if guard:
        return guard
    return jsonify({"ok": True, "history": read_history()})


# 验证管理密码是否正确（前端解锁用）；通过后下发会话 Cookie 以持久保持解锁状态。
# 失败计数在这里落地：窗口内错够次数直接 429，堵住在线爆破。
@app.post("/api/auth")
def api_auth():
    if not ADMIN_PASSWORD:
        return jsonify({"ok": True})
    wait = login_retry_after()
    if wait:
        return _locked_response(wait)
    if not admin_ok():
        record_login_failure()
        remaining = max(0, LOGIN_MAX_FAILS - len(_login_fails.get(client_ip()) or []))
        return jsonify({"ok": False, "error": "管理密码不正确", "attempts_left": remaining}), 401
    clear_login_failures()
    return _set_session_cookie(jsonify({"ok": True}))


# 主动锁定：清除会话 Cookie。
@app.post("/api/logout")
def api_logout():
    return _clear_session_cookie(jsonify({"ok": True}))


# =========================
# 后台定时调度
# =========================

def _scheduler_tick():
    """每分钟检查一次：到达设定时间且当天未跑过，则执行一次全量签到。"""
    try:
        store = read_store()
    except RuntimeError:
        return
    schedule = store.get("schedule") or {}
    if not schedule.get("enabled"):
        return
    t = str(schedule.get("time") or "").strip()
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", t)
    if not m:
        return
    hh, mm = int(m.group(1)), int(m.group(2))
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    if schedule.get("last_run_date") == today:
        return
    if (now.hour, now.minute) < (hh, mm):
        return

    with _run_lock:
        # 二次确认，避免与并发触发重复。
        store = read_store()
        schedule = store.get("schedule") or {}
        if schedule.get("last_run_date") == today:
            return
        configs = store["configs"]
        try:
            results = run_checkin(configs, store["proxy_url"])
            record_history("scheduled", results)
            # 定时签到同样回写指标快照，保持签到页与手动签到一致（修复定时不回写）。
            for cfg, r in zip(configs, results):
                if r.get("status") in ("signed", "skipped"):
                    update_metric(cfg, serialize(r), mark_signed=True)
        except Exception as exc:  # noqa: BLE001
            record_history("scheduled", error="定时签到失败：{0}".format(exc))
        # 定时签到联动刷新收藏字段：配置了接口字段的收藏顺带取一次，
        # 成败状态写回各字段（失败写 error），供前端展示。
        snapshots = refresh_all_bookmarks(store)
        # 上面的签到 + 刷新可能跑了几十秒，期间用户可能保存过配置：重新读一份最新
        # store，只贴回快照与调度时间，避免把这些编辑冲掉。
        # 无论签到成功与否都标记当天已跑，避免循环重试。
        persist_field_snapshots(snapshots, schedule_run={
            "last_run_date": today, "last_run_time": _now_str(),
            # 新的一天重新计数；失败项交给 _retry_tick 稍后补签。
            "retry_count": 0, "last_attempt_ts": time.time(),
        })


def pending_configs(store, metrics=None):
    """今天还没签到成功的启用配置。

    判据用指标快照里的 last_checkin_date，而不是上一轮的 results 数组——
    快照按 base_url|user_id 索引，配置增删改排序都不会错位，当天晚些时候
    新加的配置也会被自然带上。

    metrics 可由调用方传入，避免同一次请求里重复读盘。
    """
    if metrics is None:
        metrics = read_metrics()
    today = _today_str()
    pending = []
    for cfg in store.get("configs") or []:
        if not cfg.get("enabled", True):
            continue
        snap = metrics.get(metrics_key(cfg)) or {}
        if snap.get("last_checkin_date") != today:
            pending.append(cfg)
    return pending


def _retry_tick():
    """补签：定时签到当天失败的账号，隔一段时间自动再试几次。

    原先无论成功与否都把当天标记为「已跑」，于是 08:30 恰好断网就等于这天彻底没签。
    这里只重跑「今天还没签成功」的账号，不会给已成功的账号重复发请求。
    """
    if SCHEDULE_RETRY_LIMIT <= 0:
        return
    try:
        store = read_store()
    except RuntimeError:
        return
    schedule = store.get("schedule") or {}
    if not schedule.get("enabled"):
        return
    today = _today_str()
    if schedule.get("last_run_date") != today:
        return  # 当天的主轮次还没跑，轮不到补签
    try:
        attempts = int(schedule.get("retry_count") or 0)
    except (TypeError, ValueError):
        attempts = 0
    if attempts >= SCHEDULE_RETRY_LIMIT:
        return
    try:
        last_ts = float(schedule.get("last_attempt_ts") or 0)
    except (TypeError, ValueError):
        last_ts = 0.0
    now = time.time()
    # 时钟回拨（now - last_ts < 0）同样当作「等够了」，否则会卡住不再补签。
    if last_ts and 0 <= now - last_ts < SCHEDULE_RETRY_DELAY_MIN * 60:
        return
    pending = pending_configs(store)
    if not pending:
        return

    with _run_lock:
        # 二次确认：等锁期间可能已经被手动签到补上了。
        store = read_store()
        schedule = store.get("schedule") or {}
        if schedule.get("last_run_date") != today:
            return
        pending = pending_configs(store)
        if not pending:
            return
        try:
            results = run_checkin(pending, store["proxy_url"])
            record_history("retry", results)
            for cfg, r in zip(pending, results):
                if r.get("status") in ("signed", "skipped"):
                    update_metric(cfg, serialize(r), mark_signed=True)
        except Exception as exc:  # noqa: BLE001
            record_history("retry", error="补签失败：{0}".format(exc))
        persist_field_snapshots({}, schedule_run={
            "retry_count": attempts + 1, "last_attempt_ts": time.time(),
        })


def _refresh_tick():
    """按设定间隔刷新站点看板的全部接口字段，让余额 / 到期时间不必手点也保持新鲜。

    与定时签到相互独立：签到一天一次，刷新按分钟级间隔。上一轮还没跑完就跳过本轮
    （站点慢的时候不堆叠请求）。
    """
    try:
        store = read_store()
    except RuntimeError:
        return
    cfg = store.get("refresh") or {}
    if not cfg.get("enabled"):
        return
    if not any(b.get("fields") for b in store.get("bookmarks") or []):
        return
    interval = int(cfg.get("interval_minutes") or DEFAULT_REFRESH_INTERVAL) * 60
    last = float(cfg.get("last_run_ts") or 0)
    now = time.time()
    # last 为 0（从未跑过 / 刚开启）时立刻跑一次，让用户马上看到效果。
    # 时钟回拨会让 now - last 变负数，同样当作「该跑了」。
    if last and 0 <= now - last < interval:
        return
    if not _refresh_lock.acquire(False):
        return  # 上一轮还在跑
    try:
        persist_field_snapshots(refresh_all_bookmarks(store), refresh_run=True)
    finally:
        _refresh_lock.release()


def _scheduler_loop():
    while True:
        for tick in (_scheduler_tick, _retry_tick, _refresh_tick):
            try:
                tick()
            except Exception:  # noqa: BLE001 - 调度线程必须长存。
                pass
        time.sleep(30)


def start_scheduler():
    global _sched_thread
    if not SCHEDULER_ENABLED or _sched_thread is not None:
        return
    _sched_thread = threading.Thread(target=_scheduler_loop, name="gyqd-scheduler", daemon=True)
    _sched_thread.start()


# 模块导入即启动调度（gunicorn 单 worker 下仅启动一次）。
start_scheduler()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5525")))
