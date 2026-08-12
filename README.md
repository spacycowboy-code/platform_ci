# 后端服务的流水线

服务仓维护者只需要这一份。

> 本文描述的是已对齐的目标状态。截至最后一次修改,`deploy-test` / `deploy-prod` 两个按钮尚未
> 加进模板(见 [MAINTAINING.md](MAINTAINING.md) 末尾的"待实现")。在那之前发布方式仍是手工改
> 环境描述里的镜像标签。

## 三件事

**上生产**:改动合并到 `main`,在自己仓的流水线里点一下 `deploy-prod`。

**上测试环境**:在你的开发分支上,点一下 `deploy-test`。测试环境(`:9100` 后端 / `:9101` 前端)
就换成你这个分支的版本。同时只有一个分支能占用它,谁点谁占,用完不用清理——下一个人点的时候
就顶掉了。

**回滚**:`git revert` 那次合并,再点一次 `deploy-prod`。

生产只可能来自 `main`;测试环境给分支用。除此之外没有别的环境。

流水线在所有分支上都自动跑 `lint`、`test`、`build`,但 `build` **只校验能不能构建,不推镜像**。
镜像在你点部署按钮时才产出并推上去,所以只有真正部署过的版本才有镜像。同一个 commit 第二次
部署会跳过构建,直接用已有的那个镜像——分支上验过的产物和后来上生产的是同一个。

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
