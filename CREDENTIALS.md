# 凭据与身份

这个平台上每一个密钥、令牌、账号:是什么、配在哪、到期、谁能换、失效了什么会坏。

**这里不写任何密钥的值。** 值只在 GitLab 的 CI 变量里。

读凭据文件时注意一条:命令必须让"解析失败"等于"什么都不输出"。写成 `解析 || cat 文件`
这种回落形式,一旦解析失败就会把明文打到日志里。

---

## 镜像仓库

镜像推到 `armdocker.rnd.ericsson.se/proj-rp-ps-ai/`。旧空间 `proj-rvc/rp-ai/` 属于 RVC 项目,
连同他们的 CI 服务账号 `rvccie` 一起不再使用。

`docker_user` 是 `ezhgmio`,一个人的账号,不是服务账号。

需要知道的一点:**这不是服务账号**,所以口令一变,所有仓的 `build` 与 `deploy` 会同时断,
而现象是 `docker login` 失败,离原因不近。换成该空间的服务账号(functional ID)可以去掉这个耦合,
但那是个可选项,不是欠债。

配在组 `rp-ai-miao` 的三个变量:

| 变量 | 内容 | 谁要用 |
|---|---|---|
| `docker_user` | 账号名 | 构建推镜像、部署拉镜像 |
| `docker_password` | 口令,掩码 | 同上 |
| `DOCKER_AUTH_CONFIG` | `{"auths":{"armdocker.rnd.ericsson.se":{"auth":"<base64 账号:口令>"}}}` | **runner 拉作业自己的镜像**要用它,缺了作业根本起不来 |

三个都**不能设成受保护变量**:`build` 在任意分支上跑,受保护变量只在保护分支可见,
特性分支的构建取不到。

**`NPM_AUTH_B64` 目前没有配。** 只有前端构建(`kind: web`)要它,它指向的 npm 空间是
`proj-bce-npm` —— 别人的空间,当前账号未必有权限。前端下次要重新构建时先配上并验证。

拉基础镜像与第三方镜像走 armdocker 上的 Docker Hub 代理(`dockerhub-ericsson-remote`),
与推送空间是两回事。当前账号对这个代理有读权限(基础镜像已用它构建成功)。

### 换空间是怎么做的,以后再换照这条路

没有重建任何服务:在跑的镜像本来就在机器本地,打上新空间的标签推上去,digest 一模一样,
再重放版本集合,容器只是换了镜像引用。

三个坑:

* swarm 节点**按 digest 拉取**,本地只有 digest 引用没有标签引用,`docker tag 仓库:标签` 会报
  `No such image`。要用 `docker tag 仓库@sha256:... 新仓库:标签`,digest 从 `docker service inspect` 取。
* 判定器的镜像只在老机器上、登录态只在 manager 上,凑不齐时让 CI 重建那一个镜像更快。
* 重放版本集合原来**不同步环境描述**,所以"重放成功但拉的还是旧路径"。已修:重放前先 rsync 环境描述。

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

### rp-ai-user-tools 的组访问令牌

用户上传 Tool 包时,门户要在 `rp-ai/rp-ai-user-tools` 组下建项目。**和上面那个是两个凭据。**

* 配在 `AUDIT_PROD_ENV` 和 `AUDIT_TEST_ENV` 的 `GITLAB_USER_TOOLS_TOKEN`
* 角色与 scope 和 agents 那个一致:Owner + `api`,`write_repository`。Owner 不是为了建项目
  (Maintainer 就够),是为了**推送失败后归档**那个刚建出来的空项目
* 失效了会坏:只影响 Tool 包上传,Agent 包与技能相关功能不受影响

为什么不能合成一个:组访问令牌只能访问它所属的那一个组,所以两个组必然两个令牌。唯一的
"一个令牌"方案是建在父组 `rp-ai` 上,而那个令牌顺带拿到技能仓和 sync 那两个组的写权限 ——
比任何一个功能需要的都大,也正是当初把这类凭据从个人 PAT 拆出来要避免的事。

还有一件事和这两个令牌无关但会一起坏:**门户下载包用的是 `GITLAB_TOKEN`(全域只读的个人
PAT),不是这两个组令牌。** 那个 PAT 在新组里没有可读身份时,现象是上传成功、审核页正常,
而别人点下载拿到 GitLab 的 403/404。

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
删掉变量不影响那个令牌本身。

### skill-audit-engine-read

生产技能仓上的第二个只读令牌,2026-08-20 之后再没被使用过,不在任何环境变量里。
`skill-audit-engine-read-prod` 才是引擎在用的那个。

建议:确认无人使用后删除。

---

## 挂在个人账号上的东西

### 个人 GitLab 令牌

`glab` 用的那个个人令牌 **2026-08-31 到期**。所有文档里的命令都不要依赖它 ——
运维动作应当走流水线,或者用上面那些项目令牌。

### 机器上的 ssh 公钥

manager 节点(`node-100-78-224-45`)的 `authorized_keys` 里有一行个人公钥,对应账号有免密 sudo。
老机器(`node-100-77-162-66`)的 `authorized_keys` 里没有,走的是别的机制。

接手的人要能上这两台机器,得把自己的公钥加进 `authorized_keys` —— 部署流水线用的是
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
