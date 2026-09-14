#!/bin/sh
# 在 swarm manager 上执行一次部署。由 apply 作业上传后运行 —— 不写在 ssh 的双引号字符串里:
# 那样每个 $ 和引号都要多层转义,已经因此坏过两次(config 名被写成一段错误提示文字)。
#
# 入参(位置参数):
#   $1     环境名(= stack 名,也是 GitLab 环境名)
#   $2...  一个或多个 变量名=标签,如 RP_AI_PORTAL_FE_TAG=e70a3471
#
# 接受多个而不是固定一个:发布一个服务时传一对,整组重放一个版本集合时传全部
# (见 README 的"版本集合"),两种情形共用同一条部署路径。
set -eu

ENV_NAME="$1"
shift
[ "$#" -ge 1 ] || { echo "至少要传一个 变量名=标签"; exit 1; }

cd "$(dirname "$0")"
# 目录权限自己兜住:同环境的密钥就在这里,不能让同机其它用户读到
chmod 750 . 2>/dev/null || true

# ---------- 1. 解析本次要部署的版本(**先不落盘**) ----------
# 顺序是这个脚本最容易出错的地方,所以写明:解析 -> 校验 -> 落盘 -> 部署 -> 失败回滚。
# 以前是先把 V=TAG 写进 .env 再校验,于是校验失败退出时 .env 已经记下新标签而集群还是旧版本,
# 两件坏事:(a) 重试一次就"莫名"通过了 —— 通过的原因是上一次失败留下的残留,不是问题解决了;
# (b) 那个没验证过的标签会在下一次**任何人**部署时被整栈渲染顺手带上去,而没人会注意到。
PENDING=""
for kv in "$@"; do
  case "$kv" in
    *=*) ;;
    *) echo "参数 '$kv' 不是 变量名=标签 的形式"; exit 1 ;;
  esac
  v="${kv%%=*}"; t="${kv#*=}"
  [ -n "$v" ] && [ -n "$t" ] || { echo "参数 '$kv' 的变量名或标签为空"; exit 1; }
  PENDING="${PENDING} ${v}=${t}"
done
touch .env

# 某个版本变量本次是否已经有值:先看本次入参,再看机器上的 .env。
# 循环变量用 _kv 而不是 kv:POSIX sh 的函数没有局部作用域,重名会把调用方的 for 变量改掉。
resolved() {
  for _kv in $PENDING; do
    [ "${_kv%%=*}" = "$1" ] && return 0
  done
  grep -q "^${1}=" .env
}

# ---------- 2. 版本必须声明完整 ----------
# stack.yml 里的版本变量写成朴素 ${X_TAG}(不带 :? 提示 —— docker 对 :? 里的文字解析不可靠,
# 曾把提示文字渲染进 config 名字)。所以"必填"由这里保证:缺声明就在动任何东西之前退出。
# stack deploy 会重新渲染整个栈,所以任何没声明版本的服务都会让部署失败。
#
# 四个来源,优先级从高到低:
#   1) 本次入参          —— 这次要部署的就是它
#   2) 机器上的 .env     —— 本环境当前声明的版本,权威
#   3) 集群现状          —— 第一次纳入本环境但已经在跑,按现状补齐
#   4) versions.txt      —— 本环境committed 的**初始版本**(最低优先级)
#
# 第 4 条是后加的,专治"一个服务没部署过就卡住所有人":以前只有 1-3,于是谁把一个新服务块写进
# stack.yml,从那一刻起所有人的部署都渲染不出来,直到那个服务被单独部署一次。现在初始版本在
# 同一个 MR 里声明,连坐消失。
# 它**不会**重演"有兜底值时漏声明的服务被静默拉回 git 里那个数字"这个老问题 —— 因为它排在
# .env 与集群现状之后,永远盖不住运行态;已经在跑的服务在 versions.txt 里写 '-' 即可。
missing=""
parked=""
adopted=""
for v in $(grep -oE '\$\{[A-Z_]+_TAG' stack.yml | sed 's/.*{//' | sort -u); do
  if resolved "$v"; then
    continue
  fi
  img=$(echo "$v" | sed 's/_TAG$//' | tr 'A-Z_' 'a-z-')
  cur=$(docker stack services "$ENV_NAME" --format '{{.Image}}' 2>/dev/null \
        | grep "/${img}:" | head -1 | sed 's/.*://; s/@.*//')
  if [ -n "$cur" ]; then
    echo "注意:${v} 原先没有声明,已按集群现状补为 ${cur}"
  elif [ -f versions.txt ]; then
    # 顺手去掉空白与 CR:这个文件是人手编辑的,而 CRLF 在这套部署里已经坑过一次
    # (deploy.yml 投放私钥前要先 tr -d '\r' 就是因为这个)。
    cur=$(sed -n "s/^${v}=//p" versions.txt | tail -1 | tr -d ' \t\r')
    if [ "$cur" = "-" ]; then
      cur=""
      parked="${parked} ${v}"
    elif [ -n "$cur" ]; then
      echo "注意:${v} 取 versions.txt 里声明的初始版本 ${cur}"
    fi
  fi
  if [ -n "$cur" ]; then
    adopted="${adopted} ${v}=${cur}"
  else
    missing="${missing} ${v}"
  fi
done
if [ -n "$missing" ]; then
  echo "以下版本变量在 stack.yml 里被引用,但本次入参、机器 .env、集群现状、versions.txt 都没有值:${missing}"
  for v in $parked; do
    echo "  ${v} 在 versions.txt 里写的是 '-'(意思是「已经在本环境跑着,版本由 .env 决定」),"
    echo "  但集群里查不到这个服务。二者只能有一个是真的:"
    echo "    * 它其实是**新**服务 -> 把 '-' 换成真实的初始标签"
    echo "    * 它本该在跑却掉了   -> 先查集群,不要改这个文件"
  done
  others=""
  for v in $missing; do
    case " $parked " in
      *" $v "*) ;;
      *) others="${others} ${v}" ;;
    esac
  done
  if [ -n "$others" ]; then
    echo "其余的(${others}):把初始版本写进 versions.txt(和新增服务块放同一个 MR),或先单独部署一次。"
  fi
  exit 1
fi

# ---------- 2b. 校验通过了,现在才落盘 ----------
# 回滚是**逐行**的,不是整份文件的快照。两个理由,都实际成立:
#  * 这个模板被各个服务仓 include,deploy 任务跑在**各自**的流水线里,没有 resource_group、
#    没有锁,所以同一台 manager 上完全可能有别人的部署正在写同一个 .env。整份还原会把对方
#    刚写成功的标签一起抹掉 —— 那比原来那个 bug 更坏。
#  * 快照要落成文件,而上面第 67 行的 rsync 是 --delete 且只排除 .env 与 *.env,别人的部署
#    一跑就会把这个快照文件删掉,回滚时反而 mv 不到东西。
# 所以只在内存里记下本次要动的那几行的原值,失败时只还原它们。
ROLLBACK_SET=""   # 原来有值的,记成 变量=原值
ROLLBACK_DEL=""   # 原来根本没有这一行的,失败时删掉
for kv in $PENDING $adopted; do
  v="${kv%%=*}"; t="${kv#*=}"
  old=$(sed -n "s/^${v}=//p" .env | tail -1)
  if [ -n "$old" ]; then
    ROLLBACK_SET="${ROLLBACK_SET} ${v}=${old}"
  else
    ROLLBACK_DEL="${ROLLBACK_DEL} ${v}"
  fi
  sed -i "/^${v}=/d" .env
  echo "${v}=${t}" >> .env
  echo "记下 ${v}=${t}"
done
sort -o .env .env
echo "--- 本环境声明的版本 ---"
cat .env

# 循环变量一律用下划线开头:POSIX sh 的函数没有局部作用域。
rollback_env() {
  for _kv in $ROLLBACK_SET; do
    _v="${_kv%%=*}"
    sed -i "/^${_v}=/d" .env
    echo "$_kv" >> .env
  done
  for _v in $ROLLBACK_DEL; do
    sed -i "/^${_v}=/d" .env
  done
  sort -o .env .env
}

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
#
# 部署失败要把 .env 回滚:否则文件记着一个没生效的版本,重试会"莫名"通过,而且下一次别人部署
# 时整栈渲染会把它顺手带上去。
if ! docker stack deploy -c stack.yml --with-registry-auth --prune --detach=false "$ENV_NAME"; then
  rollback_env
  echo "部署失败,.env 里本次改的那几行已还原(没碰别人的行)"
  if [ -n "$adopted" ]; then
    echo "注意:本次曾按集群现状或 versions.txt 补齐过${adopted}。"
    echo "如果失败原因是拉不到镜像,先确认这些标签真的发布过 —— versions.txt 里的初始版本"
    echo "是人手写的,没人替你验证过它在仓库里存在。"
  fi
  exit 1
fi

# ---------- 5. 清理不再被引用的 config ----------
# --prune 不管 config 对象,内容哈希命名下它们会累积。
used=$(docker stack services "$ENV_NAME" -q \
       | xargs -r -n1 docker service inspect \
         --format '{{range .Spec.TaskTemplate.ContainerSpec.Configs}}{{.ConfigName}} {{end}}' \
       | tr ' ' '\n' | sed '/^$/d' | sort -u)
# 两种前缀都要覆盖:早期手工版本号命名带 stack 自动前缀(test_edge_static_v1),
# 现在显式 name: 命名不带前缀(edge_static_<hash>)。
for c in $(docker config ls --format '{{.Name}}' | grep -E "^(${ENV_NAME}_)?edge_(static|dynamic)_" || true); do
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
