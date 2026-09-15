# 安全说明与凭据处置

本服务把第三方平台的登录凭据（签到用的 `access_token`、收藏站点接口的
`Authorization` / `Cookie` 请求头）保存在 `data/config.json` 里，并把一个
可公网访问的页面挂在前面。因此「谁能读到什么」需要明确约定。

---

## 1. 必须立刻处理：曾经明文入库的管理密码

`docker-compose.yml` 自 **first commit** 起就把 `GYQD_ADMIN_PASSWORD` 的值
硬编码在文件里，而本仓库在 GitHub 上是 **public**。也就是说：

- 该密码对任何人可见，且**存在于 git 历史中**——从当前文件删掉并不会让历史里的那份消失；
- 知道密码的人可以解锁管理面板，进而读取全部真实 token 与接口请求头。

### 处置步骤（按顺序）

1. **换一个新的管理密码**（旧的一律视为已泄漏）：

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   ```

2. **写进 `.env`，不要再写回 `docker-compose.yml`**：

   ```bash
   cd /root/AICheckIn
   cp .env.example .env
   chmod 600 .env
   vi .env            # 填入 GYQD_ADMIN_PASSWORD=<上一步生成的值>
   ```

   `.env` 已在 `.gitignore` 内；`docker-compose.yml` 现在用
   `${GYQD_ADMIN_PASSWORD:?...}` 引用它，没设置时 compose 会直接报错退出，
   不会出现「以为设了、其实全开放」。

3. **轮换所有已存的第三方凭据**。页面此前对未解锁访问者也下发过收藏站点的
   完整接口配置，必须假设它们已经泄漏：

   - 每组签到配置的 `access_token`：去对应平台重新签发，在「配置管理」里逐条更新；
   - 每个收藏站点字段里的 `Authorization` / `Cookie`：在对应站点退出登录 / 重置 API Key，
     再重新用「Copy as cURL」粘贴一份新的；
   - `proxy_url` 若形如 `http://user:pass@host`，一并换掉代理口令。

4. **让已签发的会话 Cookie 全部失效**。会话签名密钥 = 持久随机串 + 当前管理密码，
   所以第 1 步改密码后旧 Cookie 自动失效，无需额外操作。
   若还想连持久随机串一起换：删除 `data/.session_secret` 后重启容器
   （代价：所有浏览器需要重新解锁一次）。

5. **（可选，需你自己决定）清理 git 历史**。彻底移除历史里的那份明文需要改写历史
   （`git filter-repo` / BFG）并强制推送，会让所有已有 clone 与 fork 失效。
   鉴于密码已经轮换，这一步属于「清理痕迹」而非「止血」，优先级低于上面 4 步。
   本次改动**没有**执行任何历史改写或强制推送。

---

## 2. 当前的权限模型

设置了 `GYQD_ADMIN_PASSWORD` 后：

| 能力 | 未解锁访客 | 已解锁（管理） |
|---|---|---|
| 打开首页、看收藏库网址分组 | ✅ | ✅ |
| 看收藏站点的名称 / 网址 / 字段展示值（余额、到期时间…） | ✅ | ✅ |
| 看签到配置的名称 / base_url / user_id / token 掩码 | ✅ | ✅ |
| 收藏字段的 curl / 请求头 / 接口 URL / JSON 路径 | ❌ | ✅（`/api/bookmarks/<i>/secret`） |
| 签到 token 原文 | ❌ | ✅（`/api/configs/<i>/secret`） |
| 代理地址原文 | ❌（只回「是否已配置」） | ✅ |
| 执行签到 / 单站签到 / 测试连接 | ❌ | ✅ |
| 运行历史 | ❌ | ✅ |
| 增删改配置 / 收藏 / 分组、导入导出、定时设置 | ❌ | ✅ |

**未设置 `GYQD_ADMIN_PASSWORD` 时上表整列放开**——这是给本地 / 内网部署留的口子，
公网部署务必设置。

### 私密模式（可选）

上表里「未解锁访客」那一列仍然能看到收藏库的网址清单——首页本来就是设计给人直接
打开的导航页。如果你不想让公网路人看到自建服务的地址与站点清单，设 `GYQD_PRIVATE=1`：

- 未解锁时 **所有** 接口都回 403，`/api/configs` 只回一个不含任何数据的空壳，
  页面据此渲染解锁面板（而不是把人怼到 403 白屏上）；
- 仍然开放的只有应用外壳（`/`、`/static/*`、`/sw.js`）、`/api/health`
  （容器 HEALTHCHECK 在调，堵掉会让容器被反复重启）与 `/api/auth`、`/api/logout`；
- 没设管理密码时私密模式不生效（无从校验），启动日志会明确告警。

### 实现要点

- `/api/configs` 是首页唯一的开放读接口，走 `public_bookmark` / `public_config` 脱敏：
  剥掉 `headers` / `curl` / `body` / `url` / `method` / `json_path` / `balance_config`，
  token 只给首尾各 4 位的掩码。**解锁与否都走同一份脱敏视图**，凭据不随列表广播；
  编辑态需要的原文由 admin-only 的单条接口按需拉取。
- 字段错误文案会去掉附带的上游响应片段（`public_error`），避免上游返回体被转发到公开页面。
- 管理密码失败按「客户端 IP + 全局」双桶计数，窗口内超限回 `429` + `Retry-After`；
  全局桶用于兜底伪造 `X-Forwarded-For` 换头重试的情况。
- 会话 Cookie 为 `HttpOnly` + `SameSite=Lax`，https 下加 `Secure`；令牌是服务端 HMAC 签名，
  浏览器端不保存任何明文密码。
- 所有响应带 `X-Content-Type-Options` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer`
  / `Cross-Origin-Opener-Policy` / CSP（`frame-ancestors 'none'`），`/api/*` 默认 `no-store`。

---

## 3. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `GYQD_ADMIN_PASSWORD` | 空 | 管理密码。空 = 完全开放，公网必须设置 |
| `GYQD_PRIVATE` | `0` | 设 `1` 开启私密模式：未解锁时连只读浏览也不给 |
| `GYQD_CONFIG_FILE` | `/app/data/config.json` | 配置文件路径，其所在目录同时存放历史 / 指标 / 图标缓存 |
| `GYQD_HSTS` | `0` | 设 `1` 时在 https 请求上下发 HSTS（会波及整个域名） |
| `GYQD_SCHEDULER` | `1` | 设 `0` 关掉后台线程：定时签到、当日补签与站点数据自动刷新都不再跑 |
| `GYQD_RETRY_LIMIT` | `3` | 定时签到当日最多补签几次（只重跑当天未签成的账号）。`0` 关闭 |
| `GYQD_RETRY_DELAY_MINUTES` | `30` | 两次补签之间至少间隔多少分钟 |
| `GYQD_LOGIN_MAX_FAILS` | `8` | 单个来源 IP 在窗口内允许的管理密码失败次数 |
| `GYQD_LOGIN_GLOBAL_MAX_FAILS` | `40` | 全局失败上限，兜底伪造来源 IP |
| `GYQD_LOGIN_WINDOW` | `900` | 失败计数窗口 / 锁定时长（秒） |
| `GYQD_MAX_REQUEST_BYTES` | `4194304` | 请求体上限（字节）。超出回 413，防止大 body 撑爆单 worker 内存 |

---

## 4. 部署建议

- **不要把 5525 直接暴露到 0.0.0.0**。推荐 `ports: ["127.0.0.1:5525:5525"]`，
  由 nginx 反代并只对外开 https 端口。
- 反代需要转发真实来源 IP，否则防爆破会把所有人算成同一个来源：

  ```nginx
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  ```

- `data/` 目录含明文凭据，备份时按机密处理（加密存放，不要传到公开位置）。
- `data/config.json.bak` 是写入前的自动备份，同样含明文凭据。
- 配置落盘走「同目录临时文件 + fsync + 原子替换」，写到一半被中断也不会留下半截
  JSON；新建的配置文件按 `0600` 创建，已存在的文件沿用原权限。

---

## 5. 报告问题

发现安全问题请直接在仓库提 issue 前先私下联系维护者；不要在公开 issue 里粘贴
任何 token、Cookie、密码或完整 curl 命令。
