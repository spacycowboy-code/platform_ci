# 后端服务的流水线

服务仓维护者只需要这一份:怎么接进来、怎么发布回滚、出问题看哪。

运维那一侧(改流水线本身、集群、备份恢复、grafana)见 [PLATFORM.md](PLATFORM.md);
每个密钥配在哪、到期、失效了什么会坏见 [CREDENTIALS.md](CREDENTIALS.md);
构建器为什么是现在这个见 [BUILDER.md](BUILDER.md);需求清单逐条到哪了见 [ROADMAP.md](ROADMAP.md)。

## 镜像仓库

镜像推到我们自己的空间:

```
armdocker.rnd.ericsson.se/proj-rp-ps-ai/<服务>:<commit sha>
```

生产与测试在跑的每个标签在这个空间里都有。旧空间 `proj-rvc/rp-ai/`(RVC 项目的)不再使用。

推镜像用的账号不是服务账号 —— 口令一变所有仓的构建与部署同时断,见 [CREDENTIALS.md](CREDENTIALS.md)。

## 接一个新服务

改动分在两个仓。只改服务仓的话,流水线能跑、镜像能推,但**不会部署** —— 服务还没出现在环境描述里。
平台仓这一半需要 Maintainer 权限,服务仓的维护者提 MR 或找 platform-ci 的维护者。

**服务仓侧**

1. `.gitlab-ci.yml` 写 include(格式见下面「服务仓里的那个文件」)。
2. 容器内监听 **8000**。
3. 提供一个**免鉴权**的健康端点。路径不必是 `/health` —— `eridoc-client` 用的是 `/api/health`,
   探测哪个路径写在环境描述里,服务本身不用改。

**平台仓侧(每个要上的环境都要做一遍)**

| 改哪 | 加什么 |
|---|---|
| `deploy/<环境>/stack.yml`(集群)或 `compose.yml`(单机) | 一段 `services:`:镜像、宿主机端口、挂载、`healthcheck`。多副本再加 `deploy.replicas` 和 `placement.max_replicas_per_node: 1` |
| `deploy/<环境>/services.txt` | 一行 `<镜像名>: swarm` 或 `<镜像名>: compose <compose 服务名…>`。这一行是"部署这个镜像在这个环境里意味着什么"的唯一依据 |
| `deploy/<环境>/env-files.txt` | 要密钥或配置才加:`<文件名>.env=<CI 变量名>[=swarm]`,变量本身在 platform-ci 的 CI 变量里建(File 类型) |

MR 里需要说明的就是这几项:服务名(也是镜像名)、要上哪些环境、宿主机端口、健康端点路径、
要不要挂目录、要哪些环境变量或密钥、要几个副本。

细节和踩过的坑见 [PLATFORM.md](PLATFORM.md)。

## 一次发布经过哪些环节

| 步 | 在哪发生 | 做什么 |
|---|---|---|
| 1 | 服务仓,自动 | `lint` / `test` / `build`。**`build` 只验证能不能构建,不推镜像** |
| 2 | 服务仓,手动点按钮 | `deploy-prod`(仅 `main`)或 `deploy-test`(任意分支) |
| 3 | **GitLab runner 的容器里** | 用 kaniko 构建镜像并推到 Artifactory:`armdocker.rnd.ericsson.se/proj-rp-ps-ai/<服务>:<commit sha>` |
| 4 | platform-ci 的下游流水线(点按钮后自动出现) | ssh 到部署机:同步该环境的 compose 与配置、投放密钥、把这个服务的版本写进部署机的 `.env` |
| 5 | 部署机 `100.77.162.66` | `docker compose pull` + `up -d`,**只重建这个服务的容器** |

构建发生在 runner 的容器里,不在开发机上,也不在部署机上。部署机没有源码、不构建,只拉镜像。

第 4 步之所以跨项目:镜像属于"发布",谁在哪台机器跑哪个版本属于"环境"。生产的密钥、端口、卷都在
platform-ci,服务仓里一个都没有。

## 三件事

**上生产**:合并到 `main` → 在自己仓的流水线点 `deploy-prod`。

**上测试环境**:在开发分支的流水线点 `deploy-test`。测试环境(后端 `:9100`,前端 `:9101`)换成这个
分支的版本。它只有一套,谁点谁占,不用清理。

**回滚**:`git revert` 那次合并 → 再点一次 `deploy-prod`(会重新构建,约一分钟)。或者在 GitLab 的
Deployments → Environments → prod 里对上一次成功的部署点 Re-deploy,直接换回旧镜像。

## 服务仓里的那个文件

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v1                        # 固定写 v1,不用改
    file: templates/service.yml
    inputs:
      service: my-service          # 也是镜像名
      app_module: app.main:app     # uvicorn 启动目标
```

前端仓多两项:`kind: web`、`context`(前端工程子目录)、`dist_dir`(构建产物目录)。
可选输入五个:`apt_packages`、`extra_pip`、`ruff_select`、`extra_dockerfile`、`extra_service`。

## 三条约定

| 约定 | 为什么 |
|---|---|
| 提供 `GET /health`,返回 200,不查数据库 | 容器健康检查用它。查数据库会分不清是服务挂了还是依赖挂了 |
| 测试依赖写 `requirements-dev.txt` | 有这个文件就装它 |
| 启动前要跑迁移之类,放 `docker-entrypoint.sh` 在仓库根目录 | 构建时自动设为容器入口 |

## 线上跑的是哪个版本

部署机上 `cat /proj/rp_ait_prod/services/prod-env/.env`,每个服务一行,值是 commit sha。
测试环境同理,目录换成 `test-env`。GitLab 的 Environments 页面另有一份部署历史。

## 端口、环境变量、挂载目录

在 `deploy/<环境>/compose.yml`,不在服务仓。密钥是 File 型 CI 变量,由部署任务投放到环境目录,
不进 git。

服务的新版本要多一个环境变量时,要把它加进那个环境的 File 变量(整份覆盖,所以是在里面加一行),
并在环境描述里注释一句它是干什么的 —— 见 [PLATFORM.md 的「服务要多一个环境变量」](PLATFORM.md)。
**别让漏配表现为功能静默失效**:能启动、但某个功能一直报错,是最难从现象猜到原因的一种。

## 出问题看这里

| 现象 | 原因与处理 |
|---|---|
| `build` 报 `docker_user 未设置`,或 `deploy` 失败在 `docker login` | 组的 CI 变量里缺 `docker_user` / `docker_password` / `DOCKER_AUTH_CONFIG`。见 [CREDENTIALS.md](CREDENTIALS.md) |
| `build` 报 `dial tcp: lookup docker` | 引入了需要 docker 守护进程的写法。runner 不允许特权容器,构建走 kaniko |
| `build` 说"已存在,跳过构建" | 这个 commit 已有镜像。正常 |
| `test` 报 `ModuleNotFoundError` | 测试以 `包.tests.模块` 引用自己;模板已设 `PYTHONPATH`,仍失败查本仓 pytest 配置 |
| `test` 报缺 `httpx` 之类 | 测试依赖没声明,写进 `requirements-dev.txt` |
| 点了按钮但没部署 | 看下游流水线(bridge)的日志,失败通常在 ssh 或密钥变量 |
| 容器 unhealthy 但服务能访问 | 健康检查打了 `localhost`,会走 `::1`,而 nginx 只监听 IPv4;要写 `127.0.0.1` |
| 审计判定器变成"未登录",上传全转人工复核 | 登录态在命名卷里,卷名随 compose 项目目录变化;环境描述里的卷必须 `external` 且写死卷名 |

## 测试环境在集群上(2026-08-14 起)

测试环境的入口仍然是 `http://100.77.162.66:9101`,地址没变,但后面已经全部换成集群:

| 服务 | 副本 | 在哪 |
|---|---|---|
| 入口(Traefik) | 1 | 调度决定 |
| 前端 | 2 | 两台各一 |
| 数据库 / 判定器 | 各 1 | **钉在 100.77.162.66**:数据库的数据在它的硬盘上,判定器的登录态在它的命名卷里 |
| 审计后端 | 1 | 调度决定。2026-08 已解钉 —— 内容改成按引用读写后它没有本地状态,验证过调度到另一台照常工作 |
| 审计引擎(`skill-audit`) | 2 | 两台各一(无状态) |
| reconciler | 1 | 调度决定 |

登录:测试环境**没有配 AD 凭据**,用应用内置的本地管理员登录(见 portal-be 的
`LOCAL_ADMIN_*` 配置)。用真实 AD 账号登录会 503,那是缺配置,不是故障。

生产仍然是单机 compose,入口 `:9001`,一个容器都没动。

