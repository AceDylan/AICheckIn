FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5525

# 非 root 运行，符合容器安全最佳实践。
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5525/api/health',timeout=3).status==200 else 1)" || exit 1

# 单 worker + 多线程：签到是短时阻塞 IO，无需多进程。
CMD ["gunicorn", "-b", "0.0.0.0:5525", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]

