# 构建器:现状与需要的决定

## 事实

| | |
|---|---|
| 现在用什么 | kaniko `v1.23.2`,锁 digest `sha256:c3109d5926a9…` |
| 状态 | 上游已归档,不再发版 |
| 为什么不用 `docker build` | 本组 runner 不允许特权容器,dind 报 `mount: permission denied` |
| 推荐替代 | BuildKit rootless(维护中) |
| 它为什么起不来 | 需要非特权用户命名空间,而这台机器上被显式关闭 |

可自行验证:

```
$ sysctl -n kernel.unprivileged_userns_clone
0                                    # Ubuntu 22.04 默认是 1
$ grep -r userns /etc/sysctl.d/
/etc/sysctl.d/90-disable-unpriv-userns.conf:kernel.unprivileged_userns_clone = 0
```

rootless 构建器的报错:

```
[rootlesskit:parent] error: failed to start the child: fork/exec /proc/self/exe: operation not permitted
```

## 其它路径的结果

| 路径 | 结果 |
|---|---|
| Chainguard 维护的 kaniko fork | `cgr.dev/chainguard/kaniko:latest` 返回 404,公开目录里没有 |
| 请 Artifactory 加 `cgr.dev` 代理 | 无用,代理不到不存在的镜像 |
| buildx 通过 SSH 用部署机的引擎 | 需把部署机私钥开放给任意分支的流水线。构建作业只应持有镜像仓库推送凭据,不持有主机凭据 |

## 维持现状的风险边界

- 不再发版,所以没有"漏了安全更新";digest 锁定,内容不会在背后变化
- 运行范围:CI 里寿命几分钟的临时容器,无对外端口,持有的凭据只有镜像仓库推送账号(能推不能删)
- 换构建器只改 `templates/service.yml` 一处,服务仓不动(它们只写 `ref: v1`)

## 需要选一个

| 选项 | 谁做 | 代价 |
|---|---|---|
| A 维持现状 | — | 继续用已归档但锁定的工具 |
| B 放开 `kernel.unprivileged_userns_clone=1` | runner 宿主机维护者;若属组织加固基线,需走安全例外 | 之后可换成维护中的 BuildKit rootless |
| C 自己从源码编译 Chainguard fork | 我们 | 半天到一天,此后该工具由我们维护 |

新机器到手后先查 `sysctl -n kernel.unprivileged_userns_clone`。如果同样是 0,B 影响的不止这一台机器。
