# 修复与变更记录（Changelog）

> 本文件记录项目历次 bug 修复与功能维护，供排障与回溯。重大版本发布记录见 `RELEASE_NOTES.md`。

## 2026-09-25

代码改动与优化记录（数据源接入、策略补齐、架构统一、问题修复）。

### 1. 新增 baostock 免费数据源
- `utils/data_sources.py` 新增 `BaostockDataSource`（行情/K线/股票列表/行业），`utils/data_fetcher.py` 接入并纳入多源容错链，`config/data_sources.json` 配置启停与优先级。
- 行业数据由 baostock 兜底：新增 `fetch_stock_industry_map`（申万一级行业），`data_initializer._write_industry_to_stock_basic` 写入 `stock_basic.industry`。

### 2. 市值腾讯兜底
- `get_stock_market_cap` 在 Tushare 无 token / 失败时降级腾讯 `qt.gtimg.cn`（字段[45] 总市值，单位亿元），避免新股票市值落 0。

### 3. 补齐 KHunter 策略
- 新增 4 个选股策略：`LeaderStrategy`（龙头）、`MainUptrendDipBuyStrategy`（主升低吸）、`LowTD9Strategy`（低位九转）、`OversoldReboundStrategy`（超跌反弹），以及 2 个交易策略。
- 新增 20 份策略说明书（`strategy/spec/`），同步 `config/strategy_params.yaml`、`strategy_order.yaml`、`strategy_name_mapping.yaml`、`strategy_kelly_config.yaml`、`support_methods.yaml` 与 `README.md`。

### 4. Tushare 统一客户端与限流
- 新建 `utils/tushare_client.py`：统一读取 token、设置 `_DataApi__token` / `_DataApi__http_url`、全局 `RateLimiter`（380 次/分）与 `_ThrottledPro` 代理。
- 全项目 19 个文件 `ts.pro_api(...)` 统一替换为 `get_pro(...)`；限流收敛为 `get_pro` 内唯一限速点（移除 `fund_flow_fetcher` 手动 sleep、`_TushareRateLimiter` 降级 no-op）。

### 5. 数据库 schema 修复
- 补齐 `key_date` 列：`data/DataSql.sql` 建表含 `key_date DATE`，现存库已 `ALTER TABLE` 加列，修复 khunter 相关查询 `no such column: key_date`。

### 6. trading / utils / web 三层检查修复
- `utils/buy_signal_judger.py`：未闭合 f-string 补引号。
- `utils/fund_flow_updater.py`：`_save_industry` / `_save_sector_fund_flow_records` 列对齐 Tushare 标准。
- `trading/backtest_dao.py`：`save_trade` 列名改 `profit_loss` / `return_rate`。

### 7. werkzeug 访问日志过滤
- `utils/log_config.py` 新增 `_WerkzeugAccessFilter`：只拦截含 `"` 的访问请求行，保留启动地址等关键日志。

---

## 2026-09-26

针对 Tushare 自定义镜像（`config/tushare.md` 规范：`pro._DataApi__token` + `pro._DataApi__http_url = 'https://tuaremax.top'` + 300~400 次/分限流，统一由 `utils/tushare_client.get_pro` 承担）接入后的数据质量问题修复，均已实测验证。

### 1. 配置文件编码修复（gbk → utf-8）
- **问题**：4 个评分器（`moneyflow_scorer` / `sector_scorer` / `fundamental_scorer` / `event_scorer`）与 `trade_date_utils` 读取 `config/tushare_config.json` 未指定编码，Windows 默认 gbk 解码 UTF-8 文件（含中文注释）失败 → token 加载失败、交易日判断回退周末判断。
- **修复**：统一为 `encoding='utf-8'`。
- **影响**：资金面 / 板块评分恢复，交易日按 Tushare 真实日历判断。

### 2. 市值数据错乱修复
- **问题**：`utils/stock_data_fetcher.get_stock_market_cap` 调用 `daily_basic` 未带 `trade_date`，镜像接口返回多日历史数据（同一股票多行不同市值）→ 市值大量错漏。
- **修复**：`daily_basic` 显式传入最新交易日（由 `trade_date_utils.get_previous_trading_day` 计算）。
- **验证**：全市场返回正确市值（如 000001 平安银行 = 2192.87 亿元）。

### 3. 龙头策略涨停池取数修复
- **问题**：`scripts/collect_limit_up_pool.py` 中自定义 `def get_pro()`（0 参数）同名覆盖了 `utils.tushare_client.get_pro`，导致 `get_pro(token)` 报 `takes 0 positional arguments but 1 was given`，涨停池取数全部失败。
- **修复**：删除冗余自定义 `get_pro`，统一使用 `utils.tushare_client.get_pro`（自动读取 token + 限流）。
- **验证**：`fetch_limit_up_day(pro, '20260924')` 返回 52 只涨停股。

### 4. 回测股票池移除配置补齐（pool_removal）
- **问题**：回测主升低吸等 KHunter 新策略报 `策略 主升低吸策略 未配置股票池移除参数`——`config/pool_removal_config.yaml` 未同步 5 个新增策略；且新策略候选以中文名传入，而配置用类名作 key。
- **修复**：`config/pool_removal_config.yaml` 补齐 5 个新策略（龙头/主升低吸/低位九转/超跌反弹/趋势共振反转），同时加类名与中文名 key。
- **验证**：yaml 语法通过，类名与中文名均命中（如 主升低吸策略→5天、龙头策略→5天）。
