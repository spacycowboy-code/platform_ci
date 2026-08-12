# platform-ci

`rp-ai-miao` 下所有服务的**流水线与部署入口**。要做什么,跳到对应小节。

## 我要……

| 我要做的事 | 看哪一节 |
|---|---|
| 把合并好的改动发到验收 / 生产 | [发布](#发布) |
| 把线上退回上一个版本 | [回滚](#回滚) |
| 知道现在线上跑的是哪个版本 | [查现在跑的是什么](#查现在跑的是什么) |
| 改端口、环境变量、挂载目录 | [改环境](#改环境) |
| 让一个新服务用上这套流水线 | [接入新服务](#接入新服务) |
| 流水线或部署失败了 | [排查](#排查) |

## 全貌

```
服务仓                              platform-ci(本仓)                  机器
──────                              ──────────────────                  ────
.gitlab-ci.yml ──include──▶ templates/python-service.yml
      │                                    │
   合并到 main                        构建 + 推镜像
      └───────────────────────▶ proj-rvc/rp-ai/<服务>:<commit sha>
                                           │
                        改 deploy/<环境>/compose.yml 里的镜像标签
                                           │
                                     触发部署任务 ──ssh──▶ docker compose pull + up -d
```

三件事分开放:

- **代码、依赖、构建输入** 在各服务仓
- **构建 / 测试 / 部署的过程** 在本仓 `templates/`
- **哪台机器跑哪个版本、端口、卷** 在本仓 `deploy/<环境>/`

服务器上没有源码,部署只拉镜像、不构建。

## 发布

1. 改动合并到服务仓的 `main`,流水线自动构建并推镜像。取 tag:看该流水线 `build` 任务日志
   最后一行,形如
   `推送完成:armdocker.rnd.ericsson.se/proj-rvc/rp-ai/<服务>:<sha>`。也可以直接用那次合并的
   commit 短 sha。

2. 在本仓改环境描述里的镜像标签,例如把验收环境的审计后端换成新版本:

   ```diff
   # deploy/staging-audit/compose.yml
   -    image: armdocker.rnd.ericsson.se/proj-rvc/rp-ai/rp-ai-portal-be:f33b3587
   +    image: armdocker.rnd.ericsson.se/proj-rvc/rp-ai/rp-ai-portal-be:5f9660e8
   ```

3. 合并到本仓 `main`,到本仓流水线里点该环境的部署任务(`deploy-staging-audit` 或
   `deploy-prod`,都是手动)。

4. 部署日志末尾会列出每个容器实际跑的镜像和 image id,核对一下就完成了。

一次部署只会重建标签变了的容器,同一环境里其它服务不动。

## 回滚

`git revert` 上一步那次提交,合并,再点一次同一个部署任务。不需要重新构建,也不需要找旧
镜像——标签还在仓库里。

## 查现在跑的是什么

```bash
ssh <部署机>
docker inspect <容器名> --format '{{.Config.Image}} {{.Image}}'
```

前一个是环境描述里写的标签(标签里就是 commit sha),后一个是实际镜像 id。历史记录在本仓
`deploy/<环境>/compose.yml` 的 git log 和部署任务的日志里。

## 改环境

端口、环境变量、挂载目录、要不要跑某个服务,都在 `deploy/<环境>/compose.yml`。改完合并、
点部署任务。

| 环境 | 文件 | 机器上的目录 |
|---|---|---|
| 验收 | `deploy/staging-audit/compose.yml` | `/proj/rp_ait_prod/services/staging-audit-env` |
| 生产 | `deploy/prod/compose.yml` | `/proj/rp_ait_prod/services/prod-env` |

密钥不写在这里。它们是 File 型 CI 变量,由部署任务投放到环境目录:

| 变量 | 落地成 |
|---|---|
| `PSI_PROD_ENV` | `product-structure-information.env` |
| `AUDIT_PROD_ENV` | `ai-portal-be.env` |
| `GRAFANA_PROD_ENV` | `grafana.env` |
| `AUDIT_STAGING_ENV` | `audit-staging.env` |

要加一个,在部署任务的 `env_files` 里加一项 `目标文件名=变量名`。

## 接入新服务

在服务仓写一个 `.gitlab-ci.yml`,内容只有 include 和几个输入。后端:

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v0.12.0                     # 锁版本,不要写 main
    file: templates/python-service.yml
    inputs:
      service: 服务名               # 也是镜像名
      app_module: 包.模块:app       # uvicorn 启动目标
```

前端:

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v0.12.0
    file: templates/web-service.yml
    inputs:
      service: 服务名
      context: 前端工程所在子目录
      dist_dir: 构建产物目录
```

服务仓不需要 Dockerfile,也不需要 compose。其余输入都有默认值,按需覆盖:

| 输入 | 默认 | 什么时候要改 |
|---|---|---|
| `python_version` | `3.12` | 依赖不兼容 3.12 |
| `apt_packages` | 空 | 需要额外系统包 |
| `extra_pip` | 空 | 需要在镜像里装 pip 之外的工具 |
| `entrypoint` | 空 | 启动前要先做事(如跑数据库迁移) |
| `ruff_select` | `E9,F63,F7,F82` | 想让更多 lint 规则阻塞流水线 |
| `base_tag` | 固定标签 | 平台发了新基础镜像 |

测试依赖写本仓 `requirements-dev.txt`(有就用它);要跳过哪些测试写本仓的 pytest 配置。

服务还要提供 `/health`,返回 200 即可,不要依赖数据库或外部系统——它是容器健康检查用的。

接完后新增一个环境条目:在 `deploy/<环境>/compose.yml` 里加一段 `services:`,写镜像、
`container_name`、宿主机端口、要挂的目录。容器里统一监听 8000。

## 排查

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `build` 报 `dial tcp: lookup docker` | 用了需要 docker 守护进程的写法 | 本组 runner 不允许特权容器,构建走 kaniko,别引入 `docker build` |
| `build` 打印"标签已存在,跳过构建" | 这个 commit 已经有镜像了 | 正常。要新产物就提交新改动 |
| `test` 报 `ModuleNotFoundError` | 测试以 `包.tests.模块` 引用自己 | 模板已设 `PYTHONPATH`;仍失败就检查本仓 pytest 配置 |
| `test` 报缺 `httpx` 之类 | 测试依赖没声明 | 写进本仓 `requirements-dev.txt` |
| 部署任务报 `未设置` | 少了 CI 变量 | 组级要有 `docker_user`/`docker_password`/`DEPLOY_*` |
| 新栈起不来,报容器名冲突 | 旧栈里 `profiles:` 后面的服务没被 `docker compose down` 停掉 | 在机器上 `docker ps -a` 找到残留,删掉再部署 |
| 容器一直 unhealthy 但服务能访问 | 健康检查打了 `localhost`,解析到 `::1`,而 nginx 只监听 IPv4 | 健康检查写 `127.0.0.1` |
| 审计判定器变成"未登录",所有上传转人工复核 | 判定器登录态在命名卷里,卷名随 compose 项目目录变化 | 环境描述里的卷必须是 `external` 并写死卷名 |
| 同一个标签的内容变了 | 服务仓锁的模板版本早于标签保护 | 把该仓 `.gitlab-ci.yml` 的 `ref` 升上去 |
| 改了模板但没生效 | 服务仓锁着旧版本 | 升 `ref`;改保护类逻辑时要一次升完所有仓 |

## 切换一个还在用旧方式部署的服务

按顺序,每步验证:

1. 先确认目标环境的部署任务可触发(手动任务在同一条流水线里只能点一次)。
2. 停旧栈,`docker ps -a` 确认无残留。
3. 点部署任务,按健康端点验证。
4. 失败时只回滚本次涉及的服务;旧目录那份 compose 里可能有已归别的环境管理的服务,整份
   `up -d` 会因容器名冲突失败。

## 文件在哪

| 路径 | 作用 |
|---|---|
| `templates/python-service.yml` | 后端流水线:lint / test / 构建推镜像 |
| `templates/web-service.yml` | 前端流水线:构建静态资源 + nginx 镜像 |
| `templates/extra-image.yml` | 同一个仓的第二个镜像(用仓里自带的 Dockerfile) |
| `templates/deploy-env.yml` | 环境部署任务 |
| `templates/_build-common.yml` | 上面三个构建模板共用的凭据与标签保护 |
| `docker/Dockerfile.base` | 所有 Python 服务的基础镜像配方 |
| `deploy/prod/`、`deploy/staging-audit/` | 各环境跑什么 |
| `.gitlab-ci.yml` | 本仓自己的流水线:校验模板、发基础镜像、部署各环境 |

## 为什么这么分

短版:一条事实只放在一个地方,改一次只动一处;镜像构建一次就不再变,部署只是选择跑哪一个。
完整推导、判据和已核实的约束见 [DESIGN.md](DESIGN.md)。
