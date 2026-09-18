# AICheckIn · Bookmark Hub

一个自托管的个人面板：**收藏库**（网址分组 + 站点图标）、**站点看板**
（把任意接口的余额 / 到期时间 / 用量抓回来显示）、以及 **自动签到**
（复用 `gyqd.py` 的签到逻辑，支持每日定时执行与运行历史）。

单文件 Flask 应用 + 单文件前端模板，数据全部落在一个 `config.json` 里，
没有数据库，容器只挂一个 `data/` 目录。

---

## 快速开始

```bash
git clone https://github.com/AceDylan/AICheckIn.git
cd AICheckIn

# 1. 设置管理密码（公网部署必须，见 SECURITY.md）
cp .env.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # 把结果填进 .env

# 2. 起服务
mkdir -p data
docker compose up -d --build

# 3. 打开 http://127.0.0.1:5525
```

`.env` 里没有 `GYQD_ADMIN_PASSWORD` 时 compose 会直接报错退出——这是刻意的，
避免「以为设了密码、其实完全开放」。本地玩票不想设密码的话，在 `.env` 里填一个
值即可（未设置密码的语义是**全部开放**，不要用于公网）。

---

## 功能

- **收藏库**：默认进入独立收藏首页，通过「自定义首页」从所有分组勾选展示的网址；首页沿用原分组顺序与卡片内容，编辑、移动和删除实时同步。网址菜单也支持「展示到首页 / 从首页移除」。
- **网址图标**：编辑网址时可上传备用图片（PNG、JPEG、WebP、GIF、ICO，最大 2 MB，自动缩为 128 像素静态 PNG）。显示顺序为自动获取的图标 → 上传图片 → 文字头像（可选 emoji，否则首字母）；上传图标始终保留，自动图标再次不可用时继续回退。首页选择及图片随配置保存、备份和导入导出。
- **分组管理**：支持拖拽排序、置顶、标签；站点看板仍是独立子页面。
- **精简导航**：左侧仅保留收藏库、签到中心、系统设置；收藏分组随收藏库展开。签到中心顶部的「服务配置」「运行记录」按钮切换内部面板，可随时返回签到概览。原有 `#configs` / `#history` 链接继续兼容，新地址为 `#checkin/configs` / `#checkin/history`。
- **站点看板**：为每个站点配置若干「接口字段」，粘贴浏览器的
  `Copy as cURL`，指定 JSON 取值路径，即可把余额 / 到期时间 / 请求数抓回卡片。
  支持金额换算（除数 + 单位）、时间戳转本地时间（可指定时区）、原值直显三种类型。
- **自动签到**：配置多组平台账号，一键全量签到或单站签到；可开启每日定时执行，
  保留最近 50 条运行历史。当天失败的账号会自动补签几次（只重跑没签成的那些）。
- **自动刷新**：站点看板的接口字段可按 15 分钟 ~ 1 天的间隔后台自动取数，
  余额 / 到期时间不必手点也保持新鲜。
- **全局搜索**：⌘K / Ctrl+K 唤起命令面板，跨全部分组搜网址、看板站点与签到配置；
  输入 `#标签` 按标签筛选。
- **死链检查**：分组页点「检查链接」逐条探测，打不开的加角标并可一键筛出；
  401/403 这类「活着但不给匿名访问」不算死链，内网服务可单独设为忽略检查。
- **配置恢复**：每次写入前留 `.bak`、每天首次改动另存一份（默认留 7 天）；
  误删分组或配置文件损坏时，可在「系统设置 → 配置恢复」里选一份回退。
- **PWA**：可安装为独立应用，应用外壳离线可用（接口数据一律不缓存）。
- **快速收藏**：装成应用后出现在手机系统「分享」菜单里；桌面可把书签小工具拖到
  书签栏，在任意网页点一下即带着当前网址与标题打开收藏弹窗（只预填，不自动保存）。
- **导入导出**：整份配置一键导出 / 导入（含凭据，**按机密文件处理**）。

---

## 目录结构

```
app.py              Flask 应用：路由、配置存取、鉴权、字段抓取、图标缓存、定时调度
gyqd.py             签到核心逻辑（被 app.py 复用，也可单独命令行运行）
templates/index.html  单文件前端（HTML + CSS 变量 + 原生 JS，无构建步骤）
static/app-v3.css   界面样式
tests/              unittest 测试套件
data/               运行数据（config.json / history.json / metrics.json / favicons/），不入库
```

---

## 开发

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 跑整套测试（必须在仓库根目录执行）
python -m unittest discover -s tests -t .

# 只跑一个模块
python -m unittest tests.test_api_exposure

# 本地起开发服务器
GYQD_CONFIG_FILE=$PWD/data/config.json GYQD_ADMIN_PASSWORD=dev-password-please-change \
  python app.py
```

测试用 `tests/_support.py` 的 `StoreIsolationMixin` 给每个用例分配独立数据目录，
因此整套一起跑与单跑结果一致。新增会读写 `config.json` 的测试类请继承它。

---

## 升级已部署的实例

> **从旧版本升级时必读**：管理密码已从 `docker-compose.yml` 移到 `.env`。
> 升级前不建好 `.env`，`docker compose` 会直接报错退出（这是刻意的，
> 避免「以为设了密码、其实全开放」）。

```bash
cd /root/AICheckIn

# 0. 备份（含明文凭据，妥善保管）
cp -a data "../aicheckin-data-backup-$(date +%Y%m%d-%H%M%S)"

# 1. 拉取
git fetch origin && git checkout main && git merge --ff-only origin/main

# 2. 建 .env（只需一次）。旧密码若曾写在 docker-compose.yml 里并进过 git 历史，
#    请换成新的随机口令，详见 SECURITY.md。
cp -n .env.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # 把结果填进 .env
grep -q '^GYQD_ADMIN_PASSWORD=.\+' .env || echo "⚠️  .env 里还没填管理密码"

# 3. 构建并起服务（本版新增了 .dockerignore，务必重新 build 而不是只 up）
docker compose config >/dev/null            # 先验证变量都齐了
docker compose up -d --build

# 4. 验证
docker compose logs --tail=50 aicheckin
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5525/api/health
```

升级后打开「系统设置 → 部署自检」（或解锁后 `GET /api/diagnostics`）确认
管理密码、数据目录、后台调度、定时签到这些项都是绿的。

回滚：`git checkout <上一个提交> && docker compose up -d --build`。
数据在 `data/` 里、不随代码回滚，旧版本读得了新版本写的 `config.json`
（新增的 `refresh` 键会被旧版本忽略）。

---

## 安全

公网部署前请读 [SECURITY.md](SECURITY.md)——里面写了权限模型、环境变量、
反代配置要点，以及**凭据泄漏后的轮换步骤**。
