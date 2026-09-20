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
| 执行签到 / 单站签到 / 测试连接（`/api/checkin`、`/api/checkin/<i>`、`/api/test/<i>`） | ❌ | ✅ |
| 运行历史（`/api/history`） | ❌ | ✅ |
| 增删改配置 / 收藏 / 分组、定时设置 | ❌ | ✅ |
| 导出 / 导入整份配置（`/api/configs/export`、`/api/configs/import`） | ❌ | ✅ |
| 部署自检（`/api/diagnostics`） | ❌ | ✅ |
| 死链检查（`/api/link_groups/<id>/check`） | ❌ | ✅ |
| 查看 / 恢复配置备份（`/api/backups`、`/api/configs/restore`） | ❌ | ✅ |
| 加载壁纸（全部页面共用；内置 `/static/wallpapers/*`、自定义 `GET /api/wallpaper`） | ✅（私密模式未解锁时自定义壁纸不可见，回落到内置） | ✅ |
| 上传 / 移除自定义壁纸（`PUT` / `DELETE /api/wallpaper`） | ❌ | ✅ |
| 首页待办、倒数日、便签的读写（`/api/todos*`、`/api/deck*`） | ❌（`/api/configs` 里也不下发） | ✅ |
| 首页日历、到期提醒（浏览器里现算，只用上面已经公开的字段） | ✅ | ✅ |

**未设置 `GYQD_ADMIN_PASSWORD` 时上表整列放开**——这是给本地 / 内网部署留的口子，
公网部署务必设置。

### 私密模式（可选）

上表里「未解锁访客」那一列仍然能看到收藏库的网址清单——首页本来就是设计给人直接
打开的导航页。如果你不想让公网路人看到自建服务的地址与站点清单，设 `GYQD_PRIVATE=1`：

- 未解锁时 **所有** 接口都回 403，`/api/configs` 只回一个不含任何数据的空壳，
  页面据此渲染解锁面板（而不是把人怼到 403 白屏上）；
- 仍然开放的只有应用外壳（`/`、`/static/*`、`/sw.js`）、`/api/health`
  （容器 HEALTHCHECK 在调，堵掉会让容器被反复重启）与 `/api/auth`、`/api/logout`；
  配了受信任嵌入方时再加上 `/embed/enter`、`/api/embed/handshake`（同样是解锁入口，靠签名票据把关，见下一节）；
- 没设管理密码时私密模式不生效（无从校验），启动日志会明确告警。

### 被自己的 HaloWebUI 嵌入：iframe 白名单与共享管理员会话（可选）

两项都默认关闭，互相独立，通常一起用。

**1. iframe 白名单 `HUB_FRAME_ANCESTORS`**

- 默认只允许同源嵌入：`X-Frame-Options: SAMEORIGIN` + CSP `frame-ancestors 'self'`，任何外站都嵌不进来。
- 要让另一个站点嵌入，逐个写出它的完整源，例如 `HUB_FRAME_ANCESTORS=https://host.acedylan.us:3001`。
  此时 CSP 变为 `frame-ancestors 'self' https://host.acedylan.us:3001`，并且不再发 `X-Frame-Options`
  （它表达不了「同源 + 指定站点」，`ALLOW-FROM` 早已废弃；现代浏览器以 `frame-ancestors` 为准）。
- **不支持通配符**：`*`、`https://*.example.com`、带路径的地址、裸域名都会被丢弃并在启动日志里点名。
  写错的后果是「嵌不进来」，而不是「谁都能嵌」。`http://` 只放行 `localhost` / `127.0.0.1`（本机调试）。
- 页面被嵌入时（后端看 `Sec-Fetch-Dest: iframe`，页面脚本再用 `window.top` 校正一次）所有外链一律开新标签页，
  「当前页打开」的偏好暂不生效——否则会把整个框导航到外站。

**2. 共享管理员会话 `HUB_TRUSTED_EMBED_ADMIN_SECRET`**

让「HaloWebUI 的管理员已登录」等于「这里已解锁」，框里不用再输一遍管理密码。方向是单向的：
HaloWebUI → 本站。反过来（用本站的管理密码登录 HaloWebUI）没有做，也不应该做。

- 密钥流转：管理员部署时现场生成一个随机值（`python3 -c "import secrets; print(secrets.token_hex(32))"`），
  分别写进**两台机器各自的 `.env`**（本站与 HaloWebUI 用同名变量 `HUB_TRUSTED_EMBED_ADMIN_SECRET`）。
  它**绝不入库**，不下发给浏览器，不出现在任何 URL、日志或接口响应里；少于 32 个字符会被忽略（视同未配置）。
- 票据：HaloWebUI 后端确认当前用户是它的管理员后，用密钥签一张
  `v1.<用途>.<过期时间戳>.<nonce>.<HMAC-SHA256>`，浏览器把 iframe 指向 `/embed/enter?ticket=…`。
  本站依次验签名、用途、有效期（只认 5 分钟以内，HaloWebUI 实际签 2 分钟）、nonce 未用过，
  通过后下发的就是 `/api/auth` 那枚 HMAC 会话 Cookie（同一套签发与校验代码），再 `303` 跳到不带票据的地址。
- 票据会出现在 URL 里（因此也会进反代的访问日志），所以它**一次性 + 短命**：进日志的那一刻已经作废。
  验签失败、格式不对计入上面的防爆破计数；过期 / 重放不计数（那说明对方确实持有密钥，多半是两台机器时钟没对上）。
- 经票据换来的会话默认 12 小时（`HUB_TRUSTED_EMBED_SESSION_TTL`，HaloWebUI 每次打开 `/hub` 都会重新换票）；
  浏览器里已有一枚有效会话（比如用密码解锁的 30 天会话）时不会被这枚短的顶掉。
- `POST /api/embed/handshake`：HaloWebUI 启动时带一张 `probe` 用途的票据来，只回答「密钥一致、时钟没偏」，
  不下发任何凭据；`probe` 票据换不了会话（用途写在签名里）。
- nonce 记在进程内存里：当前 Dockerfile 是 gunicorn 单 worker，够用；改成多 worker 必须换成共享存储，
  否则同一张票据在不同 worker 上各能用一次。
- **谁持有这个密钥，谁就是本站管理员**。它的保管等级与 `GYQD_ADMIN_PASSWORD` 相同；怀疑泄露就两边同时换掉。
  换密钥不会让已下发的会话失效——要一并踢掉就同时改管理密码（会话签名密钥里含管理密码）。

### 实现要点

- `/api/configs` 是首页唯一的开放读接口，走 `public_bookmark` / `public_config` 脱敏：
  剥掉 `headers` / `curl` / `body` / `url` / `method` / `json_path` / `balance_config`，
  token 只给首尾各 4 位的掩码。**解锁与否都走同一份脱敏视图**，凭据不随列表广播；
  编辑态需要的原文由 admin-only 的单条接口按需拉取。
- 字段错误文案会去掉附带的上游响应片段（`public_error`），避免上游返回体被转发到公开页面。
- 死链检查只在管理员点击时才跑，且只探测库里已有的网址（不接受任意 URL 参数）；
  单次有并发上限与时间预算，不会被拿来当端口扫描器或流量放大器。
- 配置恢复的备份标识只接受 `bak` 或 `YYYY-MM-DD` 两种形态，绝不把请求里的字符串
  拼进路径——否则就是一个任意文件读取、以及用任意文件覆盖 `config.json` 的洞。
  恢复前会校验备份本身能否解析，被覆盖的那份另存为 `config.json.corrupt-<时间戳>`。
- 壁纸（对全部页面生效）只从本站加载：CSP 保持 `img-src 'self' data:`，没有为它放行外链或 `blob:`。
  自定义壁纸上传要管理权限，类型按魔数判定且只收 PNG / JPEG / WebP（SVG 能内嵌脚本，不收），
  上限 2 MB，固定文件名落在 `data/wallpaper/`（请求里没有任何字符串会进路径）；回吐时带
  `nosniff` 与 `default-src 'none'; sandbox`。浏览器上报的「画面亮度」只接受 0~1 的数，
  不可信就丢弃并按最亮画面处理——它只影响遮罩深浅，不影响权限。
- 图标底板的明暗判定完全在浏览器里完成：把已经加载好的同源图标（`/api/favicon` 或 `data:` 上传图）缩到 24px
  画到离屏 canvas 上读像素，不发新请求、不落盘、不进 Cookie，也不需要放宽 CSP；读不到像素就保持默认底板。
  导航形态沿用 Cookie `bh_home_nav`（值域白名单 `rail` / `full`），`<head>` 里只对它做固定字符串匹配。
- 首页待办比收藏更私人：`/api/todos*` 全部要管理权限，开放的 `/api/configs` 只在已解锁（或未设密码）时下发待办，
  私密模式的空壳里同样没有。内容存在 `data/todos.json`（不进 `config.json`，勾选待办不会重写装着凭据的那个文件），
  长度 ≤ 200 字、最多 200 条，渲染时一律转义。旧版的显示偏好 Cookie `bh_home_todo`（值域白名单 `open` / `closed` / `off`）现在只读不写，用来给首页组件推默认值。
  排序接口 `POST /api/todos/<id>/move` 同样要管理权限：只接受 `before` / `after` 二选一、值必须是另一条现存待办的 id，
  校验不过（缺参、两个都给、以自己为参照、参照物不存在）一律不落盘；它只改顺序，不接收也不回显任何新内容。
- 首页组件（日历 / 待办 / 倒数日 / 便签 / 到期提醒 / 签到状态）：倒数日与便签和待办同一个待遇——`/api/deck*` 全部要管理权限，
  开放的 `/api/configs` 只在已解锁（或未设密码）时下发，私密模式的空壳里没有；内容存在 `data/deck.json`（不进 `config.json`，自带 `.bak`，
  随「导出 JSON」的 `deck` 键带走）。校验：名称 ≤ 40 字、日期必须是 1900–2200 年间真实存在的 `YYYY-MM-DD`、重复方式只认 `none` / `year` / `lunar`、
  最多 50 个；便签 ≤ 2000 字，去掉换行和制表符之外的控制字符。名称渲染时一律转义，便签内容只写进 `<textarea>` 的 `value`，不经过 HTML。
  日历、节日、农历全部在浏览器里现算（页面内置 1900–2100 年农历表与当年的放假安排），**不向任何外部服务发请求**，CSP 没有为它放宽；
  到期提醒只用看板本来就公开的字段，签到状态在未解锁时不显示（账号清单本来就不下发）。
  组件的布局是纯本机偏好：Cookie `bh_home_deck`（已添加的组件及顺序）、`bh_home_deck_fold`（右侧一列里收起的）、`bh_home_deck_open`（标签条里展开的那个），
  读取时按 `[a-z.]` 字符集整体匹配、再逐个按组件 id 白名单过滤，被改成别的内容就回落到默认值，不会被拼进页面。
- 待办里的网址可点，但只认 `http://` / `https://`（`javascript:`、`data:` 等不会被识别成链接，`safeUrl` 再兜一层），
  链接一律 `target="_blank" rel="noopener noreferrer nofollow"`：对方拿不到 `window.opener`，Referer 里也不会出现起始页地址。
  显示文字取自 `new URL()` 解析后的主机名 + 路径而不是原文，`https://银行@evil.example` 这类障眼法会如实显示成 `evil.example`；
  网址只取 ASCII 合法字符，非 ASCII 的同形异义域名不会被当成链接。拼进 HTML 的每一段（前后文、`href`、`title`、显示文字）都转义。
- 「常用一行」与「分组折叠」是纯本机功能，没有新增接口、不向服务端上报点击。项目约定不使用 Web Storage，记录放在 Cookie 里：
  `bh_home_hits`（`天数.哈希-次数…`，只含网址的 32 位哈希，不含网址）与 `bh_home_fold`（分组 id 列表）；两者读取时都按严格的
  字符集 / 格式白名单整体校验，被改成别的内容就整个作废，不会被拼进页面。「外观」里关闭「常用一行」会同时清掉已有记录。
- 「编辑首页」（拖动图标排序 / 移除）没有新增接口：排序复用 `POST /api/link_groups/<gid>/links/reorder` 与
  `POST /api/bookmarks/reorder`（都要管理权限、都要求是现有条目的一个全排列），移除复用既有的 `show_on_home` 开关；
  未解锁的访客看不到「添加 / 编辑」入口，直接调接口也会被 403 挡下。编辑状态下图标的名称进 `aria-label` 前同样转义，
  「自定义首页」弹窗里用于筛选的 `data-pick-text` / `data-pick-name` 也是转义后的属性值，不经过 `innerHTML` 之外的任何求值。
- 配置恢复接口的备份 id 白名单是 `bak`、`pre-import` 与 `YYYY-MM-DD`，全是固定字面量或严格格式，请求里的字符串不会进路径。
  `pre-import` 对应 `config.json.pre-import.bak`：那是一次性数据迁移（从 WeTab 备份迁入网址与待办）前留下的回溯点，
  本站自己不再生成它——迁移做完后，导入入口与 `POST /api/import/wetab` 接口都已移除，不留长期开放的批量写入口。
- 管理密码失败按「客户端 IP + 全局」双桶计数，窗口内超限回 `429` + `Retry-After`；
  全局桶用于兜底伪造 `X-Forwarded-For` 换头重试的情况。
- 会话 Cookie 为 `HttpOnly` + `SameSite=Lax`，https 下加 `Secure`；令牌是服务端 HMAC 签名，
  浏览器端不保存任何明文密码。
- 所有响应带 `X-Content-Type-Options` / `X-Frame-Options: SAMEORIGIN` / `Referrer-Policy: no-referrer`
  / `Cross-Origin-Opener-Policy` / CSP（`frame-ancestors 'self'`），`/api/*` 默认 `no-store`。
  配了 `HUB_FRAME_ANCESTORS` 白名单后 `frame-ancestors` 追加白名单里的源、`X-Frame-Options` 不再下发（见上一节）。
- 传输压缩：文本类响应（HTML / CSS / JS / JSON，≥ 1 KB）在客户端声明支持时用 gzip 压缩。**会回真实凭据的接口不压缩**
  （`/api/configs/<idx>/secret`、`/api/bookmarks/<idx>/secret`、`/api/configs/export`；有测试保证新增的 `*_secret` 端点必须进这张名单）：
  「压缩 + 可观测的密文长度」是 BREACH 一类攻击的前提。其余接口的响应里没有请求方可控的回显，会话 Cookie 又是 `SameSite=Lax`
  （跨站子请求不带 Cookie），本就不满足攻击条件；不压缩只是把这条路彻底关死。压缩结果只缓存带 ETag 的外壳文件（首页、静态文件），
  接口数据不进缓存，`no-store` 等响应头原样保留。
- 首页外壳（`/`）带内容哈希的 ETag，`Cache-Control: no-cache`（可以留存、但每次都要回源验证）。外壳里没有任何数据——收藏、待办、
  配置全部来自 `/api/*`（`no-store`）——所以让浏览器留着它不泄漏任何东西，私密模式下同样如此。
- 「`<head>` 里提前发出的数据请求」「回到页面时自动对一下数据」与「首次加载失败后重试」请求的都是同一个 `/api/configs`（固定地址、不带任何参数），权限与脱敏规则不变；
  会话在离开期间过期的话，回来后页面会如实回到未解锁状态。

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
| `GYQD_BACKUP_DAYS` | `7` | 配置文件每日留档保留天数。`0` 只保留一份 `.bak` |
| `HUB_FRAME_ANCESTORS` | 空 | 允许把本站嵌进 iframe 的外站白名单（完整的源，空格分隔，不支持通配符）。空 = 只许同源 |
| `HUB_TRUSTED_EMBED_ADMIN_SECRET` | 空 | 受信任嵌入方（HaloWebUI）的共享密钥，≥ 32 字符。部署时现场生成，**绝不入库**。空 = 免密入口关闭 |
| `HUB_TRUSTED_EMBED_SESSION_TTL` | `43200` | 经票据换来的管理员会话有效期（秒），范围 300 ～ 30 天 |

---

## 4. 部署建议

- **不要把 5525 直接暴露到 0.0.0.0**。推荐 `ports: ["127.0.0.1:5525:5525"]`，
  由 nginx 反代并只对外开 https 端口。
- 反代需要转发真实来源 IP，否则防爆破会把所有人算成同一个来源：

  ```nginx
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  ```

- **不要在没有 `.dockerignore` 的情况下构建镜像**。Dockerfile 里是 `COPY . .`，
  仓库内已提供 `.dockerignore` 把 `data/`、`.env`、`.git` 挡在构建上下文之外；
  缺了它，装着全部凭据的 `data/config.json` 和存管理密码的 `.env` 会被烘进镜像层
  （运行时被 bind mount 盖住，看不出异常，但镜像一旦导出或推送就是明文泄漏）。
- `data/` 目录含明文凭据，备份时按机密处理（加密存放，不要传到公开位置）。
- `data/config.json.bak` 是写入前的自动备份；`data/config.json.<日期>.bak` 是每天
  第一次改动前的留档（默认保留 7 天）。**它们同样含明文凭据**，删除或外传前请留意。
- 配置落盘走「同目录临时文件 + fsync + 原子替换」，写到一半被中断也不会留下半截
  JSON；新建的配置文件按 `0600` 创建，已存在的文件沿用原权限。

---

## 5. 报告问题

发现安全问题请直接在仓库提 issue 前先私下联系维护者；不要在公开 issue 里粘贴
任何 token、Cookie、密码或完整 curl 命令。
