#!/usr/bin/env python3
"""check_stack_config.py 的行为测试。不联网、不需要 docker、不碰任何真实主机。

为什么值得写:这个检查本身是一道闸门,而闸门最糟的失效方式是**它看起来在工作但什么都拦不住**
—— 那比没有闸门更糟,因为后面写 stack.yml 的人会以为有人替他把关。所以这里对每一类
问题各造一份坏输入,断言它恰好被点出来;同时造一份干净的 stack.yml,断言它不被误报。

跑法: python3 tools/test_check_stack_config.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_stack_config as csc  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail and not cond else ""))


def lint_text(text: str) -> list[str]:
    """把一段 YAML 当成 deploy/<环境>/stack.yml 喂给 lint,返回它报的问题。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "deploy" / "fake"
        d.mkdir(parents=True)
        p = d / "stack.yml"
        p.write_text(text, encoding="utf-8")
        # lint 用 REPO 算相对路径,临时目录不在仓库里,所以临时把它指过去。
        old = csc.REPO
        csc.REPO = pathlib.Path(tmp)
        try:
            return csc.lint(p)
        finally:
            csc.REPO = old


CLEAN = """\
services:
  frontend:
    image: example/fe:1
    networks: [inner]
    environment:
      AUDIT_UPSTREAM: "http://audit-backend:8000"
    deploy:
      replicas: 2
      restart_policy:
        condition: any
      resources:
        limits:
          memory: 512M
      placement:
        constraints: [node.labels.site == seli]
networks:
  inner:
    driver: overlay
"""


def main():
    print("干净的 stack.yml 不该被误报")
    problems = lint_text(CLEAN)
    check("零误报", problems == [], f"却报了 {problems}")
    # deploy 段里的 restart_policy / resources.limits.memory 与被禁的 restart / mem_limit
    # 同名近似,最容易被写成子串匹配而误伤 —— 单独钉住。
    check(
        "deploy.restart_policy 不被当成 restart",
        not any("restart" in p for p in problems),
        f"却报了 {problems}",
    )
    check(
        "deploy.resources 不被当成 mem_limit",
        not any("mem_limit" in p for p in problems),
        f"却报了 {problems}",
    )

    print("docker 会直接报错的那一类(forbidden)")
    for key in ("mem_limit", "memswap_limit", "cpu_shares", "cpuset", "volumes_from", "volume_driver"):
        problems = lint_text(f"services:\n  a:\n    image: x\n    {key}: 1g\n")
        check(f"抓到 {key}", any(f"`{key}`" in p for p in problems), f"报的是 {problems}")

    print("swarm 下被静默忽略的那一类(最危险,docker 自己不报)")
    for key in ("container_name", "restart", "depends_on", "privileged", "network_mode", "expose"):
        problems = lint_text(f"services:\n  a:\n    image: x\n    {key}: v\n")
        check(f"抓到 {key}", any(f"`{key}`" in p for p in problems), f"报的是 {problems}")

    print("值层面的坑")
    problems = lint_text(
        'services:\n  a:\n    image: x\n    extra_hosts:\n      - "host.docker.internal:host-gateway"\n'
    )
    check("抓到 extra_hosts 的 host-gateway", any("host-gateway" in p for p in problems), f"报的是 {problems}")

    problems = lint_text("services:\n  a:\n    image: x\n    extra_hosts:\n      - \"db:10.0.0.9\"\n")
    check("普通 extra_hosts 不误报", problems == [], f"却报了 {problems}")

    for vol in ("./edge/nginx.conf:/etc/nginx/nginx.conf:ro", "../shared/x:/x"):
        problems = lint_text(f'services:\n  a:\n    image: x\n    volumes:\n      - "{vol}"\n')
        check(f"抓到相对路径挂载 {vol.split(':')[0]}", any("相对路径" in p for p in problems), f"报的是 {problems}")

    problems = lint_text('services:\n  a:\n    image: x\n    volumes:\n      - "/proj/x:/x:ro"\n')
    check("绝对路径挂载不误报", problems == [], f"却报了 {problems}")

    problems = lint_text(
        "services:\n  a:\n    image: x\n    volumes:\n      - type: bind\n        source: ./rel\n        target: /x\n"
    )
    check("长语法的相对路径也抓到", any("相对路径" in p for p in problems), f"报的是 {problems}")

    print("行号要能定位到出问题那一行")
    text = "services:\n  a:\n    image: x\n  b:\n    image: y\n    container_name: nope\n"
    problems = lint_text(text)
    check("行号指向第 6 行", any(":6 " in p for p in problems), f"报的是 {problems}")

    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败项:", ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
