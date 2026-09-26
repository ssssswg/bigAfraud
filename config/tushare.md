``` python
import tushare as ts
#tushare版本 1.4.24
token = "你的token"

pro = ts.pro_api(token)

pro._DataApi__token = token
pro._DataApi__http_url = 'https://tuaremax.top'  # 保证有这个代码，不然不可以获取

# #  正常使用（与官方API完全一致）
df = pro.daily(ts_code='000001.SZ', start_date='20240101', end_date='20240131')


print(df)
```
修改2行代码即可
调用频率每分钟控制在300~400次

---

## 数据质量实践要点（2026-09-26）

使用上述镜像时，注意以下要点以保证数据质量（已在代码中落实）：

1. **配置文件编码**：读取 `config/tushare_config.json`（UTF-8、含中文注释）必须显式 `encoding='utf-8'`，否则 Windows 默认 gbk 解码失败，导致 token 加载失败、交易日判断回退周末判断。

2. **`daily_basic` 必须带 `trade_date`**：`pro.daily_basic(fields='ts_code,total_mv')` 不带 `trade_date` 时，镜像接口会返回多日历史数据（同一股票多行不同市值），导致市值错乱。应传入最新交易日：`pro.daily_basic(trade_date='YYYYMMDD', fields='ts_code,total_mv')`。

3. **统一使用 `utils.tushare_client.get_pro()`**：全项目经 `get_pro()` 统一初始化（自动读取 token + 设置 `_DataApi__http_url` + 全局限流 300~400 次/分），调用侧不要再自定义 `ts.pro_api` / `get_pro`（此前 `collect_limit_up_pool.py` 自定义 `get_pro` 同名覆盖 import，导致涨停池取数报 `takes 0 positional arguments but 1 was given` 而全部失败）。