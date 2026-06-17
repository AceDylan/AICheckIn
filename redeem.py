#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""claude-zhongzhuan.cloud 批量兑换逻辑。

与签到（gyqd）相互独立：签到面向 new-api 站点，本模块面向中转站的
登录 + 兑换接口。复用 gyqd 的 curl_cffi / 代理辅助函数以获得接近真实
Chrome 的 TLS 指纹（中转站多在 Cloudflare 之后），不可用时回退标准库 urllib。

对外主入口 run_redeem：按账号轮换批量兑换——每个账号成功兑换达到
per_account_limit 次后切换下一个账号；兑换码为共享队列，每个码只尝试一次。
由于无法本地验证中转站真实响应结构，登录取 token 与兑换成功判定均采用
「尽力解析 + 透出原始信息」的防御式策略，便于按真实返回再行调整。
"""

import json
import re

import gyqd

BASE_URL = "https://claude-zhongzhuan.cloud"
LOGIN_URL = BASE_URL + "/api/v1/auth/login"
REDEEM_URL = BASE_URL + "/api/v1/redeem"

# 与浏览器一致的请求超时（秒）。
TIMEOUT_SECONDS = 25

# JWT 形态：三段 base64url 以点分隔。用于从登录响应里兜底识别 token。
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
# 常见 token 字段名（按优先级）。
_TOKEN_KEYS = ("token", "access_token", "accessToken", "jwt", "id_token")


class RedeemError(Exception):
    """登录/兑换流程中的可预期错误。"""


def _base_headers(referer_path):
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh",
        "content-type": "application/json",
        "origin": BASE_URL,
        "referer": BASE_URL + referer_path,
        "user-agent": gyqd.USER_AGENT,
    }


def build_fetcher(proxy_url):
    """返回 fetch(method, url, headers, body_dict) -> (status, parsed_json_or_None, raw_text)。

    优先 curl_cffi（浏览器 TLS 指纹，支持 socks 与 Cloudflare 友好），否则标准库 urllib。
    """
    proxy_url = gyqd.normalize_proxy_url(proxy_url)
    curl_requests = gyqd.load_curl_requests()

    if curl_requests is not None:
        proxies = gyqd.proxy_mapping(proxy_url)

        def fetch(method, url, headers, body):
            kwargs = {
                "method": method,
                "url": url,
                "headers": headers,
                "timeout": TIMEOUT_SECONDS,
                "impersonate": gyqd.CURL_IMPERSONATE_BROWSER,
            }
            if body is not None:
                kwargs["json"] = body
            if proxies:
                kwargs["proxies"] = proxies
            try:
                resp = curl_requests.request(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise RedeemError("网络请求失败：{0}".format(exc))
            text = resp.text
            return int(resp.status_code), _safe_json(text), text

        return fetch

    if gyqd.is_socks_proxy(proxy_url):
        raise RedeemError("SOCKS 代理需要 curl_cffi，但当前未能加载；请安装 curl_cffi 或改用 http/https 代理")

    from urllib import request as _req
    from urllib import error as _err

    opener = gyqd.build_urllib_opener(proxy_url)

    def fetch(method, url, headers, body):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = _req.Request(url=url, data=data, headers=headers, method=method)
        try:
            with opener(req, timeout=TIMEOUT_SECONDS) as resp:
                status = getattr(resp, "status", resp.getcode())
                text = resp.read().decode("utf-8", errors="replace")
                return status, _safe_json(text), text
        except _err.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            return exc.code, _safe_json(text), text
        except _err.URLError as exc:
            raise RedeemError("网络请求失败：{0}".format(exc.reason))
        except (TimeoutError, OSError) as exc:
            raise RedeemError("网络请求失败：{0}".format(exc))

    return fetch


def _safe_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _find_token(obj, depth=0):
    """在解析后的 JSON 里递归找 token：先按常见键名，再兜底找 JWT 形态字符串。"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for key in _TOKEN_KEYS:
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for val in obj.values():
            found = _find_token(val, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for val in obj:
            found = _find_token(val, depth + 1)
            if found:
                return found
    elif isinstance(obj, str):
        s = obj.strip()
        if _JWT_RE.match(s):
            return s
    return None


def _extract_message(parsed, raw):
    """从响应里提取面向用户的一句话信息。"""
    if isinstance(parsed, dict):
        for key in ("message", "msg", "error", "detail", "description", "data"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    text = (raw or "").strip().replace("\n", " ")
    return text[:160] if text else ""


def login(fetch, email, password):
    """登录返回 JWT token；失败抛 RedeemError（带服务端信息）。"""
    status, parsed, raw = fetch("POST", LOGIN_URL, _base_headers("/login"),
                                {"email": email, "password": password})
    token = _find_token(parsed)
    if token:
        return token
    msg = _extract_message(parsed, raw) or "未返回 token"
    raise RedeemError("登录失败（HTTP {0}）：{1}".format(status, msg))


def redeem_code(fetch, token, code):
    """用一个 token 兑换一个码，返回 (success, message)。"""
    headers = _base_headers("/redeem")
    headers["authorization"] = "Bearer " + token
    status, parsed, raw = fetch("POST", REDEEM_URL, headers, {"code": code})
    message = _extract_message(parsed, raw)

    success = _judge_success(status, parsed)
    if not message:
        message = "兑换成功" if success else "兑换失败（HTTP {0}）".format(status)
    return success, message


def _judge_success(status, parsed):
    """尽力判定兑换是否成功：显式成功标志优先，其次业务 code，最后回落到 HTTP 状态。"""
    if isinstance(parsed, dict):
        for key in ("success", "ok"):
            if key in parsed and isinstance(parsed[key], bool):
                return parsed[key]
        # 业务码：0 / 200 视为成功；其余视为失败。
        for key in ("code", "status", "errno", "errcode"):
            val = parsed.get(key)
            if isinstance(val, int):
                return val in (0, 200)
        # 存在非空 error 字段视为失败。
        err = parsed.get("error") or parsed.get("err")
        if isinstance(err, str) and err.strip():
            return False
    return 200 <= int(status) < 300


def parse_accounts(raw_accounts):
    """规整账号列表为 [{email, password}]；兼容前端结构化数组或字符串行。"""
    out = []
    if isinstance(raw_accounts, str):
        raw_accounts = raw_accounts.splitlines()
    for item in raw_accounts or []:
        email = password = ""
        if isinstance(item, dict):
            email = str(item.get("email") or "").strip()
            password = str(item.get("password") or "").strip()
        else:
            line = str(item or "").strip()
            if not line:
                continue
            # 支持 ---- / 空白 / 逗号 / 冒号 作为分隔。
            parts = re.split(r"----|[\s,:]+", line, maxsplit=1)
            email = parts[0].strip()
            password = parts[1].strip() if len(parts) > 1 else ""
        if email and password:
            out.append({"email": email, "password": password})
    return out


def parse_codes(raw_codes):
    """规整兑换码：去空白、去重（保序）。兼容数组或多行字符串。"""
    if isinstance(raw_codes, str):
        raw_codes = re.split(r"[\s,]+", raw_codes)
    seen = set()
    out = []
    for c in raw_codes or []:
        code = str(c or "").strip()
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def run_redeem(accounts, codes, per_account_limit, proxy_url="", progress=None):
    """按账号轮换批量兑换。

    - 每个账号成功兑换达到 per_account_limit 次后切换下一个账号。
    - codes 为共享队列，按顺序消费，每个码只尝试一次（无论成功失败都消费）。
    - 登录失败的账号跳过且不消费兑换码。
    - progress(event) 回调用于实时上报（可选）：event 为单条日志字典。

    返回 {logs, summary}。
    """
    accounts = parse_accounts(accounts)
    codes = parse_codes(codes)
    try:
        limit = max(1, int(per_account_limit))
    except (TypeError, ValueError):
        limit = 1

    def emit(event):
        if progress:
            try:
                progress(event)
            except Exception:  # noqa: BLE001 - 上报失败不影响主流程。
                pass

    logs = []

    def log(entry):
        logs.append(entry)
        emit(entry)

    if not accounts:
        raise RedeemError("未提供有效账号（格式：邮箱 密码，每行一个）")
    if not codes:
        raise RedeemError("未提供有效兑换码")

    fetch = build_fetcher(proxy_url)

    code_idx = 0
    total_success = 0
    total_fail = 0
    accounts_used = 0

    for acc in accounts:
        if code_idx >= len(codes):
            break  # 兑换码已用尽，剩余账号无需登录。
        accounts_used += 1
        email = acc["email"]
        log({"type": "login", "account": email, "ok": None, "message": "正在登录…"})
        try:
            token = login(fetch, email, acc["password"])
        except RedeemError as exc:
            log({"type": "login", "account": email, "ok": False, "message": str(exc)})
            continue
        log({"type": "login", "account": email, "ok": True, "message": "登录成功"})

        acc_success = 0
        while acc_success < limit and code_idx < len(codes):
            code = codes[code_idx]
            code_idx += 1
            try:
                ok, message = redeem_code(fetch, token, code)
            except RedeemError as exc:
                ok, message = False, str(exc)
            if ok:
                acc_success += 1
                total_success += 1
            else:
                total_fail += 1
            log({
                "type": "redeem", "account": email, "code": code,
                "ok": ok, "message": message,
                "account_success": acc_success, "limit": limit,
            })

        log({
            "type": "account_done", "account": email, "ok": True,
            "message": "本账号成功 {0}/{1} 次".format(acc_success, limit),
        })

    leftover = codes[code_idx:]
    summary = {
        "accounts_total": len(accounts),
        "accounts_used": accounts_used,
        "codes_total": len(codes),
        "codes_consumed": code_idx,
        "success": total_success,
        "fail": total_fail,
        "leftover_codes": leftover,
        "per_account_limit": limit,
    }
    return {"logs": logs, "summary": summary}
