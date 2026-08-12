# platform-ci

`rp-ai-miao` 下五个仓共用的构建配方、流水线模板,以及各环境的部署描述。

本文是操作手册。为什么这样设计见 [DESIGN.md](DESIGN.md);出过什么事见
[INCIDENTS.md](INCIDENTS.md)。

## 服务仓怎么接

后端:

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v0.10.1                     # 锁 tag,不要用 main
    file: templates/python-service.yml
    inputs:
      service: product-structure-information
      app_module: hw_change_analysis.app:app
```

前端:

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v0.10.1
    file: templates/web-service.yml
    inputs:
      service: rp-ai-portal-fe
      context: ai-portal-frontend
      dist_dir: dist/ai-portal-frontend/browser
```

同一个仓要出第二个镜像(配方与薄层实质不同时),再 include 一次 `extra-image.yml`,
给它 `dockerfile` 和一个不同的 `job_name`。

服务仓里**不需要** Dockerfile,也**不需要** compose。可选输入都有默认值:
`python_version`、`base_tag`、`apt_packages`、`extra_pip`、`entrypoint`、`ruff_version`;
前端另有 `node_version`、`nginx_version`、`build_command`。

测试依赖放本仓的 `requirements-dev.txt`(存在就用它);要跳过哪些测试写本仓的 pytest 配置。

## 发布与回滚

合并到 `main` 后流水线推出 `proj-rvc/rp-ai/<service>:<commit sha>`,日志末尾打印完整标签。
同一个 commit 重跑不会覆盖已有标签。

**发布** = 在 `deploy/<env>/compose.yml` 改镜像那一行,写成 `:<sha>@sha256:<digest>`,合并后
触发该环境的部署任务。digest 可以这样取:

```bash
curl -sI -u "$user:$token" \
  -H "Accept: application/vnd.oci.image.manifest.v1+json" \
  https://armdocker.rnd.ericsson.se/v2/proj-rvc/rp-ai/<service>/manifests/<sha> \
  | grep -i docker-content-digest
```

**回滚** = revert 那次提交再触发一次。不需要重新构建。

## 环境

| 环境 | 目录 | 宿主机目录 | 触发 |
|---|---|---|---|
| 验收 | `deploy/staging-audit/` | `/proj/rp_ait_prod/services/staging-audit-env` | 手动 |
| 生产 | `deploy/prod/` | `/proj/rp_ait_prod/services/prod-env` | 手动 |

环境目录里只放 `compose.yml`(以及 grafana 这类纯配置文件)。镜像只写 `image:`,不写
`build:`——服务器上不需要源码。生产描述里 `agent-artifex` 标了 `profiles: [stopped]`:
它本来就没在跑,如实记下而不顺手启动。

## 凭据放在哪

| 位置 | 内容 |
|---|---|
| 组级(五个仓共享) | `docker_user` / `docker_password`(推镜像)、`DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY`(部署)、`NPM_AUTH_B64`(前端私有 registry) |
| 本项目级 | 各环境的 env 文件:`PSI_PROD_ENV`、`AUDIT_PROD_ENV`、`GRAFANA_PROD_ENV`、`AUDIT_STAGING_ENV`(File 型) |
| 五个服务项目 | **空**,不需要任何变量 |

部署任务按 `env_files: "目标文件名=CI变量名"` 把 env 文件投放到环境目录,不进 git。

## 切换旧栈时的顺序(都踩过,见 INCIDENTS)

1. 先确认目标环境的部署任务处于可触发状态,**再**停运行中的容器。
2. 停旧栈后用 `docker ps -a` 确认没有残留——`docker compose down` 不会停 `profiles:`
   后面的服务,重名会让新栈起不来。
3. 回滚只针对本次涉及的服务。旧目录那份 compose 里可能还有已被别的环境接管的服务,
   整份 `up -d` 会因容器名冲突而失败。
4. 改动模板里的保护类逻辑后,立刻把所有消费方升上去;锁住旧版本的消费方拿不到保护。
