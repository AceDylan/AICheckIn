#!/bin/sh
# 容器入口：以 root 启动，修正挂载数据目录的属主后降权到 appuser 运行应用。
# 解决 bind mount（./data）宿主文件常属 root、非 root 容器用户无写权限导致
# 「Permission denied: /app/data/config.json」的问题，免去手动 chown。
set -e

DATA_DIR="$(dirname "${GYQD_CONFIG_FILE:-/app/data/config.json}")"
mkdir -p "$DATA_DIR"

# 尝试把数据目录交给 appuser；只读挂载或特殊文件系统会失败，忽略不致命。
chown -R appuser:appuser "$DATA_DIR" 2>/dev/null || true
chmod -R u+rwX "$DATA_DIR" 2>/dev/null || true

# 降权后执行 CMD（gunicorn）。
exec gosu appuser "$@"
