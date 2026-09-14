#!/usr/bin/env python3
"""templates/swarm-apply.sh 的行为测试。不联网、不需要 docker、不碰任何真实环境。

跑法:
    python3 tools/test_swarm_apply.py                     # 测当前脚本
    python3 tools/test_swarm_apply.py --script <旧版路径>   # 反面探针:对旧版应当挂

做法是给它一个假的 `docker`(放在 PATH 最前面)和一个临时环境目录,然后断言两件事:
  1. 校验失败或部署失败时,.env 必须与动手之前**逐字节相同** —— 这是这次修的 bug:
     以前先写 .env 再校验,失败后文件里留着一个没生效的标签,于是"重试一次就好了",
     而那个标签会在下一次别人部署时被整栈渲染顺手带上去。
  2. stack.yml 里引用了但谁都没声明过版本的服务,可以由本环境 versions.txt 提供初始版本,
     不再让所有人的部署一起退出。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

STACK = """\
services:
  mine:
    image: reg/mine:${MINE_TAG}
  other:
    image: reg/other:${OTHER_TAG}
"""

# 假 docker:只认这个脚本用到的两个子命令。
# FAKE_RUNNING 形如 "reg/other:abc123",让 `docker stack services` 报它在跑。
# FAKE_DEPLOY_FAIL=1 让 `docker stack deploy` 非零退出。
FAKE_DOCKER = """\
#!/bin/sh
case "$1 $2" in
  "stack services") [ -n "${FAKE_RUNNING:-}" ] && echo "$FAKE_RUNNING"; exit 0 ;;
  "stack deploy")
    echo "$*" >> "$FAKE_LOG"
    [ "${FAKE_DEPLOY_FAIL:-}" = 1 ] && exit 1
    exit 0 ;;
  "config ls") exit 0 ;;
  *) exit 0 ;;
esac
"""

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail and not cond else ""))


def run(script, env_text=None, versions_text=None, running=None, deploy_fail=False,
        args=("MINE_TAG=newsha",)):
    d = tempfile.mkdtemp(prefix="swarmapply-")
    try:
        with open(os.path.join(d, "stack.yml"), "w") as fh:
            fh.write(STACK)
        for name in ("edge-static.yml", "edge-dynamic.yml"):
            with open(os.path.join(d, name), "w") as fh:
                fh.write("# placeholder\n")
        if env_text is not None:
            with open(os.path.join(d, ".env"), "w") as fh:
                fh.write(env_text)
        if versions_text is not None:
            with open(os.path.join(d, "versions.txt"), "w") as fh:
                fh.write(versions_text)
        binp = os.path.join(d, "bin")
        os.mkdir(binp)
        dockerp = os.path.join(binp, "docker")
        with open(dockerp, "w") as fh:
            fh.write(FAKE_DOCKER)
        os.chmod(dockerp, 0o755)
        target = os.path.join(d, "swarm-apply.sh")
        shutil.copy(script, target)
        os.chmod(target, 0o755)

        env = dict(os.environ)
        env["PATH"] = binp + os.pathsep + env["PATH"]
        env["FAKE_LOG"] = os.path.join(d, "deploy.log")
        if running:
            env["FAKE_RUNNING"] = running
        if deploy_fail:
            env["FAKE_DEPLOY_FAIL"] = "1"

        proc = subprocess.run(["sh", target, "test", *args], cwd=d, env=env,
                              capture_output=True, text=True, timeout=60)
        after = ""
        if os.path.exists(os.path.join(d, ".env")):
            with open(os.path.join(d, ".env")) as fh:
                after = fh.read()
        deployed = os.path.exists(env["FAKE_LOG"])
        return proc, after, deployed
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="templates/swarm-apply.sh")
    a = ap.parse_args()
    print(f"测 {a.script}")

    base = "OTHER_TAG=old\n"

    # 1) 另一个服务从没声明过版本,也没有 versions.txt -> 拒绝部署,且 .env 一个字节都不能动
    proc, after, deployed = run(a.script, env_text="MINE_TAG=old\n")
    check("缺声明时拒绝部署", proc.returncode != 0)
    check("缺声明时没有真的去部署", not deployed)
    check("缺声明退出后 .env 未被写入(本次修的 bug)", after == "MINE_TAG=old\n",
          f"实际={after!r}")

    # 2) versions.txt 给出初始版本 -> 不再连坐,照常部署
    proc, after, deployed = run(a.script, env_text="MINE_TAG=old\n",
                                versions_text="OTHER_TAG=seed1\n")
    check("versions.txt 提供初始版本后可以部署", proc.returncode == 0, proc.stdout[-300:])
    check("versions.txt 的值被采纳", "OTHER_TAG=seed1" in after, f"实际={after!r}")
    check("本次入参落盘", "MINE_TAG=newsha" in after, f"实际={after!r}")

    # 3) versions.txt 写 '-' 表示"版本由 .env 决定",不能当初始版本用
    proc, _, _ = run(a.script, env_text="MINE_TAG=old\n", versions_text="OTHER_TAG=-\n")
    check("versions.txt 里的 '-' 不充当初始版本", proc.returncode != 0)

    # 4) 集群里已经在跑的服务,按现状补齐(既有行为,不能被这次改动弄坏)
    proc, after, _ = run(a.script, env_text="MINE_TAG=old\n", running="reg/other:live9")
    check("集群现状可以补齐缺失的声明", proc.returncode == 0, proc.stdout[-300:])
    check("补齐用的是集群里的值", "OTHER_TAG=live9" in after, f"实际={after!r}")

    # 5) 部署本身失败 -> .env 回滚到部署前
    proc, after, deployed = run(a.script, env_text="MINE_TAG=old\n" + base,
                                deploy_fail=True)
    check("部署失败时非零退出", proc.returncode != 0)
    check("部署失败后 .env 回滚(本次修的 bug)",
          after == "MINE_TAG=old\n" + base, f"实际={after!r}")

    # 6) 一切正常
    proc, after, deployed = run(a.script, env_text="MINE_TAG=old\n" + base)
    check("正常路径部署成功", proc.returncode == 0, proc.stdout[-300:])
    check("正常路径记下新标签", "MINE_TAG=newsha" in after, f"实际={after!r}")
    check("正常路径不留回滚残留", ".env.rollback" not in after)

    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败项:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
