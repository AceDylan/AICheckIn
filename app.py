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
import hmac
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

# 镜像内 gyqd.py 与 app.py 同级；本地开发时回退到仓库根目录。
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import gyqd  # noqa: E402
import redeem  # noqa: E402

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

# 批量兑换任务：后台线程执行，前端轮询 /api/redeem/status/<id> 取实时进度。
# 内存态，重启即清空；同一时间仅允许一个进行中的兑换任务，避免并发登录互相干扰。
_redeem_jobs = {}
_redeem_lock = threading.Lock()
_redeem_seq = 0
# 已完成任务保留上限，超出按创建顺序淘汰最旧，避免内存无限增长。
REDEEM_JOB_CAP = 20

# 预热（预备-倒计时抢码）：放码前先登录选中账号、预热每账号 keep-alive 连接并挂在内存，
# 开抢时把这些「热上下文」交给 run_redeem(warm=...) 复用，跳过登录直接兑换。
# 单 gunicorn worker（见 Dockerfile：--workers 1）下模块级状态可跨请求存活。
# 结构：{id, status:'warming'|'ready'|'error', accounts:{email:ctx}, emails:[...],
#        logs:[...], error, proxy_url, started_at, ready_at, created_at(monotonic)}。
_redeem_armed = None
_redeem_armed_lock = threading.Lock()
_redeem_armed_seq = 0
# 预热保留时长（秒）：ready 后超过此时长自动作废并关连接。覆盖「2 分钟内可开抢」需求并留足余量。
ARMED_TTL_SECONDS = 300


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
                "redeem": dict(raw.get("redeem") or {}),
            }
        else:
            raise RuntimeError("config.json 格式应为数组或对象")
    else:
        store = {"configs": list(gyqd.CONFIGS), "proxy_url": "", "schedule": {}, "bookmarks": []}
    store.setdefault("configs", [])
    store.setdefault("proxy_url", "")
    store.setdefault("schedule", {})
    store.setdefault("bookmarks", [])  # 仅收藏不签到的站点：[{name, url}]
    # 兑换配置：账号（含密码、余额快照）与每账号成功/失败次数上限。
    redeem_cfg = dict(store.get("redeem") or {})
    redeem_cfg.setdefault("accounts", [])
    try:
        redeem_cfg["limit"] = max(1, int(redeem_cfg.get("limit") or 2))
    except (TypeError, ValueError):
        redeem_cfg["limit"] = 2
    # 兑换请求间隔（秒）：缓解服务端「too many attempts」限流，回落 redeem 模块默认值。
    def _norm_delay(key, default):
        try:
            v = float(redeem_cfg.get(key))
        except (TypeError, ValueError):
            return default
        return v if v >= 0 else default
    dmin = _norm_delay("delay_min", redeem.DEFAULT_DELAY_MIN)
    dmax = _norm_delay("delay_max", redeem.DEFAULT_DELAY_MAX)
    if dmax < dmin:
        dmax = dmin  # 上界不得小于下界。
    redeem_cfg["delay_min"] = round(dmin, 1)
    redeem_cfg["delay_max"] = round(dmax, 1)
    store["redeem"] = redeem_cfg
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


def clean_bookmark(payload):
    """校验并规整单条收藏站点；仅保留 name / url 两个字段。"""
    name = str(payload.get("name") or "").strip()
    url = str(payload.get("url") or "").strip()
    errors = []
    if not name:
        errors.append("name 必填")
    if not url:
        errors.append("url 必填")
    elif not url.startswith(("http://", "https://")):
        errors.append("url 需以 http:// 或 https:// 开头")
    if errors:
        raise ValueError("；".join(errors))
    return {"name": name, "url": url}


def clean_redeem_account(payload, existing=None):
    """校验并规整单个兑换账号；保留 email/password/enabled，沿用旧余额快照。

    password 留空且有 existing 时沿用旧密码（编辑时不必重填）。
    """
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "").strip()
    if not password and existing:
        password = str(existing.get("password") or "")
    enabled = bool(payload.get("enabled", True))

    errors = []
    if not email:
        errors.append("email 必填")
    if not password:
        errors.append("password 必填")
    if errors:
        raise ValueError("；".join(errors))

    item = {"email": email, "password": password, "enabled": enabled}
    if existing:  # 保留余额快照，避免编辑账号时丢失上次获取的余额。
        if existing.get("balance") is not None:
            item["balance"] = existing.get("balance")
        if existing.get("balance_updated_at"):
            item["balance_updated_at"] = existing.get("balance_updated_at")
    return item


def public_redeem_account(item):
    """对外暴露的兑换账号视图（不含密码明文）。"""
    return {
        "email": item.get("email", ""),
        "enabled": bool(item.get("enabled", True)),
        "has_password": bool(item.get("password")),
        "balance": item.get("balance"),
        "balance_updated_at": item.get("balance_updated_at"),
    }


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
        bookmarks[idx] = clean_bookmark(payload)
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


# =========================
# 路由：批量兑换（claude-zhongzhuan.cloud 登录 + 兑换，账号轮换）
# =========================

def _persist_redeem_balance(email, balance):
    """把某账号最新余额写回 config.json，刷新页面后仍可见。"""
    email_key = str(email or "").strip().lower()
    if not email_key:
        return
    try:
        store = read_store()
    except RuntimeError:
        return
    changed = False
    for acc in store["redeem"]["accounts"]:
        if str(acc.get("email") or "").strip().lower() == email_key:
            acc["balance"] = balance
            acc["balance_updated_at"] = _now_str()
            changed = True
    if changed:
        try:
            write_store(store)
        except RuntimeError:
            pass  # 余额持久化失败不影响兑换主流程。


def _redeem_worker(job_id, accounts, codes, limit, proxy_url, throttle=None, warm=None):
    """后台线程：执行批量兑换，进度实时写入 job；余额事件落盘持久化。

    warm：可选，由预热阶段（prewarm）得到的 {email: 热上下文}，命中账号跳过登录直接复用。
    """
    def progress(event):
        with _redeem_lock:
            job = _redeem_jobs.get(job_id)
            if job is not None:
                job["logs"].append(event)
        # 余额事件落库（在锁外执行，避免文件 IO 占用任务锁）。
        if event.get("type") == "balance" and event.get("ok") and event.get("balance"):
            _persist_redeem_balance(event.get("account"), event.get("balance"))

    try:
        result = redeem.run_redeem(accounts, codes, limit, proxy_url,
                                   progress=progress, throttle=throttle, warm=warm)
        with _redeem_lock:
            job = _redeem_jobs.get(job_id)
            if job is not None:
                job["status"] = "done"
                job["summary"] = result["summary"]
                job["finished_at"] = _now_str()
    except redeem.RedeemError as exc:
        _mark_redeem_error(job_id, str(exc))
    except Exception as exc:  # noqa: BLE001
        _mark_redeem_error(job_id, "兑换执行异常：{0}".format(exc))


# =========================
# 预热（预备-倒计时抢码）辅助
# =========================

def _close_armed(armed):
    """关闭一份预热态里所有账号的连接，释放资源。"""
    if not armed:
        return
    for ctx in (armed.get("accounts") or {}).values():
        close = ctx.get("close")
        if close:
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


def _expire_armed_if_stale():
    """若已就绪的预热超过 TTL，则作废并关连接（懒清理，在各预热相关接口入口调用）。"""
    global _redeem_armed
    with _redeem_armed_lock:
        armed = _redeem_armed
        if armed and armed.get("status") == "ready":
            if time.monotonic() - armed.get("created_at", 0.0) > ARMED_TTL_SECONDS:
                _close_armed(armed)
                _redeem_armed = None


def _prewarm_worker(armed_id, accounts, proxy_url):
    """后台线程：并行预登录并预热连接，完成后把热上下文挂到 _redeem_armed。"""
    def progress(event):
        with _redeem_armed_lock:
            a = _redeem_armed
            if a is not None and a.get("id") == armed_id:
                a["logs"].append(event)

    try:
        warm = redeem.prewarm_accounts(accounts, proxy_url, progress=progress)
        with _redeem_armed_lock:
            a = _redeem_armed
            if a is None or a.get("id") != armed_id:
                # 本次预热已被新的预热替换/作废：关闭刚建立的连接，避免泄漏。
                for ctx in warm.values():
                    close = ctx.get("close")
                    if close:
                        try:
                            close()
                        except Exception:  # noqa: BLE001
                            pass
                return
            a["accounts"] = warm
            a["status"] = "ready"
            a["ready_at"] = _now_str()
            a["created_at"] = time.monotonic()  # 有效期以「预热完成」为起点。
    except redeem.RedeemError as exc:
        with _redeem_armed_lock:
            a = _redeem_armed
            if a is not None and a.get("id") == armed_id:
                a["status"] = "error"
                a["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        with _redeem_armed_lock:
            a = _redeem_armed
            if a is not None and a.get("id") == armed_id:
                a["status"] = "error"
                a["error"] = "预热异常：{0}".format(exc)


def _mark_redeem_error(job_id, message):
    with _redeem_lock:
        job = _redeem_jobs.get(job_id)
        if job is not None:
            job["status"] = "error"
            job["error"] = message
            job["finished_at"] = _now_str()


def _redeem_has_running():
    return any(j.get("status") == "running" for j in _redeem_jobs.values())


def _select_redeem_accounts(all_accounts, indices):
    """按下标选取参与兑换/预热的账号，归一化为 [{email, password}]。

    indices 为非空列表时按下标选（越界忽略）；否则选全部。供 start 与 prewarm 共用，保持选择口径一致。
    """
    if isinstance(indices, list) and indices:
        try:
            picked = [all_accounts[int(i)] for i in indices if 0 <= int(i) < len(all_accounts)]
        except (TypeError, ValueError):
            picked = []
    else:
        picked = list(all_accounts)
    return [{"email": a.get("email"), "password": a.get("password")} for a in picked]


def _evict_old_redeem_jobs():
    """超过保留上限时，删除最旧的已结束任务。"""
    if len(_redeem_jobs) <= REDEEM_JOB_CAP:
        return
    finished = [(jid, j) for jid, j in _redeem_jobs.items() if j.get("status") != "running"]
    finished.sort(key=lambda kv: kv[1].get("seq", 0))
    while len(_redeem_jobs) > REDEEM_JOB_CAP and finished:
        jid, _ = finished.pop(0)
        _redeem_jobs.pop(jid, None)


@app.get("/api/redeem/config")
def api_redeem_config():
    """兑换账号（脱敏，不含密码）+ 次数上限 + 管理态。开放访问。"""
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    rc = store["redeem"]
    return jsonify({
        "ok": True,
        "accounts": [public_redeem_account(a) for a in rc["accounts"]],
        "limit": rc["limit"],
        "delay_min": rc["delay_min"],
        "delay_max": rc["delay_max"],
        "admin_required": bool(ADMIN_PASSWORD),
        "admin_unlocked": admin_ok(),
    })


@app.post("/api/redeem/accounts")
def api_redeem_account_create():
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        cleaned = clean_redeem_account(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        store = read_store()
        store["redeem"]["accounts"].append(cleaned)
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "index": len(store["redeem"]["accounts"]) - 1})


@app.put("/api/redeem/accounts/<int:idx>")
def api_redeem_account_update(idx):
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    accounts = store["redeem"]["accounts"]
    if idx < 0 or idx >= len(accounts):
        return jsonify({"ok": False, "error": "账号不存在"}), 404
    try:
        accounts[idx] = clean_redeem_account(payload, existing=accounts[idx])
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.delete("/api/redeem/accounts/<int:idx>")
def api_redeem_account_delete(idx):
    guard = _guard_admin()
    if guard:
        return guard
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    accounts = store["redeem"]["accounts"]
    if idx < 0 or idx >= len(accounts):
        return jsonify({"ok": False, "error": "账号不存在"}), 404
    accounts.pop(idx)
    try:
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.put("/api/redeem/settings")
def api_redeem_settings():
    """设置每账号成功/失败次数上限 + 兑换请求间隔（管理）。"""
    guard = _guard_admin()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        limit = int(payload.get("limit"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "limit 需为整数"}), 400
    if limit < 1:
        return jsonify({"ok": False, "error": "limit 至少为 1"}), 400

    # 请求间隔（秒，可选）：缓解服务端限流。提供则校验，否则保留原值。
    delay_min = delay_max = None
    if payload.get("delay_min") is not None or payload.get("delay_max") is not None:
        try:
            delay_min = float(payload.get("delay_min"))
            delay_max = float(payload.get("delay_max"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "请求间隔需为数字（秒）"}), 400
        if delay_min < 0 or delay_max < 0:
            return jsonify({"ok": False, "error": "请求间隔不能为负"}), 400
        if delay_max < delay_min:
            return jsonify({"ok": False, "error": "间隔上限不能小于下限"}), 400

    try:
        store = read_store()
        store["redeem"]["limit"] = limit
        if delay_min is not None:
            store["redeem"]["delay_min"] = round(delay_min, 1)
            store["redeem"]["delay_max"] = round(delay_max, 1)
        write_store(store)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.post("/api/redeem/start")
def api_redeem_start():
    """启动批量兑换（开放，无需管理密码）。

    账号取自服务端持久化配置，客户端仅传选中的账号下标 indices 与兑换码 codes；
    次数上限同样取自服务端配置（修改它需管理密码）。
    """
    payload = request.get_json(silent=True) or {}
    codes = redeem.parse_codes(payload.get("codes"))
    if not codes:
        return jsonify({"ok": False, "error": "请填写至少一个兑换码"}), 400

    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    all_accounts = store["redeem"]["accounts"]
    if not all_accounts:
        return jsonify({"ok": False, "error": "尚未配置兑换账号，请先在「兑换账号」中添加（需管理密码）"}), 400

    indices = payload.get("indices")
    accounts = _select_redeem_accounts(all_accounts, indices)
    if not accounts:
        return jsonify({"ok": False, "error": "请至少选择一个账号"}), 400

    limit = store["redeem"]["limit"]
    proxy_url = store.get("proxy_url", "")
    throttle = {
        "delay_min": store["redeem"]["delay_min"],
        "delay_max": store["redeem"]["delay_max"],
    }

    _expire_armed_if_stale()
    global _redeem_seq, _redeem_armed
    with _redeem_lock:
        if _redeem_has_running():
            # 注意：必须在确认可运行后才消费预热——否则一次失败的开抢会白白清掉预热。
            return jsonify({"ok": False, "error": "已有兑换任务进行中，请等待完成"}), 409

        # 消费/接管预热：ready 取走命中的热上下文复用；warming/error 一并作废，避免遗留热连接池
        # （warming 的预热线程完成时会检测 armed id 失配并自行关闭其连接，故此处无需再关）。
        warm = None
        with _redeem_armed_lock:
            armed = _redeem_armed
            if armed and armed.get("status") == "ready":
                sel_emails = {a["email"] for a in accounts}
                ctxs = armed.get("accounts") or {}
                warm = {e: c for e, c in ctxs.items() if e in sel_emails} or None
                # 关闭未被本次选择命中的热连接，避免泄漏。
                for e, c in ctxs.items():
                    if e not in sel_emails:
                        close = c.get("close")
                        if close:
                            try:
                                close()
                            except Exception:  # noqa: BLE001
                                pass
            if armed is not None:
                _redeem_armed = None  # 开抢即接管/作废当前预热（一次性消费）。

        _redeem_seq += 1
        job_id = "rj{0}".format(_redeem_seq)
        _redeem_jobs[job_id] = {
            "seq": _redeem_seq,
            "status": "running",
            "logs": [],
            "summary": None,
            "error": None,
            "started_at": _now_str(),
            "finished_at": None,
            "total_codes": len(codes),
        }
        _evict_old_redeem_jobs()

    thread = threading.Thread(
        target=_redeem_worker,
        args=(job_id, accounts, codes, limit, proxy_url, throttle, warm),
        name="redeem-" + job_id,
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "job_id": job_id, "total_codes": len(codes),
                    "prewarmed": bool(warm)})


@app.get("/api/redeem/status/<job_id>")
def api_redeem_status(job_id):
    with _redeem_lock:
        job = _redeem_jobs.get(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404
        # 返回副本，避免与后台线程并发读写同一列表。
        return jsonify({
            "ok": True,
            "status": job["status"],
            "logs": list(job["logs"]),
            "summary": job["summary"],
            "error": job["error"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "total_codes": job["total_codes"],
        })


@app.post("/api/redeem/prewarm")
def api_redeem_prewarm():
    """预热（预备-倒计时抢码）：后台并行登录选中账号、预热连接并挂在内存待开抢复用。

    开放访问，无需管理密码（与「开始兑换」一致）。客户端仅传 indices（选中账号下标）。
    """
    _expire_armed_if_stale()
    payload = request.get_json(silent=True) or {}
    try:
        store = read_store()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    all_accounts = store["redeem"]["accounts"]
    if not all_accounts:
        return jsonify({"ok": False, "error": "尚未配置兑换账号，请先在「兑换账号」中添加（需管理密码）"}), 400

    accounts = _select_redeem_accounts(all_accounts, payload.get("indices"))
    if not accounts:
        return jsonify({"ok": False, "error": "请至少选择一个账号"}), 400
    proxy_url = store.get("proxy_url", "")

    global _redeem_armed, _redeem_armed_seq
    with _redeem_armed_lock:
        if _redeem_has_running():
            return jsonify({"ok": False, "error": "有兑换任务进行中，暂无法预热"}), 409
        _close_armed(_redeem_armed)  # 关闭旧预热，避免连接泄漏。
        _redeem_armed_seq += 1
        armed_id = _redeem_armed_seq
        _redeem_armed = {
            "id": armed_id,
            "status": "warming",
            "accounts": {},
            "emails": [a["email"] for a in accounts],
            "logs": [],
            "error": None,
            "proxy_url": proxy_url,
            "started_at": _now_str(),
            "ready_at": None,
            "created_at": time.monotonic(),
        }

    thread = threading.Thread(
        target=_prewarm_worker,
        args=(armed_id, accounts, proxy_url),
        name="prewarm-{0}".format(armed_id),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "count": len(accounts), "armed_id": armed_id})


@app.get("/api/redeem/prewarm/status")
def api_redeem_prewarm_status():
    """预热状态：idle / warming / ready / error。开放访问。"""
    _expire_armed_if_stale()
    with _redeem_armed_lock:
        a = _redeem_armed
        if a is None:
            return jsonify({"ok": True, "status": "idle"})
        remaining = None
        if a["status"] == "ready":
            remaining = max(0, int(ARMED_TTL_SECONDS - (time.monotonic() - a.get("created_at", 0.0))))
        ready_emails = list((a.get("accounts") or {}).keys())
        return jsonify({
            "ok": True,
            "status": a["status"],
            "id": a.get("id"),
            "emails": a.get("emails", []),
            "ready_emails": ready_emails,
            "ready_count": len(ready_emails),
            "total": len(a.get("emails", [])),
            "logs": list(a.get("logs", [])),
            "error": a.get("error"),
            "started_at": a.get("started_at"),
            "ready_at": a.get("ready_at"),
            "remaining": remaining,
            "ttl": ARMED_TTL_SECONDS,
        })


@app.post("/api/redeem/disarm")
def api_redeem_disarm():
    """作废预热并关闭连接。开放访问。

    带 id 时仅作废 id 匹配的那一份（防止旧标签页/过期倒计时误清掉后来新建的预热）；
    不带 id 视为强制清理当前预热。
    """
    payload = request.get_json(silent=True) or {}
    want = payload.get("id")
    global _redeem_armed
    with _redeem_armed_lock:
        a = _redeem_armed
        if a is None:
            return jsonify({"ok": True})
        if want is not None and a.get("id") != want:
            return jsonify({"ok": True, "skipped": True})  # id 不匹配：不动当前预热。
        _close_armed(a)
        _redeem_armed = None
    return jsonify({"ok": True})


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
