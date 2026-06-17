#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
new-api 多平台自动签到脚本
YanX
"""

# =========================
# 配置区
# =========================

CONFIGS = [
    {
        "name": "烁",
        "base_url": "https://elysiver.h-e.top",
        "user_id": "29929",
        "access_token": "xxxxxxxxxxxxxxxxx",
        "enabled": True,
        "turnstile": "",
    }
]

# 全局代理：留空表示不使用代理；只需配置一个代理，所有平台请求都会共用。
# 示例：http://127.0.0.1:7890、https://127.0.0.1:7890、socks5://127.0.0.1:7890
PROXY_URL = ""
# 浏览器 User-Agent；所有请求都会使用该 UA。
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"

# 单次请求超时时间，单位秒；网络慢可适当调大。
TIMEOUT_SECONDS = 20
# 网络错误重试次数；登录失败、令牌无效这类业务错误不会重试。
RETRY_TIMES = 3
# 每次网络重试前的等待时间，单位秒。
RETRY_INTERVAL_SECONDS = 2

ENABLE_COLOR = True
QUOTA_PER_UNIT = 500000
QUOTA_DECIMALS = 2
USE_BROWSER_TLS_FINGERPRINT = True
AUTO_INSTALL_DEPENDENCIES = True
CURL_CFFI_PACKAGE = "curl_cffi"
CURL_IMPERSONATE_BROWSER = "chrome"
DEPENDENCY_ERRORS = {}
PROCESS_LOG_ENABLED = False
PROCESS_LOG_FORCE_COLOR = False

# =========================
# 脚本逻辑区
# =========================

import datetime as _datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import importlib
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import time
import traceback
import unicodedata

try:
    from urllib import parse as _urlparse
    from urllib import request as _urlrequest
    from urllib import error as _urlerror
except ImportError:  # pragma: no cover - Python 2 不支持本脚本主体逻辑。
    print("当前解释器过旧，请使用 Python 3 运行：python3 new_api_auto_checkin.py")
    sys.exit(2)


class CheckinError(Exception):
    """签到流程中的可预期错误。"""


def normalize_proxy_url(proxy_url):
    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return ""
    if "://" not in proxy_url:
        proxy_url = "http://{0}".format(proxy_url)

    parsed = _urlparse.urlparse(proxy_url)
    if parsed.scheme not in ("http", "https", "socks4", "socks5", "socks5h"):
        raise CheckinError("代理协议不支持：{0}".format(parsed.scheme or "未知"))
    if not parsed.netloc:
        raise CheckinError("代理地址格式不正确")
    return proxy_url


def proxy_mapping(proxy_url):
    proxy_url = normalize_proxy_url(proxy_url)
    if not proxy_url:
        return {}
    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def proxy_scheme(proxy_url):
    proxy_url = normalize_proxy_url(proxy_url)
    if not proxy_url:
        return ""
    return _urlparse.urlparse(proxy_url).scheme


def is_socks_proxy(proxy_url):
    return proxy_scheme(proxy_url) in ("socks4", "socks5", "socks5h")


def build_urllib_opener(proxy_url):
    mapping = proxy_mapping(proxy_url)
    if not mapping:
        return _urlrequest.urlopen

    scheme = proxy_scheme(next(iter(mapping.values())))
    if scheme not in ("http", "https"):
        raise CheckinError("标准库 urllib 不支持 {0} 代理，请启用 curl_cffi 或改用 http/https 代理".format(scheme))
    return _urlrequest.build_opener(_urlrequest.ProxyHandler(mapping)).open


# =========================
# 阿里云盾 acw_sc__v2 反爬绕过
# =========================
# 部分站点（如 anyrouter）由阿里云盾防护：首个请求返回 HTTP 200，但响应体是一段
# 混淆 JS（含 var arg1='<hex>'）。浏览器执行后用固定算法把 arg1 变换为 acw_sc__v2
# cookie，再带该 cookie 重放才放行。此处纯 Python 复刻该变换，免去执行 JS。
# unsbox 的位置映射表与 hexXor 异或掩码为阿里云盾通用默认值，与挑战 JS 的内联数组一致。

_ACW_SC_V2_POS = [
    0x0f, 0x23, 0x1d, 0x18, 0x21, 0x10, 0x01, 0x26, 0x0a, 0x09,
    0x13, 0x1f, 0x28, 0x1b, 0x16, 0x17, 0x19, 0x0d, 0x06, 0x0b,
    0x27, 0x12, 0x14, 0x08, 0x0e, 0x15, 0x20, 0x1a, 0x02, 0x1e,
    0x07, 0x04, 0x11, 0x05, 0x03, 0x1c, 0x22, 0x25, 0x0c, 0x24,
]
_ACW_SC_V2_MASK = "3000176000856006061501533003690027800375"
_ACW_SC_V2_ARG1_RE = re.compile(r"arg1\s*=\s*['\"]([0-9A-Fa-f]+)['\"]")


def _acw_unsbox(arg1):
    """按位置映射表重排 arg1 字符（对应挑战 JS 的 unsbox 函数）。"""
    out = [""] * len(_ACW_SC_V2_POS)
    for i, ch in enumerate(arg1):
        for j, pos in enumerate(_ACW_SC_V2_POS):
            if pos == i + 1:
                out[j] = ch
    return "".join(out)


def _acw_hex_xor(text, mask):
    """逐两位十六进制做异或（对应挑战 JS 的 hexXor 函数）。"""
    n = min(len(text), len(mask))
    parts = []
    for i in range(0, n - 1, 2):
        parts.append("{:02x}".format(int(text[i:i + 2], 16) ^ int(mask[i:i + 2], 16)))
    return "".join(parts)


def compute_acw_sc_v2(html):
    """从 acw_sc__v2 挑战页解析 arg1 并算出 cookie 值；非挑战页或解析失败返回 None。"""
    if not html or "arg1" not in html:
        return None
    match = _ACW_SC_V2_ARG1_RE.search(html)
    if not match:
        return None
    arg1 = match.group(1)
    if len(arg1) != len(_ACW_SC_V2_POS):
        return None  # 长度不符说明非标准 v2 挑战，交由上层按原错误处理。
    try:
        return _acw_hex_xor(_acw_unsbox(arg1), _ACW_SC_V2_MASK)
    except (ValueError, IndexError):
        return None


def _merge_cookie_header(existing, name, value):
    """把 name=value 合并进已有 Cookie 头字符串（覆盖同名项）。"""
    name = str(name).strip()
    pairs = []
    for chunk in str(existing or "").split(";"):
        chunk = chunk.strip()
        if not chunk or chunk.split("=", 1)[0].strip() == name:
            continue
        pairs.append(chunk)
    pairs.append("{0}={1}".format(name, str(value).strip()))
    return "; ".join(pairs)


def _cookies_from_headers(headers):
    """从响应头的 Set-Cookie 提取 name=value 串（忽略 Path/Expires 等属性）。"""
    if headers is None:
        return ""
    raw = []
    getter = getattr(headers, "get_all", None)
    if callable(getter):
        raw = getter("Set-Cookie") or []
    else:
        value = headers.get("Set-Cookie") if hasattr(headers, "get") else None
        if value:
            raw = [value]
    pairs = []
    for item in raw:
        first = str(item).split(";", 1)[0].strip()
        if "=" in first:
            pairs.append(first)
    return "; ".join(pairs)


class HttpClient(object):
    """基于标准库 urllib 的最小 HTTP 客户端。"""

    def __init__(self, retry_times=RETRY_TIMES, retry_interval=RETRY_INTERVAL_SECONDS, opener=None):
        self.retry_times = max(1, int(retry_times or 1))
        self.retry_interval = max(0, float(retry_interval or 0))
        self.proxy_url = normalize_proxy_url(PROXY_URL)
        self.startup_error = None
        self.curl_requests = None if opener else load_curl_requests()
        if opener:
            self.opener = opener
        elif self.curl_requests is not None:
            emit_process_log("依赖检查", "请求将使用 curl_cffi，并启用浏览器 TLS 指纹", "ok")
            self.opener = _urlrequest.urlopen
        elif is_socks_proxy(self.proxy_url):
            reason = DEPENDENCY_ERRORS.get(CURL_CFFI_PACKAGE) or "curl_cffi 未安装或无法导入"
            self.startup_error = CheckinError(
                "SOCKS 代理需要 curl_cffi，但自动安装/导入失败：{0}；请在当前解释器执行：{1} -m pip install -U {2}".format(
                    reason,
                    sys.executable,
                    CURL_CFFI_PACKAGE,
                )
            )
            emit_process_log("依赖检查", "SOCKS 代理需要 curl_cffi，但当前未能加载", "fail")
            self.opener = _urlrequest.urlopen
        else:
            self.opener = build_urllib_opener(self.proxy_url)
            if self.proxy_url:
                emit_process_log("依赖检查", "请求将使用标准库 urllib，并通过 HTTP/HTTPS 代理连接", "ok")
            else:
                emit_process_log("依赖检查", "请求将使用标准库 urllib 直连", "ok")

    def request_json(self, method, url, headers=None, timeout=TIMEOUT_SECONDS):
        if self.startup_error is not None:
            raise self.startup_error

        last_error = None
        for attempt in range(1, self.retry_times + 1):
            try:
                return self._request_json_once(method, url, headers=headers, timeout=timeout)
            except CheckinError as exc:
                last_error = exc
                if attempt >= self.retry_times:
                    break
                if self.retry_interval:
                    time.sleep(self.retry_interval)
        raise CheckinError("{0}（已尝试 {1} 次）".format(last_error, self.retry_times))

    def _request_json_once(self, method, url, headers=None, timeout=TIMEOUT_SECONDS):
        headers = dict(headers or {})
        status, body, set_cookie = self._fetch_raw(method, url, headers, timeout)
        # 阿里云盾 acw_sc__v2 挑战：HTTP 200 但返回混淆 JS 而非 JSON。复刻其算法算出
        # cookie，连同首次响应下发的 cookie（如 acw_tc）一起重放一次。
        acw = compute_acw_sc_v2(body)
        if acw is not None:
            cookie = headers.get("Cookie") or ""
            for piece in str(set_cookie or "").split(";"):
                name, sep, value = piece.strip().partition("=")
                if sep and name != "acw_sc__v2":
                    cookie = _merge_cookie_header(cookie, name, value)
            cookie = _merge_cookie_header(cookie, "acw_sc__v2", acw)
            retry_headers = dict(headers, Cookie=cookie)
            status, body, _ = self._fetch_raw(method, url, retry_headers, timeout)
        return status, parse_json_body(body)

    def _fetch_raw(self, method, url, headers, timeout):
        """发起一次请求，返回 (status, 原始响应体, 首响 Set-Cookie 串)。"""
        if self.curl_requests is not None:
            return self._fetch_raw_curl(method, url, headers, timeout)
        return self._fetch_raw_urllib(method, url, headers, timeout)

    def _fetch_raw_urllib(self, method, url, headers, timeout):
        req = _urlrequest.Request(url=url, headers=headers, method=method)
        context = ssl.create_default_context()
        try:
            with self.opener(req, timeout=timeout, context=context) as resp:
                status = getattr(resp, "status", resp.getcode())
                body = resp.read().decode("utf-8", errors="replace")
                return status, body, _cookies_from_headers(getattr(resp, "headers", None))
        except _urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return exc.code, body, _cookies_from_headers(getattr(exc, "headers", None))
        except _urlerror.URLError as exc:
            raise CheckinError("网络请求失败：{0}".format(exc.reason))
        except (TimeoutError, OSError) as exc:
            raise CheckinError("网络请求失败：{0}".format(exc))

    def _fetch_raw_curl(self, method, url, headers, timeout):
        try:
            request_kwargs = {
                "method": method,
                "url": url,
                "headers": headers or {},
                "timeout": timeout,
                "impersonate": CURL_IMPERSONATE_BROWSER,
            }
            proxies = proxy_mapping(self.proxy_url)
            if proxies:
                request_kwargs["proxies"] = proxies
            resp = self.curl_requests.request(**request_kwargs)
            try:
                cookie_str = "; ".join(
                    "{0}={1}".format(k, v) for k, v in dict(resp.cookies).items()
                )
            except Exception:  # noqa: BLE001 - cookie 解析失败不影响主流程。
                cookie_str = ""
            return int(resp.status_code), resp.text, cookie_str
        except Exception as exc:
            raise CheckinError("网络请求失败：{0}".format(exc))


def load_curl_requests():
    """优先启用 curl_cffi，让 TLS/HTTP2 指纹更接近真实 Chrome。"""
    if not USE_BROWSER_TLS_FINGERPRINT:
        emit_process_log("依赖检查", "未启用浏览器 TLS 指纹，将使用标准库 urllib", "skip")
        return None

    module = import_optional_module("curl_cffi.requests", CURL_CFFI_PACKAGE)
    if module is not None:
        emit_process_log("依赖检查", "curl_cffi 已可用，启用 Chrome TLS 指纹", "ok")
    return module


def import_optional_module(module_name, package_name):
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        DEPENDENCY_ERRORS[package_name] = "导入失败：{0}".format(compact_error(exc))
        if not AUTO_INSTALL_DEPENDENCIES:
            emit_process_log("依赖检查", "{0} 不可用，且未开启自动安装".format(package_name), "fail")
            return None

    errors = []
    emit_process_log("依赖检查", "{0} 未安装，准备自动安装".format(package_name), "warn")
    for step in dependency_install_steps(package_name):
        name = step["name"]
        command = step["command"]
        emit_process_log("依赖安装", "尝试 {0}".format(name), "info")
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if completed.returncode != 0:
                reason = compact_error(completed.stderr or completed.stdout or "exit {0}".format(completed.returncode))
                emit_process_log("依赖安装", "{0} 失败：{1}".format(name, reason), "fail")
                errors.append("{0}: {1}".format(name, reason))
                continue
            if not step["try_import"]:
                emit_process_log("依赖安装", "{0} 完成，继续后续安装步骤".format(name), "ok")
                continue
            try:
                module = importlib.import_module(module_name)
                emit_process_log("依赖安装", "{0} 安装成功，依赖已可用".format(name), "ok")
                return module
            except ImportError as exc:
                reason = compact_error(exc)
                emit_process_log("依赖安装", "{0} 安装后仍无法导入：{1}".format(name, reason), "fail")
                errors.append("{0}: 安装后仍无法导入：{1}".format(name, reason))
        except Exception as exc:
            reason = compact_error(exc)
            emit_process_log("依赖安装", "{0} 失败：{1}".format(name, reason), "fail")
            errors.append("{0}: {1}".format(name, reason))

    DEPENDENCY_ERRORS[package_name] = compact_error(" | ".join(errors) or "自动安装失败")
    emit_process_log("依赖检查", "{0} 自动安装失败".format(package_name), "fail")
    return None


def dependency_install_steps(package_name, command_lookup=None):
    command_lookup = command_lookup or shutil.which
    steps = pip_install_steps("python -m pip", [sys.executable, "-m", "pip"], package_name)
    steps.extend([
        {
            "name": "python -m ensurepip",
            "command": [sys.executable, "-m", "ensurepip", "--upgrade"],
            "try_import": False,
        },
    ])
    steps.extend(pip_install_steps("python -m pip", [sys.executable, "-m", "pip"], package_name))

    seen = set()
    for command_name in ("pip3", "pip"):
        command_path = command_lookup(command_name)
        if not command_path or command_path in seen:
            continue
        seen.add(command_path)
        steps.extend(pip_install_steps(command_name, [command_path], package_name))

    steps.extend(system_pip_install_steps(command_lookup=command_lookup))
    steps.extend(pip_install_steps("python -m pip", [sys.executable, "-m", "pip"], package_name))

    for command_name in ("pip3", "pip"):
        steps.extend(pip_install_steps(command_name, [command_name], package_name))
    return steps


def pip_install_steps(name, base_command, package_name):
    return [
        {
            "name": name,
            "command": list(base_command) + ["install", "--quiet", package_name],
            "try_import": True,
        },
        {
            "name": "{0} --user".format(name),
            "command": list(base_command) + ["install", "--user", "--quiet", package_name],
            "try_import": True,
        },
    ]


def system_pip_install_steps(command_lookup=None, geteuid=None):
    command_lookup = command_lookup or shutil.which
    package_managers = [
        {
            "command": "apt-get",
            "steps": [
                ("apt-get update", ["apt-get", "update"]),
                ("apt-get install python3-pip", ["apt-get", "install", "-y", "python3-pip"]),
            ],
        },
        {
            "command": "apt",
            "steps": [
                ("apt update", ["apt", "update"]),
                ("apt install python3-pip", ["apt", "install", "-y", "python3-pip"]),
            ],
        },
        {
            "command": "dnf",
            "steps": [("dnf install python3-pip", ["dnf", "install", "-y", "python3-pip"])],
        },
        {
            "command": "yum",
            "steps": [("yum install python3-pip", ["yum", "install", "-y", "python3-pip"])],
        },
        {
            "command": "apk",
            "steps": [("apk add py3-pip", ["apk", "add", "--no-cache", "py3-pip"])],
        },
        {
            "command": "pacman",
            "steps": [("pacman install python-pip", ["pacman", "-Sy", "--noconfirm", "python-pip"])],
        },
        {
            "command": "zypper",
            "steps": [("zypper install python3-pip", ["zypper", "--non-interactive", "install", "python3-pip"])],
        },
    ]

    steps = []
    for manager in package_managers:
        command_path = command_lookup(manager["command"])
        if not command_path:
            continue
        for name, command in manager["steps"]:
            command = [command_path] + command[1:]
            steps.append(
                {
                    "name": name,
                    "command": maybe_sudo_command(command, command_lookup=command_lookup, geteuid=geteuid),
                    "try_import": False,
                }
            )
    return steps


def maybe_sudo_command(command, command_lookup=None, geteuid=None):
    command_lookup = command_lookup or shutil.which
    geteuid = geteuid or getattr(os, "geteuid", None)
    if not callable(geteuid):
        return command
    try:
        if geteuid() == 0:
            return command
    except Exception:
        return command

    sudo_path = command_lookup("sudo")
    if sudo_path:
        return [sudo_path, "-n"] + command
    return command


def compact_error(error):
    text = str(error or "").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return "未知错误"
    message = "；".join(lines[:2])
    if len(message) > 180:
        return message[:177] + "..."
    return message


def ensure_python3():
    """提前阻断 Python 2，避免运行到 urllib 行为差异后才失败。"""
    if sys.version_info[0] < 3:
        python3 = shutil.which("python3")
        if python3:
            print("检测到当前不是 Python 3，请改用：{0} {1}".format(python3, sys.argv[0]))
        else:
            print("当前不是 Python 3，且未找到 python3 命令。请先安装 Python 3。")
        sys.exit(2)


def runtime_summary():
    """输出运行环境，方便定位 python/python3 命令差异。"""
    python_cmds = []
    for cmd in ("python", "python3"):
        path = shutil.which(cmd)
        if path:
            python_cmds.append("{0}={1}".format(cmd, path))
    return {
        "system": platform.system(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "commands": ", ".join(python_cmds) if python_cmds else "未发现",
        "cwd": os.getcwd(),
    }


def normalize_base_url(base_url):
    base_url = str(base_url or "").strip()
    if not base_url:
        raise CheckinError("base_url 为空")
    if not base_url.startswith(("http://", "https://")):
        raise CheckinError("base_url 必须以 http:// 或 https:// 开头")
    return base_url.rstrip("/")


def build_headers(config):
    token = str(config.get("access_token", "")).strip()
    user_id = str(config.get("user_id", "")).strip()
    if not token or token in ("USER_ACCESS_TOKEN", "USER_ACCESS_TOKEN_2"):
        raise CheckinError("access_token 未配置")
    if not user_id:
        raise CheckinError("user_id 未配置")

    auth_value = token if token.lower().startswith("bearer ") else "Bearer {0}".format(token)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Authorization": auth_value,
        "Cache-Control": "no-cache",
        "New-Api-User": user_id,
        "Pragma": "no-cache",
        "Priority": "u=1, i",
        "Sec-CH-UA": '"Chromium";v="148", "Google Chrome";v="148", "Not=A?Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": USER_AGENT,
    }
    base_url = str(config.get("base_url", "")).strip()
    if base_url:
        origin = normalize_base_url(base_url)
        headers["Origin"] = origin
        headers["Referer"] = "{0}/".format(origin)
    return headers


def parse_json_body(body):
    if not body:
        return {}
    try:
        return json.loads(body)
    except ValueError:
        return {"success": False, "message": body}


def api_url(config, path, params=None):
    base = normalize_base_url(config.get("base_url", ""))
    query = _urlparse.urlencode(params or {})
    if query:
        return "{0}{1}?{2}".format(base, path, query)
    return "{0}{1}".format(base, path)


def current_month():
    return _datetime.datetime.now().strftime("%Y-%m")


def api_message(payload, default="未知错误"):
    if not isinstance(payload, dict):
        return default
    for key in ("message", "error", "msg"):
        value = payload.get(key)
        if value:
            return str(value)
    return default


def is_checked_in_today(payload):
    if not isinstance(payload, dict):
        return False
    data = payload.get("data") or {}
    stats = data.get("stats") or {}
    return bool(stats.get("checked_in_today"))


def quota_awarded(payload):
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return None
    return data.get("quota_awarded")


def to_int_or_none(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_wallet(payload):
    """从 /api/user/self 的响应中提取账户额度信息。"""
    if not isinstance(payload, dict):
        raise CheckinError("钱包额度响应不是 JSON 对象")

    candidates = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.insert(0, data)
        user = data.get("user")
        if isinstance(user, dict):
            candidates.insert(0, user)

    for source in candidates:
        wallet = {
            "quota": to_int_or_none(source.get("quota")),
            "used_quota": to_int_or_none(source.get("used_quota")),
            "request_count": to_int_or_none(source.get("request_count")),
        }
        if any(value is not None for value in wallet.values()):
            return wallet

    raise CheckinError("钱包额度字段不存在")


def get_wallet(config, client):
    url = api_url(config, "/api/user/self")
    status, payload = client.request_json("GET", url, headers=build_headers(config))
    if status >= 400 or payload.get("success") is False:
        raise CheckinError("查询钱包额度失败：HTTP {0}，{1}".format(status, api_message(payload)))
    return extract_wallet(payload)


def result_item(name, status, message, quota=None, wallet=None, wallet_status="skipped", wallet_message="未查询"):
    return {
        "name": name,
        "status": status,
        "message": message,
        "quota": quota,
        "wallet": wallet,
        "wallet_status": wallet_status,
        "wallet_message": wallet_message,
    }


def attach_wallet(result, config, client):
    try:
        result["wallet"] = get_wallet(config, client)
        result["wallet_status"] = "ok"
        result["wallet_message"] = "额度已获取"
    except Exception as exc:
        result["wallet"] = None
        result["wallet_status"] = "failed"
        result["wallet_message"] = "钱包额度获取失败：{0}".format(exc)
    return result


def check_status(config, client):
    url = api_url(config, "/api/user/checkin", {"month": current_month()})
    status, payload = client.request_json("GET", url, headers=build_headers(config))
    if status >= 400 or payload.get("success") is False:
        raise CheckinError("查询签到状态失败：HTTP {0}，{1}".format(status, api_message(payload)))
    return payload


def do_checkin(config, client):
    params = {}
    turnstile = str(config.get("turnstile", "")).strip()
    if turnstile:
        params["turnstile"] = turnstile

    url = api_url(config, "/api/user/checkin", params)
    status, payload = client.request_json("POST", url, headers=build_headers(config))
    message = api_message(payload, "")

    if status >= 400 or payload.get("success") is False:
        if "今日已签到" in message:
            return {
                "status": "skipped",
                "message": "服务端返回今日已签到",
                "quota": None,
            }
        raise CheckinError("执行签到失败：HTTP {0}，{1}".format(status, message or "未知错误"))

    return {
        "status": "signed",
        "message": message or "签到成功",
        "quota": quota_awarded(payload),
    }


def run_one(config, client):
    name = str(config.get("name", "") or config.get("base_url", "未命名平台"))
    if not config.get("enabled", True):
        return result_item(name, "disabled", "配置已禁用")

    try:
        status_payload = check_status(config, client)
        if is_checked_in_today(status_payload):
            return attach_wallet(result_item(name, "skipped", "今日已签到，跳过"), config, client)

        result = do_checkin(config, client)
        result["name"] = name
        result.setdefault("wallet", None)
        result.setdefault("wallet_status", "skipped")
        result.setdefault("wallet_message", "未查询")
        return attach_wallet(result, config, client)
    except Exception as exc:
        return result_item(name, "failed", str(exc))


def run_all(configs, client=None):
    client = client or HttpClient()
    ensure_client_ready(client)
    results = []
    for config in configs:
        name = str(config.get("name", "") or config.get("base_url", "未命名平台"))
        emit_process_log("自动签到", "开始处理 {0}".format(name), "info")
        result = run_one(config, client)
        results.append(result)
        label, _ = STATUS_STYLE.get(result["status"], (result["status"], "gray"))
        emit_process_log("自动签到", "{0}：{1}，{2}".format(name, label, result_note(result)), result["status"])
    return results


def ensure_client_ready(client):
    startup_error = getattr(client, "startup_error", None)
    if startup_error is not None:
        raise startup_error
    return client


ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")
COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "gray": "\033[90m",
}
STATUS_STYLE = {
    "signed": ("成功", "green"),
    "skipped": ("跳过", "cyan"),
    "disabled": ("禁用", "gray"),
    "failed": ("失败", "red"),
}


def color_enabled(force=False):
    if not ENABLE_COLOR or os.environ.get("NO_COLOR"):
        return False
    if force:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def colorize(text, color, enabled=True):
    if not enabled or color not in COLORS:
        return text
    return "{0}{1}{2}".format(COLORS[color], text, COLORS["reset"])


def enable_process_logs(force_color=False):
    global PROCESS_LOG_ENABLED, PROCESS_LOG_FORCE_COLOR
    PROCESS_LOG_ENABLED = True
    PROCESS_LOG_FORCE_COLOR = bool(force_color)


def emit_process_log(stage, message, status="info"):
    if not PROCESS_LOG_ENABLED:
        return
    print_process_log(stage, message, status=status, force_color=PROCESS_LOG_FORCE_COLOR)


def print_process_log(stage, message, status="info", force_color=False):
    use_color = color_enabled(force_color)
    colors = {
        "info": "cyan",
        "ok": "green",
        "warn": "yellow",
        "fail": "red",
        "failed": "red",
        "signed": "green",
        "skipped": "cyan",
        "disabled": "gray",
    }
    color = colors.get(status, "cyan")
    timestamp = _datetime.datetime.now().strftime("%H:%M:%S")
    prefix = "[{0}] {1}：".format(timestamp, stage)
    print("{0}{1}".format(colorize(prefix, color, use_color), message))


def print_dependency_failure(error, force_color=False):
    use_color = color_enabled(force_color)
    print("")
    print("{0}{1}".format(colorize("依赖准备失败：", "red", use_color), error))


def strip_ansi(text):
    return ANSI_PATTERN.sub("", str(text))


def display_width(text):
    width = 0
    for char in strip_ansi(text):
        width += 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
    return width


def pad_right(text, width):
    text = str(text)
    return text + " " * max(0, width - display_width(text))


def status_text(status, use_color):
    label, color = STATUS_STYLE.get(status, (status, "gray"))
    return colorize(label, color, use_color)


def quota_display_amount(value):
    if value is None:
        return None
    try:
        precision = Decimal("1").scaleb(-QUOTA_DECIMALS)
        amount = Decimal(int(value)) / Decimal(str(QUOTA_PER_UNIT))
        return amount.quantize(precision, rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, InvalidOperation):
        return None


def format_quota(value):
    if value is None:
        return "-"
    amount = quota_display_amount(value)
    if amount is None:
        return str(value)
    return str(amount)


def format_quota_total(values):
    total = Decimal("0")
    for value in values:
        amount = quota_display_amount(value)
        if amount is not None:
            total += amount
    return str(total)


def format_count(value):
    if value is None:
        return "-"
    try:
        return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return str(value)


def wallet_value(item, key):
    wallet = item.get("wallet") or {}
    if not isinstance(wallet, dict):
        return None
    return wallet.get(key)


def result_note(item):
    message = item.get("message") or ""
    wallet_status = item.get("wallet_status")
    if wallet_status == "failed":
        return "{0}；{1}".format(message, item.get("wallet_message") or "钱包额度未知")
    return message


def print_runtime():
    info = runtime_summary()
    print("运行环境：{0} / Python {1}".format(info["system"], info["python"]))
    print("解释器：{0}".format(info["executable"]))
    print("命令识别：{0}".format(info["commands"]))
    print("")


def print_summary(results, force_color=False):
    use_color = color_enabled(force_color)
    if use_color and platform.system().lower().startswith("win"):
        os.system("")

    total = len(results)
    signed = sum(1 for item in results if item["status"] == "signed")
    skipped = sum(1 for item in results if item["status"] == "skipped")
    disabled = sum(1 for item in results if item["status"] == "disabled")
    failed = sum(1 for item in results if item["status"] == "failed")
    quota_values = [item.get("quota") for item in results]
    wallet_ok = sum(1 for item in results if item.get("wallet_status") == "ok")
    wallet_failed = sum(1 for item in results if item.get("wallet_status") == "failed")
    wallet_values = [wallet_value(item, "quota") for item in results]

    print(colorize("执行结果", "bold", use_color))
    columns = [
        ("状态", 6),
        ("平台", 18),
        ("签到奖励", 12),
        ("钱包余额", 14),
        ("已用额度", 14),
        ("请求数", 8),
        ("说明", 38),
    ]
    header = "  ".join(pad_right(title, width) for title, width in columns)
    print(colorize(header, "bold", use_color))
    print(colorize("-" * display_width(header), "gray", use_color))
    for item in results:
        row = [
            pad_right(status_text(item["status"], use_color), 6),
            pad_right(item["name"], 18),
            pad_right(format_quota(item.get("quota")), 12),
            pad_right(format_quota(wallet_value(item, "quota")), 14),
            pad_right(format_quota(wallet_value(item, "used_quota")), 14),
            pad_right(format_count(wallet_value(item, "request_count")), 8),
            pad_right(result_note(item), 38),
        ]
        print("  ".join(row))

    print("")
    print(colorize("最终统计", "bold", use_color))
    print("配置总数：{0} | 成功：{1} | 已签到跳过：{2} | 禁用：{3} | 失败：{4}".format(
        total,
        colorize(str(signed), "green", use_color),
        colorize(str(skipped), "cyan", use_color),
        colorize(str(disabled), "gray", use_color),
        colorize(str(failed), "red" if failed else "green", use_color),
    ))
    print("本次累计获得额度：{0} | 已获取钱包：{1} | 钱包查询失败：{2} | 钱包余额合计：{3}".format(
        colorize(format_quota_total(quota_values), "green", use_color),
        colorize(str(wallet_ok), "green", use_color),
        colorize(str(wallet_failed), "yellow" if wallet_failed else "green", use_color),
        format_quota_total(wallet_values),
    ))


MAIN_ARGS = ("--force-color", "--no-color")


def unsupported_args(args):
    return [arg for arg in args if arg not in MAIN_ARGS]


def main():
    ensure_python3()
    force_color = "--force-color" in sys.argv
    if "--no-color" in sys.argv:
        os.environ["NO_COLOR"] = "1"

    unknown_args = unsupported_args(sys.argv[1:])
    if unknown_args:
        print("不支持的参数：{0}".format(", ".join(unknown_args)))
        print("可用参数：{0}".format(", ".join(MAIN_ARGS)))
        return 2

    print_runtime()
    if color_enabled(force_color) and platform.system().lower().startswith("win"):
        os.system("")
    enable_process_logs(force_color=force_color)

    if not CONFIGS:
        print("未配置任何平台，请在脚本顶部 CONFIGS 中添加配置。")
        return 1

    try:
        emit_process_log("启动", "检查请求引擎和代理依赖", "info")
        client = HttpClient()
        ensure_client_ready(client)
        emit_process_log("自动签到", "开始处理 {0} 个配置".format(len(CONFIGS)), "info")
        results = run_all(CONFIGS, client=client)
        emit_process_log("自动签到", "平台处理完成，生成最终统计", "ok")
        print("")
        print_summary(results, force_color=force_color)
        return 1 if any(item["status"] == "failed" for item in results) else 0
    except CheckinError as exc:
        print_dependency_failure(exc, force_color=force_color)
        return 1
    except KeyboardInterrupt:
        print("用户中断。")
        return 130
    except Exception:
        print("脚本异常：")
        traceback.print_exc()
        return 1


def _self_test():
    def capture_output(func, *args, **kwargs):
        import io as _io

        old_stdout = sys.stdout
        buffer = _io.StringIO()
        try:
            sys.stdout = buffer
            func(*args, **kwargs)
            return buffer.getvalue()
        finally:
            sys.stdout = old_stdout

    class FakeClient(object):
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []

        def request_json(self, method, url, headers=None, timeout=TIMEOUT_SECONDS):
            self.calls.append((method, url, headers or {}))
            if not self.responses:
                raise AssertionError("没有预置响应")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    checked_payload = {
        "success": True,
        "data": {"stats": {"checked_in_today": True}},
    }
    unchecked_payload = {
        "success": True,
        "data": {"stats": {"checked_in_today": False}},
    }
    signed_payload = {
        "success": True,
        "message": "签到成功",
        "data": {"quota_awarded": 1888, "checkin_date": "2026-06-03"},
    }
    wallet_payload = {
        "success": True,
        "data": {"quota": 120000, "used_quota": 34567, "request_count": 89},
    }

    assert is_checked_in_today(checked_payload) is True
    assert is_checked_in_today(unchecked_payload) is False
    assert normalize_base_url("https://example.com/") == "https://example.com"
    headers = build_headers({"base_url": "https://example.com/", "access_token": "abc", "user_id": 7})
    assert headers["Authorization"] == "Bearer abc"
    assert headers["User-Agent"] == USER_AGENT
    assert headers["Sec-CH-UA-Platform"] == '"Windows"'
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Referer"] == "https://example.com/"
    assert format_quota(7012744) == "14.03"
    assert format_quota(26999425) == "54.00"
    assert format_quota(1688) == "0.00"
    assert format_quota_total([7012744, 26999425]) == "68.03"
    assert format_quota_total([1888, 666]) == "0.00"
    assert format_count(2) == "2"

    # acw_sc__v2 反爬算法自检：非挑战页返回 None，挑战页产出 40 位十六进制且幂等。
    assert compute_acw_sc_v2('{"success": true}') is None
    assert compute_acw_sc_v2(None) is None
    _acw_arg1 = "F1A09A2F1D7C1B56E1B9A1AAD54B24D332A44AF3"
    _acw_html = "<script>var arg1='{0}';(function(){{}})()</script>".format(_acw_arg1)
    _acw_cookie = compute_acw_sc_v2(_acw_html)
    assert _acw_cookie is not None and len(_acw_cookie) == 40
    assert all(c in "0123456789abcdef" for c in _acw_cookie)
    assert compute_acw_sc_v2(_acw_html) == _acw_cookie  # 幂等
    assert _merge_cookie_header("a=1; acw_sc__v2=old", "acw_sc__v2", "new") == "a=1; acw_sc__v2=new"
    assert _cookies_from_headers(None) == ""

    process_line = capture_output(
        print_process_log,
        "依赖检查",
        "SOCKS 代理需要 curl_cffi，准备自动安装",
        status="warn",
        force_color=False,
    )
    assert "失败    依赖检查" not in process_line
    assert re.search(r"^\[\d{2}:\d{2}:\d{2}\] 依赖检查：SOCKS 代理需要 curl_cffi，准备自动安装", process_line)
    dependency_failure = capture_output(print_dependency_failure, CheckinError("curl_cffi 安装失败"), force_color=False)
    assert "未进入任何平台" not in dependency_failure
    assert "不展示平台执行统计" not in dependency_failure
    assert "依赖准备失败：" in dependency_failure
    assert normalize_proxy_url("") == ""
    assert normalize_proxy_url("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert proxy_mapping("http://127.0.0.1:7890") == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    install_steps = dependency_install_steps(
        "curl_cffi",
        command_lookup=lambda name: "/usr/bin/{0}".format(name) if name == "pip3" else None,
    )
    install_step_names = [step["name"] for step in install_steps]
    assert install_step_names[:7] == [
        "python -m pip",
        "python -m pip --user",
        "python -m ensurepip",
        "python -m pip",
        "python -m pip --user",
        "pip3",
        "pip3 --user",
    ]
    assert "apt-get install python3-pip" not in install_step_names

    missing_pip_steps = dependency_install_steps(
        "curl_cffi",
        command_lookup=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
    )
    missing_pip_step_names = [step["name"] for step in missing_pip_steps]
    assert "apt-get update" in missing_pip_step_names
    assert "apt-get install python3-pip" in missing_pip_step_names
    assert missing_pip_step_names.index("apt-get install python3-pip") < missing_pip_step_names.index("pip3")
    linux_package_steps = system_pip_install_steps(
        command_lookup=lambda name: "/usr/bin/{0}".format(name)
        if name in ("apt-get", "dnf", "yum", "apk", "pacman", "zypper")
        else None,
        geteuid=lambda: 0,
    )
    linux_package_names = [step["name"] for step in linux_package_steps]
    assert "apt-get install python3-pip" in linux_package_names
    assert "dnf install python3-pip" in linux_package_names
    assert "yum install python3-pip" in linux_package_names
    assert "apk add py3-pip" in linux_package_names
    assert "pacman install python-pip" in linux_package_names
    assert "zypper install python3-pip" in linux_package_names

    skipped = run_one(
        {
            "name": "已签到",
            "base_url": "https://example.com",
            "user_id": "1",
            "access_token": "tok",
        },
        FakeClient([(200, checked_payload), (200, wallet_payload)]),
    )
    assert skipped["status"] == "skipped"
    assert skipped["wallet_status"] == "ok"
    assert skipped["wallet"]["quota"] == 120000
    assert format_quota(skipped["wallet"]["quota"]) == "0.24"

    fake = FakeClient([(200, unchecked_payload), (200, signed_payload), (200, wallet_payload)])
    signed = run_one(
        {
            "name": "未签到",
            "base_url": "https://example.com",
            "user_id": "1",
            "access_token": "tok",
            "turnstile": "ts-token",
        },
        fake,
    )
    assert signed["status"] == "signed"
    assert signed["quota"] == 1888
    assert signed["wallet_status"] == "ok"
    assert signed["wallet"]["used_quota"] == 34567
    assert format_quota(signed["wallet"]["used_quota"]) == "0.07"
    assert fake.calls[1][0] == "POST"
    assert "turnstile=ts-token" in fake.calls[1][1]

    failed = run_one(
        {
            "name": "失败平台",
            "base_url": "https://example.com",
            "user_id": "1",
            "access_token": "tok",
        },
        FakeClient([(200, {"success": False, "message": "签到功能未启用"})]),
    )
    assert failed["status"] == "failed"

    disabled = run_one({"name": "禁用平台", "enabled": False}, FakeClient([]))
    assert disabled["status"] == "disabled"

    isolated_results = run_all(
        [
            {
                "name": "失败平台",
                "base_url": "https://example.com",
                "user_id": "1",
                "access_token": "tok",
            },
            {
                "name": "后续平台",
                "base_url": "https://example.com",
                "user_id": "2",
                "access_token": "tok2",
            },
        ],
        FakeClient(
            [
                CheckinError("登录失败"),
                (200, checked_payload),
                (200, wallet_payload),
            ]
        ),
    )
    assert [item["status"] for item in isolated_results] == ["failed", "skipped"]
    assert isolated_results[1]["wallet_status"] == "ok"

    original_proxy_url = globals()["PROXY_URL"]
    original_load_curl_requests = globals()["load_curl_requests"]
    try:
        globals()["PROXY_URL"] = "socks5://127.0.0.1:1081"
        globals()["load_curl_requests"] = lambda: None
        try:
            run_all(
                [
                    {
                        "name": "SOCKS代理平台",
                        "base_url": "https://example.com",
                        "user_id": "1",
                        "access_token": "tok",
                    },
                    {"name": "禁用SOCKS平台", "enabled": False},
                ]
            )
            raise AssertionError("SOCKS 依赖失败时不应进入平台执行")
        except CheckinError as exc:
            assert "curl_cffi" in str(exc)
    finally:
        globals()["PROXY_URL"] = original_proxy_url
        globals()["load_curl_requests"] = original_load_curl_requests

    class FakeResponse(object):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"success": true, "data": {"ok": true}}'

        def getcode(self):
            return 200

    class FlakyOpener(object):
        def __init__(self):
            self.calls = 0

        def __call__(self, req, timeout=None, context=None):
            self.calls += 1
            if self.calls == 1:
                raise _urlerror.URLError("temporary dns failure")
            return FakeResponse()

    opener = FlakyOpener()
    retry_client = HttpClient(retry_times=2, retry_interval=0, opener=opener)
    status, retry_payload = retry_client.request_json("GET", "https://example.com/api/user/self")
    assert status == 200
    assert retry_payload["data"]["ok"] is True
    assert opener.calls == 2

    removed_function = "de" + "mo_results"
    removed_arg = "--" + "de" + "mo-output"
    assert removed_function not in globals()
    assert unsupported_args(["--force-color", "--no-color"]) == []
    assert unsupported_args([removed_arg]) == [removed_arg]

    print("self-test passed")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)
    sys.exit(main())