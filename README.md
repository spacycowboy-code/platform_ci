# platform-ci

`rp-ai-miao` 下后端服务共用的流水线、镜像定义与 compose 骨架。

| 文件 | 作用 |
|---|---|
| `templates/python-service.yml` | lint + test + deploy 流水线,带 `spec:inputs` |
| `docker/Dockerfile.python` | 唯一的后端镜像定义,参数由 compose 的 build.args 传入 |
| `docker/entrypoint.sh` | 入口分发:仓库有 `docker-entrypoint.sh` 就用它,否则直起 uvicorn |
| `compose/backend.base.yml` | 单服务 compose 骨架,消费仓只覆盖端口与挂载 |
| `PORTS.md` | 宿主机端口登记 |

## 消费方式

消费仓 `.gitlab-ci.yml`:

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v0.1.0                      # 锁 tag,不要用 main
    file: templates/python-service.yml
    inputs:
      app_module: "api:app"
      rsync_excludes: '--exclude "data/"'
```

`docker/` 与 `compose/` 里的文件由部署任务在同步前取到消费仓的 `.platform/`
目录,因此镜像仍在服务器上构建,消费仓不保留这些文件的副本。

## 约定

- **锁版本**:消费方引用 tag。五个仓共用一份模板,不锁版本时一次误改会同时打断五条流水线。
- **默认值优先于参数**:能有合理默认的就给默认(Python 3.12、`/health`、`git`),
  只有真正属于服务身份的东西才作为输入(应用路径、端口、额外依赖)。
- **私有的留在私有仓**:端口、数据卷、密钥、该服务自己的测试排除项、以及多服务
  编排(如 rp-ai-portal-be 的 judge / reconciler / db)。
- **变量不上提到组级**:`DEPLOY_*`、`PROD_ENV`、`NPM_AUTH_B64` 都是各项目私有。

## 已知边界

- 镜像在服务器上构建:Artifactory 仓库 `proj-rvc-docker-global` 目前只有读权限
  (`rvccie` 账号推送返回 `User is unauthorized to upload`)。拿到写权限后,
  `compose/backend.base.yml` 里的 `build` 换成 `image:` 即可,回滚也随之变成换标签。
- `rp-ai-portal-be` 需要 pipx 安装的 cisco skill-scanner,当前仍用自己的 Dockerfile;
  其余三个后端共用本仓的定义。
