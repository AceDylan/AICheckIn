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
import string
import sys
import threading
import time
from urllib.parse import urlparse
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
# 指标快照文件：持久化每组配置上一次获取到的签到奖励/钱包余额/已用额度/请求数。
# 以 base_url|user_id 为键，独立于 configs 数组索引，避免增删改导入打乱对齐。
METRICS_FILE = str(DATA_DIR / "metrics.json")

# 管理密码：保护配置写入 / 查看真实 token / 定时设置。留空表示完全开放（公网部署强烈建议设置）。
ADMIN_PASSWORD = os.environ.get("GYQD_ADMIN_PASSWORD", "").strip()
# 是否启用后台定时调度线程。
SCHEDULER_ENABLED = os.environ.get("GYQD_SCHEDULER", "1") == "1"

if not ADMIN_PASSWORD:
    sys.stderr.write("[gyqd-web] 警告：未设置 GYQD_ADMIN_PASSWORD，配置可被任意访问者编辑/查看 token\n")

# 并发保护：配置写、历史写、签到执行各一把锁。
_store_lock = threading.Lock()
_history_lock = threading.Lock()
_run_lock = threading.Lock()
_metrics_lock = threading.Lock()
_sched_thread = None

# =========================
# 配置存取（持久化层）
# =========================

def read_store():
    """读取完整配置存储，归一化为 {configs, proxy_url, schedule}。

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
            store = {"configs": list(raw), "proxy_url": "", "schedule": {}, "bookmarks": []}
        elif isinstance(raw, dict):
            store = {
                "configs": list(raw.get("configs") or []),
                "proxy_url": str(raw.get("proxy_url") or "").strip(),
                "schedule": dict(raw.get("schedule") or {}),
                "bookmarks": list(raw.get("bookmarks") or []),
            }
        else:
            raise RuntimeError("config.json 格式应为数组或对象")
    else:
        store = {"configs": list(gyqd.CONFIGS), "proxy_url": "", "schedule": {}, "bookmarks": []}
    store.setdefault("configs", [])
    store.setdefault("proxy_url", "")
    store.setdefault("schedule", {})
    store.setdefault("bookmarks", [])  # 仅收藏不签到的站点：[{name, url}]
    return store


def write_store(store):
    """原子性较弱但兼容单文件 bind mount 的就地写入；写前留 .bak 备份。"""
    text = json.dumps(store, ensure_ascii=False, indent=2)
    json.loads(text)  # 写前自检，确保可往返。
    path = Path(CONFIG_FILE)
    with _store_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                try:
                    backup = path.with_name(path.name + ".bak")
                    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass
            path.write_text(text, encoding="utf-8")
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


def _fetch_balance(balance_cfg, proxy_url):
    """根据 balance_config 获取余额，返回 (balance_str_or_None, error_msg)。"""
    method = balance_cfg["method"]
    url = balance_cfg["url"]
    headers = dict(balance_cfg.get("headers") or {})
    body = balance_cfg.get("body")
    json_path = balance_cfg["json_path"]

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

    # 可选换算系数：如分→元填 100。未配置或非法时按 1（不换算）。
    try:
        divisor = float(balance_cfg.get("divisor"))
        if divisor <= 0:
            divisor = 1.0
    except (TypeError, ValueError):
        divisor = 1.0

    if isinstance(value, (int, float)):
        return "{0:.2f}".format(float(value) / divisor), ""
    # 字符串值：仅当配置了换算系数（≠1）时才数值化，否则保持原样精度。
    if divisor != 1.0:
        try:
            return "{0:.2f}".format(float(str(value).strip()) / divisor), ""
        except (TypeError, ValueError):
            pass
    return str(value).strip(), ""


def _apply_balance_to_bookmark(bookmark, proxy_url):
    """拉取并就地写回单条收藏的余额/错误状态。返回 (ok, balance_or_errmsg)。

    成功：写 balance + balance_updated_at，清除 balance_error。
    失败：写 balance_error + balance_updated_at，保留上次 balance 不动。
    """
    cfg = bookmark.get("balance_config")
    if not cfg:
        return False, "未配置余额接口"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        balance, error_msg = _fetch_balance(cfg, proxy_url)
    except Exception as exc:  # noqa: BLE001 - 单条异常不应影响调用方批量流程。
        balance, error_msg = None, "刷新余额失败：{0}".format(exc)
    bookmark["balance_updated_at"] = now
    if balance is None:
        bookmark["balance_error"] = error_msg or "获取余额失败"
        return False, bookmark["balance_error"]
    bookmark["balance"] = balance
    bookmark.pop("balance_error", None)
    return True, balance


def clean_bookmark(payload, existing=None):
    """校验并规整单条收藏站点；保留 name/url/balance_config 及余额快照。

    balance_config 可选；传入时为完整配置对象，不传又无 existing 时清除旧配置。
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

    # balance_config 可选：三个分支
    #  1. payload 不含 balance_config → 编辑时沿用旧值，新建时不设
    #  2. payload.balance_config 为 null → 显式清除
    #  3. payload.balance_config 为 dict → 校验并存储
    has_balance_config = "balance_config" in payload
    balance_cfg = None
    if has_balance_config:
        balance_cfg_raw = payload["balance_config"]
        if balance_cfg_raw is not None:
            if not isinstance(balance_cfg_raw, dict):
                errors.append("balance_config 需为对象或 null")
            else:
                json_path = str(balance_cfg_raw.get("json_path") or "").strip()
                curl_text = str(balance_cfg_raw.get("curl") or "").strip()

                if curl_text:
                    # 新式：用户粘贴 curl，后端解析出 method/url/headers/body
                    try:
                        parsed = parse_curl(curl_text)
                        method = parsed["method"]
                        api_url = parsed["url"]
                        headers = parsed["headers"]
                        body = parsed["body"]
                    except ValueError as exc:
                        errors.append("curl 解析失败：{0}".format(exc))
                        method, api_url, headers, body = "", "", {}, None
                else:
                    # 兼容旧式：分字段提交
                    method = str(balance_cfg_raw.get("method") or "").strip().upper()
                    api_url = str(balance_cfg_raw.get("url") or "").strip()
                    headers = balance_cfg_raw.get("headers")
                    body = balance_cfg_raw.get("body")

                if method not in ("GET", "POST"):
                    errors.append("balance_config.method 需为 GET 或 POST")
                if not api_url:
                    errors.append("balance_config.url 必填")
                elif not api_url.startswith(("http://", "https://")):
                    errors.append("balance_config.url 需以 http:// 或 https:// 开头")
                if not isinstance(headers, dict):
                    errors.append("balance_config.headers 需为对象")
                if body is not None and not isinstance(body, str):
                    errors.append("balance_config.body 需为字符串或 null")
                if not json_path:
                    errors.append("balance_config.json_path 必填")

                # 可选换算系数（如分→元填 100）；缺省/空表示不换算。
                divisor = None
                divisor_raw = balance_cfg_raw.get("divisor")
                if divisor_raw is not None and str(divisor_raw).strip() != "":
                    try:
                        divisor = float(divisor_raw)
                    except (TypeError, ValueError):
                        errors.append("balance_config.divisor 需为数字")
                    else:
                        if divisor <= 0:
                            errors.append("balance_config.divisor 需大于 0")

                balance_cfg = {
                    "method": method,
                    "url": api_url,
                    "headers": dict(headers or {}),
                    "body": body,
                    "json_path": json_path,
                }
                # 保留原始 curl，便于编辑时回填文本框
                if curl_text:
                    balance_cfg["curl"] = curl_text
                # 仅在配置了有效且 ≠1 的系数时存储，避免污染旧配置
                if divisor and divisor != 1:
                    balance_cfg["divisor"] = divisor
            # balance_cfg_raw is None → 显式清除，balance_cfg 保持 None
    elif existing and existing.get("balance_config"):
        # 编辑时 payload 不含 balance_config 字段 → 沿用旧配置
        balance_cfg = existing["balance_config"]

    if errors:
        raise ValueError("；".join(errors))

    item = {"name": name, "url": url}
    if balance_cfg:
        item["balance_config"] = balance_cfg

    # 保留余额快照（编辑时避免丢失上次查询结果）
    if existing:
        if existing.get("balance") is not None:
            item["balance"] = existing.get("balance")
        if existing.get("balance_updated_at"):
            item["balance_updated_at"] = existing.get("balance_updated_at")
        if existing.get("balance_error"):
            item["balance_error"] = existing.get("balance_error")

    return item

def mask_token(token):
    """token 脱敏：保留首尾各 4 位。"""
    token = str(token or "")
    if not token:
        return ""
    if len(token) <= 8:
        return "•" * len(token)
    return "{0}…{1}".format(token[:4], token[-4:])


def public_config(item):
    """对外暴露的脱敏配置视图（不含真实 token）。"""
    return {
        "name": item.get("name", ""),
        "base_url": item.get("base_url", ""),
        "user_id": item.get("user_id", ""),
        "enabled": bool(item.get("enabled", True)),
        "turnstile": item.get("turnstile", ""),
        "token_masked": mask_token(item.get("access_token", "")),
        "has_token": bool(item.get("access_token")),
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

def admin_ok():
    """签到放行；仅配置写/查 token/定时设置需要管理密码。未设密码则完全放行。"""
    if not ADMIN_PASSWORD:
        return True
    return hmac.compare_digest(request.headers.get("X-Admin-Password", ""), ADMIN_PASSWORD)


def _guard_admin():
    if not admin_ok():
        return jsonify({"ok": False, "error": "需要管理密码"}), 403
    return None


# =========================
# 路由：页面
# =========================

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


# =========================
# 路由：签到（开放）
# =========================

@app.post("/api/checkin")
def api_checkin():
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
    """脱敏配置列表 + 全局设置 + 鉴权状态。开放访问。"""
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    schedule = store.get("schedule") or {}
    metrics = read_metrics()
    today = _today_str()
    configs_out = []
    for c in store["configs"]:
        pc = public_config(c)
        snap = metrics.get(metrics_key(c)) or None
        pc["metrics"] = snap  # 上一次获取的指标快照，供刷新后填充。
        # 今日是否已签到（手动/定时签到成功或已签到跳过时写入），供前端「待签到」跨刷新统计。
        pc["checked_in_today"] = bool(snap and snap.get("last_checkin_date") == today)
        configs_out.append(pc)
    return jsonify({
        "ok": True,
        "configs": configs_out,
        "bookmarks": list(store.get("bookmarks", [])),  # 仅收藏不签到的站点。
        "proxy_url": store.get("proxy_url", ""),
        "schedule": {
            "enabled": bool(schedule.get("enabled")),
            "time": schedule.get("time", "08:30"),
            "last_run_time": schedule.get("last_run_time"),
            "last_run_date": schedule.get("last_run_date"),
        },
        "admin_required": bool(ADMIN_PASSWORD),
        "admin_unlocked": admin_ok(),
        "scheduler_running": SCHEDULER_ENABLED,
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


@app.post("/api/bookmarks/<int:idx>/refresh_balance")
def api_bookmark_refresh_balance(idx):
    """刷新指定收藏的余额；需管理密码。"""
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
    if not bookmark.get("balance_config"):
        return jsonify({"ok": False, "error": "该收藏未配置余额接口"}), 400

    ok, msg = _apply_balance_to_bookmark(bookmark, store.get("proxy_url", ""))
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    # 请求本身成功（HTTP 200）；余额拉取成败由 ok 字段体现，失败信息已落盘。
    resp = {"ok": ok, "balance_updated_at": bookmark.get("balance_updated_at")}
    if ok:
        resp["balance"] = bookmark.get("balance")
    else:
        resp["error"] = msg
    return jsonify(resp)


# =========================
# 路由：导入 / 导出（管理）
# =========================

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
        raw_configs, proxy_url, schedule, bookmarks_raw = payload, None, None, None
    elif isinstance(payload, dict):
        raw_configs = payload.get("configs")
        proxy_url = payload.get("proxy_url")
        schedule = payload.get("schedule")
        bookmarks_raw = payload.get("bookmarks")
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

    try:
        store = read_store()
        store["configs"] = cleaned
        if proxy_url is not None:
            store["proxy_url"] = str(proxy_url or "").strip()
        if isinstance(schedule, dict):
            store["schedule"] = _clean_schedule(schedule, store.get("schedule") or {})
        if cleaned_bookmarks is not None:
            store["bookmarks"] = cleaned_bookmarks
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "count": len(cleaned)})


# =========================
# 路由：全局设置（管理）
# =========================

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

    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.get("/api/history")
def api_history():
    return jsonify({"ok": True, "history": read_history()})


# 验证管理密码是否正确（前端解锁用）。
@app.post("/api/auth")
def api_auth():
    return jsonify({"ok": admin_ok()})


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
        # 定时签到联动刷新收藏余额：配置了接口的收藏顺带取一次，
        # 成败状态写回 bookmark（失败写 balance_error），供前端展示。
        for bm in store.get("bookmarks") or []:
            if bm.get("balance_config"):
                try:
                    _apply_balance_to_bookmark(bm, store.get("proxy_url", ""))
                except Exception:  # noqa: BLE001 - 单条余额失败不影响调度主流程。
                    pass
        # 无论成功与否都标记当天已跑，避免循环重试。
        schedule["last_run_date"] = today
        schedule["last_run_time"] = _now_str()
        store["schedule"] = schedule
        try:
            write_store(store)
        except RuntimeError:
            pass


def _scheduler_loop():
    while True:
        try:
            _scheduler_tick()
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
