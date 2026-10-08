#!/usr/bin/env python3
"""校验 deploy/<环境>/stack.yml 在 swarm 下是真的能部署。

为什么要有这个检查:swarm 的 `docker stack deploy` 是**整栈重渲染** —— 任何一个服务块
写坏,整个栈谁都部不了,而不是只有那一个服务失败(单机 compose 的 `up -d <服务>` 只影响
它自己)。所以这类错误必须在提出改动的那个 MR 上变红,不能等到别人点部署时才炸。

两道闸门,缺一不可:

  1. 自己的 lint(本文件下半部分)—— 点名是哪个服务、哪一行、哪个属性。
     这是主力:`container_name` / `restart` / `depends_on` 这一类在 swarm 下是**静默忽略**,
     docker 自己一个字都不说,而后果是"容器名全变、重启策略没生效、启动顺序假设不成立"。

  2. `docker stack config` —— 权威的 schema 校验(类型写错、deploy 段结构不对、
     placement 语法错这些 lint 盖不住的)。它不需要 docker daemon,纯客户端渲染(实测过)。
     代价是它只回一句 "Configuration contains forbidden properties",不说是哪一个 ——
     所以它排第二,先让 lint 把话说清楚。
     而且它的 forbidden 名单只有资源限制那几项(mem_limit 等),这正是为什么第 1 道必须存在。

只检查 stack.yml,**不碰 compose.yml**:生产现在仍是单机 compose,那份文件里 17 个
container_name、17 个 restart 都是对的,拿 swarm 的尺子去量它只会得到一堆假警报。
某个服务迁到集群时,它的服务块会被翻译进 stack.yml,那时才落入本检查的范围。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent

# docker 会直接报错的属性(它的 ForbiddenProperties)。列在这里不是为了替它报,
# 而是为了在它那句没有信息量的错误之前先指名道姓。
FORBIDDEN = {
    "mem_limit": "改用 deploy.resources.limits.memory",
    "memswap_limit": "改用 deploy.resources.limits.memory",
    "cpu_shares": "改用 deploy.resources",
    "cpu_quota": "改用 deploy.resources",
    "cpuset": "改用 deploy.placement 或 deploy.resources",
    "volumes_from": "在顶层 volumes 定义卷,再在每个服务的 volumes 里引用",
    "volume_driver": "在顶层 volumes 里给出 driver",
}

# swarm 下被**静默忽略**的属性。这一类最危险:没有任何报错,行为却变了。
IGNORED = {
    "container_name": "会被忽略,容器名变成 <栈>_<服务>.<序号>.<随机> —— 所有按容器名写的运维命令和监控要一起改",
    "restart": "会被忽略,改用 deploy.restart_policy",
    "depends_on": "会被忽略。若配置里按名字写死了上游(nginx 的 proxy_pass 就是),要靠部署顺序或 resolver 解决",
    "privileged": "会被忽略",
    "network_mode": "会被忽略,用 networks",
    "links": "会被忽略,用服务名直接访问",
    "external_links": "会被忽略",
    "devices": "会被忽略",
    "dns": "会被忽略",
    "dns_search": "会被忽略",
    "domainname": "会被忽略",
    "ipc": "会被忽略",
    "pid": "会被忽略",
    "read_only": "会被忽略",
    "security_opt": "会被忽略",
    "shm_size": "会被忽略",
    "stdin_open": "会被忽略",
    "tty": "会被忽略",
    "ulimits": "会被忽略",
    "userns_mode": "会被忽略",
    "cgroup_parent": "会被忽略",
    "mac_address": "会被忽略",
    "expose": "会被忽略(swarm 里同网络的服务本来就互相可达)",
}


def line_of(path: pathlib.Path, service: str, key: str) -> int:
    """粗略定位 `key` 在 `service` 块里的行号,只为让报错能点到行。

    故意不做完整的 YAML 定位:只需要在服务名出现之后找第一个该属性,
    找不到就返回 0(报错里省略行号),不值得为此引入一个带位置信息的解析器。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, s in enumerate(lines) if s.strip().startswith(f"{service}:")), None)
    if start is None:
        return 0
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if i > start and stripped and not lines[i].startswith((" ", "\t")):
            break  # 出了 services 块
        if stripped.startswith(f"{key}:") or stripped.startswith(f"{key} :"):
            return i + 1
    return 0


def lint(path: pathlib.Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    problems: list[str] = []
    rel = path.relative_to(REPO)

    for name, spec in (doc.get("services") or {}).items():
        if not isinstance(spec, dict):
            continue

        for key, hint in FORBIDDEN.items():
            if key in spec:
                ln = line_of(path, name, key)
                problems.append(f"{rel}:{ln} 服务 {name}:`{key}` 会让 stack deploy 直接失败 —— {hint}")

        for key, hint in IGNORED.items():
            if key in spec:
                ln = line_of(path, name, key)
                problems.append(f"{rel}:{ln} 服务 {name}:`{key}` {hint}")

        # extra_hosts 本身 swarm 支持,但 host-gateway 这个值不行:它是 compose 的特性,
        # 名字解析不出来。而且即使解析出来,它指的是"副本所在那台机器",多副本时
        # 症状是一部分请求通、一部分 502 —— 离原因很远,必须在这里拦。
        for entry in spec.get("extra_hosts") or []:
            if "host-gateway" in str(entry):
                ln = line_of(path, name, "extra_hosts")
                problems.append(
                    f"{rel}:{ln} 服务 {name}:extra_hosts 里的 `{entry}` 在 swarm 下解析不出来,"
                    f"改用服务名访问目标服务"
                )

        # 相对路径的 bind mount:副本可能落在任何节点,而相对路径是相对于 manager 上
        # 那个目录的。要么换 docker config/secret,要么用绝对路径加放置约束。
        for vol in spec.get("volumes") or []:
            src = vol.get("source") if isinstance(vol, dict) else str(vol).split(":", 1)[0]
            if isinstance(src, str) and src.startswith((".", "..")):
                ln = line_of(path, name, "volumes")
                problems.append(
                    f"{rel}:{ln} 服务 {name}:bind mount 的相对路径 `{src}` 在 swarm 下不可用,"
                    f"改用 docker config / secret,或绝对路径加放置约束"
                )

    return problems


def stack_config(path: pathlib.Path) -> tuple[bool, str]:
    """用 `docker stack config` 做权威校验。

    env_file 指向的那些 .env 不在仓库里(部署时才从 CI 变量投放),所以先把整个环境目录
    复制到临时目录、把缺的 .env 建成空文件再校验 —— 这里要验的是结构,不是取值。
    同理把 ${...} 变量喂一个占位值,否则镜像会渲染成 `foo:` 这种非法形态。
    """
    if not shutil.which("docker"):
        return True, "跳过(本机没有 docker 命令;CI 里有)"

    raw = path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / path.parent.name
        shutil.copytree(path.parent, work)

        doc = yaml.safe_load(raw) or {}
        for spec in (doc.get("services") or {}).values():
            if not isinstance(spec, dict):
                continue
            ef = spec.get("env_file") or []
            for name in [ef] if isinstance(ef, str) else ef:
                (work / name).touch(exist_ok=True)

        env = dict(os.environ)
        # 占位值统一给,避免 image 渲染成非法形态;取值在这里无关紧要。
        for token in raw.split("${")[1:]:
            var = token.split("}")[0].split(":")[0]
            env.setdefault(var, "ci-check")

        proc = subprocess.run(
            ["docker", "stack", "config", "-c", path.name],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout).strip()
        return True, "通过"


def main() -> int:
    stacks = sorted(REPO.glob("deploy/*/stack.yml"))
    if not stacks:
        print("deploy/*/stack.yml 一个都没找到 —— 检查本身大概写错了")
        return 1

    bad = 0
    for path in stacks:
        rel = path.relative_to(REPO)
        problems = lint(path)
        ok, detail = stack_config(path)

        print(f"{rel}:")
        for p in problems:
            print(f"  ✗ {p}")
        if not problems:
            print("  ✓ 无 swarm 不兼容的写法")
        print(f"  docker stack config: {detail if ok else '失败 —— ' + detail}")
        if problems or not ok:
            bad = 1
        print()

    if bad:
        print("有 stack.yml 在 swarm 下不能按写的那样工作。上面每一条都点明了服务与属性。")
        print("注意 compose.yml 不在本检查范围内:生产仍是单机 compose,那边的写法是对的。")
    return bad


if __name__ == "__main__":
    sys.exit(main())
