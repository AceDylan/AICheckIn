FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# gosu：入口脚本以 root 修正挂载目录属主后，降权到 appuser 运行应用。
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5525

# 非 root 运行应用：创建 appuser；实际降权在 entrypoint 中完成（需 root 才能 chown bind mount）。
RUN useradd --create-home appuser \
    && chown -R appuser /app \
    && chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5525/api/health',timeout=3).status==200 else 1)" || exit 1

# entrypoint 以 root 修正 /app/data 属主，再用 gosu 降权到 appuser 执行下方 CMD。
# 单 worker + 多线程：签到是短时阻塞 IO，无需多进程。
# 线程数从 4 提到 8：/api/favicon 首次抓取站点图标时会阻塞若干秒（有磁盘缓存，只在冷启动出现），
# 一屏卡片最多并发 3 个，留足余量给页面自身的接口调用。
# --worker-tmp-dir /dev/shm：worker 每隔几秒要改一次心跳文件，默认放在 /tmp（容器里是 overlay 磁盘层），
# 磁盘忙时这一下可能卡住，只有一个 worker 时整个服务跟着停顿、严重时被判超时重启。放进内存盘就没有这个问题
# （gunicorn 官方文档对 Docker 部署的建议）。
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "-b", "0.0.0.0:5525", "--workers", "1", "--threads", "8", "--timeout", "120", "--worker-tmp-dir", "/dev/shm", "app:app"]
