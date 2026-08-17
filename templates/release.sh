#!/bin/sh
# 版本集合(一组服务版本的快照)。在目标机器上执行,由 CI 上传。
#
#   release.sh capture <环境名> <swarm|compose>
#       打印这个环境当前跑着的一组版本,格式就是可以直接存进 git 的样子。
#       同时对比"声明"(机器上的 .env)与"实际"(集群/容器里跑的镜像),不一致会标出来。
#
#   release.sh apply-compose <环境名> <要重建的服务...> -- <变量名=标签...>
#       compose 形态的整组重放。swarm 形态直接用 swarm-apply.sh,它已经接受多个 变量名=标签。
set -eu

ACTION="$1"
ENV_NAME="$2"
shift 2
cd "$(dirname "$0")"

# 镜像路径 -> 版本变量名:取最后一段路径,大写并把 - 换成 _,加 _TAG。
# 这与 stack.yml / compose.yml 里变量的命名约定一致,也与 deploy.yml 的反向映射一致。
var_of_image() {
  base="${1##*/}"      # 去掉 registry 与项目路径
  base="${base%%:*}"   # 去掉 :tag
  base="${base%%@*}"
  echo "$(echo "$base" | tr 'a-z-' 'A-Z_')_TAG"
}

case "$ACTION" in
  capture)
    MODE="$1"
    DESC=stack.yml
    [ "$MODE" = compose ] && DESC=compose.yml
    test -f "$DESC" || { echo "找不到 $DESC" >&2; exit 1; }

    echo "# 版本集合  环境=$ENV_NAME  形态=$MODE  采集=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# 每行一个服务的版本;# 后面是当时实际跑的镜像摘要,重放时用来核对拿到的是不是同一个镜像。"

    # 以描述文件里声明的版本变量为准,第三方镜像(traefik/mysql/grafana)在描述里写死,不进版本集合
    for v in $(grep -oE '\$\{[A-Z_]+_TAG' "$DESC" | sed 's/.*{//' | sort -u); do
      declared="$(sed -n "s/^${v}=//p" .env 2>/dev/null | tail -1)"
      svc="$(echo "$v" | sed 's/_TAG$//' | tr 'A-Z_' 'a-z-')"
      if [ "$MODE" = swarm ]; then
        img="$(docker stack services "$ENV_NAME" --format '{{.Image}}' 2>/dev/null \
               | grep "/${svc}:" | head -1)"
      else
        cid="$(docker compose ps -q 2>/dev/null | while read -r c; do
                 di="$(docker inspect "$c" --format '{{.Config.Image}}' 2>/dev/null || true)"
                 case "$di" in */${svc}:*) echo "$c"; break ;; esac
               done)"
        if [ -n "$cid" ]; then
          img="$(docker inspect "$cid" --format '{{.Config.Image}}' 2>/dev/null)"
          dg="$(docker inspect "$cid" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' 2>/dev/null || true)"
          [ -n "$dg" ] && img="${img}@${dg##*@}"
        else
          img=""
        fi
      fi
      live_tag="${img##*:}"; live_tag="${live_tag%@*}"
      digest="${img##*@}"; [ "$digest" = "$img" ] && digest="(取不到摘要)"
      if [ -z "$img" ]; then
        echo "${v}=${declared:-未声明}   # 没在运行,值来自机器上的 .env"
      elif [ -n "$declared" ] && [ "$declared" != "$live_tag" ]; then
        echo "${v}=${live_tag}   # ${digest}"
        echo "#   注意:.env 里声明的是 ${declared},实际跑的是 ${live_tag} —— 以实际为准记录"
      else
        echo "${v}=${live_tag}   # ${digest}"
      fi
    done
    ;;

  apply-compose)
    TARGETS=""
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do TARGETS="$TARGETS $1"; shift; done
    [ "${1:-}" = "--" ] && shift
    [ "$#" -ge 1 ] || { echo "至少要传一个 变量名=标签" >&2; exit 1; }
    touch .env
    for kv in "$@"; do
      case "$kv" in *=*) ;; *) echo "参数 '$kv' 不是 变量名=标签 的形式" >&2; exit 1 ;; esac
      v="${kv%%=*}"; t="${kv#*=}"
      [ -n "$v" ] && [ -n "$t" ] || { echo "参数 '$kv' 有空值" >&2; exit 1; }
      sed -i "/^${v}=/d" .env
      echo "${v}=${t}" >> .env
      echo "记下 ${v}=${t}"
    done
    # shellcheck disable=SC2086
    docker compose pull $TARGETS
    # shellcheck disable=SC2086
    docker compose up -d $TARGETS
    echo "--- 重放后实际在跑的镜像 ---"
    for c in $(docker compose ps -q); do
      docker inspect "$c" --format '  {{.Name}} {{.Config.Image}}' 2>/dev/null || true
    done
    ;;

  *)
    echo "用法:release.sh capture <环境名> <swarm|compose> | release.sh apply-compose <环境名> <服务...> -- <变量名=标签...>" >&2
    exit 1
    ;;
esac
