# 多方案实验评审台

用于查阅多方案实验评审的业务记录、当前摘要与保存的分析版本。现有功能包括业务范围筛选、历史时点查阅、原始资料展开以及不可变分析结果回看。统计口径见 `docs/data-contract.md`，HTTP 结构见 `docs/http-api.md`。

在此之上增加了确认性实验分析（`docs/confirmatory.md`）：先保存分析计划，再按计划打开结果。
收益指标与保护指标各自按业务方向判定；多个待评方案共用同一对照，比较数进入 Bonferroni 校正；
重复查看按 O'Brien–Fleming 消耗分配 α，跨查看与比较的族错误率不超过计划设定的 α。
未成熟、待可见与空值都不补零。计划一经打开结果，后改的指标或人群只能保存为探索，不能沿用原
确认性结论，也不能通过换筛选重新获得查看额度。每次查看都冻结并展示当时的计划、资料指纹与数据范围。

## 运行

需要 Python >=3.11,<3.12，不依赖特定补丁版本。应用运行只使用标准库，浏览器 JavaScript 无构建步骤。以下命令在项目根目录执行：

```sh
python3 -m experiment_family
```

浏览器打开本机 8080 端口即可操作。资料默认来自 `fixtures/data.json`；分析结果默认保存在 `.state/analyses.sqlite3`。可用 `--data` 指定同口径资料，`--state` 指定状态文件，`--port` 调整监听端口。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

测试涵盖领域口径、真实 HTTP 往返、错误筛选、跨进程存储及不可变保存；确认性分析另覆盖 α 分配求和、
共用对照的校正水平、收益与保护双门槛、未成熟与缺失不补零、查看额度与重复时点、计划替换与探索降级、
并发查看时序以及结果可追溯。页面直接消费与测试相同的 API，不使用预录结果。

浏览器验证工具与产品运行依赖分开锁定；准备一次后可重复执行筛选、保存、刷新回看和资料查阅：

```sh
python3 -m pip install -r requirements-test.txt
python3 -m playwright install --with-deps chromium
python3 tests/browser_smoke.py
```

前两条是首次测试环境安装，完成后无需重复执行。基础容器只运行产品和标准库测试；浏览器测试在安装了 `requirements-test.txt` 与 Chromium 的开发环境运行。

也可使用无第三方 JavaScript 依赖的 npm 工作区入口：`npm ci --ignore-scripts` 后，`npm start -- --host 127.0.0.1 --port 8080` 启动同一个 Python HTTP 服务，`npm run test:browser` 执行上述浏览器回归。npm 仅负责命令编排，不是产品运行依赖；不要把 `web/` 单独作为静态站点运行，页面需要同源 API。

## 容器

```sh
docker build -t experiment-family:local .
docker run --rm -p 127.0.0.1:8080:8080 experiment-family:local
```

基础镜像以内容摘要固定，Python 打包依赖固定在 `requirements-build.txt`。容器构建完成后可断网运行；若要保留容器外分析版本，可将宿主目录挂载到 `/app/.state`。

## 快速业务核对

```sh
python3 -m experiment_family --demo
```

此入口启动真实本地 HTTP 服务，计算当前摘要，保存后重新读取再退出。输出依赖当前资料，不预先指定统计结论。初次阅读可先在页面调整时点或业务范围，再保存和回看分析；测试和该核对入口可在九十秒内完成，不包含镜像构建时间。
