# 维护这套流水线

给改动流水线本身的人。日常发布见 [README.md](README.md)。

## 文件

| 路径 | 作用 |
|---|---|
| `templates/service.yml` | 服务仓唯一 include 的文件:lint / test / build + 两个部署按钮 |
| `templates/deploy.yml` | 被按钮触发时执行部署的 `apply` 任务(本仓内部用) |
| `docker/Dockerfile.base` | 所有 Python 服务的基础镜像配方 |
| `deploy/<环境>/compose.yml` | 这台机器上跑什么:镜像、端口、卷、env 文件 |
| `deploy/<环境>/services.txt` | 镜像名 → 要重建哪些 compose 服务;缺映射直接失败 |
| `deploy/<环境>/env-files.txt` | 目标文件名 = CI 变量名,部署时投放 |

## 环境

| 环境 | 目录 | 机器上的目录 | 谁能部 |
|---|---|---|---|
| test | `deploy/test/` | `/proj/rp_ait_prod/services/test-env` | 任意分支 |
| prod | `deploy/prod/` | `/proj/rp_ait_prod/services/prod-env` | 只有 `main` |

镜像标签写成 `${X_TAG:-上次已知}`:实际版本在部署机的 `.env` 里,由部署任务更新;git 里的默认值只是
兜底,不代表线上。生产描述里 `agent-artifex` 带 `profiles: [stopped]`,部署不会启动它。

## 版本

服务仓写 `ref: v1`,永不改。`v1` 是指针,由平台维护者移动。

| 改动性质 | 做法 |
|---|---|
| 不影响消费方(修行为、加可选输入、换基础镜像) | 验证后移 `v1`,并打一个永久编号 tag 便于回退 |
| 影响消费方(删/改名输入、可选变必填、改任务名、要求仓里多一个文件) | 同一批改动里连五个消费方一起改,再移 `v1` |
| 只改文档或本仓流水线 | 不移 `v1`,不打 tag |

`v0.1.0`–`v0.15.0` 是重构期间边做边发的,不代表兼容性承诺,保留为历史点。

## 加一个服务

服务仓写 `.gitlab-ci.yml`(见 README),提供 `/health`;在需要的环境描述里加一段 `services:`(镜像、
`container_name`、宿主机端口、挂载),容器内统一监听 8000;在 `services.txt` 加映射;要密钥就加 File
变量并写进 `env-files.txt`。

## 加一个环境

复制 `deploy/<某环境>/` 改端口与路径,在 `deploy.yml` 的 `case` 里加它的宿主机目录。命名卷一律
`external` 并写死卷名——compose 默认按目录名派生卷名,换目录会挂到空卷上。

## 切换一个还在用旧方式部署的服务

1. 先确认目标环境的部署任务可触发(手动任务在同一条流水线里只能点一次)。
2. 停旧栈,`docker ps -a` 查残留:`docker compose down` 不停 `profiles:` 后面的服务,重名会让新栈起不来。
3. 部署,按健康端点验证。
4. 失败只回滚本次涉及的服务;旧目录那份 compose 里可能有已归别的环境管理的服务,整份 `up -d` 会冲突。

## 实现上的约束(都撞过)

| 约束 | 结果 |
|---|---|
| runner 不允许特权容器 | 用 kaniko;dind 报 `mount: permission denied` |
| kaniko 已归档 | 上游不再发版,已锁 digest。四条替代路径都验过且各有代价(推荐的 BuildKit rootless 需要放开被刻意关闭的非特权用户命名空间)。原因、证据与待选项见 [BUILDER.md](BUILDER.md) |
| 项目级与组级都要求 GPG 签名提交 | CI 不能提交 git,所以版本记在部署机 `.env`,不写回仓库 |
| runner 拉私有命名空间镜像 | 组级 `DOCKER_AUTH_CONFIG`;kaniko 拉基础镜像也要凭据,所以只校验时也要写 `/kaniko/.docker/config.json` |
| 跨项目触发 | 本仓 job token 入站白名单里要有那个服务项目 |
| `needs` 依赖手动任务 | 该手动任务不能 `allow_failure: true`,否则下游任务不会被创建 |

## 为什么这样分

一条事实只放在拥有它的那一层:代码与构建输入在服务仓;构建、测试、部署的过程在本仓 `templates/`;
产物是带 commit 的镜像;哪台机器跑哪个版本在 `deploy/<环境>/`。由此得到:改一处部署流程只动一个地方
(迁移前五个仓各一份,部署任务各 32 行、只差 2 行);部署不重新构建,所以部署时的网络与基础镜像变化
不会改变行为;线上跑的东西能追到某个 commit。

触发部署的入口留在服务仓——共享的是过程与环境事实,不是入口。

不做灰度、多副本、跨机编排、按分支自动分环境。

## 待别人处理

| 事项 | 卡在哪 |
|---|---|
| 生产审计后端的推送密钥仍从旧部署目录挂载 | 私钥 `600 ezhgmio`,目录属 `ezhgmio:erpait` |
| 仓库里删不掉的一次性标签 | 推送账号无删除权限 |
| `services/test-env` 空目录 | 由部署任务以 `ezhgmio` 身份创建 |
