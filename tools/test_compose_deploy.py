#!/usr/bin/env python3
"""生产(compose 形态)那段部署脚本的行为测试。不联网、不需要 docker、不碰任何真实主机。

为什么值得单独写一个:那段逻辑内联在 deploy.yml 里一个双引号包着的 ssh 参数里,每个 $ 和引号
都要多层转义 —— 文件自己的注释写着「已经因此坏过两次」。这个测试把 deploy.yml 里那一段
**原文取出来**,用和 CI 一样的变量值让本地 shell 去渲染,再把渲染结果当远端脚本执行(ssh 与
docker 都是桩)。也就是说它验证的是真正会被送到生产机器上的那份文本,不是一份抄本。

跑法: python3 tools/test_compose_deploy.py
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

# 新旧两版都有这句注释,所以反面探针也定位得到。
MARKER = "以下是 compose(单机)形态"

PREAMBLE = r"""
set -eu
# ssh 桩:真实调用形如  ssh $SSHBASE "user@host" "<脚本>",取最后一个参数当远端脚本执行。
# 这样被执行的就是本地 shell 渲染后的那份文本 —— 转义错了这里就会暴露。
ssh() {
  _s=""
  for _a in "$@"; do _s="$_a"; done
  printf '%s\n' "$_s" > "$RENDERED"
  sh -e -c "$_s"
}
# scp 桩:真的复制过去(最后两个参数是 源 与 目标),这样 ssh 桩接着执行的就是
# 真正被上传的那份 release.sh —— 整条"渲染 -> 上传 -> 执行"都是真的。
scp() {
  while [ "$#" -gt 2 ]; do shift; done
  _src="$1"; _dst="$2"
  cp "$_src" "${_dst#*:}"
}
"""

FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "compose pull") [ "${FAKE_PULL_FAIL:-}" = 1 ] && exit 1 ;;
  "compose up")   [ "${FAKE_UP_FAIL:-}" = 1 ] && exit 1 ;;
  "compose ps")   exit 0 ;;
esac
exit 0
"""

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail and not cond else ""))


def fragment(path="templates/deploy.yml"):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    _head, sep, body = text.partition("\n---\n")
    yaml.SafeLoader.add_multi_constructor("!reference", lambda l, s, n: None)
    doc = yaml.safe_load(body if sep else text)
    for job in doc.values():
        if not isinstance(job, dict):
            continue
        for item in job.get("script") or []:
            if isinstance(item, str) and MARKER in item:
                return item
    raise SystemExit(f"在 {path} 里找不到含 {MARKER!r} 的 script 片段 —— "
                     "要么被改动了,要么这个测试的定位方式过时了")


def run(frag, env_text, extra=False, pull_fail=False, up_fail=False, tag="newsha"):
    d = tempfile.mkdtemp(prefix="composedeploy-")
    try:
        (pathlib.Path(d) / ".env").write_text(env_text, encoding="utf-8")
        envdir = pathlib.Path(d) / "envdir"
        envdir.mkdir()
        (envdir / "services.txt").write_text(
            "main-svc: compose svc-a\nextra-svc: compose svc-b\n", encoding="utf-8")
        # deploy.yml 用相对路径 scp templates/release.sh,所以按 job 的工作目录摆好
        tdir = pathlib.Path(d) / "templates"
        tdir.mkdir()
        shutil.copy("templates/release.sh", tdir / "release.sh")
        binp = pathlib.Path(d) / "bin"
        binp.mkdir()
        (binp / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
        os.chmod(binp / "docker", 0o755)

        vars_ = {
            "MODE": "compose",
            "HOST_DIR": d,
            "ENV_DIR": str(envdir),
            "TARGETS": "svc-a",
            "VAR": "MAIN_SVC_TAG",
            "EXTRA_VAR": "EXTRA_SVC_TAG" if extra else "",
            "DEPLOY_TAG": tag,
            "DEPLOY_SERVICE": "main-svc",
            "DEPLOY_EXTRA_SERVICE": "extra-svc" if extra else "",
            "DEPLOY_ENV": "prod",
            "SSHBASE": "-i /dev/null",
            "TARGET_USER": "u",
            "TARGET_HOST": "h",
        }
        script = PREAMBLE + "\n" + "\n".join(f"{k}='{v}'" for k, v in vars_.items()) + "\n" + frag
        sp = pathlib.Path(d) / "job.sh"
        sp.write_text(script, encoding="utf-8")

        env = dict(os.environ)
        env["PATH"] = str(binp) + os.pathsep + env["PATH"]
        env["FAKE_LOG"] = str(pathlib.Path(d) / "docker.log")
        env["RENDERED"] = str(pathlib.Path(d) / "rendered.sh")
        if pull_fail:
            env["FAKE_PULL_FAIL"] = "1"
        if up_fail:
            env["FAKE_UP_FAIL"] = "1"

        proc = subprocess.run(["sh", str(sp)], cwd=d, env=env, capture_output=True,
                              text=True, timeout=60)
        after = (pathlib.Path(d) / ".env").read_text(encoding="utf-8")
        log = ""
        if os.path.exists(env["FAKE_LOG"]):
            log = pathlib.Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
        return proc, after, log
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_release(env_text, pull_fail=False, up_fail=False,
                script="templates/release.sh"):
    """release.sh apply-compose 是第三个会写 .env 的地方(手工重放用),同样要覆盖。"""
    d = tempfile.mkdtemp(prefix="release-")
    try:
        shutil.copy(script, os.path.join(d, "release.sh"))
        (pathlib.Path(d) / ".env").write_text(env_text, encoding="utf-8")
        binp = pathlib.Path(d) / "bin"
        binp.mkdir()
        (binp / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
        os.chmod(binp / "docker", 0o755)
        env = dict(os.environ)
        env["PATH"] = str(binp) + os.pathsep + env["PATH"]
        env["FAKE_LOG"] = str(pathlib.Path(d) / "docker.log")
        if pull_fail:
            env["FAKE_PULL_FAIL"] = "1"
        if up_fail:
            env["FAKE_UP_FAIL"] = "1"
        proc = subprocess.run(
            ["sh", os.path.join(d, "release.sh"), "apply-compose", "prod", "svc-a",
             "--", "MAIN_SVC_TAG=newsha"],
            cwd=d, env=env, capture_output=True, text=True, timeout=60)
        after = (pathlib.Path(d) / ".env").read_text(encoding="utf-8")
        log = ""
        if os.path.exists(env["FAKE_LOG"]):
            log = pathlib.Path(env["FAKE_LOG"]).read_text(encoding="utf-8")
        return proc, after, log
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="templates/deploy.yml",
                    help="要测的 deploy.yml(反面探针时指向改动前的版本)")
    a = ap.parse_args()
    frag = fragment(a.file)
    print(f"从 {a.file} 取出 {len(frag.splitlines())} 行的部署片段")
    # 别人的服务在同一个 .env 里的一行:任何回滚都不许碰它。
    other = "OTHER_SVC_TAG=untouched\n"

    # 1) 正常路径
    proc, after, log = run(frag, "MAIN_SVC_TAG=old\n" + other)
    check("正常路径成功", proc.returncode == 0, proc.stdout[-400:] + proc.stderr[-400:])
    check("正常路径写入新标签", "MAIN_SVC_TAG=newsha" in after, f"实际={after!r}")
    check("重建带 --force-recreate", "--force-recreate" in log, f"实际={log!r}")

    # 2) 拉取失败 -> 只还原自己那一行
    proc, after, log = run(frag, "MAIN_SVC_TAG=old\n" + other, pull_fail=True)
    check("拉取失败时非零退出", proc.returncode != 0)
    check("拉取失败后自己那行还原成原值", "MAIN_SVC_TAG=old" in after, f"实际={after!r}")
    check("拉取失败后没留下新标签", "newsha" not in after, f"实际={after!r}")
    check("拉取失败后别人那行没被碰", other.strip() in after, f"实际={after!r}")
    check("拉取失败后没有真的去重建", "--force-recreate" not in log)

    # 3) 原来根本没有这一行 -> 回滚要把它删掉,而不是留着没验证过的标签
    proc, after, _ = run(frag, other, pull_fail=True)
    check("原本没有该行时回滚后仍然没有", "MAIN_SVC_TAG" not in after, f"实际={after!r}")
    check("原本没有该行时别人那行没被碰", other.strip() in after, f"实际={after!r}")

    # 4) 重建失败 -> 同样只还原自己那一行
    proc, after, _ = run(frag, "MAIN_SVC_TAG=old\n" + other, up_fail=True)
    check("重建失败时非零退出", proc.returncode != 0)
    check("重建失败后自己那行还原成原值", "MAIN_SVC_TAG=old" in after, f"实际={after!r}")
    check("重建失败后别人那行没被碰", other.strip() in after, f"实际={after!r}")

    # 5) 一个仓构建两个镜像:两行一起写、一起还原
    proc, after, _ = run(frag, "MAIN_SVC_TAG=old\nEXTRA_SVC_TAG=oldx\n" + other, extra=True)
    check("双镜像正常路径两行都写", "MAIN_SVC_TAG=newsha" in after and "EXTRA_SVC_TAG=newsha" in after,
          f"实际={after!r}")
    proc, after, _ = run(frag, "MAIN_SVC_TAG=old\nEXTRA_SVC_TAG=oldx\n" + other,
                         extra=True, up_fail=True)
    check("双镜像失败时两行都还原",
          "MAIN_SVC_TAG=old" in after and "EXTRA_SVC_TAG=oldx" in after, f"实际={after!r}")
    check("双镜像失败时别人那行没被碰", other.strip() in after, f"实际={after!r}")

    # 5b) 守护 PLATFORM.md 那条原则:compose 分支不许再把逻辑内联进 ssh 的字符串
    inline = [w for w in ("sed -i", "docker compose pull", "docker compose up") if w in frag]
    check("compose 分支不再内联 .env / docker 逻辑(只上传脚本再执行)",
          not inline, f"仍然内联着: {inline}")
    check("compose 分支确实是调用 release.sh apply-compose",
          "release.sh' apply-compose" in frag or "release.sh apply-compose" in frag)

    # 6) release.sh apply-compose(手工重放)——同一套要求
    print("  -- release.sh apply-compose --")
    proc, after, log = run_release("MAIN_SVC_TAG=old\n" + other)
    check("重放正常路径成功", proc.returncode == 0, proc.stdout[-300:] + proc.stderr[-300:])
    check("重放写入新标签", "MAIN_SVC_TAG=newsha" in after, f"实际={after!r}")
    check("重放也带 --force-recreate(否则改了配置没换标签时会静默不生效)",
          "--force-recreate" in log, f"实际={log!r}")
    proc, after, _ = run_release("MAIN_SVC_TAG=old\n" + other, up_fail=True)
    check("重放失败时非零退出", proc.returncode != 0)
    check("重放失败后自己那行还原", "MAIN_SVC_TAG=old" in after, f"实际={after!r}")
    check("重放失败后别人那行没被碰", other.strip() in after, f"实际={after!r}")

    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败项:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
