#!/usr/bin/env python3
"""清理 kaniko 层缓存仓里不再被使用的层。

## 为什么需要这个

服务模板的 kaniko 调用带 ``--cache=true --cache-repo``,每遇到一个没缓存过的层就往
``proj-rvc/rp-ai/build-cache`` 推一份。kaniko 自己有 ``--cache-ttl``(默认 336h,即两周),
过期的缓存它**不再采用**,但**不会去删** —— 所以这个仓只会长,不会缩。

## 按什么删

按**最后一次被下载的时间**,不是按创建时间。

kaniko 命中缓存的动作是"拉取",这不会改变制品的 ``created``。所以一个三个月前建立、但每天都
在命中的稳定层(比如装 apt 包那层),它的 ``created`` 很老而 ``lastDownloaded`` 是今天。按
创建时间删会把它删掉,于是天天命中的层反复被重建 —— 正好把缓存的作用抵消。

artifactory 的 AQL 能给出 ``stat.downloaded``(最后下载时间),所以这里用它;从未被下载过的
层(推上去之后一次没命中,例如某个只跑过一次的分支留下的)回落到 ``created``。

删错的后果是**一次缓存未命中**:kaniko 重建该层并重新推一份。不影响任何已发布的镜像,也不影响
正确性 —— 缓存不是事实来源,Dockerfile 才是。所以这里的取舍偏向"宁可多留几天"。

## 安全边界

只删名字是 64 位十六进制的 tag。kaniko 的缓存 tag 是内容哈希,长这个样子;凡是不长这个样子的
(有人手工推的、探针留下的)一律不碰,只报告出来让人看见。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

# kaniko 缓存 tag 是内容哈希。不符合这个形状的不是缓存,不许删。
CACHE_TAG = re.compile(r"^[0-9a-f]{64}$")

DEFAULT_BASE = "https://armdocker.rnd.ericsson.se/artifactory"
DEFAULT_REPO_KEY = "proj-rvc-docker-global"
DEFAULT_IMAGE_PATH = "proj-rvc/rp-ai/build-cache"


class Artifactory:
    def __init__(self, base: str, user: str, password: str, timeout: int = 60) -> None:
        self.base = base.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}
        self.timeout = timeout

    def _open(self, url: str, *, method: str = "GET", data: bytes | None = None,
              content_type: str | None = None):
        headers = dict(self.headers)
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        return urllib.request.urlopen(req, timeout=self.timeout)

    def aql(self, query: str) -> dict:
        with self._open(f"{self.base}/api/search/aql", method="POST",
                        data=query.encode(), content_type="text/plain") as resp:
            return json.load(resp)

    def delete(self, repo_key: str, path: str) -> int:
        try:
            with self._open(f"{self.base}/{repo_key}/{path}", method="DELETE") as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code


def parse_time(value) -> dt.datetime | None:
    """artifactory 的时间戳有两种形态:ISO 字符串(created)与毫秒整数(stat.downloaded)。"""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def collect(af: Artifactory, repo_key: str, image_path: str) -> list[dict]:
    """列出缓存仓里的每个 tag,连同它最后一次被用到的时间。"""
    query = (
        'items.find({'
        f'"repo":"{repo_key}",'
        f'"path":{{"$match":"{image_path}/*"}},'
        '"name":"manifest.json"'
        '}).include("repo","path","name","created","stat.downloaded")'
    )
    results = af.aql(query).get("results") or []
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for item in results:
        path = item.get("path") or ""
        tag = path.rsplit("/", 1)[-1]
        stats = item.get("stats") or [{}]
        downloaded = parse_time(stats[0].get("downloaded"))
        created = parse_time(item.get("created"))
        last_used = downloaded or created
        rows.append({
            "tag": tag,
            "path": path,
            "created": created,
            "downloaded": downloaded,
            "last_used": last_used,
            "age_days": (now - last_used).days if last_used else None,
            "is_cache": bool(CACHE_TAG.match(tag)),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-days", type=int, default=30,
                    help="最后一次被用到超过这么多天就删(默认 30;kaniko 自己的 cache-ttl 是 14 天,"
                         "留一倍余量)")
    ap.add_argument("--apply", action="store_true",
                    help="真的删除。不给这个参数只报告(默认)")
    ap.add_argument("--base", default=os.environ.get("ARTIFACTORY_BASE", DEFAULT_BASE))
    ap.add_argument("--repo-key", default=os.environ.get("ARTIFACTORY_REPO_KEY", DEFAULT_REPO_KEY))
    ap.add_argument("--image-path", default=os.environ.get("CACHE_IMAGE_PATH", DEFAULT_IMAGE_PATH))
    args = ap.parse_args()

    user = os.environ.get("docker_user") or os.environ.get("ARTIFACTORY_USER")
    password = os.environ.get("docker_password") or os.environ.get("ARTIFACTORY_PASSWORD")
    if not user or not password:
        print("需要 docker_user / docker_password(或 ARTIFACTORY_USER / ARTIFACTORY_PASSWORD)",
              file=sys.stderr)
        return 2

    af = Artifactory(args.base, user, password)
    rows = collect(af, args.repo_key, args.image_path)
    if not rows:
        print(f"{args.image_path} 里没有制品,无事可做")
        return 0

    foreign = [r for r in rows if not r["is_cache"]]
    cache = [r for r in rows if r["is_cache"]]
    stale = [r for r in cache if r["age_days"] is not None and r["age_days"] > args.keep_days]
    unknown = [r for r in cache if r["age_days"] is None]

    print(f"缓存仓 {args.image_path}")
    print(f"  制品总数        {len(rows)}")
    print(f"  其中缓存层      {len(cache)}")
    print(f"  非缓存(不处理) {len(foreign)}" + (f" -> {[r['tag'] for r in foreign]}" if foreign else ""))
    print(f"  时间取不到      {len(unknown)}(保留)")
    print(f"  超过 {args.keep_days} 天没被用到 {len(stale)}")

    if cache:
        newest = max((r["age_days"] for r in cache if r["age_days"] is not None), default=None)
        print(f"  最久未使用      {newest} 天" if newest is not None else "")

    if not stale:
        print("没有需要清理的层")
        return 0

    for row in sorted(stale, key=lambda r: -r["age_days"]):
        used = "从未命中" if not row["downloaded"] else row["downloaded"].date().isoformat()
        print(f"  {'删除' if args.apply else '将删除'} {row['tag'][:16]}… "
              f"建于 {row['created'].date() if row['created'] else '?'} 最后命中 {used} "
              f"({row['age_days']} 天前)")

    if not args.apply:
        print(f"\n以上为报告。加 --apply 才真的删除。")
        return 0

    failed = 0
    for row in stale:
        code = af.delete(args.repo_key, row["path"])
        if code not in (200, 204):
            print(f"  删除失败 {row['tag'][:16]}… HTTP {code}", file=sys.stderr)
            failed += 1
    print(f"\n已删除 {len(stale) - failed} 个,失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
