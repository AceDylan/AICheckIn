#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""claude-zhongzhuan.cloud 批量兑换逻辑。

与签到（gyqd）相互独立：签到面向 new-api 站点，本模块面向中转站的
登录 + 兑换接口。复用 gyqd 的 curl_cffi / 代理辅助函数以获得接近真实
Chrome 的 TLS 指纹（中转站多在 Cloudflare 之后），不可用时回退标准库 urllib。

对外主入口 run_redeem：多账号「并行」批量兑换——抢码场景优化版。相对旧版的
单线程交叉轮换，关键提速有三处：
1) 并行：每个参与账号一个 worker 线程，同时从共享兑换码队列里抢码，整体吞吐≈账号数倍；
2) 预登录：任务启动即并行登录所有账号并预热连接，兑换爆发阶段零登录/握手延迟；
3) 连接复用：每账号独占一个 curl_cffi Session（HTTP keep-alive），省去每次请求的 TLS 握手。
共享队列保证不同账号天然覆盖不同码（动态分片、自负载均衡）；节流由「全局」改为「按账号」，
账号之间不再互相等待。兑换码为共享队列，已成功/已被使用的码不再重试，仅因限流而未实际
兑换的码会放回队列留给其他账号重抢。账号触发频率限制时不终止整个流程，进入冷却后转交其他账号。
由于无法本地验证中转站真实响应结构，登录取 token 与兑换成功判定均采用
「尽力解析 + 透出原始信息」的防御式策略，便于按真实返回再行调整。
"""

import json
import queue
import random
import re
import threading
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
# 并行模型下该间隔为「单账号内」相邻两次请求的间隔（不同账号互不等待），用于规避单账号限流；
# 因此可取较小值而不牺牲整体并发吞吐。仅在收到服务端限流信号时让该账号冷却。
# 以下为默认值，可由 run_redeem(throttle=...) 覆盖。
DEFAULT_DELAY_MIN = 0.3              # 同一账号相邻两次兑换请求的随机间隔下界。
DEFAULT_DELAY_MAX = 0.8             # 上界（实际间隔在 [min, max] 间均匀取值，制造抖动）。
DEFAULT_RATE_LIMIT_COOLDOWN = 15.0  # 账号触发限流后的冷却秒数；冷却期间转交其他账号，到期自动重入。
MAX_RATE_LIMIT_PER_ACCOUNT = 3      # 单账号累计限流达到此数则停用（成功一次即清零），防止反复无效重试。
MAX_NET_ERRORS_PER_ACCOUNT = 3      # 单账号累计网络异常达此数则停用（收到任意响应即清零），避免坏网络下无限重试不收敛。

# 预登录阶段的并发上界（避免账号过多时同一时刻发起过多登录请求触发 IP 级限流）。
# 兑换阶段为每个已登录账号各起一个 worker，不受此上界约束（抢码需要全员同时开火）。
DEFAULT_MAX_WORKERS = 16

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
    注意：此函数每次调用走模块级 request（不复用连接），保留供单发场景/向后兼容；
    并行批量兑换请改用 build_account_fetcher（每账号一个 keep-alive Session）。
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


def build_account_fetcher(proxy_url):
    """构造「单账号专用」请求器，返回 (fetch, close)。

    与 build_fetcher 的关键区别：curl_cffi 路径下复用同一个 Session（HTTP keep-alive），
    使该账号的登录、预热、连续兑换共用一条已建立的 TCP+TLS 连接，省去每次请求的握手开销——
    这是「抢码」场景压低单请求延迟的关键。每账号一个独立 Session，便于并行且互不串扰。
    标准库 urllib 回退路径无连接复用（仅作兜底），close 为空操作。

    返回的 fetch 签名与 build_fetcher 一致：fetch(method, url, headers, body) ->
    (status, parsed_json_or_None, raw_text)。
    """
    proxy_url = gyqd.normalize_proxy_url(proxy_url)
    curl_requests = gyqd.load_curl_requests()

    if curl_requests is not None:
        proxies = gyqd.proxy_mapping(proxy_url)
        session = curl_requests.Session()

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
                resp = session.request(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise RedeemError("网络请求失败：{0}".format(exc))
            text = resp.text
            return int(resp.status_code), _safe_json(text), text

        def close():
            try:
                session.close()
            except Exception:  # noqa: BLE001 - 关闭失败无害。
                pass

        return fetch, close

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

    return fetch, (lambda: None)


def _run_in_threads(funcs, max_workers):
    """并发执行一批无参函数并等待全部完成（每个函数自行处理异常/上报）。

    用信号量把同一时刻在跑的线程数限制在 max_workers，用于预登录阶段控制并发，
    避免账号过多时瞬间发起过多登录请求。
    """
    funcs = list(funcs or [])
    if not funcs:
        return
    sem = threading.Semaphore(max(1, int(max_workers)))
    threads = []

    def runner(fn):
        try:
            fn()
        finally:
            sem.release()

    for fn in funcs:
        sem.acquire()
        t = threading.Thread(target=runner, args=(fn,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


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


def _dedup_accounts(accounts):
    """按 email 去重（保留首次出现）。

    防止同一邮箱配置多次导致：① 兑换阶段同一账号起多个 worker、重复抢码且彼此竞争；
    ② 命中预热时多个 worker 共享同一个 curl_cffi Session（非线程安全）并在收尾重复关闭。
    """
    seen = set()
    out = []
    for a in accounts or []:
        email = (a or {}).get("email")
        if email and email not in seen:
            seen.add(email)
            out.append(a)
    return out


def prewarm_accounts(accounts, proxy_url="", progress=None, max_workers=None):
    """并行预登录账号并预热连接，返回 {email: {email, fetch, close, token, balance}}。

    供「预备-倒计时」抢码：放码前先点预热——把所有账号登好、把每账号的 keep-alive
    连接焐热、并顺带取一次余额；开抢时把返回的热上下文交给 run_redeem(warm=...) 复用，
    跳过登录直接兑换，从而把「点击→第一发兑换」之间的登录往返延迟降到零。

    注意：返回的 fetch / Session 为「活对象」，必须与后续 run_redeem 在同一进程内复用；
    其生命周期由调用方负责——交给 run_redeem 后由其收尾时统一关闭，未被复用的须自行 close。
    token（JWT）有效期通常远超数分钟，故预热后数分钟内开抢仍可直接复用；连接若因空闲被
    服务端断开，至多多付一次握手，但无需重新登录。
    """
    accounts = parse_accounts(accounts)
    accounts = _dedup_accounts(accounts)
    if not accounts:
        raise RedeemError("未提供有效账号（格式：邮箱 密码，每行一个）")
    concurrency = max(1, int(max_workers or DEFAULT_MAX_WORKERS))

    warm = {}
    warm_lock = threading.Lock()

    def emit(event):
        if progress:
            try:
                progress(event)
            except Exception:  # noqa: BLE001 - 上报失败不影响主流程。
                pass

    def do(acc):
        email = acc["email"]
        emit({"type": "login", "account": email, "ok": None, "message": "正在预热（登录）…"})
        try:
            fetch, close = build_account_fetcher(proxy_url)
        except RedeemError as exc:
            emit({"type": "login", "account": email, "ok": False, "message": "预热失败：" + str(exc)})
            return
        try:
            token = login(fetch, email, acc["password"])
        except RedeemError as exc:
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
            emit({"type": "login", "account": email, "ok": False, "message": "预热失败：" + str(exc)})
            return
        # 预热连接 + 验证 token + 顺带取余额（前端可即时显示）。
        balance = None
        try:
            balance, _ = get_balance(fetch, token)
        except RedeemError:
            balance = None
        with warm_lock:
            warm[email] = {"email": email, "fetch": fetch, "close": close,
                           "token": token, "balance": balance}
        msg = "预热成功" + ("（余额 {0}）".format(balance) if balance is not None else "")
        emit({"type": "login", "account": email, "ok": True, "message": msg})
        if balance is not None:
            emit({"type": "balance", "account": email, "ok": True,
                  "balance": balance, "message": "余额 " + balance})

    _run_in_threads([lambda a=a: do(a) for a in accounts], concurrency)
    return warm


def run_redeem(accounts, codes, per_account_limit, proxy_url="", progress=None,
               throttle=None, max_workers=None, warm=None):
    """多账号「并行」批量兑换（抢码优化版）。

    - 并行抢码：阶段一并行预登录所有账号并预热连接；阶段二为每个登录成功的账号各起一个
      worker 线程，同时从共享线程安全队列里取码兑换。整体吞吐≈账号数倍，首波即可同时覆盖
      多达 N 个不同的码（N=账号数）。
    - 共享队列：codes 按序入队；每个 worker 取走「下一个未被取走的码」——天然做到不同账号
      覆盖不同码（动态分片、自负载均衡）。成功或「已被使用/无效」的码立即永久消费、不再重试。
    - 按账号节流（throttle，单位秒）：同一账号相邻两次请求间插入 [delay_min, delay_max] 随机
      间隔（账号内首发不等待）；不同账号之间互不等待，故并行度不被节流拖累。
    - 限流处理：兑换失败信息形似「too many attempts / try again later」时视为服务端限流，
      **不消费该码**（放回队列立即留给其他账号去抢）；触发限流的账号进入冷却（cooldown 秒）
      并由其他账号继续，冷却到期自动重抢；单账号累计限流达 MAX_RATE_LIMIT_PER_ACCOUNT 次则
      停用该账号（成功一次即清零计数）。
    - 单账号累计成功达 per_account_limit 即退出。
    - 余额刷新：每次兑换成功后取一次；账号退出时补取一次；流程结束后对所有未取过余额的账号补取，
      以 type='balance' 事件上报供上层持久化。
    - progress(event) 回调用于实时上报（可选，且会被多个 worker 线程并发调用）。
    - max_workers：预登录阶段的并发上界（默认 DEFAULT_MAX_WORKERS）；兑换阶段每个已登录账号
      各一个 worker，不受此上界约束。
    - warm：可选，由 prewarm_accounts 预热得到的 {email: {fetch, close, token}}。命中的账号
      跳过登录、直接复用热连接（零登录延迟）；未命中的账号正常登录。复用的连接由本函数收尾关闭。

    返回 {logs, summary}，结构与旧版完全一致，前端无需改动。
    """
    accounts = parse_accounts(accounts)
    accounts = _dedup_accounts(accounts)
    codes = parse_codes(codes)
    try:
        limit = max(1, int(per_account_limit))
    except (TypeError, ValueError):
        limit = 1
    delay_min, delay_max, cooldown = _normalize_throttle(throttle)
    login_concurrency = max(1, int(max_workers or DEFAULT_MAX_WORKERS))

    if not accounts:
        raise RedeemError("未提供有效账号（格式：邮箱 密码，每行一个）")
    if not codes:
        raise RedeemError("未提供有效兑换码")

    # 全局锁：保护 logs、共享计数器、balance_done 集合的并发读写。
    lock = threading.Lock()
    logs = []

    def emit(event):
        if progress:
            try:
                progress(event)
            except Exception:  # noqa: BLE001 - 上报失败不影响主流程。
                pass

    def log(entry):
        # 锁内追加 + 上报，保证本地 logs 与上报顺序一致（多 worker 并发调用）。
        with lock:
            logs.append(entry)
            emit(entry)

    # 每个账号一份运行态；只有「自己的」worker 会改写自己的态，跨线程共享量另用 lock 保护。
    states = [{
        "email": acc["email"], "password": acc["password"],
        "fetch": None, "close": None, "token": None,
        "logged_in": False, "participated": False,
        "success": 0, "fail": 0, "rl_count": 0,
        "active": True, "done_logged": False, "cooldown_until": 0.0,
        "net_err": 0,
    } for acc in accounts]

    balance_done = set()  # 已取过余额的 email，避免重复取。

    def refresh_balance(state):
        """取一次余额并上报；线程安全。"""
        token, email = state["token"], state["email"]
        try:
            bal, msg = get_balance(state["fetch"], token)
        except RedeemError as exc:
            bal, msg = None, str(exc)
        if bal is not None:
            log({"type": "balance", "account": email, "ok": True,
                 "balance": bal, "message": "余额 " + bal})
        else:
            log({"type": "balance", "account": email, "ok": False,
                 "message": "余额获取失败：" + (msg or "")})
        with lock:
            balance_done.add(email)

    def finish_account(state, note):
        """账号退出：补取余额（若未取）并发出汇总日志。仅由账号自己的 worker 或收尾阶段调用。"""
        state["active"] = False
        if state["done_logged"]:
            return
        state["done_logged"] = True
        need_balance = False
        with lock:
            if state["token"] and state["email"] not in balance_done:
                need_balance = True
        if need_balance:
            refresh_balance(state)
        log({"type": "account_done", "account": state["email"], "ok": True,
             "message": "本账号成功 {0} 次 · 失败 {1} 次{2}".format(
                 state["success"], state["fail"], note)})

    # ---- 阶段一：并行预登录 + 连接预热 ----
    # 抢码爆发前先把所有账号登录好、连接焐热，使兑换阶段零登录/握手延迟。
    def prelogin(state):
        email = state["email"]
        # 命中预热：直接复用已登录的热连接，跳过登录（抢码零登录延迟）。
        w = warm.get(email) if warm else None
        if w and w.get("token") and w.get("fetch"):
            state["fetch"] = w["fetch"]
            state["close"] = w.get("close") or (lambda: None)
            state["token"] = w["token"]
            state["logged_in"] = True
            state["participated"] = True
            log({"type": "login", "account": email, "ok": True,
                 "message": "已预热·复用登录（零登录延迟）"})
            return
        try:
            fetch, close = build_account_fetcher(proxy_url)
        except RedeemError as exc:
            state["active"] = False
            log({"type": "login", "account": email, "ok": False, "message": str(exc)})
            return
        state["fetch"], state["close"] = fetch, close
        log({"type": "login", "account": email, "ok": None, "message": "正在登录…"})
        try:
            state["token"] = login(fetch, email, state["password"])
        except RedeemError as exc:
            state["active"] = False
            log({"type": "login", "account": email, "ok": False, "message": str(exc)})
            return
        state["logged_in"] = True
        state["participated"] = True
        log({"type": "login", "account": email, "ok": True, "message": "登录成功"})

    _run_in_threads([lambda s=s: prelogin(s) for s in states], login_concurrency)

    active_states = [s for s in states if s["active"] and s["logged_in"]]

    # ---- 阶段二：多账号并行抢码 ----
    q = queue.Queue()
    for c in codes:
        q.put(c)
    # 共享统计量（lock 保护）。outstanding=尚未被永久消费的码数，归零即全员收工。
    shared = {"outstanding": len(codes), "success": 0, "fail": 0, "consumed": 0,
              "rate_limited_accounts": 0, "rl_emails": set(),
              "net_err_accounts": 0, "net_err_emails": set()}

    def worker(state):
        email = state["email"]
        fetch = state["fetch"]
        first = True
        while True:
            with lock:
                if shared["outstanding"] <= 0:
                    break
            if state["success"] >= limit:
                break
            if state["rl_count"] >= MAX_RATE_LIMIT_PER_ACCOUNT:
                break
            # 冷却中：睡到到期（不超过一个 cooldown）再继续，让出时间给其他账号。
            wait = state["cooldown_until"] - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, cooldown))
                continue
            try:
                code = q.get(timeout=0.2)
            except queue.Empty:
                # 队列暂空但 outstanding>0：可能有码正被其他账号在途/将被放回，稍后重试。
                continue

            # 账号内节流：首发不等待，其后插入随机间隔（不同账号互不等待）。
            if not first:
                t = random.uniform(delay_min, delay_max)
                if t > 0:
                    time.sleep(t)
            first = False

            try:
                ok, message = redeem_code(fetch, state["token"], code)
            except RedeemError as exc:
                # 网络异常（超时/连接失败，fetch 包装成 RedeemError）属临时性失败：绝不消费该码，
                # 放回队列重试；账号累计网络错误达上限则停用，避免坏网络/坏账号下无限重试不收敛。
                state["net_err"] += 1
                with lock:
                    if email not in shared["net_err_emails"]:
                        shared["net_err_emails"].add(email)
                        shared["net_err_accounts"] += 1
                q.put(code)
                if state["net_err"] >= MAX_NET_ERRORS_PER_ACCOUNT:
                    log({"type": "redeem", "account": email, "code": code, "ok": False,
                         "message": "网络异常：{0}（多次失败已停用本账号，未消耗此兑换码）".format(exc),
                         "account_success": state["success"], "account_fail": state["fail"], "limit": limit})
                    finish_account(state, "（多次网络异常，已停用）")
                    break
                state["cooldown_until"] = time.monotonic() + cooldown
                log({"type": "redeem", "account": email, "code": code, "ok": False,
                     "message": "网络异常：{0}（已放回队列，本账号冷却 {1:.0f}s 后重试，未消耗此兑换码）".format(exc, cooldown),
                     "account_success": state["success"], "account_fail": state["fail"], "limit": limit})
                continue
            except Exception as exc:  # noqa: BLE001 - 兜底：意外异常也不能丢码。
                q.put(code)
                log({"type": "redeem", "account": email, "code": code, "ok": False,
                     "message": "兑换异常：{0}（已放回队列）".format(exc),
                     "account_success": state["success"], "account_fail": state["fail"], "limit": limit})
                continue
            state["net_err"] = 0  # 收到任意服务端响应即说明网络正常，清零网络错误计数。

            if (not ok) and _looks_rate_limited(message):
                # 限流：不消费该码（放回队列立即留给其他账号去抢）；该账号冷却，多次后停用。
                state["rl_count"] += 1
                with lock:
                    if email not in shared["rl_emails"]:
                        shared["rl_emails"].add(email)
                        shared["rate_limited_accounts"] += 1
                q.put(code)
                if state["rl_count"] >= MAX_RATE_LIMIT_PER_ACCOUNT:
                    log({"type": "redeem", "account": email, "code": code, "ok": False,
                         "message": message + "（多次触发频率限制，已停用本账号，未消耗此兑换码）",
                         "account_success": state["success"], "account_fail": state["fail"], "limit": limit})
                    finish_account(state, "（多次限流，已停用）")
                    break
                state["cooldown_until"] = time.monotonic() + cooldown
                log({"type": "redeem", "account": email, "code": code, "ok": False,
                     "message": message + "（触发频率限制，本账号冷却 {0:.0f}s 后重试，转交其他账号，未消耗此兑换码）".format(cooldown),
                     "account_success": state["success"], "account_fail": state["fail"], "limit": limit})
                continue

            # 非限流（成功 或 已被使用/无效）：永久消费该码。
            with lock:
                shared["outstanding"] -= 1
                shared["consumed"] += 1
                if ok:
                    shared["success"] += 1
                else:
                    shared["fail"] += 1
            if ok:
                state["success"] += 1
                state["rl_count"] = 0  # 成功：清零限流计数。
            else:
                state["fail"] += 1
            log({
                "type": "redeem", "account": email, "code": code,
                "ok": ok, "message": message,
                "account_success": state["success"], "account_fail": state["fail"], "limit": limit,
            })
            if ok:
                refresh_balance(state)  # 兑换成功后取余额。
                if state["success"] >= limit:
                    finish_account(state, "（已达上限）")
                    break

    threads = [
        threading.Thread(target=worker, args=(s,), name="redeem-w-" + s["email"], daemon=True)
        for s in active_states
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 阶段二收尾：参与过但未通过 finish_account 登记的账号（如码已抢完而未达上限）补发汇总 + 补余额。
    for s in states:
        if s["participated"] and not s["done_logged"]:
            finish_account(s, "")

    accounts_used = sum(1 for s in states if s["participated"])

    # ---- 阶段三：对所有选中账号补取余额（含登录失败/未参与兑换的账号）----
    for s in states:
        email = s["email"]
        if email in balance_done:
            continue
        if s["token"] and s["fetch"]:
            refresh_balance(s)
            continue
        # 登录失败的账号：再试一次登录取余额（与旧版一致；通常仍会失败）。
        fetch = close = token = None
        try:
            fetch, close = build_account_fetcher(proxy_url)
            token = login(fetch, email, s["password"])
        except RedeemError as exc:
            if close:  # 修复：登录失败时关闭刚建立的连接，避免 Session 泄漏。
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
            log({"type": "balance", "account": email, "ok": False,
                 "message": "余额获取失败（登录失败）：" + str(exc)})
            with lock:
                balance_done.add(email)
            continue
        s["fetch"], s["close"], s["token"] = fetch, close, token
        refresh_balance(s)

    # 关闭所有 Session，释放连接。
    for s in states:
        if s["close"]:
            try:
                s["close"]()
            except Exception:  # noqa: BLE001
                pass

    # 收集未消费的兑换码（队列残留）。
    leftover = []
    while True:
        try:
            leftover.append(q.get_nowait())
        except queue.Empty:
            break

    # 仍有未处理兑换码且因「限流」或「网络异常」中断时，提示用户稍后重试
    # （区别于账号容量富余导致的正常剩余）。
    rl = shared["rate_limited_accounts"]
    ne = shared["net_err_accounts"]
    incomplete = bool(leftover) and (rl > 0 or ne > 0)
    stop_reason = ""
    if incomplete:
        stop_reason = "rate_limited" if rl > 0 else "network"
    summary = {
        "accounts_total": len(accounts),
        "accounts_used": accounts_used,
        "codes_total": len(codes),
        "codes_consumed": shared["consumed"],
        "success": shared["success"],
        "fail": shared["fail"],
        "leftover_codes": leftover,
        "per_account_limit": limit,
        "rate_limited_accounts": rl,
        "network_error_accounts": ne,
        "stopped": incomplete,
        "stop_reason": stop_reason,
    }
    return {"logs": logs, "summary": summary}
