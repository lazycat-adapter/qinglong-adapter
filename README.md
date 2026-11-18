# 青龙面板 · 懒猫微服自动更新适配器

维护懒猫官方应用商店现有应用 `cloud.lazycat.app.qinglong`。上游是 [whyour/qinglong](https://github.com/whyour/qinglong)，共享工作流使用 [wcaqrl/lazycat-action@v1](https://github.com/wcaqrl/lazycat-action)。

## 定制与构建

- 旧版定制以 [懒猫青龙源码](https://gitee.com/lazycatcloud/qinglong) 和 [旧版编译产物](https://gitee.com/lazycatcloud/qinglong-static) 为参考。新版首次设置预填可编辑的管理员用户名，用户在面板中设置自己的密码。已初始化的工作空间继续使用原凭据。懒猫 Inject 在首次设置、登录或改密成功后按当前微服用户记住凭据，后续在青龙登录页自动填写和提交，青龙的两步验证仍有效。仍在使用旧默认账号密码的工作空间会进入青龙初始化向导，原有数据保留。
- 每次选择稳定 Release 和 Git 提交，从该提交应用 [首次使用补丁](image/first-run.patch)，使用项目声明的 Node.js 和 pnpm 版本编译前端 `static/dist`、后端 `static/build` 和构建信息。仓库保存补丁和构建配方，Runner 生成编译产物。
- 运行环境采用同版的上游 Docker Hub 镜像，通过镜像内部 Git 树与发布提交的树逐项对应，并以 amd64 摘要锁定。Runner 将配套的新产物放入 `/ql/static`，然后验证首次安装、旧版数据升级及任务执行。
- 通过验证后，构建出的公开镜像暂存至 `ttl.sh`，共享 Action 用 `LZC_API_TOKEN` 转存到 `registry.lazycat.cloud`。最终打包检查官方镜像与已测试镜像的运行配置及文件系统，携带仅对应本次版本的发布说明送审，并生成 GitHub Release 的 LPK 下载附件。

旧商店 LPK `0.1.3` 内部运行青龙 `2.17.11`。新版使用 `application.multi_instance: true`，由 LzcOS 将请求转到当前用户实例的 `gateway`，并基于路由检查服务是否就绪。每个实例通过 `/lzcapp/var/qinglong:/ql/data` 保存自己的工作区。LPK v2 要求 LzcOS 1.5.0 或更新版本。 [多实例](https://developer.lazycat.cloud/advanced-multi-instance.html) · [服务启动依赖](https://developer.lazycat.cloud/advanced-depends.html) · [文件访问](https://developer.lazycat.cloud/advanced-file.html)

从旧商店包升级时，请在新版首次启动前给青龙授予读取用户文稿的权限。每个实例启动时会将其原有 `/lzcapp/run/mnt/home/.ql` 复制到空的应用数据目录。旧目录保留，导入中断会重试，已有新版数据的实例继续使用自己的工作区。缺少文稿授权时只能使用新版数据目录，因此旧用户应在首次启动前完成授权。 [旧包配置](https://github.com/lazycatcloud/appdb/blob/master/qinglong/lzc-manifest.yml) · [文稿权限](https://developer.lazycat.cloud/spec/package.html)

## 使用和测试

推送到 `main` 触发仅检查。GitHub 计划任务每六小时检查，有新版本且通过验证时送审。手动运行 **Update QingLong for LazyCat** 时，默认勾选 `dry-run`。可填写 `test-tag` 额外运行真实镜像构建与本地升级测试，或者取消 `dry-run` 执行官方发布流程。开发者 PAT 配置在 Actions 的 Repository secret `LZC_API_TOKEN`。

```bash
python3 -m venv .lazycat-action/venv
source .lazycat-action/venv/bin/activate
python -m pip install -r requirements-test.txt
python -B -m unittest discover -s tests -v
node --test tests/injects.test.cjs
python -B scripts/prepare.py --test-tag v2.24.0
```

真实镜像测试需要 Linux amd64 Docker、Docker Hub、npm 和懒猫官方 CDN。测试使用随机账号和临时卷，结束后清理测试容器和卷。
