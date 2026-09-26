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
### 修复：重新初始化结果统计缺失（成功/失败/总数显示 0）
- **现象**：数据管理-初始化数据 走"重新初始化"（REINIT）后显示"初始化成功！"，但成功数量/失败数量/总数量均为 0。
- **根因**：`data_collection_service._run_reinit`（重新初始化专用路径）完成块**缺少 `statistics` 统计、`success=1`、`running=False` 复位**（对比 `_run_initialization` 有完整完成块 + finally），前端 `data.statistics` 为空 → 三块全 0，且完成后 running 仍为 true。
- **修复**：`_run_reinit` 完成块补齐 `statistics = get_tables_stats()`、`success=1`、`progress=100`、`message`；新增 `finally` 复位 `running=False`；完成/失败均推送最终状态。
- **验证**：模拟 `_run_reinit` 完成态完整（status=completed、running=False、statistics.success=5400/stock_kline=3927687）；编译通过。需重启服务生效。

### 修复：基础数据页部分股票最新价显示 0 / 数据条数 0（K线缺失）
- **现象**：数据管理-基础数据页 平安银行（000001）最新价 ¥0、数据条数 0（万科A 正常）。用户怀疑"更新出问题"。
- **排查链**（日志 + 数据库 + 代码 + 复现实验逐层钉死）：
  1. 数据库实测：**75 只股票 K线=0**（`stock_basic` 有记录但 `stock_kline` 无任何行）；表结构确认 `stock_basic.code` / `stock_kline.code` 才是股票代码列（无串写）；实时拉数验证 000001/000156/000338 价格一致，**排除 symbol↔code 串写**。
  2. 日志解析（22:43 全量初始化 53 批次）：事务日志逐批"开始→提交成功"，无回滚/锁定；"命中 100/100"、无保存失败。
  3. **两类缺失**：**15 只不在任何批次 URL**（`000991/001235/001246/002257/002525/002720/300060/300361/300728/301660/301716/600349/603302/603361/688688`，即 00:33 new_stock_detector 报告的"新股票"）；**60 只在批次 URL 中却未入库**（集中 22:47-22:50）。
  4. **15 只真相**：名称即铁证——"无效里得""无效恒久"（名称带"无效"）、"蚂蚁集团 688688"（从未上市）、"奥赛康/浙江国祥/胜景山河/立立电子"等均为**历史上 IPO 被否/撤单的申购残留代码**；经 **Tushare（5569 上市+340 退市）与 baostock 双重核验不存在** → **akshare 降级源（东财接口）混入无效申购代码**，写入 stock_basic 后永远无 K线。
  5. **60 只真相**：当前磁盘代码复现实验（临时库 + 真实 TickFlow 拉 100 只含缺失样本）**100/100 全部入库**；同时复现 TickFlow 100 只批次响应体在 **~4.4MB 处被截断**导致 JSON 解析失败（重试后成功）→ 22:43 那轮为**网络/响应瞬态导致部分批次数据缺失**（非当前代码保存逻辑缺陷）。
- **修复**：
  1. **数据修复**：60 只缺失股票已补拉入库（000001 恢复 750 条、最新 2026-09-24）；15 只无效代码已从 `stock_basic` 删除（无任何关联数据，删除安全）。
  2. **代码根治（黑名单）**：`utils/stock_data_fetcher.py` `get_all_stock_codes` 新增 `invalid_codes` 黑名单（15 只核验不存在的申购残留代码），Tushare/腾讯/akshare 三个源统一过滤；akshare 分支排除关键词增加"无效"。验证：get_all_stock_codes 返回 5203 只、无假代码残留。
  3. **自动补拉兜底**：`utils/data_initializer.py` `_init_kline_history_data` 保存失败日志 DEBUG→WARNING；记录 `saved_codes` 集合；**初始化完成后校验请求过的股票是否全部入库，缺失自动补拉**（TickFlow→腾讯降级，最多 1 轮），仍缺失仅 WARNING 不计入中断。
- **验证**：编译通过；冒烟测试（临时库 + 20 只真实拉取）20/20 入库无缺失；库内假代码清零。**黑名单与自动补拉需重启服务生效，数据修复已即时生效**。

### 修复：狩猎场保存结果与页面计算不一致（保存了旧缓存记录）
- **现象**：狩猎场计算显示 2 只（如 000411/002238），点"保存"提示"已保存 1 条记录"，且狩猎跟踪里查到的不是页面显示的那 2 只。
- **排查链**：
  1. 日志证据：保存请求 `timing_strategy=turtle`，但页面下拉框显示"顺势宝"（value=`macd_bollinger`）→ **计算与保存参数不一致**。
  2. 前端根因：`index.html` 存在**两个重复 id 的 `#timing-strategy` 下拉框**（其他页面 1119 行 + 狩猎场页 1696 行）。`calculate()` 用 `querySelector('#khunter-page #timing-strategy')`（读到 macd_bollinger），`saveResults()` 用 `getElementById('timing-strategy')`（读到**第一个**=turtle）→ 保存时传了 turtle。
  3. 后端根因：`KHunterAPI.save()` 内部**重新调用 `process()`**，而 `process()` 的缓存 `_check_cache` 以 **khunter 表已存记录为缓存**（`WHERE hunting_date=? AND timing_strategy=?`）→ 命中了**昨天（09-25 16:16）保存的 (2026-09-24, turtle) 旧记录 600000** → 保存了旧缓存而非用户当前计算的结果。
- **修复**：
  1. 前端 `saveResults()`：选择器改为 `#khunter-page #timing-strategy`（与计算一致）；**把当前计算结果 `currentResults` 一并传给保存接口**（所见即所得）。
  2. 后端 `KHunterAPI.save()`：新增 `results` 参数——**传入结果则直接保存**（不再走 process 命中缓存）；**未传结果则 `force_refresh=True` 强制重算**后再保存。
  3. `KHunterDataProcessor.process()`：新增 `force_refresh` 参数，为 True 时跳过 `_check_cache` 强制重新计算。
  4. `routes.khunter_save()`：接收并透传前端 `results` 字段（非列表时忽略，走强制重算）。
- **验证**：编译通过；mock 验证 save 两条路径——传 results 时 process 调用 0 次、直接保存 2 条；不传时 process 被调用且 `force_refresh=True`；`force_refresh` 跳过缓存逻辑存在；前端 JS 语法检查通过。**需重启服务 + 刷新浏览器生效**。
- **说明**：历史旧记录（如 600000/turtle）为用户此前保存的数据，予以保留；修复后重新保存会新增正确的 macd_bollinger 结果。

---

## v4 ｜ 2026-09-27 · 选股池回归全市场（创业板/科创板不再被排除）

### 修复：招标股份（301136）长期不被选股结果命中（3 个策略均缺失）
- **现象**：选股结果里一直没有"招标股份"，但用户此前见过该公司被 **2560战法 / 多金叉共振策略 / 趋势起点策略** 3 个策略同时选中（信号日 2026-09-24）。
- **排查链**（数据库 → 代码 → git blame → 实测逐步钉死）：
  1. 数据库实证：301136 K线 **750 条完整**（2023-08-23 ~ 2026-09-24），09-24 收盘 **13.30**/量 258730、09-23 收盘 11.84/量 48665——与用户提供的选股理由（13.30 > MA25 12.02、量能 2.58 倍等）**完全吻合**；`stock_selection_record` 中该股记录 **0 条**；09-24 全市场 2560战法 1 只 / 多金叉共振 4 只 / 趋势起点 9 只——**策略正常跑了，但从未对 301136 执行**。
  2. git blame 定位：`web_server.py` run_selection 的 `stock_codes` 过滤"只保留主板（600/601/603/605/000/001/002/003）"由 **62c2dc0「选股加速」引入**（初始版本 ae0fe0b 不过滤，全市场）；`main.py` select 命令同样过滤——**30 开头创业板、68 开头科创板全部被排除出选股池**。
  3. 实测验证：用当前策略代码 + registry + 数据库对 301136（截至 09-24）执行三个策略——**全部命中**，signals 与用户提供的选股理由**逐字一致**（股价突破25日均线、VOL_MA5=100333/VOL_MA60=73146、收盘价在MA10之上 13.30>12.16、涨幅12.33%、量能2.58倍；均线/KDJ/MACD 三金叉共振；MACD金叉+布林带上穿+阳线+站上5日线+量能放大）→ **策略与数据均无问题，纯粹是股票池过滤排除所致**。
- **修复**：`web_server.py` run_selection 与 `main.py` select 的过滤条件由"仅主板"改为 **沪市主板 + 深市主板 + 创业板**（代码前 3 位 `600/601/603/605/000/001/002/003/300/301`；排除科创板 688、北交所及其它代码段）。
- **验证**：新过滤后选股池 **4767 只**（沪市主板 1782 + 深市主板 1575 + 创业板 1411，旧过滤仅 3357 只，恢复创业板 1411 只）；科创板 616 只全部排除（池内 0）；301136（招标股份）在池内；`web_server.py` / `main.py` 编译通过；web_server 完整导入 OK（20 策略正常加载）。**需重启服务生效**。
- **说明**：2560战法 reason 中"量能 >= 1.2倍"为用户旧版本记录的 1.5 倍——该阈值参数已被调整（`config/strategy_params.yaml`），不影响本案例命中（2.58 倍均满足）；如需恢复 1.5 可自行调整。

### 修复：保存选股结果时触发行业数据源拉取导致 ERROR/ProxyError 刷屏
- **现象**：选股结果页点"保存结果"，日志大量出现 `所有数据源都获取失败`、`ProxyError('Unable to connect to proxy', RemoteDisconnected(...))`、tushare `stock_basic` 反复重试（3 源 × 3 次全表拉取）。
- **排查链**：
  1. 保存 66 只选股结果时，`save_selection_result` 对每只股票调 `_get_stock_industry`：**stock_basic 表行业为空则走 `IndustryFetcher` 数据源拉取**（tushare → 东财行业 → 东财行业排名，各 3 次重试）。
  2. 实测 tushare `stock_basic`：返回 5569 行、列名正常，但 **002505 / 600321 匹配行数 = 0**（镜像缺失/退市状态股票，**从任何源都拉不到行业**）；本地库 **5385 只中 183 只行业为空**（含 002505、600321）。
  3. 东财行业源经 akshare 底层 requests **走系统代理**（`127.0.0.1:7688`，代理软件已关闭）→ `ProxyError` → 三源全失败 → ERROR 刷屏；同时每次保存对空行业股票做 9 次全表 stock_basic 请求，严重拖慢保存。
- **修复**：`_get_stock_industry` 改为**只读 stock_basic 表**（有则返回，空则返回空字符串），**不再触发任何数据源网络拉取**；行业补全统一由数据初始化流程负责。保存选股结果全链路零网络调用（行业/价格/关键日期均为本地读取）。
- **验证**：000001 → 'J66货币金融服务'、301136 → 'M74专业技术服务业'（DB 命中）；002505 / 600321 / 不存在代码 → ''（无网络调用）；`selection_record_manager.py` 编译通过。**需重启服务生效**。
- **遗留说明**：183 只行业为空股票如需补全行业，可在数据初始化时补拉（此时仍会触发数据源，东财源代理问题待后续在初始化链路统一处理）；不影响保存功能。


