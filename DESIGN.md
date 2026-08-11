# 后端基础设施收敛 · 设计

> 范围:`rp-ai-miao` 下的四个 Python 后端(`rp-ai-portal-be`、`rtep-flow-manager`、
> `product-structure-information`、`agent-artifex`)与前端 `rp-ai-portal-fe`。
> 状态:抽象已定,实施中。

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

| 判据 | 怎么测 | 目标 |
|---|---|---|
| 单一事实来源 | 在所有仓 grep 一个具体值(`9010`、`3.12`、部署主机名) | 各出现 1 次 |
| 变更影响面 = 变更意图 | 改部署流程 / 改一个服务的端口 / 改代码,各触及几个仓 | 1 / 1 / 1 |
| 构建与部署解耦 | 回滚一次,是否需要重新构建 | 不需要 |
| 不可变 | 同一 tag 的 digest 是否恒定;能否覆盖 tag | 恒定;禁止覆盖 |
| 可追溯 | 从生产容器的镜像标签能否反查到 commit | 能 |
| 故障可控 | 平台变更后,未升版本的消费方是否受影响 | 不受 |

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
| Python 统一到 3.12 | 可行。portal-be 全部依赖在 3.12 装通,`cisco-ai-skill-scanner==2.0.12`(唯一硬约束)也装通并可运行 |
| 健康检查 | 只有 `agent-artifex` 与 `portal-be` 有 `/health`,另两个打的是 `/docs`、`/swagger`,需各补三行 |
| 审批规则 | 该实例支持(approval rules 接口返回 200),可按路径设审批而不只靠约定 |

## 参照与不照搬

组织里已有同形状的做法:`radio-verification-cloud` 的服务仓只保留 `.gitlab-ci.yml`,
部署描述集中在 `configuration-files` 仓并按环境分文件,镜像从 armdocker 拉取。本设计沿用
这个分工,但不引入以下部件——它们服务于多节点集群,我们是单机:

- Docker Swarm(`docker stack deploy`、`replicas`、`placement.constraints`)
- Nacos 配置与注册中心、SkyWalking 探针

一处刻意的差异:他们的 `include` 用 `ref: main`(不锁版本)。本仓要求消费方锁 tag。
五个仓共用一份模板,不锁版本时一次误改会同时打断五条流水线。

## 迁移顺序

1. 补 `/health`(两个仓)、统一 Python 3.12、把 `product-structure-information` 的
   5 条 `pytest --ignore` 挪进它自己的 pytest 配置。三步各自独立可回退。
2. `platform-ci` 构建并推基础镜像,打 tag 发布模板。
3. 试点 `product-structure-information`:先只换流水线,用 CI lint 导出渲染后的完整配置与
   现状逐行对比;再切到镜像部署。
4. `rtep-flow-manager`、`agent-artifex`。
5. `rp-ai-portal-be` 最后:它的 judge / reconciler / db 三个服务进环境描述,
   `PROD_ENV` 推送与 skill 仓 SSH key 自愈是它独有的步骤。

## 边界

- 前端(Angular)不与后端共用模板,单独一份构建流水线。
- `agent-artifex` 声明端口 10000,当前不在这台机器上运行;它的环境描述等宿主机确定后再加。
- 镜像清理需要删除权限,当前账号没有;探针镜像 `proj-rvc/rp-ai/infra-probe:t1` 与
  `proj-rvc/test/rp-ai-infra-probe:t1` 需要有权限的人清理。
