# 维护这套流水线本身

日常发布、回滚、排查看 [README.md](README.md)。这份是给要**改动流水线或加新服务**的人看的。
设计取舍见 [DESIGN.md](DESIGN.md)。

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

服务仓不需要 Dockerfile,也不需要 compose。除必填的两项外,只有三个可选输入:

| 输入 | 什么时候用 |
|---|---|
| `apt_packages` | 镜像里需要额外系统包 |
| `extra_pip` | 镜像里需要装 pip 之外的工具 |
| `ruff_select` | 想让更多 lint 规则阻塞流水线(默认只阻塞语法错误与未定义名字) |

Python 版本、基础镜像、ruff 版本都由平台决定,不是输入:它们同时出现在 lint、test 和构建里,
分开设置只会出现"测试用一个解释器、镜像里是另一个"这种不一致。

启动前要先做事(例如跑数据库迁移)的服务,在仓库根目录放一个 `docker-entrypoint.sh`,构建时会
自动把它设成容器入口,不需要任何配置。

测试依赖写本仓 `requirements-dev.txt`(有就用它);要跳过哪些测试写本仓的 pytest 配置。

服务还要提供 `/health`,返回 200 即可,不要依赖数据库或外部系统——它是容器健康检查用的。

接完后新增一个环境条目:在 `deploy/<环境>/compose.yml` 里加一段 `services:`,写镜像、
`container_name`、宿主机端口、要挂的目录。容器里统一监听 8000。

## 切换一个还在用旧方式部署的服务

按顺序,每步验证:

1. 先确认目标环境的部署任务可触发(手动任务在同一条流水线里只能点一次)。
2. 停旧栈,`docker ps -a` 确认无残留。
3. 点部署任务,按健康端点验证。
4. 失败时只回滚本次涉及的服务;旧目录那份 compose 里可能有已归别的环境管理的服务,整份
   `up -d` 会因容器名冲突失败。

## 本仓自己的版本号

服务仓锁 `ref`,所以这里的 tag 必须对消费方有意义:

- **只有影响消费方的改动才发版本**:模板的行为、输入、保护逻辑,或基础镜像换代。
- **只改文档、注释、或本仓自己的流水线,不发版本。**
- 同一处的反复修正合并成一个版本再发,不要每次编辑一个 tag。

已发的 tag 不移动、不删除。前 29 个 tag 是重构期间边做边发的,里面有只改文档的,也有修正上一个
tag 的;从 `v0.14.0` 起按上面三条执行。

## 生产必须先过验收

`deploy-prod` 声明了 `needs: deploy-staging-audit(optional)`:

- 一次改动同时动了验收和生产的描述 → 验收部署成功后才允许部生产,验收失败则生产不可触发。
- 只动了生产描述(例如只给生产换一个镜像标签) → 验收任务不在这条流水线里,不阻塞。

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
