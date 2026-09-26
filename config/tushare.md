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
4. **数据本地化（回测加速）**：评分优先读本地库，避免逐股实时调接口。
   - `utils/event_data_fetcher.py`：9 类事件接口（forecast/express/repurchase/stk_holdernumber/stk_holdertrade/share_float/stk_seasoned/top_list/top_inst）按公告日/交易日单日全市场拉取，写入 `stock_event`；按 (event_type,event_date) 先删后插幂等；**镜像不支持的接口（如 stk_seasoned）首次失败自动禁用**。
   - `trading/event_scorer.py`：forecast/股东增减持/股票回购 命中 `stock_event` 直接读库（`_query_local_events`），不再实时调接口；top_list/block_trade/stk_shock 走 `utils/tushare_bulk_cache` 按交易日全市场缓存。
   - `utils/data_initializer._init_fund_flow_data` / `_init_event_data`：初始化/增量时分别入库近 60 日资金流、近 90 日事件。
   - 时效：事件按公告日入库，评分按有效期窗口查询，均为 Tushare 当日最新。

5. **事件接口可用性（实测，2026-09-26）**：镜像支持 top_inst / share_float / stk_holdernumber / top_list / stk_holdertrade / repurchase（单日 20260924 合计入库 1296 条）；`stk_seasoned`（增发）接口不存在已自动禁用；forecast/express 视当日是否有公告而定（业绩预告/快报稀疏，常见返回空属正常）。

6. **`fina_indicator` 仅支持 ts_code**：镜像接口不支持 `period` 参数做全市场批量（报"必填参数, ts_code"），必须传 `ts_code` 逐股拉取。财务数据本地化（`utils/financial_data_fetcher`）按股票逐股入库 `stock_financial`，评分读库优先；初始化全市场一次性，增量只拉新股票。
