#!/bin/sh
# 共享入口:仓库自带 docker-entrypoint.sh 就交给它(例如需要先跑数据库迁移),
# 否则直接起 uvicorn。这样"要不要迁移"是仓库自己的事,共享镜像不需要分支逻辑。
set -e
if [ -x /app/docker-entrypoint.sh ]; then
  exec /app/docker-entrypoint.sh "$@"
fi
exec uvicorn "$APP_MODULE" --host 0.0.0.0 --port "$APP_PORT" \
  $([ -f /app/log_conf.yaml ] && echo "--log-config log_conf.yaml")
