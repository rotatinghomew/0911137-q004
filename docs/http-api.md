# 本地接口

- `GET /api/catalog`：资料名称、可选业务范围、业务时点范围、表计数与资料指纹，以及可选指标定义、方案清单与判定规则文本。
- `GET /api/view`：描述性摘要，可选 `cutoff` 整数和 `group` 业务范围；未知筛选项返回 400。不消耗确认性额度。
- `GET /api/source?table=assignments`：按实际资料表名称查询原始记录；不存在的表返回 404。
- `POST /api/analyses`：JSON 对象包含 `title` 与 `options`，重新计算并保存描述性版本，成功返回 201。
- `GET /api/analyses`、`GET /api/analyses/{id}`：描述性版本目录与不可变回看。

确认性分析：

- `POST /api/designs`：JSON 对象包含 `title`、`preregistered` 与 `options`；返回 201 及计划详情（含 `confirmatory`、`note`、`looks_remaining`）。`preregistered=false` 保存为探索计划，没有确认性额度。
- `GET /api/designs`：计划目录，含每次计划已用查看、剩余额度与是否已被替换。
- `GET /api/designs/{id}`：计划快照、`can_look`、`looks`（已打开的查看摘要）。
- `GET /api/designs/{id}/preview?cutoff=…`：预备检查，只返回成熟度、缺失、错误率分配；不计算效应或判定，也不占用额度。
- `GET /api/designs/{id}/schedule`：完整 α 分配表，用于复核错误率。
- `POST /api/designs/{id}/looks`：JSON 对象包含 `cutoff`；成功返回 201，重复打开同一时点与原资料指纹返回同一记录（200）。查看时点必须晚于已打开过的时点；额度用尽或计划已被替换返回 400。
- `GET /api/designs/{id}/looks/{look_index}`：返回该次查看的完整冻结结果（`result`），不再重算。

`options` 的确认性字段为 `cutoff`、`group`、`control`、`treatments`、`metrics`、`roles`、`alpha`、`max_looks`、`min_observed`、`max_missing_rate`；未知字段返回 400。指标的方向、业务门槛与成熟天数在保存计划时从资料定义冻结，之后不再跟随资料变化。

API 的 `rows` 是描述性领域行，`summary` 是当前汇总，`notes` 是业务口径提示，`options` 是实际采用的筛选值。无记录使用空列表，不能把无观测偷偷表示为有效的零值。错误 JSON 的 `error` 可直接在页面反馈。统计口径详见 `confirmatory.md`。

这是本地单用户应用，不包含认证与外网部署设置。默认只监听回环地址，容器中可显式监听所有接口，但宿主机端口建议只绑定回环地址。
