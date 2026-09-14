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

    # 先从运行现场收一张 变量名 -> 标签@摘要 的表。
    # 不能按"变量名推服务名"来查:同一个镜像可能跑成多个服务(后端与对账器就是同一个镜像),
    # 服务名也不等于镜像名(镜像 rp-ai-portal-fe 跑成服务 frontend)。所以反过来:
    # 遍历现场的每个服务/容器,读它的镜像,再由镜像名推出变量名。
    LIVE=$(mktemp)
    if [ "$MODE" = swarm ]; then
      # stack services 的 {{.Image}} 不带摘要,service inspect 才带
      docker stack services "$ENV_NAME" --format '{{.Name}}' 2>/dev/null | while read -r sname; do
        img="$(docker service inspect "$sname" \
               --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' 2>/dev/null || true)"
        [ -n "$img" ] && printf '%s %s\n' "$(var_of_image "$img")" "$img"
      done > "$LIVE"
    else
      docker compose ps -q 2>/dev/null | while read -r c; do
        img="$(docker inspect "$c" --format '{{.Config.Image}}' 2>/dev/null || true)"
        [ -n "$img" ] || continue
        dg="$(docker inspect "$c" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' 2>/dev/null || true)"
        [ -n "$dg" ] && img="${img}@${dg##*@}"
        printf '%s %s\n' "$(var_of_image "$img")" "$img"
      done > "$LIVE"
    fi

    echo "# 版本集合  环境=$ENV_NAME  形态=$MODE  采集=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# 每行一个服务的版本;# 后面是当时实际跑的镜像摘要,重放时用来核对拿到的是不是同一个镜像。"

    # 以描述文件里声明的版本变量为准,第三方镜像(traefik/mysql/grafana)在描述里写死,不进版本集合
    for v in $(grep -oE '\$\{[A-Z_]+_TAG' "$DESC" | sed 's/.*{//' | sort -u); do
      declared="$(sed -n "s/^${v}=//p" .env 2>/dev/null | tail -1)"
      img="$(awk -v k="$v" '$1==k {print $2; exit}' "$LIVE")"
      if [ -z "$img" ]; then
        echo "${v}=${declared:-未声明}   # 没在运行,值来自机器上的 .env"
        continue
      fi
      rest="${img#*:}"                 # tag 或 tag@sha256:...
      live_tag="${rest%%@*}"
      case "$img" in
        *@*) digest="${img##*@}" ;;
        *)   digest="(镜像里没有摘要信息)" ;;
      esac
      echo "${v}=${live_tag}   # ${digest}"
      if [ -n "$declared" ] && [ "$declared" != "$live_tag" ]; then
        echo "#   注意:.env 里声明的是 ${declared},实际跑的是 ${live_tag} —— 这里以实际为准"
      fi
    done
    rm -f "$LIVE"
    ;;

  apply-compose)
    TARGETS=""
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do TARGETS="$TARGETS $1"; shift; done
    [ "${1:-}" = "--" ] && shift
    [ "$#" -ge 1 ] || { echo "至少要传一个 变量名=标签" >&2; exit 1; }
    touch .env
    # 先把全部入参校验完再落盘。回滚逐行做而不是整份还原:同一台机器上可能有 CI 的部署正在
    # 写同一个 .env(两边都没有锁),整份还原会把对方刚写成功的标签一起抹掉。
    for kv in "$@"; do
      case "$kv" in *=*) ;; *) echo "参数 '$kv' 不是 变量名=标签 的形式" >&2; exit 1 ;; esac
      v="${kv%%=*}"; t="${kv#*=}"
      [ -n "$v" ] && [ -n "$t" ] || { echo "参数 '$kv' 有空值" >&2; exit 1; }
    done
    RB_SET=""; RB_DEL=""
    for kv in "$@"; do
      v="${kv%%=*}"; t="${kv#*=}"
      old=$(sed -n "s/^${v}=//p" .env | tail -1)
      if [ -n "$old" ]; then RB_SET="${RB_SET} ${v}=${old}"; else RB_DEL="${RB_DEL} ${v}"; fi
      sed -i "/^${v}=/d" .env
      echo "${v}=${t}" >> .env
      echo "记下 ${v}=${t}"
    done
    rollback_env() {
      for _kv in $RB_SET; do
        _v="${_kv%%=*}"
        sed -i "/^${_v}=/d" .env
        echo "$_kv" >> .env
      done
      for _v in $RB_DEL; do sed -i "/^${_v}=/d" .env; done
    }
    # shellcheck disable=SC2086
    if ! docker compose pull $TARGETS; then
      rollback_env
      echo "拉取镜像失败,.env 里本次改的那几行已还原" >&2
      exit 1
    fi
    # --force-recreate 的理由见 templates/deploy.yml 里那段长注释:compose 只在**服务定义**
    # 变化时重建容器,env_file 的**内容**变化不算定义变化。少了这个标志,重放会报成功而容器
    # 还是旧的 —— 生产上轮换密钥时已经这么坑过一次。重放工具尤其不能有这种无迹象的失败。
    # shellcheck disable=SC2086
    if ! docker compose up -d --force-recreate $TARGETS; then
      rollback_env
      echo "重建容器失败,.env 里本次改的那几行已还原" >&2
      exit 1
    fi
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
