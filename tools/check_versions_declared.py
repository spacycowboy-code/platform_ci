#!/usr/bin/env python3
"""检查每个环境的 stack.yml 与 versions.txt 对得上。不联网。

为什么要有这道关卡:swarm 的 `stack deploy` 整栈重渲染,stack.yml 里任何一个 ${X_TAG}
渲染不出来,**所有人**的部署都会在动手之前退出。以前这件事只在运行时暴露 —— 谁把新服务块
写进 stack.yml,别人的部署就开始挂,而挂的原因看起来跟他自己毫无关系(fi-analysis 卡过一次)。

有了这道检查,漏声明会在**你自己的 MR** 上变红,附带一句写清该往哪儿加。
运行时的兜底仍然在 templates/swarm-apply.sh 里(优先级最低),两者互补:
这里防漏写,那里保证即使漏了也只影响新服务自己而不是所有人。
"""
import pathlib
import re
import sys

TAG_REF = re.compile(r"\$\{([A-Z_]+_TAG)")
DECL = re.compile(r"^([A-Z_]+_TAG)=(.*)$")


def declared(path):
    if not path.exists():
        return None
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = DECL.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def main():
    bad = 0
    stacks = sorted(pathlib.Path("deploy").glob("*/stack.yml"))
    if not stacks:
        print("deploy/*/stack.yml 一个都没找到,检查跳过")
        return 0
    for stack in stacks:
        env = stack.parent.name
        refs = set(TAG_REF.findall(stack.read_text(encoding="utf-8")))
        vfile = stack.parent / "versions.txt"
        decl = declared(vfile)
        if decl is None:
            print(f"{env}: 缺 {vfile} —— stack.yml 引用了 {len(refs)} 个版本变量,"
                  f"每个都要在这个文件里有一行")
            bad = 1
            continue
        missing = sorted(refs - set(decl))
        extra = sorted(set(decl) - refs)
        if missing:
            print(f"{env}: {vfile} 里缺这些行(stack.yml 引用了它们): {' '.join(missing)}")
            print(f"{env}: 新服务写真实的初始标签;已经在跑的服务写 '-'")
            bad = 1
        if extra:
            print(f"{env}: {vfile} 里这些行在 stack.yml 里已经没人引用,应当删掉: "
                  f"{' '.join(extra)}")
            bad = 1
        if not missing and not extra:
            seeded = sorted(k for k, v in decl.items() if v != "-")
            print(f"{env}: {len(refs)} 个版本变量全部声明"
                  + (f",其中带初始标签的: {' '.join(seeded)}" if seeded else ""))
    return bad


if __name__ == "__main__":
    sys.exit(main())
