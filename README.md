# 后端服务的流水线

服务仓维护者只需要这一份。改动流水线本身见 [PLATFORM.md](PLATFORM.md)。

## 一次发布经过哪些环节

| 步 | 在哪发生 | 做什么 |
|---|---|---|
| 1 | 你的服务仓,自动 | `lint` / `test` / `build`。**`build` 只验证能不能构建,不推镜像** |
| 2 | 你的服务仓,你点按钮 | `deploy-prod`(仅 `main`)或 `deploy-test`(任意分支) |
| 3 | **GitLab runner 的容器里** | 用 kaniko 构建镜像并推到 Artifactory:`armdocker.rnd.ericsson.se/proj-rvc/rp-ai/<服务>:<commit sha>` |
| 4 | platform-ci 的下游流水线(点按钮后自动出现) | ssh 到部署机:同步该环境的 compose 与配置、投放密钥、把这个服务的版本写进部署机的 `.env` |
| 5 | 部署机 `100.77.162.66` | `docker compose pull` + `up -d`,**只重建这个服务的容器** |

构建发生在 runner 的容器里——不在你的机器上,也不在部署机上。部署机没有源码、不构建,只拉镜像。

第 4 步之所以跨项目:镜像属于"发布",谁在哪台机器跑哪个版本属于"环境"。生产的密钥、端口、卷都在
platform-ci,服务仓里一个都没有。

## 三件事

**上生产**:合并到 `main` → 在自己仓的流水线点 `deploy-prod`。

**上测试环境**:在开发分支的流水线点 `deploy-test`。测试环境(后端 `:9100`,前端 `:9101`)换成这个
分支的版本。它只有一套,谁点谁占,不用清理。

**回滚**:`git revert` 那次合并 → 再点一次 `deploy-prod`(会重新构建,约一分钟)。或者在 GitLab 的
Deployments → Environments → prod 里对上一次成功的部署点 Re-deploy,直接换回旧镜像。

## 你仓里的那个文件

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
可选输入三个:`apt_packages`、`extra_pip`、`ruff_select`。

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

## 出问题看这里

| 现象 | 原因与处理 |
|---|---|
| `build` 报 `dial tcp: lookup docker` | 引入了需要 docker 守护进程的写法。runner 不允许特权容器,构建走 kaniko |
| `build` 说"已存在,跳过构建" | 这个 commit 已有镜像。正常 |
| `test` 报 `ModuleNotFoundError` | 测试以 `包.tests.模块` 引用自己;模板已设 `PYTHONPATH`,仍失败查本仓 pytest 配置 |
| `test` 报缺 `httpx` 之类 | 测试依赖没声明,写进 `requirements-dev.txt` |
| 点了按钮但没部署 | 看下游流水线(bridge)的日志,失败通常在 ssh 或密钥变量 |
| 容器 unhealthy 但服务能访问 | 健康检查打了 `localhost`,会走 `::1`,而 nginx 只监听 IPv4;要写 `127.0.0.1` |
| 审计判定器变成"未登录",上传全转人工复核 | 登录态在命名卷里,卷名随 compose 项目目录变化;环境描述里的卷必须 `external` 且写死卷名 |
