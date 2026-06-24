#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""claude-zhongzhuan.cloud 批量兑换逻辑。

与签到（gyqd）相互独立：签到面向 new-api 站点，本模块面向中转站的
登录 + 兑换接口。复用 gyqd 的 curl_cffi / 代理辅助函数以获得接近真实
Chrome 的 TLS 指纹（中转站多在 Cloudflare 之后），不可用时回退标准库 urllib。

对外主入口 run_redeem：按账号轮换批量兑换——每个账号成功兑换达到
per_account_limit 次后切换下一个账号；兑换码为共享队列，已成功/已被使用的码
不再重试，仅因限流而未实际兑换的码会留给下一个账号重试。账号触发频率限制时
不终止整个流程，而是切换到下一个账号继续。
由于无法本地验证中转站真实响应结构，登录取 token 与兑换成功判定均采用
「尽力解析 + 透出原始信息」的防御式策略，便于按真实返回再行调整。
"""

import json
import random
import re
import time

import gyqd

BASE_URL = "https://claude-zhongzhuan.cloud"
LOGIN_URL = BASE_URL + "/api/v1/auth/login"
REDEEM_URL = BASE_URL + "/api/v1/redeem"
ME_URL = BASE_URL + "/api/v1/auth/me?timezone=Asia%2FShanghai"

# 与浏览器一致的请求超时（秒）。
TIMEOUT_SECONDS = 25

# ---- 请求节流默认参数（秒）----
# 兑换是「抢」——码放出后很快被用掉，等待越久越抢不到，故默认间隔取亚秒级、只做轻微抖动。
# 多账号交叉轮换已天然拉低单账号请求频率（避免一个账号连续高频请求触发限流），
# 因此无需对「码已被使用」这类正常失败做退避；仅在收到服务端限流信号时让该账号冷却。
# 以下为默认值，可由 run_redeem(throttle=...) 覆盖。
DEFAULT_DELAY_MIN = 0.3              # 相邻两次兑换请求（任意账号之间）的随机间隔下界。
DEFAULT_DELAY_MAX = 0.8              # 上界（实际间隔在 [min, max] 间均匀取值，制造抖动）。
DEFAULT_RATE_LIMIT_COOLDOWN = 15.0  # 账号触发限流后的冷却秒数；冷却期间转交其他账号，到期自动重入。
MAX_RATE_LIMIT_PER_ACCOUNT = 3      # 单账号累计限流达到此数则停用（成功一次即清零），防止反复无效重试。

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


def _to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _find_balance(obj, depth=0):
    """递归查找 balance 数值字段。"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        if "balance" in obj:
            n = _to_number(obj["balance"])
            if n is not None:
                return n
        for val in obj.values():
            found = _find_balance(val, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for val in obj:
            found = _find_balance(val, depth + 1)
            if found is not None:
                return found
    return None


def get_balance(fetch, token):
    """调用 /auth/me 取余额，返回 (balance_str_or_None, message)；保留两位小数。"""
    headers = _base_headers("/dashboard")
    headers.pop("content-type", None)  # GET 无请求体。
    headers["authorization"] = "Bearer " + token
    status, parsed, raw = fetch("GET", ME_URL, headers, None)
    bal = _find_balance(parsed)
    if bal is None:
        return None, (_extract_message(parsed, raw) or "HTTP {0}".format(status))
    return "{0:.2f}".format(bal), ""


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


_CODE_ENUM_RE = re.compile(r"^\d{1,4}\s*[.)、:：]\s*")
# 兑换码形态：32 位十六进制（MD5 式）。claude-zhongzhuan.cloud 当前所有兑换码均为此形态，
# 用「极大十六进制串 + 长度恰为 32」定位，等价于带前后边界，避免从更长的十六进制串里截取子串。
_HEX_RUN_RE = re.compile(r"[0-9a-fA-F]+")
_CODE_LEN = 32


def _dedup_keep_order(items):
    """去重并保持首次出现顺序。"""
    seen = set()
    out = []
    for it in items:
        it = (it or "").strip()
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def parse_codes(raw_codes):
    """从任意粘贴格式中提取真正的兑换码，去重保序。

    主路径：直接抓取所有「长度恰为 32 的十六进制串」。这样无论粘贴的是
    「1. <code>」编号列表、带空行的列表、单行多码（1. a 2. b 3. c），还是夹带
    标题文案（「今日份 $5 兑换码（共20个，先到先得）：」）与使用说明
    （「使用方式：登录 claude-zhongzhuan.cloud → …」），都只留下真正的兑换码，
    不会把编号、标题或说明文字误当成码。
    兜底：若文本里没有任何 32 位十六进制串（兑换码形态可能变化），回退到
    「去行首编号 + 按空白/逗号拆分」的逐行解析，保持向后兼容。
    """
    if isinstance(raw_codes, str):
        text = raw_codes
    elif isinstance(raw_codes, (list, tuple)):
        text = "\n".join(str(item or "") for item in raw_codes)
    else:
        text = str(raw_codes or "")

    hex_codes = [m for m in _HEX_RUN_RE.findall(text) if len(m) == _CODE_LEN]
    if hex_codes:
        return _dedup_keep_order(hex_codes)

    # 兜底：逐行解析（去行首编号 + 空白/逗号拆分）。
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = _CODE_ENUM_RE.sub("", line).strip()  # 去掉行首「1.」「2)」等编号。
        out.extend(re.split(r"[\s,]+", line))
    return _dedup_keep_order(out)


_RATE_LIMIT_HINTS = ("too many", "try again later", "rate limit", "频繁", "稍后", "later", "too many attempts")


def _looks_rate_limited(message):
    """判断兑换失败是否为服务端频率限制（而非兑换码本身无效）。"""
    m = (message or "").lower()
    return any(h in m for h in _RATE_LIMIT_HINTS)


def _normalize_throttle(throttle):
    """归一化节流参数，缺失/非法回落默认值。返回 (delay_min, delay_max, cooldown)，单位秒。"""
    src = throttle if isinstance(throttle, dict) else {}

    def num(key, default):
        try:
            v = float(src.get(key))
        except (TypeError, ValueError):
            return default
        return v if v >= 0 else default

    delay_min = num("delay_min", DEFAULT_DELAY_MIN)
    delay_max = num("delay_max", DEFAULT_DELAY_MAX)
    if delay_max < delay_min:
        delay_max = delay_min  # 上界不得小于下界。
    return delay_min, delay_max, num("cooldown", DEFAULT_RATE_LIMIT_COOLDOWN)


def run_redeem(accounts, codes, per_account_limit, proxy_url="", progress=None, throttle=None):
    """多账号交叉轮换批量兑换。

    - 交叉轮换：所有账号依次轮流，每轮各兑换一个兑换码（而非一个账号连续抢光再换），
      以拉低单账号请求频率、更好地规避服务端限流；每个账号累计成功达到 per_account_limit
      次后退出轮换。
    - codes 为共享队列，按顺序消费：成功或「已被使用/无效」的码立即消费、不再重试，
      下一个被轮到的账号自动从未消费处继续；登录失败的账号停用且不消费兑换码。
    - 限流处理：兑换失败信息形似「too many attempts / try again later」时视为服务端限流，
      **不消费该兑换码**（立即留给其他账号去抢）；触发限流的账号进入冷却（cooldown 秒）
      并转交其他账号继续，冷却到期自动重回轮换；单账号累计限流达 MAX_RATE_LIMIT_PER_ACCOUNT
      次则停用该账号（成功一次即清零计数）。
    - 请求节流（throttle，单位秒）：相邻两次兑换请求间插入 [delay_min, delay_max] 随机间隔
      （亚秒级默认值，兼顾「抢」的时效与限流规避）；冷却时长由 cooldown 控制。throttle 为
      可选 dict，缺失字段回落 DEFAULT_* 默认值。
    - 余额刷新：每次兑换成功后取一次；账号退出轮换时补取一次；流程结束后对所有未取过余额的
      账号（含未参与兑换的）补取，以 type='balance' 事件上报供上层持久化。
    - progress(event) 回调用于实时上报（可选）：event 为单条日志字典。

    返回 {logs, summary}。
    """
    accounts = parse_accounts(accounts)
    codes = parse_codes(codes)
    try:
        limit = max(1, int(per_account_limit))
    except (TypeError, ValueError):
        limit = 1
    delay_min, delay_max, cooldown = _normalize_throttle(throttle)

    def throttle_sleep(seconds):
        if seconds > 0:
            time.sleep(seconds)

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
    tokens = {}            # email -> token，供结束后补取余额复用，避免重复登录。
    balance_done = set()   # 已取过余额的 email，避免重复取。

    def refresh_balance(token, email):
        try:
            bal, msg = get_balance(fetch, token)
        except RedeemError as exc:
            bal, msg = None, str(exc)
        if bal is not None:
            log({"type": "balance", "account": email, "ok": True,
                 "balance": bal, "message": "余额 " + bal})
        else:
            log({"type": "balance", "account": email, "ok": False,
                 "message": "余额获取失败：" + (msg or "")})
        balance_done.add(email)

    code_idx = 0
    total_success = 0
    total_fail = 0
    rate_limited_accounts = 0  # 曾触发限流的不同账号数。
    rl_emails = set()
    first_request = True       # 全局首个兑换请求不等待，其余请求前按间隔等待。

    # 每个账号的运行态（懒登录：首次被轮到时才登录）。
    states = [{
        "email": acc["email"], "password": acc["password"],
        "token": None, "logged_in": False, "participated": False,
        "success": 0, "fail": 0, "rl_count": 0,
        "active": True, "done_logged": False, "cooldown_until": 0.0,
    } for acc in accounts]
    n = len(states)
    turn = 0

    def finish_account(s, note):
        """账号退出轮换：补取余额（若未取）并发出汇总日志。"""
        s["active"] = False
        s["done_logged"] = True
        if s["email"] not in balance_done and s["token"]:
            refresh_balance(s["token"], s["email"])
        log({"type": "account_done", "account": s["email"], "ok": True,
             "message": "本账号成功 {0} 次 · 失败 {1} 次{2}".format(s["success"], s["fail"], note)})

    # ---- 阶段一：多账号交叉轮换兑换 ----
    # 账号依次轮流、每轮各兑一个码；限流账号进入冷却并转交其他账号，到期自动重入。
    while code_idx < len(codes):
        # round-robin 选下一个「立即可用」账号（active 且不在冷却中）。
        picked = None
        soonest = None
        for _ in range(n):
            s = states[turn % n]
            turn += 1
            if not s["active"]:
                continue
            if s["cooldown_until"] > time.monotonic():
                soonest = s["cooldown_until"] if soonest is None else min(soonest, s["cooldown_until"])
                continue
            picked = s
            break

        if picked is None:
            if soonest is None:
                break  # 无可用账号（全部完成/停用），结束轮换。
            # 所有活跃账号都在冷却：等到最早到期（不超过一个 cooldown）再重试。
            throttle_sleep(min(max(0.0, soonest - time.monotonic()), cooldown))
            continue

        s = picked
        email = s["email"]

        # 懒登录：账号首次被选中时才登录；登录失败则停用该账号。
        if not s["logged_in"]:
            log({"type": "login", "account": email, "ok": None, "message": "正在登录…"})
            try:
                s["token"] = login(fetch, email, s["password"])
            except RedeemError as exc:
                log({"type": "login", "account": email, "ok": False, "message": str(exc)})
                s["active"] = False
                continue
            s["logged_in"] = True
            s["participated"] = True
            tokens[email] = s["token"]
            log({"type": "login", "account": email, "ok": True, "message": "登录成功"})

        # 节流：全局首个请求不等待，其余请求前等待 [delay_min, delay_max] 随机间隔。
        if not first_request:
            throttle_sleep(random.uniform(delay_min, delay_max))
        first_request = False

        code = codes[code_idx]
        try:
            ok, message = redeem_code(fetch, s["token"], code)
        except RedeemError as exc:
            ok, message = False, str(exc)

        if (not ok) and _looks_rate_limited(message):
            # 限流：不消费该码（立即留给其他账号去抢）；该账号冷却，多次后停用。
            s["rl_count"] += 1
            if email not in rl_emails:
                rl_emails.add(email)
                rate_limited_accounts += 1
            if s["rl_count"] >= MAX_RATE_LIMIT_PER_ACCOUNT:
                log({"type": "redeem", "account": email, "code": code, "ok": False,
                     "message": message + "（多次触发频率限制，已停用本账号，未消耗此兑换码）",
                     "account_success": s["success"], "account_fail": s["fail"], "limit": limit})
                finish_account(s, "（多次限流，已停用）")
            else:
                s["cooldown_until"] = time.monotonic() + cooldown
                log({"type": "redeem", "account": email, "code": code, "ok": False,
                     "message": message + "（触发频率限制，本账号冷却 {0:.0f}s 后重试，转交其他账号，未消耗此兑换码）".format(cooldown),
                     "account_success": s["success"], "account_fail": s["fail"], "limit": limit})
            continue

        code_idx += 1  # 非限流（成功或已被使用/无效）才真正消费该码。
        if ok:
            s["success"] += 1
            total_success += 1
            s["rl_count"] = 0  # 成功：清零限流计数。
        else:
            s["fail"] += 1
            total_fail += 1
        log({
            "type": "redeem", "account": email, "code": code,
            "ok": ok, "message": message,
            "account_success": s["success"], "account_fail": s["fail"], "limit": limit,
        })
        if ok:
            refresh_balance(s["token"], email)  # 兑换成功后取余额。
            if s["success"] >= limit:
                finish_account(s, "（已达上限）")

    # 阶段一收尾：参与过但仍在轮换（未达上限/未停用）的账号补发汇总 + 补取余额。
    for s in states:
        if s["participated"] and not s["done_logged"]:
            finish_account(s, "")

    accounts_used = sum(1 for s in states if s["participated"])

    # ---- 阶段二：对所有选中账号补取余额（含未参与兑换的账号）----
    for acc in accounts:
        email = acc["email"]
        if email in balance_done:
            continue
        token = tokens.get(email)
        if token is None:
            try:
                token = login(fetch, email, acc["password"])
                tokens[email] = token
            except RedeemError as exc:
                log({"type": "balance", "account": email, "ok": False,
                     "message": "余额获取失败（登录失败）：" + str(exc)})
                balance_done.add(email)
                continue
        refresh_balance(token, email)

    leftover = codes[code_idx:]
    # 仍有未处理兑换码且发生过限流时，提示用户稍后重试（区别于账号容量富余的正常剩余）。
    incomplete = bool(leftover) and rate_limited_accounts > 0
    summary = {
        "accounts_total": len(accounts),
        "accounts_used": accounts_used,
        "codes_total": len(codes),
        "codes_consumed": code_idx,
        "success": total_success,
        "fail": total_fail,
        "leftover_codes": leftover,
        "per_account_limit": limit,
        "rate_limited_accounts": rate_limited_accounts,
        "stopped": incomplete,
        "stop_reason": "rate_limited" if incomplete else "",
    }
    return {"logs": logs, "summary": summary}
