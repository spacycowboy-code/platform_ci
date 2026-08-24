# 需求逐条对照

对着最初那份需求清单逐条写:现在到哪了、**怎么实现的**、还差什么。

状态标记:✅ 完成 · 🟡 部分 · 🔴 没做 · ⬜ 明确列为 backlog

---

## 微服务代码组织

### ✅ 前端一个仓库,后端多个仓库

7 个服务仓:`rp-ai-portal-fe`(前端)、`rp-ai-portal-be`(门户/技能审计后端)、
`skill-audit-judge`、`product-structure-information`、`rtep-flow-manager`、`eridoc-client`,
外加平台仓 `platform-ci`。

### ✅ 一个 Dockerfile、一个 compose、公共 CI 参数抽到一个文件

**怎么实现的**:服务仓的 `.gitlab-ci.yml` 只写"我是谁",其余全部来自平台仓的共享模板。

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v1                      # 锁 tag,平台改动不会突然影响服务
    file: templates/service.yml
    inputs:
      service: product-structure-information
      app_module: hw_change_analysis.app:app
```

* **Dockerfile** 不在服务仓里:`templates/service.yml` 构建时生成,所有 Python 服务同一份配方,
  基础镜像 `python-base` 由平台仓构建并发布。
* **运行描述**不在服务仓里:端口、挂载、环境变量集中在 `deploy/<环境>/{compose,stack}.yml`。
  服务仓不知道自己在哪个端口上跑 —— 这是"端口动态分配"能成立的前提。
* **私有部分**就是上面那几行 inputs。最小的接入实例是 `eridoc-client`:25 行。

### ✅ 镜像部署

**怎么实现的**:CI 构建镜像并推到 Artifactory,标签是 commit sha(不可变);部署时只做"把某个
标签的镜像跑起来",不在机器上构建、不在机器上拉代码。

* 构建用 kaniko(runner 不允许 privileged,没有 docker daemon 可用),理由见 [BUILDER.md](BUILDER.md)。
* 部署按钮是手动的:`deploy-test` 任意分支、`deploy-prod` 只在 main。
* 版本落在机器上的 `.env`(`<SERVICE>_TAG=<sha>`),集群里 swarm 会把标签解析成
  `tag@sha256:…` 写进 service spec —— **集群是"现在跑的是哪一版"的真相**。

### ⬜ 消息队列

backlog,没有引入。核对过:所有服务的 `requirements*.txt` 里没有任何队列客户端
(psi 有 `redis>=4.0.0`,那是缓存用途,不是队列)。目前也没有出现真实的异步需求。

---

## 鉴权 / 权限

### ✅ LDAP 登录并拿到 token

**同事 2026-08-17 自己修完的**。原来只匹配 `sAMAccountName`(工号),而邮箱和工号通常无关,
所以"有人能登录、有人 401"。现在邮箱形式的输入额外匹配 `userPrincipalName / mail /
proxyAddresses`,并去掉 Windows 的 `DOMAIN\name` 前缀。

生产日志实证:工号 `exinijl` 与邮箱 `amy.x.wang@ericsson.com` 都能登录成功。

token 是无状态 JWT(`app/services/auth_tokens.py`),所以多副本之间不需要粘性会话。

### 🔴 API 级别权限控制:有认证,但授权不可信

**认证**是全局中间件(`app/main.py` 的 `auth_gate`):除白名单外,任何 `/api/` 请求没有有效
JWT 一律 401。实测生产 `/api/skills`、`/api/market/items`、`/api/users` 都是 401,
加 `?eid=ADMINMM` 也照样 401。

**授权**是另一回事,现在不成立:

* 中间件把可信身份放在 `request.state.eid`(来自 token 的 `sub`),注释也写明"让处理函数可以
  信任身份而不是客户端字段"
* 但生产容器里的代码,`market.py` **6 处**、`skills.py` **2 处**用的都是
  `x_user_eid: str | None = Header(None)` —— **客户端自己填的 HTTP 头**,`request.state.eid`
  一处都没用
* 管理员判定是字面量比较 `requester == "ADMINMM"`,不是配置项 `settings.audit_admin_eid`

**还差什么**:把那 8 处换成 `request.state.eid`,把字面量换成配置项。改动很小,但在服务仓里,
属于该仓的维护者。

---

## CI

### 🔴 加 SONAR 结点

没做。核对过:**所有仓库里没有任何 sonar 配置**(没有 `sonar-project.properties`,
`.gitlab-ci.yml` 里也没有 sonar 相关任务)。

**卡在哪**:需要 Sonar 项目和 token。另外查到的实例是 Community Build —— 只能对主分支出报告,
**卡不住 MR**,所以它的定位只能是"看趋势",不能当门禁。

---

## CD

### 🟡 docker swarm 两台机器 + 负载均衡

**集群**:`100.78.224.45` 是 manager,老机器 `100.77.162.66` 是 worker。地址池显式设成
`172.16.0.0/16`,因为默认的 `10.0.0.0/8` 与 Artifactory、apt 镜像、Sonar 的内网地址冲突。

**测试环境已经全量跑在集群上**:入口 Traefik、前端 2 副本(两台各一)、审计后端/判定器/数据库
钉在老机器(数据在它的硬盘上)、对账器 1 副本、审计引擎 2 副本(两台各一,无状态)。

测试环境同时也是**审计拆分之后**的形态:后端镜像里不再装第三方扫描器,审计通过 HTTP 调用独立的
`skill-audit` 服务;引擎与判定器两个镜像都由新仓 `rp-ai-miao/skill-audit` 构建。已知可用的一组标签存在
`deploy/test/releases/after-audit-split.env`。

**生产还没切**,步骤与回退见 [CUTOVER.md](CUTOVER.md)。

**负载均衡现在对前端、psi、审计引擎成立**(各 2 副本、一台一个),剩下的还不行,这一条要跟需求方对齐
预期。每个服务能不能多副本、依据是什么,逐个查过,表在 [PLATFORM.md](PLATFORM.md)。卡住的原因分两类:
本地状态要服务侧改(rtep 的 SQLite、判定器的登录卷、grafana 的库),或者环境描述里的挂载要删(审计后端
的代码已经不依赖工作副本了,挂载还在)。

### ✅ 端口动态分配

**怎么实现的**:反过来做 —— 服务不再声明端口。集群里的服务**一个端口都不发布**,只有入口
(Traefik)发布一个端口。服务之间按服务名互相访问(`http://audit-backend:8000`),端口由镜像
自己决定,外部无从依赖。

所以"动态分配"要解决的是**外部不再需要知道端口**,不是端口怎么挑。

### ✅ 按 API 路径转发到对应服务

**怎么实现的**:Traefik 的 file provider,规则在 `deploy/<环境>/edge-dynamic.yml`,
带显式优先级(越具体越高):

| 路径 | 去哪 | 优先级 |
|---|---|---|
| `/api/product-structure/…` | psi,并把前缀改写成 `/api/…` | 100 |
| `/api/…` | 审计后端 | 50 |
| `/grafana/…` | grafana | 50 |
| `/` | 前端 | 1 |

**对外前缀属于环境层,不属于服务**:四个服务内部都用 `/api/`,只有组合它们的这一层能决定谁在
哪个外部前缀上。psi 那条用 `stripPrefix` + `addPrefix` 实现。

用的是 file provider 而不是 docker provider,原因是 Traefik v3 的 swarm/docker provider 与
Docker 29 不兼容(报 `client version 1.24 is too old`,`DOCKER_API_VERSION` 无效,容器内验证过)。
副作用是好的:不再需要把 docker socket 挂给 Traefik。

### 🟡 PROD / TEST 分成两套环境

**已经分开的**:两套环境各有自己的目录(`deploy/prod`、`deploy/test`)、自己的入口端口
(生产 9001、测试 9101)、自己的环境变量(GitLab File 变量分别投放)。测试环境的审计后端连的是
测试环境自己的 MySQL 容器(库 `ai_portal`),与生产库无关。

**还没分开的**:测试环境有两条路由实际指向生产实例 ——

| 测试环境的路径 | 实际打到 |
|---|---|
| `/api/product-structure/` | 生产 psi(`100.77.162.66:9010`) |
| `/grafana/` | 生产 grafana(`:9401`) |

后果是在测试环境查一次产品结构,写的是生产库里的缓存表。**要闭环,测试环境得有自己的 psi 和
自己的 grafana。**

### ✅ 服务版本管理(版本集合)

需求是:前端版本 A + 服务 B 的 B1 + C 的 C1 + D 的 D1 组成一个稳定版本。

**已经定下来的部分**:每个环境机器上的 `.env` 就是"这个环境声明用哪些版本",集群里的
service spec(`tag@sha256:…`)是"实际跑的是哪些版本"。`stack deploy` 会重新渲染整个栈,
所以版本必须声明完整 —— 缺一个就拒绝部署,不给兜底值(曾因为有兜底值,导致没写进 `.env` 的
服务被静默拉回文件里的旧版本)。

**怎么实现的**:两个手动任务 + 一份 git 里的快照文件。

* `capture-release`(选环境)→ 打印这个环境**当前实际在跑**的一组版本,每行 `变量名=标签`,
  后面注上当时的镜像摘要。它以描述文件里声明的 `*_TAG` 为准,所以 traefik/mysql 这类写死的第三方
  镜像不会混进来;还会对比机器上 `.env` 的声明与实际在跑的镜像,不一致就标出来。
* 把输出存成 `deploy/<环境>/releases/<名字>.env` 提交 —— **由人提交,不是 CI**:本项目的推送规则是
  `reject_unsigned_commits`,CI 没有签名能力。好处是版本集合可评审,而且真相只有 git 一处。
* `apply-release`(变量 `RELEASE=<名字>`)→ 把这一组全部写进机器上的 `.env` 并重新部署整个栈。
  为此 `swarm-apply.sh` 的入参从"一个服务的变量名+标签"改成"一个或多个 `变量名=标签`",发布单个
  服务和整组重放走同一条路径。

第一个版本集合是 `deploy/test/releases/2026-08-17-test-current.env`,采集与重放都实跑验证过:
重放后六个服务全部 converged,`.env` 的声明与集群实际一致。

采集第一次运行就抓到一处真实漂移:`.env` 声明前端是 `e70a3471`,实际跑的是 `08ffc1e8`(不是流水线
部署的),而且那个服务的 spec 里**没有摘要** —— 意味着失去了不可变的版本锚点。重放之后声明与实际
对齐,摘要也重新固定上了。这正是"集群是版本真相、`.env` 是声明"这套说法需要的那把尺子。

---

## 可观测

### ⬜ 一个地方看所有服务的日志(类似 portainer)

backlog。核对过:没有引入任何相关组件(0 处 portainer / loki / promtail / prometheus)。

现状是每个服务同时写 stdout 和容器内的滚动日志文件,`docker logs` / `docker service logs` 能
分服务看。**注意**:日志文件用 bind mount 落在各自节点的 `/proj/rp_ait_prod/logs/…`,
两台机器各写各的 —— 这也是集中日志要先解决的问题。

### ⬜ 分布式链路追踪

backlog,0 处 opentelemetry / jaeger / zipkin。

### ✅ 健康检查(但都是浅的)

每个服务都有健康检查,`compose.yml` / `stack.yml` 里配了 `healthcheck`,数据库用
`mysqladmin ping`。

**局限要写清楚**:这些探针只证明进程活着。psi 的 `/health` 返回 `{"status":"ok"}` 时并不查
数据库、也不查 RSB,所以"活着但没用"检测不出来 —— 曾经出现过"健康但 RSB 调用超时"。要真正
反映可用性,得由各服务在探针里查自己的关键依赖。

## 挂着没做的事

| 事 | 卡在谁 |
|---|---|
| 生产 `*.env` 是 `-rw-rw-r--`(任何登机用户可读数据库/JWT/LDAP 口令);部署目录含 `prod-env` 是 `777`,可写 | 属主 `ezhgmio`。`chmod 640 *.env` + `chmod 750` |
| psi 仓 git 历史里两个密钥要轮换:`nacos_ml_vem.yml`(ml-vem 生产库口令)、`secrets.yml`(Azure client_secret) | 密钥持有方。清历史无用,凭据已泄露 |
| 上传报告不发 `clear`,上传者分不清"查过是干净的"和"根本没查";前端三处分支等着 | 产品定显示哪几项检查(共 25 项)。同时挡着重复性那一行的呈现 |
| 构建器 kaniko 上游已归档,替代方案 BuildKit rootless 起不来(宿主机关了非特权用户命名空间,是组织加固基线)。三个选项与实测证据见 [BUILDER.md](BUILDER.md) | 要 runner 宿主机维护者放开 `kernel.unprivileged_userns_clone`,或走安全例外 |
| grafana 看板的真相源没定:`allowUiUpdates: true`,界面上的改动不回写仓库,卷重建就丢(已分叉过一次)。收紧的前提是先有同步任务 —— `grafana` 现在不在 `services.txt` 里,没有流水线管它 | 我们自己,一个手动同步任务 |
| 生产还是单机(集群里 `prod_` 服务数 0) | 门户的代码已经不依赖工作副本(!54),现在卡在这个仓:环境描述里那两个挂载(技能仓工作副本、ssh 私钥)要删,生产的令牌要从个人令牌换成项目访问令牌。步骤见 [CUTOVER.md](CUTOVER.md) |

已完成,勿重复提:`frontend`、`product-structure-information`、`skill-audit` 均已加
`max_replicas_per_node: 1`,两副本实测分处两台。`preferences: spread` 只是偏好,单靠它滚动更新后会挤在
一台,界面仍显示 2/2。
