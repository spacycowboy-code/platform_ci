#!/bin/sh
# 在 swarm manager 上执行一次部署。由 apply 作业上传后运行 —— 不写在 ssh 的双引号字符串里:
# 那样每个 $ 和引号都要多层转义,已经因此坏过两次(config 名被写成一段错误提示文字)。
#
# 入参(位置参数):
#   $1 环境名(= stack 名,也是 GitLab 环境名)
#   $2 本次部署的版本变量名,如 RP_AI_PORTAL_FE_TAG
#   $3 本次部署的镜像标签
#   $4 可选:同一个仓的第二个镜像的版本变量名
set -eu

ENV_NAME="$1"
VAR="$2"
TAG="$3"
EXTRA_VAR="${4:-}"

cd "$(dirname "$0")"

# ---------- 1. 记下本次要部署的版本 ----------
touch .env
sed -i "/^${VAR}=/d" .env
echo "${VAR}=${TAG}" >> .env
if [ -n "$EXTRA_VAR" ]; then
  sed -i "/^${EXTRA_VAR}=/d" .env
  echo "${EXTRA_VAR}=${TAG}" >> .env
fi

# ---------- 2. 版本必须声明完整 ----------
# stack.yml 里的版本变量写成朴素 ${X_TAG}(不带 :? 提示 —— docker 对 :? 里的文字解析不可靠,
# 曾把提示文字渲染进 config 名字)。所以"必填"由这里保证:缺声明就在动任何东西之前退出。stack deploy 会重新渲染整个栈,所以任何
# 没声明版本的服务都会让部署失败 —— 这是有意的:以前有兜底值时,漏声明的服务会被静默拉回
# git 里那个数字。这里在动任何东西之前把缺失的点名报出来。
missing=""
for v in $(grep -oE '\$\{[A-Z_]+_TAG' stack.yml | sed 's/.*{//' | sort -u); do
  if grep -q "^${v}=" .env; then
    continue
  fi
  # 某个服务第一次纳入本环境时还没有声明过版本。如果它已经在集群里跑着,按现状补齐一次
  # 并记一行日志;之后 .env 就是权威。
  img=$(echo "$v" | sed 's/_TAG$//' | tr 'A-Z_' 'a-z-')
  cur=$(docker stack services "$ENV_NAME" --format '{{.Image}}' 2>/dev/null \
        | grep "/${img}:" | head -1 | sed 's/.*://; s/@.*//')
  if [ -n "$cur" ]; then
    echo "${v}=${cur}" >> .env
    echo "注意:${v} 原先没有声明,已按集群现状补为 ${cur}"
  else
    missing="${missing} ${v}"
  fi
done
if [ -n "$missing" ]; then
  echo "以下服务在本环境没有声明版本、集群里也没有在跑,请先分别部署它们各一次:${missing}"
  exit 1
fi
sort -o .env .env
echo "--- 本环境声明的版本 ---"
cat .env

# ---------- 3. config 对象名 = 内容哈希 ----------
# 内容一变名字就变,所以不需要人去记得改版本号(手工版本号时代改了文件忘改名字,
# config 会静默不更新且不报错)。
EDGE_STATIC_HASH=$(sha256sum edge-static.yml | cut -c1-12)
EDGE_DYNAMIC_HASH=$(sha256sum edge-dynamic.yml | cut -c1-12)
export EDGE_STATIC_HASH EDGE_DYNAMIC_HASH
[ -n "$EDGE_STATIC_HASH" ] && [ -n "$EDGE_DYNAMIC_HASH" ] || { echo "配置文件哈希算不出来,拒绝部署"; exit 1; }
echo "配置哈希: static=${EDGE_STATIC_HASH} dynamic=${EDGE_DYNAMIC_HASH}"

# stack deploy 不读 .env(实测:会用 stack.yml 里的值),所以先 source 进环境
set -a
. ./.env
set +a

# ---------- 4. 部署 ----------
# --with-registry-auth 必需:少了它,本地没缓存该镜像的节点会一直 Rejected: No such image
# --prune:stack.yml 是期望状态,文件里删掉的服务就该下线
docker stack deploy -c stack.yml --with-registry-auth --prune --detach=false "$ENV_NAME"

# ---------- 5. 清理不再被引用的 config ----------
# --prune 不管 config 对象,内容哈希命名下它们会累积。
used=$(docker stack services "$ENV_NAME" -q \
       | xargs -r -n1 docker service inspect \
         --format '{{range .Spec.TaskTemplate.ContainerSpec.Configs}}{{.ConfigName}} {{end}}' \
       | tr ' ' '\n' | sed '/^$/d' | sort -u)
for c in $(docker config ls --format '{{.Name}}' | grep -E '^edge_(static|dynamic)_' || true); do
  if ! echo "$used" | grep -qx "$c"; then
    docker config rm "$c" >/dev/null 2>&1 && echo "清理不再引用的 config: $c"
  fi
done

# ---------- 6. 报告生效状态 ----------
echo "--- 集群里生效的镜像(带 digest,这才是运行态的真相)---"
for svc in $(docker stack services "$ENV_NAME" --format '{{.Name}}'); do
  docker service inspect "$svc" --format '{{.Spec.Name}} {{.Spec.TaskTemplate.ContainerSpec.Image}}'
done
docker stack services "$ENV_NAME" --format '{{.Name}} {{.Replicas}}'
