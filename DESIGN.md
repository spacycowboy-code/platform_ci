# 后端基础设施收敛 · 设计

## 起因

五个仓各自维护一份 Dockerfile、compose 和流水线。迁移前的重复度:

| 文件 | 行数 | 最相似的两个仓 |
|---|---|---|
| `Dockerfile` | 28 / 28 / 30 / 47 | 86% |
| `docker-compose.yml` | 39 / 37 / 41 / 166 | 71% |
| `.gitlab-ci.yml` | 98 / 104 / 139 / 151 | 91% |

`rtep-flow-manager` 与 `product-structure-information` 的部署任务各 32 行,只差 2 行。
改一处部署流程要动五个仓。

## 分层

一个事实只放在拥有它的那一层。

| 归属 | 拥有什么 | 什么时候变 |
|---|---|---|
| 服务 | 源码、依赖、测试、构建输入(应用路径、额外 apt 包) | 功能变了 |
| 平台 | 怎么构建、怎么校验、怎么部署 | 工程标准变了 |
| 发布 | 产物:镜像及其 commit | 代码合并了 |
| 环境 | 哪台机器、哪些端口、哪些卷、跑哪个产物 | 运维决定变了 |

由此得到四条:

- 产物不可变、可寻址:构建一次,标签含 commit,部署只拉不建。否则构建配方必须跟到每个
  部署目标上,"一个 Dockerfile"无法成立。
- 环境事实集中在每个环境一份部署描述里,它的 git 历史就是部署记录。
- 平台产物有版本,消费方锁 tag。代价是锁住的消费方不会自动拿到修复,升级要显式做。
- 服务与平台之间是一小组带默认值的输入。默认值让偶然差异逐步收敛,而不是固化成接口。

前端曾把 nginx 的三个上游地址编译进镜像,于是同一份源码要为验收和生产各构一个镜像。上游
属于环境,改由环境在容器启动时注入后,两个环境跑同一个 digest。

## 判据

| 判据 | 目标 | 怎么测 | 最近结果 |
|---|---|---|---|
| 单一事实来源 | 一个值出现 1 次 | 六个仓 `grep -rIl` | `DEPLOY_DIR` 0 个文件;`docker compose up -d --build` 只在本文出现 |
| 服务仓残留 | 只剩本服务独有的 | 看 `.gitlab-ci.yml` 行数与 Dockerfile 数 | 8 / 9 / 10 / 11 / 29 行;Dockerfile 只剩 `Dockerfile.judge` |
| 不可变、可追溯 | 跑的东西不随标签变化,能反查 commit | `docker inspect --format '{{.Config.Image}}'` | 七个第一方容器都带 digest |
| 构建与部署解耦 | 部署不重新构建 | 看部署日志与宿主机目录 | 日志只有 `Pulled` / `Recreated`;目录只有 compose 与 env 文件 |
| 环境隔离 | 改一个环境不影响另一个 | 比两个环境的 digest 与容器 uptime | 前端同 digest,只有上游变量不同;部署只重建标签变化的服务 |
| 故障可控 | 未升版本的消费方不受平台改动影响 | 平台改动后不升消费方,看其流水线 | 锁 `v0.2.0` 的消费方没有收到 main 上的构建修复 |

## 仓库布局

| 路径 | 归属 | 内容 |
|---|---|---|
| `docker/Dockerfile.base` | 平台 | 唯一的 Python 构建配方 |
| `templates/_build-common.yml` | 平台 | 构建器镜像、仓库凭据、标签保护 |
| `templates/python-service.yml` | 平台 | 后端:lint / test / 构建推镜像 |
| `templates/web-service.yml` | 平台 | 前端:构建静态资源 + nginx 镜像 |
| `templates/extra-image.yml` | 平台 | 同一仓的第二个镜像,用仓里自带的 Dockerfile |
| `templates/deploy-env.yml` | 平台 | 环境部署:只拉镜像,可投放环境密钥 |
| `deploy/<env>/compose.yml` | 环境 | 这台机器上跑什么 |

## 约定

| 约定 | 理由 |
|---|---|
| 容器内统一监听 8000 | 宿主机端口属于环境 |
| 服务镜像标签 = commit sha | 产物由 commit 标识;打 git tag 会额外推一个同名标签 |
| 标签不可覆盖 | 构建前探仓库,已存在则跳过 |
| 环境引用 `:<sha>@sha256:<digest>` | 标签可写,digest 保证下次部署跑的是同一个东西;标签留在字符串里便于人读 |
| 基础镜像只发固定标签 | 浮动标签会让同一份源码构出不同产物 |
| 每个后端提供 `/health` | 探针不依赖数据库或外部系统,否则分不清服务与依赖谁挂了 |
| 命名卷用 `external` 且写死卷名 | compose 默认按项目目录派生卷名,换目录会挂到空卷 |
| 只有服务身份必填 | 其余输入给默认值 |
| 部署不在服务仓 | 发布是环境侧的动作 |

## 环境相关的已知陷阱

| 现象 | 原因 |
|---|---|
| 判定器忽然"未登录",所有上传转人工复核 | kiro-cli 登录态在命名卷里,卷名随 compose 项目目录变化 |
| 容器长期 unhealthy 但服务正常 | 探针打 `localhost` 会先走 `::1`,而 nginx 只监听 IPv4;用 `127.0.0.1` |
| 探针一直失败 | 探的路径不存在。`product-structure-information` 没有 `/swagger`;`/docs` 是 Swagger UI 页面,可用不代表服务可用 |
| 新栈起不来,报容器名冲突 | 旧栈里 `profiles:` 后面的服务没有被 `docker compose down` 停掉 |
| 标签指向的内容变了 | 锁旧版本模板的消费方没有标签保护,重跑会覆盖。环境按 digest 引用可免受影响 |

## 环境相关的已核实约束

| 事实 | 结论 |
|---|---|
| runner 不允许 privileged | dind 报 `mount: permission denied`;改用 kaniko,镜像经 `gcr-ericsson-remote` 代理拉取 |
| 镜像仓库 | 可写,路径 `armdocker.rnd.ericsson.se/proj-rvc/<path>`;Artifactory 仓库键不能当命名空间 |
| 推送账号 | 能推不能删(DELETE 403) |
| GitLab 容器仓库 | 不可用,TCP 5050 超时 |
| 组级 CI 变量 | 需要组 Owner |
| Python 3.12 | 全部依赖可装,含 `cisco-ai-skill-scanner==2.0.12` |

## 参照与不照搬

组里已有做法:`configuration-files` 是部署仓,用 `docker stack deploy`(Swarm)+ Nacos +
SkyWalking;`email-agent` 只有一个 `.gitlab-ci.yml`,include 组内模板仓。

采用:部署与服务分仓、上游流水线触发部署、镜像放 `armdocker` 的团队命名空间。

不采用:Swarm、Nacos、SkyWalking。这里是单机单副本,这三样没有使用场景。

一处有意不同:`email-agent` 用 `ref: 'main'`,模板一改所有消费方立刻跟上;这里锁 tag。

## 边界

不做灰度、不做多副本、不做跨机编排。生产部署手动触发。

## 待别人处理

| 事项 | 卡在哪 |
|---|---|
| 生产审计后端的推送密钥仍从旧部署目录挂载 | 私钥 `600 ezhgmio`,目录属 `ezhgmio:erpait`。终态是像其它环境密钥一样投放,需要密钥所有者移动或换一把 |
| 仓库里两个 `infra-probe` 镜像 | 推送账号无删除权限 |
| `agent-artifex` 是否启动 | 生产上已停;启动属于功能决定 |
| `services/test-env` 空目录 | 由部署任务以 `ezhgmio` 身份创建,已无引用 |
