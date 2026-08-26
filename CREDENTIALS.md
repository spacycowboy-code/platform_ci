# 凭据与身份

这个平台上每一个密钥、令牌、账号:是什么、配在哪、到期、谁能换、失效了什么会坏。

**这里不写任何密钥的值。** 值只在 GitLab 的 CI 变量里。查证时间 2026-08-26。

读凭据文件时注意一条:命令必须让"解析失败"等于"什么都不输出"。写成 `解析 || cat 文件`
这种回落形式,一旦解析失败就会把明文打到日志里。

---

## 现在最要紧的一件:镜像仓库没有凭据,构建和部署都停着

组变量里原来的 `docker_user` / `docker_password` / `DOCKER_AUTH_CONFIG` / `NPM_AUTH_B64`
指向的是 `rvccie` —— RVC 项目的 CI 服务账号,不属于我们。镜像也一直推在
`armdocker.rnd.ericsson.se/proj-rvc/rp-ai/` 下面,`proj-rvc` 是 RVC 在 Artifactory 的空间。

**2026-08-26 已把这四个变量删除。** 后果:

* 所有仓的 `build` 任务会停在 `docker_user 未设置`
* `deploy` 的 `docker login` 会失败
* 影响的不只是审计那几个服务 —— psi、eridoc-client、rtep-flow-manager、前端的流水线
  都引用 platform-ci 的共享模板,一样构建不了

**正在跑的服务不受影响**:两台机器上镜像都有本地副本,容器重启不需要拉取。
每天的备份也不受影响,它用的 `mysql:8.0` 在生产机器上有本地副本 —— 但这意味着
**那台机器上的镜像不能清**,清掉就没法恢复了。

恢复要三步:

1. 申请我们自己的 Artifactory 空间 + 服务账号。**申请时必须同时要拉取 Docker Hub 的通道** ——
   我们的 Dockerfile 是 `FROM python:3.11-slim`,栈里还直接用 `mysql:8.0`、`traefik`、
   `grafana/grafana:11.1.0`。只给一个能推的空间,构建照样跑不起来。
2. 在组 `rp-ai-miao` 的 CI 变量里配上新账号,变量名保持 `docker_user` / `docker_password`
   (模板按这两个名字取),镜像路径改到新空间。
3. 重建镜像并部署。所有镜像都能从对应提交重新构建,`proj-rvc` 里没有任何只存在于那里的东西。

---

## GitLab 令牌

都是项目或组的访问令牌(机器人身份),没有个人令牌。

### portal-skill-repo-prod

门户读写**生产**技能仓 `rp-ai/rp-ai-skill`。

* 配在 `AUDIT_PROD_ENV` 的 `GITLAB_TOKEN`
* scope `api`,角色 Developer,并在该仓 main 的推送白名单里
* 到期 2027-08-19
* 谁能换:该仓的 Maintainer
* 失效了会坏:技能的上传、编辑、下架全部返回 503;门户读技能正文也会失败

`api` 这个 scope 是必需的,不能换成 `write_repository` —— 后者只对 git push 有效,
调 Commits API 会得到 403 insufficient_scope。

角色只需要 Developer。Developer 加上"在保护分支的推送白名单里"就能推 protected main,
不必给 Maintainer。合并权限仍然只有 Maintainers —— 服务能提议,不能自己批准。

### portal-skill-repo-test-dev

同上,对**测试**技能仓 `rp-ai-miao/rp-ai-skill-test`。

* 配在 `AUDIT_TEST_ENV` 的 `GITLAB_TOKEN`
* scope `api`,角色 Developer,在测试仓 main 的推送白名单里
* 到期 2027-08-19
* 失效了会坏:测试环境的技能读写

测试环境还必须设 `AUDIT_MR_PROJECT=rp-ai-miao/rp-ai-skill-test`。这一项以前没设,
默认值指向**生产**仓 —— 测试环境的复核 MR 会开到生产仓里去。

### skill-audit-engine-read-prod

审计引擎读生产技能仓的内容。

* 配在 `SKILL_AUDIT_PROD_ENV` 的 `GITLAB_TOKEN`
* scope `read_api` + `read_repository`,角色 Reporter
* 到期 2027-08-19
* 失效了会坏:引擎拿不到技能内容,审计跑不了

### engine-read-test-skillrepo

同上,测试环境读测试技能仓。配在 `SKILL_AUDIT_TEST_ENV` 的 `GITLAB_TOKEN`,到期 2027-08-19。

### platform-ci-scheduler

定时流水线的 owner。**不配在任何变量里** —— 它的用途是"用它创建定时计划,
这样计划归 bot 所有,而不是归某个人"。

* 项目 platform-ci,scope `api`,角色 Developer,在 main 推送白名单里
* 到期 2027-08-19
* 失效了会坏:两个定时计划(每天备份、每周清缓存)停止运行,**而且不会有任何提示**

定时计划的所有权转不走:`PUT owner_id` 返回 200 但会被默默忽略,`take_ownership` 是
"谁调用谁拿到"。所以要换 owner 只能用目标身份的令牌重新创建计划。

**只有持有这个令牌的人能改那两个计划。** 令牌值不落盘,所以要改计划得先轮换令牌拿到新值。

### rp-ai-user-agents 的组访问令牌

用户上传 Agent 包时,门户要在 `rp-ai/rp-ai-user-agents` 组下建项目。

* 配在 `AUDIT_PROD_ENV` 和 `AUDIT_TEST_ENV` 的 `GITLAB_USER_AGENTS_TOKEN`
* **生产和测试是同一个令牌** —— 换它两个环境一起受影响
* 到期时间查不到(需要该组的 Owner 权限)
* 失效了会坏:只影响 Agent 包上传,技能相关功能不受影响

---

## 不是 GitLab 令牌的共享密钥

### AUDIT_ENGINE_TOKEN / ENGINE_TOKEN

门户与审计引擎之间的共享密钥,两边必须**配成同一个值**:

* 门户侧 `AUDIT_PROD_ENV` 的 `AUDIT_ENGINE_TOKEN`
* 引擎侧 `SKILL_AUDIT_PROD_ENV` 的 `ENGINE_TOKEN`
* 测试环境是另一对(`AUDIT_TEST_ENV` / `SKILL_AUDIT_TEST_ENV`)
* 失效或两边不一致:门户调引擎全部 401,审计停摆

### SKILL_IMPORT_TOKEN

保护门户自己的官方技能导入接口的 bearer,不是外部凭据,是我们自己定的值。

* 配在 `AUDIT_PROD_ENV`
* **不设置则该接口关闭**(不是报错,是直接不启用)
* 测试环境没有配,所以测试环境没有这个接口

---

## 机器访问

### DEPLOY_SSH_KEY / DEPLOY_HOST / DEPLOY_USER

ssh 到老机器,用 compose 部署。受保护变量,只有保护分支上的流水线能取到。

失效了会坏:老机器上所有 compose 形态的服务都没法部署。

### SWARM_SSH_KEY / SWARM_HOST / SWARM_USER

ssh 到 swarm 的 manager 节点,用 stack 部署。同样受保护。

失效了会坏:测试集群没法部署。`docker service inspect` 之类的排查命令也只能在 manager 上跑。

---

## 环境配置(不是密钥,但里面装着密钥)

每个服务每个环境一份,内容是完整的 env 文件:

```
PSI_PROD_ENV / PSI_TEST_ENV
AUDIT_PROD_ENV / AUDIT_TEST_ENV
SKILL_AUDIT_PROD_ENV / SKILL_AUDIT_TEST_ENV
GRAFANA_PROD_ENV
ERIDOC_PROD_ENV
```

生产那几个都是受保护变量。**改这些变量不会自动重新部署** —— 要走一次部署流水线才生效。

`AUDIT_PROD_ENV` 里还有生产数据库的连接串。数据库账号是我们自己的,不轮换。

---

## 待清理

### PROD_GITLAB_TOKEN

受保护、已掩码的生产变量,内容是**同事 `ezhgmio` 的个人令牌**。
在所有仓里搜过,**零引用** —— 没有任何流水线或代码用它。

建议:删掉这个变量。我们不该存别人的个人凭据,而且它没有用途。
删变量不影响她的令牌本身。

### skill-audit-engine-read

生产技能仓上的第二个只读令牌,2026-08-20 之后再没被使用过,不在任何环境变量里。
`skill-audit-engine-read-prod` 才是引擎在用的那个。

建议:确认无人使用后删除。

---

## 挂在离职者个人身份上的东西

### 个人 GitLab 令牌

`glab` 用的那个个人令牌 **2026-08-31 到期**。所有文档里的命令都不要依赖它 ——
运维动作应当走流水线,或者用上面那些项目令牌。

### 机器上的 ssh 公钥

manager 节点(`node-100-78-224-45`)的 `authorized_keys` 里有离职者一行,并且有免密 sudo。
老机器(`node-100-77-162-66`)的 `authorized_keys` 里没有,走的是别的机制。

接手的人要能上这两台机器,得自己的公钥进 `authorized_keys` —— 部署流水线用的是
`DEPLOY_SSH_KEY` / `SWARM_SSH_KEY`,和个人访问是两条路,流水线不受个人离职影响。

---

## 轮换的时候会撞到的四件事

**GitLab 的轮换接口默认只给一周有效期。** 调 `POST .../access_tokens/:id/rotate` 不带
`expires_at`,新令牌一周后就过期。必须显式传:

```
POST /projects/:id/access_tokens/:id/rotate   {"expires_at": "2027-08-19"}
```

**吊销令牌不会收回它在保护分支白名单里的授权。** 白名单条目要单独摘掉:

```
PATCH /projects/:id/protected_branches/main
     allowed_to_push[][id]=<条目 id>&allowed_to_push[][_destroy]=true
```

**轮换会换掉值,但 bot 用户不变**,所以定时计划的所有权不会丢。

**没有任何到期提醒机制。** 上面这些令牌 2027-07 到 2027-08 陆续到期,现在只有日期,
没有人会被通知。这件事还没做,见 ROADMAP。
