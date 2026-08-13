# 后端服务的流水线

服务仓维护者只需要这一份。

## 三件事

**上生产**:改动合并到 `main`,在自己仓的流水线里点一下 `deploy-prod`。

**上测试环境**:在你的开发分支上,点一下 `deploy-test`。测试环境(`:9100` 后端 / `:9101` 前端)
就换成你这个分支的版本。同时只有一个分支能占用它,谁点谁占,用完不用清理——下一个人点的时候
就顶掉了。

**回滚**:`git revert` 那次合并,再点一次 `deploy-prod`(会重新构建,约一分钟)。急的时候也可以在
GitLab 的 Deployments → Environments → prod 里对上一次成功的部署点 Re-deploy,直接换回旧镜像。

生产只可能来自 `main`;测试环境给分支用。除此之外没有别的环境。

流水线在所有分支上都自动跑 `lint`、`test`、`build`,但 `build` **只校验能不能构建,不推镜像**。
镜像在你点部署按钮时才产出并推上去,所以只有真正部署过的版本才有镜像。同一个 commit 第二次
部署会跳过构建,直接用已有的那个镜像——分支上验过的产物和后来上生产的是同一个。

## 点一下之后发生了什么

五个角色:你的服务仓 → GitLab 流水线 → Artifactory(镜像仓库)→ platform-ci 的流水线 → 部署机。

```
你的服务仓                                  platform-ci              部署机
─────────                                  ───────────              ──────
合并到 main
   │
   ├─ 自动:lint / test / build(只验证能不能构建,不推镜像)
   │
   └─ 你点 deploy-prod ──构建并推镜像──▶ Artifactory
             │                        proj-rvc/rp-ai/<服务>:<commit sha>
             │
             └─ 自动:apply-prod ──触发下游流水线──▶ apply 任务
                     (传 环境/服务/版本)              │
                                                     ├─ 同步 deploy/prod/(只有 compose 与配置,无源码)
                                                     ├─ 投放密钥文件(来自 CI 的 File 变量)
                                                     ├─ 改部署机 .env 里这个服务的版本
                                                     └─ compose pull + up -d ──▶ 只重建这个服务
```

前端上生产的实际过程:合并后 commit 是 `e70a3471` → 点 `deploy-prod` → 推出
`rp-ai-portal-fe:e70a3471` → `apply-prod` 触发 platform-ci → 部署机 `.env` 写入
`RP_AI_PORTAL_FE_TAG=e70a3471` → 只有前端容器被重建,`:9001` 换成新版本。

| 容易困惑的点 | 答案 |
|---|---|
| 为什么点完跳到另一个项目的流水线 | 镜像属于"发布",谁在哪台机器跑哪个版本属于"环境"。密钥、端口、卷都在 platform-ci,服务仓里一个都没有 |
| 为什么一个按钮是两个任务 | `deploy-*` 要用构建镜像来构建,`apply-*` 要触发另一个项目,GitLab 里是两种任务类型,合不了。点第一个,第二个自动跟上 |
| 镜像存在哪 | Artifactory(`armdocker.rnd.ericsson.se`),不是 GitLab 自带的镜像仓库 |
| 谁决定重建哪些容器 | `deploy/<环境>/services.txt` 里"镜像名 → compose 服务"的映射;缺映射直接失败 |
| 线上跑哪个版本记在哪 | 部署机环境目录下的 `.env`,每个服务一行 |
| 部署机上有源码吗 | 没有。只有 compose、配置文件和投放的密钥;部署只拉镜像,不构建 |

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

前端仓多两项:`context`(前端工程子目录)、`dist_dir`(构建产物目录)。

其余可选输入三个:`apt_packages`(额外系统包)、`extra_pip`(镜像里装额外工具)、
`ruff_select`(让更多 lint 规则阻塞流水线)。

## 三条约定

| 约定 | 为什么 |
|---|---|
| 提供 `GET /health`,返回 200,不查数据库 | 容器健康检查用它。查数据库的话,探针失败就分不清是服务挂了还是依赖挂了 |
| 测试依赖写 `requirements-dev.txt` | 流水线有这个文件就装它 |
| 启动前要跑迁移之类,放 `docker-entrypoint.sh` 在仓库根目录 | 构建时会自动设成容器入口,不用配置 |

## 看现在线上跑的是哪个版本

在部署机上 `cat /proj/rp_ait_prod/services/prod-env/.env`,里面每个服务一行,值就是 commit sha。
测试环境同理,目录换成 `test-env`。GitLab 的 Environments 页面另有一份部署历史(谁、什么时候、
哪个版本)。

## 端口、环境变量、挂载目录在哪改

在 `deploy/<环境>/compose.yml`,不在服务仓里。那份文件只写这台机器上怎么跑:镜像、端口、卷、
env 文件。

密钥也在那边(GitLab 的 File 型变量,由部署任务投放到环境目录),不进 git。

## 出问题看这里

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `build` 报 `dial tcp: lookup docker` | 引入了需要 docker 守护进程的写法 | 本组 runner 不允许特权容器,构建走 kaniko |
| `build` 说"标签已存在,跳过构建" | 这个 commit 已经有镜像 | 正常。要新产物就提交新改动 |
| `test` 报 `ModuleNotFoundError` | 测试以 `包.tests.模块` 引用自己 | 模板已设 `PYTHONPATH`;仍失败检查本仓 pytest 配置 |
| `test` 报缺 `httpx` 之类 | 测试依赖没声明 | 写进 `requirements-dev.txt` |
| `build` 通过但仓库里没有镜像 | `build` 只校验,不推送 | 点 `deploy-test` 或 `deploy-prod`,镜像在那时产出 |
| 容器一直 unhealthy 但服务能访问 | 健康检查打了 `localhost`,解析到 `::1`,nginx 只监听 IPv4 | 健康检查要写 `127.0.0.1` |
| 审计判定器变成"未登录",所有上传转人工复核 | 判定器登录态在命名卷里,卷名随 compose 项目目录变化 | 环境描述里的卷必须 `external` 且写死卷名 |
| 部署任务报某个变量"未设置" | 少了 CI 变量 | 找我,组级要有 `docker_user`/`docker_password`/`DEPLOY_*` |

改动这套流水线本身:见 [MAINTAINING.md](MAINTAINING.md)。为什么这样分:见 [DESIGN.md](DESIGN.md)。
