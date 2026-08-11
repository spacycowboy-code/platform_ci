# platform-ci

`rp-ai-miao` 下后端服务共用的构建配方、流水线模板,以及各环境的部署描述。
抽象、判据与实测结果见 [DESIGN.md](DESIGN.md)。

| 路径 | 归属 | 内容 |
|---|---|---|
| `docker/Dockerfile.base` | 平台 | 唯一的构建配方,构建成基础镜像 `proj-rvc/rp-ai/python-base` |
| `templates/python-service.yml` | 平台 | 服务流水线:lint / test / 构建并推不可变镜像 |
| `templates/web-service.yml` | 平台 | 前端:构建静态资源 + nginx 镜像,上游由环境注入 |
| `templates/extra-image.yml` | 平台 | 同一仓的第二个镜像,用仓里自带的 Dockerfile |
| `templates/deploy-env.yml` | 平台 | 环境部署:只拉镜像、不构建;可投放环境密钥 |
| `deploy/<env>/compose.yml` | 环境 | 这台机器上跑什么:镜像标签 + 主机绑定 |

## 服务仓怎么接

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v0.5.2                      # 锁 tag,不要用 main
    file: templates/python-service.yml
    inputs:
      service: product-structure-information
      app_module: hw_change_analysis.app:app
```

服务仓里**不需要** Dockerfile,也**不需要** compose。可选输入(都有默认值):
`python_version`、`base_tag`、`apt_packages`、`extra_pip`、`entrypoint`、`ruff_version`。
镜像标签就是 commit sha;在服务仓打 git tag 会额外推一个同名标签。

需要的变量已配在组级:`docker_user`、`docker_password`(推镜像),
`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`(部署)。

## 发布与回滚

合并到服务仓 `main` 后,流水线会推出 `proj-rvc/rp-ai/<service>:<version>-<sha>`,
并在日志末尾打印完整标签。

**发布** = 改 `deploy/<env>/compose.yml` 里那一行 `image:` 标签并合并。
部署任务按 `changes: deploy/<env>/**` 触发,只重建标签变化的服务。

**回滚** = revert 那次提交。不需要重新构建——产物是不可变镜像。

## 环境

| 环境 | 目录 | 宿主机目录 | 触发 |
|---|---|---|---|
| test | `deploy/test/` | `/proj/rp_ait_prod/services/test-env` | 自动 |
| staging-audit | `deploy/staging-audit/` | `/proj/rp_ait_prod/services/staging-audit-env` | 手动 |
| prod | `deploy/prod/` | `/proj/rp_ait_prod/services/prod-env` | 手动 |

生产环境里有 7 个服务描述,其中 `agent-artifex` 标了 `profiles: [stopped]`——它在这台机器上
本来就没在跑,如实记下而不顺手启动。密钥由部署任务从 File 型变量投放到环境目录
(`PSI_PROD_ENV`、`AUDIT_PROD_ENV`、`GRAFANA_PROD_ENV`),不进 git。

## 约定

- **锁版本**:消费方引用 tag。五个仓共用一份模板,不锁版本时一次误改会同时打断五条流水线。
  这一条已被验证:试点锁 `v0.2.0` 时,main 上的构建修复没有流过去。
- **标签不可覆盖**:标签含 commit sha;构建前先查,已存在则跳过。
- **容器内统一监听 8000**;宿主机端口写在环境描述里。
- **每个后端提供 `/health`**,探针不依赖数据库或外部系统。
- **默认值优先于必填**:只有真正属于服务身份的东西才是必填(镜像名、应用路径)。
- **部署不在服务仓**:服务仓没有 deploy 任务,发布是环境侧的动作。

## 切换旧栈时的顺序(踩过)

1. 先确认目标环境的部署任务处于可触发状态,**再**停运行中的容器。
2. 停旧栈后用 `docker ps -a` 确认没有残留容器——`docker compose down` 不会停 `profiles:`
   后面的服务,重名会让新栈起不来。
3. 回滚只针对本次涉及的服务;旧目录那份 compose 里可能还有已被别的环境接管的服务,
   整份 `up -d` 会因容器名冲突而失败。
