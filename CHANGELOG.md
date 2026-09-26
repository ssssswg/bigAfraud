# 变更与版本记录（Changelog）

> 按**功能版本**组织：每个版本记录一次有意义的迭代（新增能力 / 优化 / 修复闭环），
> 修复与优化并入所属版本，不作为独立版本单独记录。重大发布另见 `RELEASE_NOTES.md`。

---

## v1 ｜ 2026-09-25 · 数据源与策略扩展

### 新增能力
- **baostock 免费数据源**：`utils/data_sources.py` 新增 `BaostockDataSource`（行情/K线/股票列表/行业），`utils/data_fetcher.py` 接入并纳入多源容错链，`config/data_sources.json` 配置启停与优先级；行业数据由 baostock 兜底（`fetch_stock_industry_map` 申万一级行业 → `stock_basic.industry`）。
- **KHunter 策略补齐**：新增 4 个选股策略（`LeaderStrategy` 龙头 / `MainUptrendDipBuyStrategy` 主升低吸 / `LowTD9Strategy` 低位九转 / `OversoldReboundStrategy` 超跌反弹）与 2 个交易策略；新增 20 份策略说明书（`strategy/spec/`），同步 `config/strategy_params.yaml`、`strategy_order.yaml`、`strategy_name_mapping.yaml`、`strategy_kelly_config.yaml`、`support_methods.yaml` 与 `README.md`。
- **Tushare 统一客户端与限流**：新建 `utils/tushare_client.py`（统一 token、`_DataApi__token`/`_DataApi__http_url`、全局 `RateLimiter` 380 次/分）；全项目 19 个文件 `ts.pro_api(...)` 统一替换为 `get_pro(...)`，限流收敛为 `get_pro` 内唯一限速点。

### 增强
- **市值腾讯兜底**：`get_stock_market_cap` 在 Tushare 无 token / 失败时降级腾讯 `qt.gtimg.cn`（字段[45] 总市值，单位亿元），避免新股票市值落 0。

### 修复
- **数据库 schema**：`data/DataSql.sql` 建表含 `key_date DATE`，现存库已 `ALTER TABLE` 加列，修复 khunter 查询 `no such column: key_date`。
- **trading / utils / web 三层检查**：`buy_signal_judger.py` 未闭合 f-string 补引号；`fund_flow_updater.py` 行业/板块资金流列对齐 Tushare 标准；`backtest_dao.py` 交易表列名改 `profit_loss` / `return_rate`。
- **werkzeug 访问日志过滤**：`log_config.py` 新增 `_WerkzeugAccessFilter`，只拦截含 `"` 的访问请求行，保留启动地址等关键日志。

---

## v2 ｜ 2026-09-26 · Tushare 镜像接入适配

针对 Tushare 自定义镜像（`config/tushare.md` 规范：`_DataApi__token` + `http_url='https://tuaremax.top'` + 300~400 次/分限流，统一由 `utils/tushare_client.get_pro` 承担）接入后的数据链路修复，均已实测验证。

### 接入修复
- **配置文件编码（gbk → utf-8）**：4 个评分器与 `trade_date_utils` 读取 `config/tushare_config.json` 统一 `encoding='utf-8'`（Windows 默认 gbk 解码含中文注释的 UTF-8 文件失败 → token 加载失败、交易日回退周末判断）。修复后资金面/板块评分恢复、交易日按真实日历判断。
- **市值数据错乱**：`get_stock_market_cap` 调 `daily_basic` 未带 `trade_date`，镜像返回多日历史多行市值。改为显式传入最新交易日（`trade_date_utils.get_previous_trading_day`）。验证：全市场市值正确（000001 = 2192.87 亿元）。
- **龙头策略涨停池取数**：`scripts/collect_limit_up_pool.py` 自定义 `def get_pro()`（0 参数）同名覆盖统一客户端 → `get_pro(token)` 报参数错误。删除冗余定义，统一走 `utils.tushare_client.get_pro`。验证：`fetch_limit_up_day(pro,'20260924')` 返回 52 只涨停股。
- **回测股票池移除配置**：`config/pool_removal_config.yaml` 补齐 5 个新策略（含类名 + 中文名双 key），修复"策略 主升低吸策略 未配置股票池移除参数"。验证：类名与中文名均命中。

### 性能优化：回测缓存加速
- **背景**：回测评分逐股票实时调 Tushare，实测 19.4 分钟仅完成 119 只（~9.8 秒/只）、2067 次 POST（每只约 17 次往返、无连接复用）。
- **修复 4 项**：
  1. **按交易日全市场批量缓存**（新增 `utils/tushare_bulk_cache.py`）：top_list / block_trade / stk_shock / moneyflow_ths 改为按 `trade_date` 拉全市场缓存，同评分日多股共享（top_list 618→每日 1 次、moneyflow_ths 272→每日 1 次）；
  2. **交易日历全局缓存**（`utils/trade_date_utils.py`）：`is_trading_day`/`get_trading_days` 加进程内缓存（trade_cal 216→少量）；
  3. **HTTPS 连接复用**（`utils/tushare_client.py`）：`_inject_session_pool` 将 tushare 内部 `requests.post` 替换为共享 Session（连接池），隔离在该库内部、幂等；
  4. **低频接口**（fina_indicator 等）保留现有 `self._cache`。
- **时效保证**：缓存以交易日为失效单位——同交易日首次拉取后复用、跨日 key 变化自动重拉（Tushare 每日更新一次）；`daily_bulk.invalidate` 可强制刷新。评分器 `MemoryCache` 由 5 分钟 TTL 改为**当日 24:00 自然日失效**。
- **验证**：编译通过；mock 确认同交易日只拉 1 次、跨日自动重拉、连接池注入幂等。

### 修复（连接池复用的副作用闭环）
- **Tushare 代理连接失败**：共享 Session（持久对象，`trust_env` 默认读系统/环境代理）在服务启动时捕获系统代理 `127.0.0.1:7688`，代理软件关闭后进程仍走该代理 → 全部外呼 `WinError 10061`。修复：共享 Session 设 `trust_env=False` 强制直连（直连 tuaremax.top 实测 200）。**需重启 web 服务生效**；若必须走代理访问则需保持代理软件运行。

---

## v3 ｜ 2026-09-26 · 数据本地化与性能优化

### 数据本地化（入库 + 评分读库优先）
- **资金流入库**：`data_initializer._init_fund_flow_data` 由空实现改为调用 `FundFlowUpdater` 拉近 60 交易日写 `stock_fund_flow` / `industry_fund_flow` / `sector_fund_flow`；评分保留 `daily_bulk` 按日全市场缓存（表字段为净流入、与 moneyflow_ths 买卖分解不匹配，读库边际收益小，用户确认）。
- **事件 9 接口入库**（新增 `utils/event_data_fetcher.py`）：forecast / express / repurchase / stk_holdernumber / stk_holdertrade / share_float / stk_seasoned / top_list / top_inst（doc_id 45/46/124/166/175/160/494/106/107）按公告日/交易日单日全市场拉取写 `stock_event`；不支持的接口（stk_seasoned）探测后自动禁用；按 (event_type, event_date) 先删后插幂等；`_init_event_data` 全量拉近 90 天、**增量只拉最新日期之后**。
- **事件应用到评分**（`event_scorer`）：`_query_local_events` 读库优先（forecast / 股东增减持 / 股票回购），新增 4 个 `_check_*`（限售解禁 -10、股东户数环比 ±10、龙虎榜机构 ±10、业绩快报 ±8/15），扩展 `EVENT_VALIDITY`/`POSITIVE_SCORES`/`NEGATIVE_SCORES`/`LOCAL_EVENT_TYPES`；清理读库优先重复注入。
- **基本面财务本地化**（新增 `utils/financial_data_fetcher.py`）：`fina_indicator` 镜像**仅接受 ts_code**（不支持 period 批量）→ `fetch_all_stocks()` 逐股入库 `stock_financial`、增量 `fetch_for_stock`；`fundamental_scorer._fetch_fina_indicator` 读库优先。`_init_financial_data` 改为**后台线程（daemon）**执行，主流程立即返回。
- **验证**：单日事件入库 1296 条；评分读库命中时 Tushare `_pro` 不触发（holdertrade/repurchase/forecast + 4 新事件）；000001 入库 8 期财务、读库评分 80；综合评分冒烟 5 维度正常（000001=46.1、000504 资金面一票否决=-100）；重复跑幂等。

### 性能优化：K线更新并发拉批
- **背景**：09-26 数据更新（K线 5400 只）变慢。量化对比（同参数 count=60/批~100 只）：09-25 请求间隔 **p50=6 秒/批** → 09-26 **p50=25 秒/批**，慢约 4 倍。根因：**TickFlow 免费 API 服务端响应变慢/限流**（外部因素，非代码/代理/数据量）。
- **优化**（`utils/kline_updater.py`）：原主循环**逐批串行**（54 批排队）→ **ThreadPoolExecutor 并发拉批（默认 3 路）+ 主线程串行保存**（不争 DB 写锁）。新增 `_fetch_batch_data`（并发拉取）/ `_save_batch_data`（保存 + 腾讯降级），保留原方法备用。
- **预期**：约 22 分钟 → 8~11 分钟；TickFlow 服务端恢复后（6s/批）可回到 5 分钟内。
- **验证**：编译通过；monkeypatch 模拟每批 1.5s、2 批：全程 3.03s（就绪检查 1.5s + 2 批并发 1.5s，串行应 4.5s），保存链路正常（added=6）。
