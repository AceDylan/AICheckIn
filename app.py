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

def _redeem_worker(job_id, accounts, codes, limit, proxy_url):
    """后台线程：执行批量兑换，进度实时写入 job。"""
    def progress(event):
        with _redeem_lock:
            job = _redeem_jobs.get(job_id)
            if job is not None:
                job["logs"].append(event)

    try:
        result = redeem.run_redeem(accounts, codes, limit, proxy_url, progress=progress)
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


def _mark_redeem_error(job_id, message):
    with _redeem_lock:
        job = _redeem_jobs.get(job_id)
        if job is not None:
            job["status"] = "error"
            job["error"] = message
            job["finished_at"] = _now_str()


def _redeem_has_running():
    return any(j.get("status") == "running" for j in _redeem_jobs.values())


def _evict_old_redeem_jobs():
    """超过保留上限时，删除最旧的已结束任务。"""
    if len(_redeem_jobs) <= REDEEM_JOB_CAP:
        return
    finished = [(jid, j) for jid, j in _redeem_jobs.items() if j.get("status") != "running"]
    finished.sort(key=lambda kv: kv[1].get("seq", 0))
    while len(_redeem_jobs) > REDEEM_JOB_CAP and finished:
        jid, _ = finished.pop(0)
        _redeem_jobs.pop(jid, None)


@app.post("/api/redeem/start")
def api_redeem_start():
    payload = request.get_json(silent=True) or {}
    accounts = redeem.parse_accounts(payload.get("accounts"))
    codes = redeem.parse_codes(payload.get("codes"))
    try:
        limit = max(1, int(payload.get("limit") or 1))
    except (TypeError, ValueError):
        limit = 1
    if not accounts:
        return jsonify({"ok": False, "error": "请填写至少一个有效账号（每行：邮箱 密码）"}), 400
    if not codes:
        return jsonify({"ok": False, "error": "请填写至少一个兑换码"}), 400

    # 兑换默认走签到页配置的全局代理（中转站常需代理直连）。
    try:
        proxy_url = read_store().get("proxy_url", "")
    except RuntimeError:
        proxy_url = ""

    global _redeem_seq
    with _redeem_lock:
        if _redeem_has_running():
            return jsonify({"ok": False, "error": "已有兑换任务进行中，请等待完成"}), 409
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
        args=(job_id, accounts, codes, limit, proxy_url),
        name="redeem-" + job_id,
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "job_id": job_id, "total_codes": len(codes)})


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
