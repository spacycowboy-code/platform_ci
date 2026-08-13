# 关于构建器:我们用了一个已归档的工具,以及为什么现在不换

给需要判断这件事的人看:决定是否值得为它动机器配置。结论在最后一节。

## 现状

流水线构建镜像用的是 **kaniko**(`gcr.io/kaniko-project/executor v1.23.2`,经内网 Artifactory 代理)。
这个项目 **上游已归档**,不会再有新版本。我们已把它从 tag 锁成 digest:

```
executor:v1.23.2-debug@sha256:c3109d5926a997b100c4343944e06c6b30a6804b2f9abe0994d3de6ef92b028e
```

锁 digest 的作用:即使代理里同名 tag 的内容被替换,我们用的仍是同一份二进制。

## 为什么当初选它

构建必须满足一条硬约束:**本组 GitLab runner 不允许特权容器**。实测 docker-in-docker 会失败:

```
mount: permission denied (are you root?)
```

kaniko 不需要守护进程、不需要特权,是当时唯一跑得通的选择。

## 推荐的替代方案卡在哪

社区现在的推荐是 **BuildKit rootless**(维护中,即 `docker build` 背后的引擎)。实测在本组 runner 上
起不来:

```
[rootlesskit:parent] /proc/sys/kernel/unprivileged_userns_clone needs to be set to 1
[rootlesskit:parent] error: failed to start the child: fork/exec /proc/self/exe: operation not permitted
```

它需要**非特权用户命名空间**。而这台机器上该功能是被显式关闭的:

```
$ sysctl -n kernel.unprivileged_userns_clone
0
$ grep -r userns /etc/sysctl.d/
/etc/sysctl.d/90-disable-unpriv-userns.conf:kernel.unprivileged_userns_clone = 0
```

Ubuntu 22.04 的默认值是 `1`,所以这不是漏配,是有人**刻意加固**(文件名即 `disable-unpriv-userns`)。
需要判断的是:这属于组织基线还是单机决定。如果是基线,新申请的机器很可能也是 0——我原先"等自有机器
就能用 rootless"的说法因此不成立,要先核实。

> 说明:非特权用户命名空间正是"不需要 root 就能构建镜像"的底层机制。放开它不等于给容器特权,恰恰
> 是**避免**使用特权容器的前提。但它也确实扩大过内核攻击面(历史上有相关 CVE),所以有的加固基线会
> 关掉它。这个取舍不该由我们单方面决定。

## 另外两条路也验过,都不通

| 路径 | 结论 |
|---|---|
| 换到 Chainguard 维护的 kaniko fork | 公开目录里没有这个镜像:`cgr.dev/chainguard/kaniko:latest` 返回 404。要用只能自己从源码编译并长期自维护 |
| 请 Artifactory 加 `cgr.dev` 代理 | 由上一条否决:代理不到不存在的镜像 |

## 为什么不"在我们自己的机器上构建"

试过:作业里用 `docker buildx` 通过 SSH 驱动部署机的 Docker 引擎。**实测失败,而且不该做**:

- `DEPLOY_SSH_KEY` 是受保护变量,分支流水线拿不到,构建直接失败
- 要让分支也能构建,就得把部署机私钥开放给任意分支的流水线——**谁能推一个分支,就能在部署机上执行
  命令**

这违反一条我们要守的边界:**构建作业只应持有镜像仓库的推送凭据,不应持有任何主机凭据。**
kaniko 和 BuildKit rootless 都满足这条,buildx-over-SSH 不满足。这也是判断替代方案的标准,不是偏好。

## 为什么维持现状没问题

| 担心 | 实际情况 |
|---|---|
| 用了没人维护的工具 | 它不再发版,所以不存在"漏了安全更新"这回事;内容已锁 digest,不会在背后变化 |
| 万一它有漏洞 | 运行范围是:CI 里一个临时容器,寿命几分钟,无对外监听端口,持有的凭据只有镜像仓库推送账号(该账号能推不能删) |
| 构建结果会不会不可信 | 产物标签是 commit sha、同一 commit 只构建一次、部署只拉不建;线上跑的镜像 id 记录在部署日志与部署机 `.env` 里,可追溯 |
| 会不会越拖越难换 | 换构建器只改 `templates/service.yml` 里一处任务定义,服务仓一行都不用动(它们只写 `ref: v1`) |

## 需要决定的

三个选项,请选一个:

| 选项 | 谁做什么 | 代价 |
|---|---|---|
| **A. 维持现状**(建议) | 无 | 继续用已归档但锁定的 kaniko;风险如上表 |
| B. 放开非特权用户命名空间 | runner 宿主机的维护者设 `kernel.unprivileged_userns_clone=1`;若属组织基线,需要走安全例外 | 之后可换成维护中的 BuildKit rootless;需要有人为加固基线的偏离背书 |
| C. 自己编译 Chainguard 的 fork | 我们加一条构建该工具镜像的流水线,推到自己的命名空间 | 约半天到一天;此后这个工具由我们自己维护、自己跟进上游 |

我的建议是 **A**,并在两种情况下重新评估:一是有人愿意为 B 背书,二是新集群的机器不带这条加固基线
(需要拿到机器后核实 `sysctl -n kernel.unprivileged_userns_clone`)。

无论选哪个,服务仓都不受影响——它们只引用 `ref: v1`,构建器换了也不用改一行。
