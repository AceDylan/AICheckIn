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

- **收藏库**：网址按分组管理，支持拖拽排序、置顶、标签、自动抓取站点图标。
- **站点看板**：为每个站点配置若干「接口字段」，粘贴浏览器的
  `Copy as cURL`，指定 JSON 取值路径，即可把余额 / 到期时间 / 请求数抓回卡片。
  支持金额换算（除数 + 单位）、时间戳转本地时间（可指定时区）、原值直显三种类型。
- **自动签到**：配置多组平台账号，一键全量签到或单站签到；可开启每日定时执行，
  保留最近 50 条运行历史。
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

```bash
cd /root/AICheckIn
cp -a data "../aicheckin-data-backup-$(date +%Y%m%d-%H%M%S)"   # 含明文凭据，妥善保管
git fetch origin && git checkout main && git merge --ff-only origin/main
docker compose up -d --build
docker compose logs --tail=50 aicheckin
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5525/api/health
```

---

## 安全

公网部署前请读 [SECURITY.md](SECURITY.md)——里面写了权限模型、环境变量、
反代配置要点，以及**凭据泄漏后的轮换步骤**。
