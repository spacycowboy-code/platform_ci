# 把生产切到集群

一次性操作手册。切完之后这个文件删掉,不要留成"文档里还有一份过期的迁移说明"。

测试环境已经按这套顺序走过一遍(2026-08-14),下面每条注明的现象和时长都是那次实测的。

## 开始之前:三件必须先定的事

| | 事 | 为什么卡在这里 |
|---|---|---|
| 1 | **审计那一组的停机窗口** | 后端 + reconciler + 判定器不能与 compose 版本并行(见下),必须先停后起。测试环境实测约 40 秒,生产上要挑时间 |
| 2 | **接受"负载均衡只对一部分服务成立"** | 前端、psi、审计引擎已经是真两副本(各一台一个)。rtep 的业务数据是本地 SQLite、grafana 的数据在命名卷 —— 这两个仍然只能单副本。审计后端不依赖 git 工作副本(内容按引用读写),环境描述里也没有那两个挂载,所以它可以多副本 |

`agent-artifex` 不在这个清单里了:它是一个没有业务逻辑的骨架(只有 `/`、`/health`、`/api/version`),
`Exited` 十二天,已从生产描述里删除。要用的时候按"加一个服务"的流程加回来。

## 入口端口用 9001,不要用 80

用户现在访问 `http://100.77.162.66:9001`。最终让 Traefik 接管这个端口,**用户的地址一个字都不用改,不需要通知任何人**。

还有一个具体好处:grafana 的 `GF_SERVER_ROOT_URL` 写死成 `http://100.77.162.66:9001/grafana/`(在它的 env 文件里,不在 git)。入口保持 9001 就不用动它;换成 80 得同步改并重新部署 grafana。门户后端不读任何 grafana 配置项,不受影响。

## 每个服务能不能并行搬

"并行"指:集群副本和老机器上的 compose 容器同时跑,路由切过去后再停旧的 —— 零中断、可随时切回。

| 服务 | 能并行? | 依据 |
|---|---|---|
| `product-structure-information` | ✅ | 缓存在共享数据库表里,副本之间答案一致。集群里的声明已经不挂任何卷,测试环境两副本一台一个;**生产那份 compose 还挂着日志的 bind mount**(`/proj/rp_ait_prod/logs/…`,只在老机器上),搬的时候要一并去掉 |
| `frontend` | ✅ | 只有静态文件。但它占着 9001,所以放最后 |
| `grafana` | ❌ | 两个实例挂同一个命名卷会互相破坏。但卷里没有不可再生的东西(见 PLATFORM.md),所以它也可以改成无卷 + 配置用 docker config 下发,那样就不必钉机器 |
| `rtep-flow-manager` | ❌ | 业务数据是本地 SQLite(`/proj/rp_ait_prod/data/rtep-flow-manager` → `/app/data/rtep_flow.db`),两个进程写同一个文件会锁冲突甚至损坏 |
| `audit-backend` + `audit-reconciler` + `audit-judge` | ❌ | 判定器的 kiro 登录在命名卷里,必须和后端同一批搬。后端没有工作副本依赖,环境描述里也没有那两个挂载 |
| `skill-audit`(审计引擎) | ✅ | 无状态,可以先搬、也可以两副本跨节点。**但它必须在后端之前就位** —— 后端镜像里已经没有扫描器,审计全靠调它;顺序反了的话每次上传都拿不到结论,新上传全部转人工复核、重发布已上线技能返回 503。后端不再直接找判定器,链路是 后端 → 引擎 → 判定器 |

所以顺序是:**能并行的先搬(零中断)→ 不能并行的各自开窗口 → 前端最后(它一动就是入口切换)**。

## 步骤

每步都给"验证"和"回退"。任何一步验证不过就按该步的回退退回,不要往下走。

### 1. Traefik 进集群,路由先指向老机器上现有的服务

`deploy/prod/stack.yml` 里 Traefik 暂时发布 **80**(临时的,只自己验证用,不告诉任何人),`edge-dynamic.yml` 的四个后端全部写老机器的 `主机:端口`。`services.txt` 里加一行 `traefik: swarm`,点它的部署按钮。

* **验证**:逐条对照 `:80` 与 `:9001` 的响应码**和响应体**。测试环境那次比的是 `/`、`/api/...`、`/api/product-structure/vem/...`、`/grafana/`、`/api/version` 六条,全部一致才算过。
* **回退**:`docker stack rm prod`。老机器一个容器都没动。

### 2. 搬能并行的:psi

把服务加进 `stack.yml`(不发布端口)、`services.txt` 那一行的模式改成 `swarm`、点部署按钮;确认集群副本健康后,把 `edge-dynamic.yml` 里对应的后端从 `主机:端口` 改成服务名,再部署一次。

**`deploy/prod/stack.yml` 里 psi 那段注释已经过期**:它说 psi 把产品结构树缓存在容器本地磁盘、
所以只能单副本。那层磁盘缓存是死代码,已经从服务里删掉了(现在缓存在共享数据库表 `product_struct_cache`
里,副本之间答案一致)。搬的时候连注释一起改。

**`deploy/prod/stack.yml` 里 psi 现在声明的是 `replicas: 1`**,而测试环境是 `replicas: 2` + `max_replicas_per_node: 1`。psi 的缓存在共享数据库表里,两副本答案一致,所以搬的时候要顺手把生产这份改成 2 —— 否则搬完仍然是单副本,而文档里那张表写的是 2。

* **验证**:六条路径仍然一致;老机器上的 compose 容器还在跑(留作回退)。
* **回退**:把 `edge-dynamic.yml` 那一行改回 `主机:端口`、`services.txt` 改回 `compose`,部署。集群副本留着不碍事。
* 确认稳定一段时间后,再从 `compose.yml` 删掉这个服务并部署一次,老容器才下线。

### 3. grafana:一个窗口

停 compose 的 grafana → 起集群的(**放置约束钉在老机器**,卷 `external` 且名字写死 `ai-portal-be_grafana-data`)→ 改路由。

* **验证**:`/grafana/` 打得开、**里面的仪表盘和数据都在**(卷复用成功的唯一证据)。
* **回退**:`docker service rm prod_grafana`,老机器 `docker compose up -d grafana`。卷没动,数据不会丢。
* 窗口内 `/grafana/` 不可用。

### 4. rtep-flow-manager:一个窗口

同上。数据是 bind mount(`/proj/rp_ait_prod/data/rtep-flow-manager`),所以**必须钉在老机器**。

* **验证**:接口可用,而且**能读到迁移前的业务数据**(idea / sprint 列表条数与迁移前一致)。
* **回退**:同 grafana 的做法。

### 5. 审计那一组:一个窗口,三个服务一起

先记基线:技能条数、审计记录条数、判定器登录卷里的内容、`/health`。然后停 compose 的 `audit-backend`/`audit-reconciler`/`audit-judge` → 起集群的三个(全部钉老机器,判定器两个命名卷 `external` 名字写死)→ 改路由。

* **验证**:①技能条数与审计记录条数和基线一致 ②判定器登录卷内容还在 ③**真实上传一个技能走完整条链路**,确认判定器被调用(看判定器容器日志里有 `POST /judge 200`)④真实登录能拿到 token。
* **回退**:`docker service rm` 三个,老机器 `docker compose up -d audit-backend audit-reconciler audit-judge`。
* 测试环境实测窗口约 40 秒。**判定器登录卷丢了的后果是所有上传都因判定器不可用而落到人工复核**,所以卷名必须写死、必须钉机器。

### 6. 前端:入口切换

这一步才真正动用户看到的东西,但地址不变。

1. 前端加进 `stack.yml`,上游用集群内服务名(`audit-backend:8000` 等)—— **不能再用 `host.docker.internal`**,它只解析到副本所在那台机器
2. 停 compose 的 frontend(释放 9001)
3. `stack.yml` 里 Traefik 的发布端口从 80 改成 **9001**,并且 **`docker service rm prod_traefik` 之后再部署**

第 3 步那个删除是必须的:**在已有 swarm 服务上改发布端口,ingress 映射不会刷新**——现象是配置对、容器内部确实在听、`service inspect` 里端口也对,但外面连不上,日志没有任何报错。测试环境撞过。

* **验证**:`http://100.77.162.66:9001` 与切换前逐条一致;登录能进;grafana 的 `GF_SERVER_ROOT_URL` 不需要改(仍是 9001)。
* **回退**:`stack.yml` 里 Traefik 改回 80 + `service rm` 重建,老机器 `docker compose up -d frontend`。

### 7. 收尾

`deploy/prod/compose.yml` 里已经空了,删掉它和 `services.txt` 里的 compose 行;删掉本文件。

## 切换后仍然存在的、不由这次迁移解决的问题

* **控制面单点**:两个节点在数学上拿不到控制面容错(要 3 个 manager)。manager 挂了不能部署;有状态服务钉在老机器,老机器挂了它们无法重新调度 —— 数据不在别处。
* **密钥明文进 service spec**:`docker service inspect` 能看到 `AUTH_JWT_SECRET` 等,raft 未加锁。已知,处理方式待定(见审查记录)。
* **健康检查只证明进程活着**:各服务的 `/health` 不查数据库和外部依赖。psi 曾出现"健康但 RSB 调用超时"。
* **`adminmm` 默认口令后门在生产是开着的**,与本次迁移无关。
