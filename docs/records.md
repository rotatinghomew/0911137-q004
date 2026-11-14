# 记录样式

以下为当前业务文件中的实际记录，字段口径见 `data-contract.md`；字段标识保持业务接口写法。

### `assignments`

字段：`arm`、`assigned_at`、`segment`、`unit`。

```json
{
  "unit": "u000",
  "arm": "control",
  "assigned_at": 1,
  "segment": "新客"
}
```

### `outcomes`

字段：`known_at`、`metric`、`unit`、`value`。

```json
{
  "unit": "u000",
  "metric": "revenue",
  "value": null,
  "known_at": 8
}
```

### `metrics`

字段：`direction`、`margin`、`maturity_days`、`metric`、`name`。

```json
{
  "metric": "revenue",
  "name": "收益",
  "direction": "higher",
  "maturity_days": 7,
  "margin": 0
}
```

### `looks`（业务文件中的已打开日期）

字段：整数业务时点。

```json
20
```

### 确认性记录（本地状态库）

业务文件之外，确认性分析在本机状态库保存两类不可变记录：

`designs`：计划冻结快照，含 `control`、`treatments`、`group`、`metrics`（方向、`margin`、成熟天数、角色）、`alpha`、`max_looks`、观测下限、缺失率上限与判定规则。

```json
{
  "control": "control",
  "treatments": ["basic", "plus"],
  "group": "新客",
  "metrics": [
    {"metric": "refund", "name": "退款率", "direction": "lower", "margin": 0.02, "maturity_days": 30, "role": "guardrail"}
  ],
  "alpha": 0.05,
  "max_looks": 3,
  "min_observed": 2,
  "max_missing_rate": 0.2
}
```

`looks`：每次正式打开结果一行，含 `design_id`、`look_index`、`cutoff`、`group`、`digest`、`created_at` 与完整 `result`。同一计划的 `look_index` 唯一，额度与次序由该表计数，改页面筛选不会新增行。
