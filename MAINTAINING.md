# 维护这套流水线

给**改动流水线本身**的人看。日常发布见 [README.md](README.md),设计取舍见 [DESIGN.md](DESIGN.md)。

## 结构

| 路径 | 作用 |
|---|---|
| `templates/service.yml` | 服务仓唯一 include 的文件:lint / test / 构建 / 两个部署按钮 |
| `templates/deploy.yml` | 部署机制(ssh、拉镜像、投放密钥),由 `service.yml` 内部 include |
| `docker/Dockerfile.base` | 所有 Python 服务的基础镜像配方 |
| `deploy/<环境>/compose.yml` | 这台机器上跑什么:镜像、端口、卷、env 文件 |
| `.gitlab-ci.yml` | 本仓自己的流水线:校验模板、发基础镜像 |

服务仓只 include 一个文件。构建与部署拆成两份:触发条件、所需凭据、失败方式都不同。

## 环境

| 环境 | 描述文件 | 机器上的目录 | 谁往里放东西 |
|---|---|---|---|
| test | `deploy/test/` | `/proj/rp_ait_prod/services/test-env` | 任意分支,点 `deploy-test` |
| prod | `deploy/prod/` | `/proj/rp_ait_prod/services/prod-env` | 只有 `main`,点 `deploy-prod` |

环境目录里只有 `compose.yml` 和 grafana 这类配置文件,没有源码。镜像只写 `image:`,不写
`build:`。生产描述里 `agent-artifex` 带 `profiles: [stopped]`,部署不会启动它。

密钥是 File 型 CI 变量,由部署任务投放到环境目录:

| 变量 | 落地成 |
|---|---|
| `PSI_PROD_ENV` | `product-structure-information.env` |
| `AUDIT_PROD_ENV` | `ai-portal-be.env` |
| `GRAFANA_PROD_ENV` | `grafana.env` |
| `AUDIT_TEST_ENV` | `audit-test.env` |

加一个:在部署任务的 `env_files` 里加 `目标文件名=变量名`。

## 版本

服务仓写 `ref: v1`,永远不改。`v1` 是一个指针,由平台维护者移动。

| 改动性质 | 怎么做 |
|---|---|
| 不影响消费方(修行为、加可选输入、换基础镜像、改日志) | 验证后把 `v1` 移过去,同时打一个永久编号 tag |
| 影响消费方(删/改名输入、可选变必填、改任务名、要求仓里多一个文件) | **在同一批改动里连五个消费方一起改**,然后移 `v1` |
| 只改文档、注释、本仓流水线 | 不移 `v1`,不打 tag |

编号 tag 的唯一用途是"`v1` 曾指向哪",便于把 `v1` 移回去。这与"镜像标签不可覆盖"不冲突:
`v1` 是配置的通道指针,镜像标签是产物标识,产物永不覆盖。

`v0.1.0` 到 `v0.15.0` 这批 tag 不代表兼容性承诺,保留为历史点。消费方一律用 `v1`。

## 加一个新服务

1. 服务仓写 `.gitlab-ci.yml`(见 README),提供 `/health`。
2. 在需要的环境描述里加一段 `services:`:镜像、`container_name`、宿主机端口、要挂的目录。
   容器内统一监听 8000。
3. 需要密钥的话加一个 File 型变量并写进 `env_files`。

## 加一个新环境

复制 `deploy/<某环境>/` 改端口和路径,在 `.gitlab-ci.yml` 里加一份 `deploy.yml` 的 include。
命名卷一律 `external` 并写死卷名——compose 默认按目录名派生卷名,换目录会挂到空卷上。

## 把一个还在用旧方式部署的服务切过来

1. 先确认目标环境的部署任务可触发(手动任务在同一条流水线里只能点一次)。
2. 停旧栈,`docker ps -a` 检查残留:`docker compose down` 不停 `profiles:` 后面的服务,
   重名容器会让新栈起不来。
3. 触发部署,按健康端点验证。
4. 失败时只回滚本次涉及的服务;旧目录那份 compose 里可能有已归别的环境管理的服务,整份
   `up -d` 会因容器名冲突失败。

## 待实现(文档已对齐,代码还没跟上)

| 项 | 状态 |
|---|---|
| `deploy-test` / `deploy-prod` 两个按钮放进 `service.yml` | 未做。现在的发布方式仍是手工改环境描述里的镜像标签 |
| 五个模板合并成 `service.yml` + `deploy.yml` | 未做。现在是 5 个文件,消费方要 include 1~2 个 |
| `v1` 通道 | 未建。消费方现在锁 `v0.14.0` |
| 环境 `staging-audit` 改名 `test` 并转为分支用 | 未做 |
| `v0.15.0`(输入 10→5)生效 | 已发布未启用;启用时要同时删掉 `rp-ai-portal-be` 传的 `entrypoint` 一行 |

## 需要别人权限的事

| 事项 | 卡在哪 |
|---|---|
| 生产审计后端的推送密钥仍从旧部署目录挂载 | 私钥 `600 ezhgmio`,目录属 `ezhgmio:erpait` |
| 仓库里删不掉的一次性标签 | 推送账号无删除权限(DELETE 403) |
| `services/test-env` 空目录 | 由部署任务以 `ezhgmio` 身份创建 |
