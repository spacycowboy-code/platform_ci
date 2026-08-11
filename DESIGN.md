# 后端基础设施收敛 · 设计

> 范围:`rp-ai-miao` 下的四个 Python 后端(`rp-ai-portal-be`、`rtep-flow-manager`、
> `product-structure-information`、`agent-artifex`)与前端 `rp-ai-portal-fe`。
> 状态:四个后端仓已全部迁完,生产已切到镜像部署并验证。前端仓与审计栈的 staging 未迁。

## 要解决什么

四个后端各自维护一份几乎相同的基础设施配置。实测行级相似度:

| 文件 | 行数 | 两个孪生仓之间 | 与其余之间 |
|---|---|---|---|
| `Dockerfile` | 28 / 28 / 30 / 47 | 86% | 53–55% |
| `docker-compose.yml` | 39 / 37 / 41 / 166 | 71% | 60%(与 portal-be 仅 18%) |
| `.gitlab-ci.yml` | 98 / 104 / 139 / 151 | 91% | 60–76% |

`rtep-flow-manager` 与 `product-structure-information` 的 deploy 任务各 32 行、**只差 2 行**
(rsync 的排除项)。改一次部署流程要在四处重复,而且已经漂移了 9%。

## 抽象:四个归属

事实按"谁拥有它、它为什么会变"分给四个归属,而不是按文件类型分成"共享/私有"两类。

| 归属 | 拥有的事实 | 变更的理由 |
|---|---|---|
| **服务** | 源码、依赖、测试、构建输入(应用路径、额外系统包) | 功能变了 |
| **平台** | 怎么构建、怎么校验、怎么部署(过程) | 工程规范变了 |
| **发布** | 某一次构建的产物:镜像 + 对应的 commit | 代码合并了 |
| **环境** | 跑在哪台机器、端口、卷、主机路径、跑哪个标签 | 运维决定变了 |

只承认"共享/私有"两类时,有两种事实无处安放:"现在跑的是哪个构建"和"它跑在哪"。
结果就是端口被塞进 CI 模板的参数、构建方式被塞进 compose 的开关。四个归属分清后,
下面几条不是选择,是推论:

- 产物属于发布 ⇒ 必须不可变、可寻址:**构建一次**,按 commit 打标签推入镜像仓库,
  部署时只拉不建。"一个 Dockerfile"能成立的前提就在这里——若在部署时构建,
  构建配方必须跟到每个部署目标。
- 环境事实属于环境 ⇒ 集中在**按环境的部署描述**里,内容只有两类:引用哪个镜像标签 +
  主机绑定。它的 git 历史顺带成为"生产在跑什么"的记录。
- 过程属于平台 ⇒ 平台产物**有版本**,消费方**锁版本**。否则改平台等于同时改五个服务。
- 构建输入属于服务 ⇒ 服务与平台之间是一组很小的、**带默认值**的输入。给默认值而非必填,
  偶然分歧才会逐步收敛,而不是被固化成接口。

## 判据

抽象是否成立,用这六条验,不靠判断:

| 判据 | 目标 | 实测结果(试点:`product-structure-information`) |
|---|---|---|
| 不可变 + 可追溯 | 同一 tag digest 恒定;能反查 commit | CI 推送的 `sha256:77dc03f…`、仓库里的、容器正在跑的**三方一致**;tag 含 commit sha |
| 构建与部署解耦 | 部署不重新构建 | 部署日志只有 `Pulled` / `Recreated`,无构建;宿主机部署目录里只有 `compose.yml`,**没有源码** |
| 故障可控 | 未升版本的消费方不受影响 | 试点锁 `v0.2.0` 时,main 上的 kaniko 修复没有流过去(仍报旧的 dind 错);升到 `v0.3.0` 才生效 |
| 单一事实来源 | 一个事实出现 1 次 | 试点仓 `.gitlab-ci.yml` 104 → 11 行,`Dockerfile` 与 `docker-compose.yml` 删除(−168 / +19) |
| 变更影响面 = 变更意图 | 1 / 1 / 1 | 一次发布只改了环境描述里的一行标签 |
| 环境隔离 | 改一个环境不影响另一个 | 测试环境 9011 healthy 的同时,生产 9010 `/docs` 仍 200,全程未动 |

未完全可测的一条:"改部署流程只触及 1 个仓"要等第二个仓迁完才能验。

## 落到仓库

```
platform-ci/                      ← 平台 + 环境
├── docker/Dockerfile.base        ← 唯一的构建配方,构建成基础镜像
├── templates/python-service.yml  ← 服务流水线:lint / test / 构建并推镜像
├── templates/deploy-env.yml      ← 环境流水线:应用某个环境的部署描述
└── deploy/<env>/compose.yml      ← 环境的部署描述:镜像标签 + 主机绑定

<service>/                        ← 服务
├── .gitlab-ci.yml                ← include 模板 + 2–3 个输入
├── requirements.txt
└── (源码与测试)
```

服务仓**没有 Dockerfile、没有 compose**。

**为什么是基础镜像,而不是把共享 Dockerfile 拷到各仓构建**:服务流水线跑在服务仓里,
共享文件不在它的工作区。与其想办法把文件搬过去(跨仓拉取要处理鉴权,拷贝会漂移),
不如把配方**构建成产物**:`platform-ci` 把 `Dockerfile.base` 构建成
`python-base:<pyver>-<sha>` 推入仓库;服务构建时用一个 4 行的薄层(`FROM base` +
装该服务的依赖 + `COPY . .`),这 4 行由模板生成,不作为文件存在于任何服务仓。
配方仍然只有一份,而且是可版本化、可扫描的镜像。

## 发布流程

| 步骤 | 谁做 | 结果 |
|---|---|---|
| 合并到服务仓 `main` | 作者 | 流水线构建并推 `proj-rvc/rp-ai/<svc>:<version>-<sha>` |
| 改环境描述里的标签 | 自动开 MR(staging 直接提交) | prod 经一次评审,staging 免评审 |
| 合并环境描述 | 评审人 | 部署任务拉镜像并 `docker compose up -d` |
| 回滚 | 任何人 | revert 那次改标签的提交 |

`docker compose up -d` 只重建镜像标签发生变化的服务,同一文件里的其他服务不受影响——
所以"一个文件描述多个服务"只是文本层面的集中,不产生运行层面的耦合。

## 权限

| 对象 | 约定 |
|---|---|
| `deploy/prod/**` | `main` 受保护;改动需一名评审人批准 |
| `deploy/staging-*/**` | 不要求评审 |
| 镜像仓库凭据 | 组级 CI 变量,masked + protected;只用于推镜像 |
| 各服务的 `DEPLOY_*`、`PROD_ENV` | 留在各自项目,不上提到组级 |

## 已核实的事实

| 事实 | 结论 |
|---|---|
| 镜像仓库可写 | 可以。路径是 `armdocker.rnd.ericsson.se/proj-rvc/<path>`;Artifactory 仓库键是 `proj-rvc-docker-global`,把仓库键当命名空间会被拒 |
| 凭据 | 前端构建在用的那份(账号 `rvccie`)即可推送;该账号**能推不能删**(DELETE 返回 403) |
| 生产机能否拉 | 能,实测拉取成功 |
| GitLab 容器仓库 | 不可用,TCP 5050 超时 |
| **runner 不允许 privileged** | dind 报 `mount: permission denied (are you root?)`,`docker build` 在 CI 里不可用 |
| **无守护进程的构建** | kaniko 可用,镜像经 Artifactory 的 `gcr-ericsson-remote` 代理拉取;基础镜像与服务镜像都是这样构出来的 |
| **组级 CI 变量** | 需要组 Owner;Maintainer 只能写项目级 |
| **组级已有的变量** | `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY` / `PROD_GITLAB_TOKEN` / `NPM_AUTH_B64` 都在组级(此前只查项目级,曾误判为"不存在") |
| Python 统一到 3.12 | 可行。portal-be 全部依赖在 3.12 装通,`cisco-ai-skill-scanner==2.0.12`(唯一硬约束)也装通并可运行 |
| 健康检查 | `agent-artifex` 与 `portal-be` 有 `/health`;`product-structure-information` 已补(本轮);`rtep-flow-manager` 待补。原先打的 `/swagger` 在该应用里**不存在**,生产 9010 实测同样 404——那条健康检查一直失效 |
| 审批规则 | 该实例支持(approval rules 接口返回 200) |

## 约定

| 约定 | 说明 |
|---|---|
| 容器内端口 | 统一 `8000`(基础镜像默认 `APP_PORT`);宿主机端口是环境的事实,写在环境描述里 |
| 服务镜像标签 | `<version>-<commit sha>`,不可覆盖;构建前先查,已存在则跳过 |
| 基础镜像标签 | `<pyver>-<sha>`(不可变)与 `<pyver>-latest`(浮动)。浮动标签只影响下一次构建,不影响已发布的服务镜像 |
| 健康探针 | 每个后端提供 `/health`,不依赖数据库或外部系统——否则探针失败会分不清"服务挂了"和"依赖挂了" |

## 参照与不照搬

组织里已有同形状的做法:`radio-verification-cloud` 的服务仓只保留 `.gitlab-ci.yml`,
部署描述集中在 `configuration-files` 仓并按环境分文件,镜像从 armdocker 拉取。本设计沿用
这个分工,但不引入以下部件——它们服务于多节点集群,我们是单机:

- Docker Swarm(`docker stack deploy`、`replicas`、`placement.constraints`)
- Nacos 配置与注册中心、SkyWalking 探针

一处刻意的差异:他们的 `include` 用 `ref: main`(不锁版本)。本仓要求消费方锁 tag。
五个仓共用一份模板,不锁版本时一次误改会同时打断五条流水线。

## 迁移顺序与当前状态

| # | 步骤 | 状态 |
|---|---|---|
| 1 | `platform-ci`:构建配方、服务模板、额外镜像模板、环境部署模板、自身流水线 | 完成,`v0.5.2` |
| 2 | 基础镜像 `python-base:3.12-b3708562` | 已发布 |
| 3 | `product-structure-information` | 完成,生产已切换,healthy |
| 4 | `rtep-flow-manager` | 完成,生产已切换,healthy |
| 5 | `agent-artifex` | 镜像已产出;**该服务在生产上已停止 6 天,如实记为不运行,未启动** |
| 6 | `rp-ai-portal-be`(后端 / 对账器 / 判定器 / grafana) | 完成,生产已切换;判定器登录态经外部卷保住,端到端判定 `pass` |
| 7 | 审计栈的 staging(`:9100` / `:9101`) | 未迁,仍在宿主机上构建 |
| 8 | 前端仓 `rp-ai-portal-fe` | 未迁,需要另一套模板(node 构建 + nginx 运行) |

### 生产切换的实测结果

| 核对项 | 结果 |
|---|---|
| 三个宿主机端口 | 9010 / 9201 / 9000 全部 200 |
| 镜像 digest | 容器跑的 = 仓库里的 = CI 推的 |
| 判定器登录 | 切换后仍登录(外部卷),端到端判定返回 `pass` |
| 未变的服务 | 部署一次只重建标签变化的服务,其余容器 uptime 不变 |
| 服务器上的源码 | 生产部署目录只有 `compose.yml` + 投放的 env 文件 + grafana 配置 |
| 中断时长 | 每个服务约 40 秒(停旧栈到新栈健康) |

### 一次自己造成的偏差,记在这里

v0.4.0 的提交信息写了"基础镜像锁固定标签",实际那次字符串替换没匹配上、静默跳过,模板里
一直是 `3.12-latest`。表现是:基础镜像补了 PATH 之后,消费方没升任何版本就直接构建成功——
浮动标签自动跟上了。v0.5.2 才真正锁住。

由此定的规矩:**改模板的脚本必须先断言匹配数量**,替换失败要报错而不是静默通过。同批的另外
三项(密钥经 stdin、去掉 version 输入、lint 真实门槛)逐条核对过,均已生效。

### 生产切换前要解决的一个设计点

生产的 `.env`(DATABASE_URL 等)现在由各服务项目的 `PROD_ENV` 变量推送到各自目录。环境
集中后,密钥归环境:应改为 `platform-ci` 里按服务一份 env 文件变量,由环境部署任务投放到
环境目录。切换时该服务的宿主机端口会有秒级中断(停旧容器、起新容器)。

### `rp-ai-portal-be` 的坑(已处理)

判定器的登录态存在**命名卷**里,卷名由 compose 项目目录名派生。迁到环境目录会使卷名改变,
表现为判定器"忽然未登录",继而所有上传 fail-closed 转复核。环境描述里把三个卷声明为
`external` 并写死现有卷名(`ai-portal-be_judge-kiro-{home,share}`、`ai-portal-be_grafana-data`),
切换后 `kiro-cli whoami` 仍是登录状态。

同一个仓出两个镜像:后端与对账器是同一镜像的两种角色(靠 entrypoint 区分),判定器的配方
要在镜像里装 kiro-cli,不是薄层能表达的,由 `extra-image` 模板按同样的不变量构建。没有给
薄层加"任意构建步骤"的输入——那会让共享配方退化成占位符。

## 边界

- 前端(Angular)不与后端共用模板,单独一份构建流水线。
- `agent-artifex` 声明端口 10000,当前不在这台机器上运行;它的环境描述等宿主机确定后再加。
- 镜像清理需要删除权限,当前账号没有;探针镜像 `proj-rvc/rp-ai/infra-probe:t1` 与
  `proj-rvc/test/rp-ai-infra-probe:t1` 需要有权限的人清理。
