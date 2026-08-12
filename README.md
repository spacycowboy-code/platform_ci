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

### 把一个还没合并的分支放到验收环境

1. 在服务仓的该分支上跑一次流水线,并带上变量 `BUILD_IMAGE=1`(GitLab 界面:
   Build → Pipelines → Run pipeline,选分支,加一条变量)。这次会推出镜像
   `proj-rvc/rp-ai/<服务>:<该分支 HEAD 的 sha>`。
2. 把 `deploy/staging-audit/compose.yml` 里对应服务的标签改成那个 sha 并合并,验收环境自动应用。

MR 流水线只**校验**镜像能不能构建,不推送:镜像仓库里的标签删不掉(推送账号没有删除权限),
每个 MR 提交都推一个会让仓库无限累积。要产物就显式带 `BUILD_IMAGE=1`。

验收环境只有一套,同时只能跑一个版本。多人要用就先说一声——这里靠人协调,没有按分支自动
分环境的机制(单机、两三个人,按分支起环境要维护动态端口、目录和清理,不值得)。用完把标签
改回 main 上的版本。

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

## 为什么这么分

短版:一条事实只放在一个地方,改一次只动一处;镜像构建一次就不再变,部署只是选择跑哪一个。
完整推导、判据和已核实的约束见 [DESIGN.md](DESIGN.md)。
