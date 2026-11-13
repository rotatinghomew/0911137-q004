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
