#!/bin/sh
# 备份门户自己的事实表,并且当场验证这份备份能恢复。
#
# 在目标机器上执行(由 platform-ci 的定时任务 scp 过来后运行)。
#
# 备份的对象是"只此一份的事实":审计记录与发现(判定器不是确定性的,重跑得不到同样结论)、用户、
# 下载量、收藏、评论、评分。**不备份能重算的东西** —— 技能内容在 git 里,`status` 靠对账器重算,
# 别的团队在同一个库里的 29 张表(405 MB)不是我们该持有的。
#
# 表名不写死,按 rp_ai_ 前缀在运行时取。写死的清单会在有人加表时**静默漏掉它**,而这种漏不会报错。
# 代价是:新表必须沿用 rp_ai_ 前缀,否则不会被备份 —— 这一条写在 platform-ci 的 README 里。
#
# 验收标准是"恢复成功",不是"文件存在"。所以每次导完都灌进一个一次性的 MySQL 容器、逐表比对行数,
# 不一致就失败并删掉这份坏备份。没验证过的备份是最常见的失败方式:出事那天才发现 dump 是空的。
set -eu

CONTAINER="${CONTAINER:-ai-portal-be-backend}"
DEST="${DEST:-/proj/rp_ait_prod/backup}"
KEEP_DAYS="${KEEP_DAYS:-7}"
PREFIX="${PREFIX:-rp_ai}"
MYSQL_IMAGE="${MYSQL_IMAGE:-mysql:8.0}"
STAMP="$(date -u +%Y%m%d-%H%M%SZ)"
WORK="$(mktemp -d)"
VERIFY_NAME="portal-facts-verify-$$"

cleanup() {
  docker rm -f "$VERIFY_NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

# 凭据不复制一份:直接从运行中的容器读。它本来就在那里,多一份就多一处要轮换、要保护的地方。
URL="$(docker exec "$CONTAINER" printenv DATABASE_URL)"
[ -n "$URL" ] || { echo "拿不到 DATABASE_URL(容器 $CONTAINER 在跑吗?)" >&2; exit 1; }

# mysql+pymysql://user:pass@host:port/db  -> 拆成各段。口令里可能有 URL 编码,交给 python 解。
# docker exec 不加 -i 不会把 stdin 传进去,那段 python 就什么都不做而 eval 出空 —— 踩过一次。
eval "$(docker exec -i "$CONTAINER" python - <<'PY'
import os, shlex
from urllib.parse import unquote, urlparse
u = urlparse(os.environ["DATABASE_URL"])
for k, v in (("DB_HOST", u.hostname), ("DB_PORT", u.port or 3306),
             ("DB_USER", unquote(u.username or "")), ("DB_PASS", unquote(u.password or "")),
             ("DB_NAME", u.path.lstrip("/"))):
    print(f"{k}={shlex.quote(str(v))}")
PY
)"

mysql_client() {
  docker run --rm -i "$MYSQL_IMAGE" mysql \
    --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" --password="$DB_PASS" \
    --skip-column-names --batch "$DB_NAME" 2>/dev/null
}

echo "库 $DB_NAME @ $DB_HOST:$DB_PORT"

TABLES="$(printf "select table_name from information_schema.tables
             where table_schema = '%s' and table_name like '%s\\\\_%%' order by table_name;" \
             "$DB_NAME" "$PREFIX" | mysql_client | tr '\n' ' ')"
[ -n "$TABLES" ] || { echo "按前缀 ${PREFIX}_ 一张表都没找到,不写空备份" >&2; exit 1; }
echo "要备份的表($(echo "$TABLES" | wc -w) 张):"
echo "  $TABLES"

# 先记下源库各表行数。dump 用 --single-transaction,取的是一致快照,所以恢复后应当逐表相等。
for t in $TABLES; do
  n="$(printf "select count(*) from \`%s\`;" "$t" | mysql_client)"
  echo "$t $n" >> "$WORK/counts.src"
done

echo "导出中…"
docker run --rm "$MYSQL_IMAGE" mysqldump \
  --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" --password="$DB_PASS" \
  --single-transaction --skip-lock-tables --no-tablespaces --set-gtid-purged=OFF \
  --routines --triggers "$DB_NAME" $TABLES > "$WORK/dump.sql" 2>"$WORK/dump.err" || {
    echo "导出失败:"; grep -v "Using a password" "$WORK/dump.err" >&2; exit 1; }
BYTES="$(wc -c < "$WORK/dump.sql")"
echo "导出 $BYTES 字节"
[ "$BYTES" -gt 1000 ] || { echo "导出内容太小,不像是有数据" >&2; exit 1; }

echo "恢复验证:灌进一次性的 MySQL 容器…"
docker run -d --name "$VERIFY_NAME" -e MYSQL_ALLOW_EMPTY_PASSWORD=yes \
  "$MYSQL_IMAGE" >/dev/null
# 就绪判断必须走 TCP。MySQL 初始化时先起一个**只监听 socket** 的临时服务器,用 mysqladmin ping 或
# 走 socket 的 select 1 都会在那时就通;随后临时服务器被关掉、真服务器才起来,于是下一条语句报
# "Can't connect through socket"。真服务器才监听 3306,所以用 TCP 判就绪 —— 两种错法都踩过。
i=0
while [ "$i" -lt 90 ]; do
  docker exec "$VERIFY_NAME" mysql --protocol=TCP -h 127.0.0.1 -e "select 1" >/dev/null 2>&1 && break
  i=$((i + 1)); sleep 2
done
[ "$i" -lt 90 ] || { echo "验证用的 MySQL 起不来" >&2; exit 1; }
docker exec "$VERIFY_NAME" mysql --protocol=TCP -h 127.0.0.1 -e "create database verify" >/dev/null

docker exec -i "$VERIFY_NAME" mysql --protocol=TCP -h 127.0.0.1 --batch verify < "$WORK/dump.sql"

BAD=0
while read -r t n; do
  m="$(printf "select count(*) from \`%s\`;" "$t" \
        | docker exec -i "$VERIFY_NAME" mysql --protocol=TCP -h 127.0.0.1 \
             --skip-column-names --batch verify 2>/dev/null)"
  if [ "${m:-x}" != "$n" ]; then
    echo "  行数不一致 $t: 源 $n,恢复后 ${m:-读不到}" >&2; BAD=1
  fi
done < "$WORK/counts.src"
[ "$BAD" -eq 0 ] || { echo "恢复验证不通过,这份备份不留" >&2; exit 1; }
echo "恢复验证通过:$(wc -l < "$WORK/counts.src") 张表逐表行数一致"

mkdir -p "$DEST"; chmod 700 "$DEST"
OUT="$DEST/portal-facts-$STAMP.sql.gz"
gzip -c "$WORK/dump.sql" > "$OUT"
chmod 600 "$OUT"
echo "已写入 $OUT($(wc -c < "$OUT") 字节)"

# 保留期短是有意的:里面有用户 EID 与姓名,多留一天就是多一天的个人数据副本。
find "$DEST" -name 'portal-facts-*.sql.gz' -type f -mtime "+$KEEP_DAYS" -print -delete \
  | sed 's/^/已按保留期删除 /'
echo "现存备份:"
ls -lh "$DEST" | tail -n +2 | awk '{print "  " $5, $6, $7, $8, $9}'
