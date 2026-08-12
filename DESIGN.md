# 后端基础设施收敛 · 设计

本文只回答"为什么这样设计"。怎么用见 [README.md](README.md);出过什么事、为什么定下那些
操作规矩见 [INCIDENTS.md](INCIDENTS.md)。

## 要解决什么

`rp-ai-miao` 下五个仓各自维护一份 Dockerfile、compose 和流水线。迁移前实测的重复度:

| 文件 | 行数 | 最相似的两个仓 |
|---|---|---|
| `Dockerfile` | 28 / 28 / 30 / 47 | 86% |
| `docker-compose.yml` | 39 / 37 / 41 / 166 | 71% |
| `.gitlab-ci.yml` | 98 / 104 / 139 / 151 | 91% |

`rtep-flow-manager` 与 `product-structure-information` 的 deploy 任务各 32 行,只差 2 行。
改一处部署流程要动五个仓,而且必然漏。

## 抽象:四个归属

**一个事实只放在拥有它的那一层。**

| 归属 | 拥有什么 | 什么时候变 |
|---|---|---|
| **服务** | 源码、依赖、测试、构建输入(应用路径、额外 apt 包) | 功能变了 |
| **平台** | 怎么构建 / 怎么校验 / 怎么部署,即过程本身 | 工程标准变了 |
| **发布** | 产物:镜像及其 commit | 代码合并了 |
| **环境** | 哪台机器、哪些端口、哪些卷、跑哪个产物 | 运维决定变了 |

四条推论,不是可选项:

1. **产物不可变、可寻址**。构建一次,标签含 commit,推到仓库;部署只拉不建。这是"一个
   Dockerfile"能成立的前提——否则配方必须跟到每个部署目标上去。
2. **环境事实集中在每个环境一份部署描述里**,它的 git 历史就是部署记录。发布=改一行标签,
   回滚=revert。
3. **平台产物有版本,消费方锁 tag**。五个仓共用一份模板,不锁版本时一次误改同时打断五条
   流水线。代价见 INCIDENTS:锁住的东西不会自动跟上修复。
4. **服务与平台之间的接口是一小组带默认值的输入**。默认值让偶然的差异逐步收敛,而不是被
   固化成接口。

一个典型例子:前端 nginx 的三个上游地址原先编译进镜像,于是同一份源码要为验收和生产各构
一个镜像。上游是环境的事实,进了发布物——这与"部署时重新构建"是同一个错误的两种形态。改由
环境在容器启动时注入后,`:9001` 与 `:9101` 跑同一个 digest。

## 判据

每条都能复测,命令写在括号里。

| 判据 | 目标 | 最近一次实测 |
|---|---|---|
| 单一事实来源 | 一个值出现 1 次 | `100.77.162.66` 1 个文件、`DEPLOY_DIR` 0 个、`docker compose up -d --build` 0 个(在六个仓 `grep -rIl`) |
| 服务仓的基础设施残留 | 只剩本服务独有的 | 五个仓 `.gitlab-ci.yml` 8/9/10/11/29 行;Dockerfile 只剩 `Dockerfile.judge`(配方确实独一份) |
| 不可变 + 可追溯 | 跑的东西不随标签变化,且能反查 commit | 环境按 `:<sha>@sha256:<digest>` 锁定,七个第一方容器 `docker inspect --format '{{.Config.Image}}'` 都带 digest |
| 构建与部署解耦 | 部署不重新构建 | 部署日志只有 `Pulled` / `Recreated`;宿主机部署目录里只有 `compose.yml` 与投放的 env 文件 |
| 环境隔离 | 改一个环境不影响另一个 | 两个环境的前端跑同一 digest,只有上游环境变量不同;部署一次只重建标签变化的服务 |
| 故障可控 | 未升版本的消费方不受平台改动影响 | 试点锁 `v0.2.0` 时,main 上的构建修复没有流过去 |

## 落到仓库

| 路径 | 归属 | 内容 |
|---|---|---|
| `docker/Dockerfile.base` | 平台 | 唯一的 Python 构建配方,构建成基础镜像 |
| `templates/_build-common.yml` | 平台 | 三个构建模板共用的构建器镜像、仓库凭据、标签保护 |
| `templates/python-service.yml` | 平台 | 后端流水线:lint / test / 构建推镜像 |
| `templates/web-service.yml` | 平台 | 前端流水线:构建静态资源 + nginx 镜像 |
| `templates/extra-image.yml` | 平台 | 同一仓的第二个镜像,用仓里自带的 Dockerfile |
| `templates/deploy-env.yml` | 平台 | 环境部署:只拉镜像,可投放环境密钥 |
| `deploy/<env>/compose.yml` | 环境 | 这台机器上跑什么 |

服务仓里剩下的只有一段 include 加几个输入。

## 约定

| 约定 | 理由 |
|---|---|
| 容器内统一监听 `8000` | 宿主机端口是环境的事实,写在环境描述里 |
| 服务镜像标签 = commit sha | 产物由它的 commit 标识;打了 git tag 会额外推一个同名标签 |
| 标签不可覆盖 | 构建前先探仓库,已存在就跳过(`.tag-guard`) |
| 环境引用 `:<sha>@sha256:<digest>` | 标签是可写的,只有 digest 能保证下次部署跑的还是同一个东西;标签留在字符串里便于人读 |
| 基础镜像只发固定标签 | 浮动标签没有消费者,只剩"让人不小心跟到移动的基础镜像"这一种用途 |
| 每个后端提供 `/health` | 探针不依赖数据库或外部系统,否则探针失败分不清"服务挂了"和"依赖挂了" |
| 命名卷用 `external` 并写死卷名 | compose 默认按项目目录派生卷名,换目录会挂到空卷上:判定器会表现为未登录,继而所有上传 fail-closed 转人工复核 |
| 默认值优先于必填 | 只有真正属于服务身份的东西才必填(镜像名、应用路径) |
| 部署不在服务仓 | 发布是环境侧的动作 |

## 已核实的约束

| 事实 | 结论 |
|---|---|
| runner 不允许 privileged | dind 报 `mount: permission denied`,`docker build` 在 CI 里不可用;改用 kaniko(镜像经 Artifactory 的 `gcr-ericsson-remote` 代理拉取) |
| 镜像仓库 | 可写,路径 `armdocker.rnd.ericsson.se/proj-rvc/<path>`;把 Artifactory 仓库键当命名空间会被拒 |
| 推送账号 | 前端构建在用的那份即可推送;**能推不能删**(DELETE 403) |
| GitLab 容器仓库 | 不可用,TCP 5050 超时 |
| 组级 CI 变量 | 需要组 Owner;Maintainer 只能写项目级 |
| Python 统一 3.12 | 可行,含唯一硬约束 `cisco-ai-skill-scanner==2.0.12` |
| 健康检查的两个坑 | `product-structure-information` 没有 `/swagger` 路由(生产实测 404,容器 unhealthy 6 天);前端探针打 `localhost` 会走 `::1` 而 nginx 只监听 IPv4,须用 `127.0.0.1` |

## 参照与不照搬

组里已有的做法:`configuration-files` 是部署仓,用 `docker stack deploy`(Swarm)+ Nacos +
SkyWalking;`email-agent` 只有一个 `.gitlab-ci.yml`,include 组内模板仓。

照搬的:部署与服务分仓、上游流水线触发部署、镜像放 `armdocker` 的团队命名空间。

不照搬的:Swarm、Nacos、SkyWalking——这里是单机,引入它们等于多养三套没人用的机制。

有意不同的一处:`email-agent` 用 `ref: 'main'`,即模板一改所有消费方立刻跟上。我们锁 tag。

## 边界

不做灰度、不做多副本、不做跨机编排。单机、单副本、手动触发生产部署,与现状相符。

## 阻塞项(需要别人的权限,不是设计问题)

| 事项 | 卡在哪 |
|---|---|
| 生产审计后端的推送密钥仍从旧部署目录挂载 | 私钥 `600 ezhgmio`,目录 `ezhgmio:erpait`,我读不了也建不了。终态应像其它环境密钥一样由部署任务投放,需要 ezhgmio 移动密钥或换一把部署密钥 |
| 仓库里两个 `infra-probe` 探测镜像 | 推送账号能推不能删(403) |
| `agent-artifex` 是否启动 | 它在生产上已停 6 天;启动一个停掉的服务是功能决定,不属于迁移 |
| `services/test-env` 空目录 | 由部署任务以 `ezhgmio` 身份创建,我删不掉;已无引用 |
