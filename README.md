# platform-ci

`rp-ai-miao` 下后端服务共用的构建配方、流水线模板与各环境的部署描述。
抽象与判据见 [DESIGN.md](DESIGN.md)。

| 路径 | 归属 | 内容 |
|---|---|---|
| `docker/Dockerfile.base` | 平台 | 唯一的构建配方,构建成基础镜像 |
| `templates/python-service.yml` | 平台 | 服务流水线:lint / test / 构建并推镜像 |
| `deploy/<env>/compose.yml` | 环境 | 这台机器上跑什么:镜像标签 + 主机绑定 |

## 服务仓怎么接

```yaml
include:
  - project: rp-ai-miao/platform-ci
    ref: v0.2.0                      # 锁 tag,不要用 main
    file: templates/python-service.yml
    inputs:
      service: product-structure-information
      app_module: hw_change_analysis.app:app
```

服务仓里**不需要** Dockerfile,也**不需要** compose。可选输入(都有默认值):
`version`、`python_version`、`base_tag`、`apt_packages`、`extra_pip`、`ruff_version`。

## 约定

- **锁版本**:消费方引用 tag。五个仓共用一份模板,不锁版本时一次误改会同时打断五条流水线。
- **标签不可覆盖**:标签含 commit sha;构建前先查,已存在则跳过。
- **默认值优先于必填**:只有真正属于服务身份的东西才是必填(镜像名、应用路径)。
- **部署不在服务仓**:改 `deploy/<env>/compose.yml` 里的一行标签即发布,revert 即回滚。
