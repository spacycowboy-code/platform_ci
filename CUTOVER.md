# 把生产切到集群

一次性操作手册。切完之后这个文件删掉,不要留成"文档里还有一份过期的迁移说明"。

测试环境已经按这套顺序走过一遍(2026-08-14),下面每条注明的现象和时长都是那次实测的。
**但有六个服务只在生产存在、从没在集群上跑过**:`rtep-flow-manager`、`eridoc-client`、
`work-connector`、`genius-ac` 及其判定器,以及不在 `services.txt` 里的 `grafana`。
"测试环境已经排练过"对它们不成立 —— 搬它们之前先用 `tools/check_stack_config.py`
(CI 的 `check-stack` 任务)把服务块过一遍,再在集群上起一个一次性栈跑一遍、验完删。

## 已经完成的部分(2026-09/10,不在下面的步骤里了)

这份手册最初是按"两个节点、入口是 9001 纯 HTTP、Traefik 要当边缘"写的,那三个前提都已改变:

* **控制面容错已经有了**。2026-09-21 起是三个 manager(`100.77.161.72` / `100.77.162.66` /
  `100.78.224.45`,2 seli + 1 sero),`quorum-ok`,任何一台挂掉还能部署。`.72` 是 `Drain`,
  只参与控制面。节点标签 `site=seli` / `site=sero` 已打。
* **前端的三个上游已经改成服务名**(2026-10-08,MR !54):`AUDIT_UPSTREAM` /
  `PSI_UPSTREAM` / `GRAFANA_UPSTREAM` 不再是 `host.docker.internal:<宿主机端口>`,
  `extra_hosts` 已删。所以第 6 步里那一条已经做完了。
* **路由层已经在生产上跑起来并承载流量**(2026-10-08,MR !56 + !57),但形态是
  **compose 服务、待在 `rp-ai-edge` 后面**,不是"Traefik 当边缘":
  `浏览器 --443--> edge(TLS、域名、302)--> traefik(路由)--> 各服务`。
  路由表在 `deploy/prod/edge-dynamic.yml`,六条路由,逐条照前端的 `nginx.conf.template` 复刻。
  所以下面第 1 步要做的不再是"把 Traefik 搬上集群当边缘",而是把它**从 compose 形态翻译进
  `stack.yml`**(配置从 bind mount 换成 docker config)。

## 两个会让人以为"改了但没生效"的坑(都踩过)

搬迁期间要反复改 `edge/nginx.conf` 和 `compose.yml`,这两条一定会遇到:

* **`docker compose up -d <服务>` 不会重建容器** —— compose 比的是服务定义,bind mount 的
  文件内容不在比较范围里,它会报 `Running` 直接跳过。必须 `--force-recreate`。
* **按文件挂载的配置,rsync 换 inode 后容器永远看旧内容** —— `nginx -t` 校验的是旧配置、
  `nginx -s reload` 返回成功而一个字没变,全程不报错。`edge/nginx.conf` 已于 2026-10-08
  改成挂 `./edge` 目录(MR !58),所以 edge 不再有这个问题;**其它按文件挂的配置仍然有**。
  判定"改动是否真的生效"要用 `nginx -T` 看进程实际加载的内容,或者发一个带唯一标记的请求
  看它出现在哪个容器的日志里 —— 不要只看部署任务的颜色。

## 开始之前:三件必须先定的事

| | 事 | 为什么卡在这里 |
|---|---|---|
| 1 | **审计那一组的停机窗口** | 后端 + reconciler + 判定器不能与 compose 版本并行(见下),必须先停后起。测试环境实测约 40 秒,生产上要挑时间 |
| 2 | **接受"负载均衡只对一部分服务成立"** | 前端、psi、审计引擎已经是真两副本(各一台一个)。rtep 的业务数据是本地 SQLite、grafana 的数据在命名卷 —— 这两个仍然只能单副本。审计后端不依赖 git 工作副本(内容按引用读写),环境描述里也没有那两个挂载,所以它可以多副本 |

`agent-artifex` 不在这个清单里了:它是一个没有业务逻辑的骨架(只有 `/`、`/health`、`/api/version`),
`Exited` 十二天,已从生产描述里删除。要用的时候按"加一个服务"的流程加回来。

## 入口:443 + 域名 + 证书,不是 9001

**原来这一节写的是"入口端口用 9001,不要用 80",已经不成立。** 当时的前提是用户敲
`http://100.77.162.66:9001` 这个裸地址、而 Traefik 会接管这个端口。现在:

* 真正的入口是 **`https://rpai.ews.gic.ericsson.se`(443)**,证书在 `rp-ai-edge` 手上
  (`/proj/rp_ait_prod/certs/rpai`,挂的是目录,续期换 inode 也能生效)。
* `80` 和 `9001` 现在**由 edge 持有且只回 302** 到那个域名,它们只是历史跳板。
* **域名已经是事实上的唯一入口**:2026-10-08 查 edge 最近 7 天的访问日志,9474 行里
  referer 带域名的 8092 行、带裸 IP 的 **6** 行;302 跳转共 119 条,其中 79 条是 `GET /`
  (像书签和扫描器)。所以**换机器对用户是透明的,只要 DNS 跟着改,不需要通知任何人**。
* 代价是入口切换要处理 TLS 证书,那是原计划里没有的一步 —— 但在"Traefik 待在 edge 后面"
  这个形态下不需要:证书始终由 edge 持有,Traefik 不碰 443。

grafana 那条也变了:`GF_SERVER_ROOT_URL` 现在是
`https://rpai.ews.gic.ericsson.se/grafana/`,而且它在 **git 里**(`deploy/prod/compose.yml`),
不在 env 文件里。入口不变就不用动它。门户后端不读任何 grafana 配置项,不受影响。

**域名只有一条 A 记录,指向 `100.77.162.66`。** 所以入口仍是单点:那台挂了集群活着、
地址死了。要让多台机器分担,swarm 帮不上(它管的是流量进来之后去哪个副本)——只能动
DNS(加 A 记录做轮询,便宜但没有健康检查)或申请平台 VIP / LB(真解)。这是独立一件事,
不在本手册范围内。

## 每个服务能不能并行搬

"并行"指:集群副本和老机器上的 compose 容器同时跑,路由切过去后再停旧的 —— 零中断、可随时切回。

| 服务 | 能并行? | 依据 |
|---|---|---|
| `product-structure-information` | ✅ | 缓存在共享数据库表里,副本之间答案一致。集群里的声明已经不挂任何卷,测试环境两副本一台一个;**生产那份 compose 还挂着日志的 bind mount**(`/proj/rp_ait_prod/logs/…`,只在老机器上),搬的时候要一并去掉 |
| `frontend` | ✅ | 只有静态文件(路由表已经搬到 `edge-dynamic.yml`,不在它的镜像里了)。**仍然放最后**,但理由变了:它不再占 9001(那个端口现在是 edge 的,它自己只发布 `127.0.0.1:8901`),真正的原因是它镜像里那层 nginx **没有 `resolver`**、`proxy_pass` 的主机名在**启动时**解析,解析不到就直接拒绝加载配置 —— 所以它必须在它的上游都已就位之后才能搬,顺序反了挂的是整站而不是一个页面 |
| `grafana` | ❌ | 两个实例挂同一个命名卷会互相破坏。但卷里没有不可再生的东西(见 PLATFORM.md),所以它也可以改成无卷 + 配置用 docker config 下发,那样就不必钉机器 |
| `rtep-flow-manager` | ❌ | 业务数据是本地 SQLite(`/proj/rp_ait_prod/data/rtep-flow-manager` → `/app/data/rtep_flow.db`),两个进程写同一个文件会锁冲突甚至损坏 |
| `audit-backend` + `audit-reconciler` + `audit-judge` | ❌ | 判定器的 kiro 登录在命名卷里,必须和后端同一批搬。后端没有工作副本依赖,环境描述里也没有那两个挂载 |
| `skill-audit`(审计引擎) | ✅ | 无状态,可以先搬、也可以两副本跨节点。**但它必须在后端之前就位** —— 后端镜像里已经没有扫描器,审计全靠调它;顺序反了的话每次上传都拿不到结论,新上传全部转人工复核、重发布已上线技能返回 503。后端不再直接找判定器,链路是 后端 → 引擎 → 判定器 |

所以顺序是:**能并行的先搬(零中断)→ 不能并行的各自开窗口 → 前端最后(它一动就是入口切换)**。

## 步骤

每步都给"验证"和"回退"。任何一步验证不过就按该步的回退退回,不要往下走。

### 1. 路由层从 compose 形态翻译进 stack.yml

**这一步原来写的是"Traefik 进集群、临时发布 80 自己验证",已经不能照做,而且已经被另一条
路取代了。** 两个原因:

* `.66` 的 `80` / `443` / `9001` 全被我们自己的 `rp-ai-edge` 占着(一个容器占三个端口),
  而 swarm 的 ingress 语义是只要有服务发布某端口、**每个**节点都要绑它 —— 老机器上绑不上,
  而且这种失败很安静。
* Traefik 已经在生产上以 **compose 服务**跑着并承载全部流量(待在 edge 后面),配置用
  bind mount 挂 `edge-static.yml` / `edge-dynamic.yml`。并行验证那一步在 2026-10-08
  已经做完了:10 条路径与当时在服务的前端路由状态码和响应体 md5 全等、缓存头一致、
  访问日志里六个 router 落点全对。

所以现在要做的是把它**翻译进 `stack.yml`**:服务块照 `deploy/test/stack.yml` 里那份写
(`mode: ingress` 不要,**用 `mode: host` 或者干脆不发布端口** —— 对外端口在 edge 手上,
Traefik 只需要在 overlay 网络里被 edge 按服务名访问到),配置从 bind mount 换成两个
docker config。注意 **swarm config 不可变**:改了 `edge-dynamic.yml` 要给 config 名字换
版本号(test 那份就是 `edge_static` / `edge_dynamic` 这样命名的,生产的 `stack.yml` 里
现在叫 `edge_static_v1` / `edge_dynamic_v2`)。

* **验证**:集群副本起来后,先不改 edge 的上游 —— 用 `docker service ps` 确认副本健康,
  再从 edge 容器里按服务名打一发(`wget -qO- http://traefik:80/api/version`)。
  两边都通了再把 edge 的 `set $fe` 指过去。
* **回退**:`docker service rm prod_traefik`,edge 的 `set $fe` 指回 compose 那个
  `traefik:80`(同名,所以多数情况下连改都不用改),`docker compose up -d --force-recreate
  --no-deps traefik`。
* **注意**:compose 的 traefik 和集群的 traefik 同名会在 DNS 上打架,所以这一步要么先停
  compose 那个、要么给集群那个换个服务名再改 edge 的上游。这是本手册里唯一一处"两种编排
  下同名服务"的冲突,别留到现场才想。

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

### 6. 前端:最后一个

这一步**不再是"入口切换"** —— 入口早就是 443 + 域名,而路由表已经不在前端镜像里了。
前端现在只提供静态文件和 index.html 回退,所以这一步对用户几乎不可见。

1. 前端加进 `stack.yml`,不发布端口(单机那份只发布 `127.0.0.1:8901`,集群里不需要)
2. 它的五个 `*_UPSTREAM` 已经全是服务名(2026-10-08 改完并上线),**这一条不用再做**
3. 把 `edge-dynamic.yml` 里 `fe` 那条服务的后端从 `http://frontend:80` 保持不变 ——
   同名,compose 和集群都解析得到,所以切换期间这一行不用动
4. 停 compose 的 frontend

**仍然必须最后搬**:它镜像里那层 nginx 没有 `resolver`,`proxy_pass` 的主机名在启动时
解析,解析不到就拒绝加载配置 —— 所以它的上游必须先就位。

原来第 3 步那个"Traefik 发布端口从 80 改成 9001、且必须先 `service rm`"**不再适用**
(Traefik 不持有对外端口)。但那条坑本身仍然成立、以后改任何 swarm 服务的发布端口都会撞:
**在已有 swarm 服务上改发布端口,ingress 映射不会刷新** —— 现象是配置对、容器内部确实在听、
`service inspect` 里端口也对,但外面连不上,日志没有任何报错。测试环境撞过。办法是
`docker service rm` 之后重新部署。

* **验证**:走域名逐条对照,基线用这九条(2026-10-08 实测值,可直接复用):
  `/` 与 `/index.html` 200/`d7ac38ae`、`/api/version` 200/`57d3ea69`、
  `/api/market` 401/`48bab934`、`/api/product-structure/vem/version` 200/`88a6572c`、
  `/grafana/` 200/`7a74089a`、`/api/work-connector/health` 200/`28afe346`、
  `/api/eridoc/` 与 `/api/eridoc/files` 404/`f88c0a33`。
  另外 Grafana Live 的 websocket 要 101 —— **探针必须加 `--http1.1`**,curl 默认对 https
  协商 HTTP/2,而 nginx 不支持 h2 上的 `Upgrade`、会返回 400,那不是回归(真实浏览器的
  ws 握手走的就是 HTTP/1.1,日志实证)。
  **不要用 `/api/product-structure/vem/debug/config` 当基准** —— 它的响应体里带着对外部
  RSB 的实时调用结果,本身不确定。
* **回退**:`docker service rm prod_frontend`,老机器 `docker compose up -d
  --force-recreate --no-deps frontend`。

### 7. 收尾

`deploy/prod/compose.yml` 里已经空了,删掉它和 `services.txt` 里的 compose 行;删掉本文件。

## 切换后仍然存在的、不由这次迁移解决的问题

* **控制面单点已经解决**(2026-09-21):三个 manager、`quorum-ok`,任何一台挂掉还能部署。
  但剩下一条硬边界:三台是 **2 seli + 1 sero** 分布,所以能容忍丢任意一台、能容忍丢 sero,
  而 **seli 整个站点挂掉控制面就没了**,只能 `--force-new-cluster` 手工重建。站点级容错
  需要两边都有拿到多数派的可能(3+3 起,或三个站点)。
* **有状态服务仍然钉机器,老机器挂了它们无法重新调度** —— 数据不在别处:
  `rtep-flow-manager` 的本地 SQLite(只有 12 KB,换 MySQL 很容易)、`po-reporting-portal`
  的 `/app/data/current.xlsx`、`grafana` 的命名卷、以及四个判定器的 kiro 登录卷
  (**后者是 kiro-cli 的固有限制,共享存储也解不了**)。
  三台机器**没有共享存储**:各有一块独占的 1TB Ceph RBD(ext4 单写者),不是共享盘;
  `.72` 连这块都没有,已决定不申请。
* **入口仍是单点**:域名只有一条 A 记录指向 `.66`,而 `edge` 持有证书所以钉在那台。
  这跟迁移前一样,搬完不解决 —— 见上面「入口」一节末尾。
* **密钥明文进 service spec**:`docker service inspect` 能看到 `AUTH_JWT_SECRET` 等,
  raft 未加锁。已知,处理方式待定(见审查记录)。真正的解法是 docker secret:
  `audit-backend` 的签名密钥只有两个文件、432 和 116 字节,是 secret 的标准用例,
  而且**不需要共享存储**。
* **健康检查只证明进程活着**:各服务的 `/health` 不查数据库和外部依赖。psi 曾出现
  "健康但 RSB 调用超时"。另外实测过一个反直觉的现象:从别的容器打
  `audit-backend:8000/api/health` 得到 **401**,而它自己的 healthcheck 打
  `localhost:8000/api/health` 得 200 —— 所以"容器 healthy"不等于"从别的容器调得通",
  做副本健康判断时别拿它当依据。
* **`adminmm` 默认口令后门在生产是开着的**,与本次迁移无关。
