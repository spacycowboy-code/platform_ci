# platform-ci

`rp-ai-miao` 下五个仓共用的构建配方、流水线模板,以及各环境的部署描述。
设计取舍见 [DESIGN.md](DESIGN.md)。

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

同一个仓要出第二个镜像,再 include 一次 `extra-image.yml`,给它 `dockerfile` 和另一个
`job_name`。

服务仓不需要 Dockerfile,也不需要 compose。可选输入都有默认值:`python_version`、
`base_tag`、`apt_packages`、`extra_pip`、`entrypoint`、`ruff_version`;前端另有
`node_version`、`nginx_version`、`build_command`。

测试依赖放本仓 `requirements-dev.txt`(存在就用它);要跳过哪些测试写本仓的 pytest 配置。

## 发布

合并到 `main` 后流水线推出 `proj-rvc/rp-ai/<service>:<commit sha>`,日志末尾打印完整标签。
同一个 commit 重跑不会覆盖已有标签。

发布分两步:

1. 取该标签的 digest。

   ```bash
   curl -sI -u "$user:$token" \
     -H "Accept: application/vnd.oci.image.manifest.v1+json" \
     https://armdocker.rnd.ericsson.se/v2/proj-rvc/rp-ai/<service>/manifests/<sha> \
     | grep -i docker-content-digest
   ```

2. 在 `deploy/<env>/compose.yml` 把镜像改成 `<repo>:<sha>@sha256:<digest>`,合并,触发该
   环境的部署任务。

回滚是 revert 那次提交再触发一次,不重新构建。

## 环境

| 环境 | 目录 | 宿主机目录 | 触发 |
|---|---|---|---|
| 验收 | `deploy/staging-audit/` | `/proj/rp_ait_prod/services/staging-audit-env` | 手动 |
| 生产 | `deploy/prod/` | `/proj/rp_ait_prod/services/prod-env` | 手动 |

环境目录只放 `compose.yml` 和 grafana 这类配置文件。镜像只写 `image:`,不写 `build:`。
生产描述里 `agent-artifex` 带 `profiles: [stopped]`,部署不会启动它;删掉那两行才会启动。

## 凭据

| 位置 | 内容 |
|---|---|
| 组级 | `docker_user` / `docker_password` 推镜像;`DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY` 部署;`NPM_AUTH_B64` 前端私有 registry |
| 本项目级 | 各环境 env 文件(File 型):`PSI_PROD_ENV`、`AUDIT_PROD_ENV`、`GRAFANA_PROD_ENV`、`AUDIT_STAGING_ENV` |
| 服务项目级 | 空 |

部署任务按 `env_files: "目标文件名=CI变量名"` 把 env 文件投放到环境目录,不进 git。

## 把一个服务从旧方式切到环境描述

按顺序做,每步都验证:

1. 确认该环境的部署任务处于可触发状态(手动任务在同一条流水线里只能触发一次)。
2. 停旧栈,然后 `docker ps -a` 检查残留:`docker compose down` 不停 `profiles:` 后面的
   服务,重名容器会让新栈起不来。
3. 触发部署任务,按服务的健康端点验证。
4. 失败时只回滚本次涉及的服务。旧目录那份 compose 里可能有已归别的环境管理的服务,整份
   `up -d` 会因容器名冲突失败。

改动模板里的校验或保护逻辑后,把所有消费方的 `ref` 一并升上去;锁在旧版本的消费方拿不到
新增的保护。
