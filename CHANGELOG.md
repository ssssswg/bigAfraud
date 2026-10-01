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
- **市值数据错乱**：`get_stock_market_cap` 调 `daily_basic` 未带 `trade_date`，镜像返回多日历史多行市值。改为显式传入最新交易日（`trade_date_utils.get_previous_trading_day`）。验证：全市场市值正确（示例股 = 2192.87 亿元）。
- **龙头策略涨停池取数**：`scripts/collect_limit_up_pool.py` 自定义 `def get_pro()`（0 参数）同名覆盖统一客户端 → `get_pro(token)` 报参数错误。删除冗余定义，统一走 `utils.tushare_client.get_pro`。验证：`fetch_limit_up_day(pro,'20260924')` 返回 52 只涨停股。
- **回测股票池移除配置**：`config/pool_removal_config.yaml` 补齐 5 个新策略（含类名 + 中文名双 key），修复"策略 主升低吸策略 未配置股票池移除参数"。验证：类名与中文名均命中。

### 性能优化：回测缓存加速
- **背景**：回测评分逐股票实时调 Tushare（无连接复用），实测 19.4 分钟仅完成 119 只（~9.8 秒/只、2067 次 POST）。
- **修复 4 项**：① **按交易日全市场批量缓存**（新增 `utils/tushare_bulk_cache.py`）：top_list / block_trade / stk_shock / moneyflow_ths 按 `trade_date` 拉全市场缓存，同评分日多股共享（原每只多次 POST → 每日 1 次）；② **交易日历全局缓存**（`trade_date_utils`，trade_cal 216→少量）；③ **HTTPS 连接复用**（`tushare_client._inject_session_pool` 共享 Session 连接池，幂等）；④ 低频接口保留现有缓存。
- **时效保证**：缓存以交易日为失效单位——同交易日首次拉取后复用、跨日自动重拉（Tushare 每日更新一次）；评分器缓存由 5 分钟 TTL 改为**当日 24:00 自然日失效**。

### 修复（连接池复用的副作用闭环）
- **Tushare 代理连接失败**：共享 Session（持久对象，`trust_env` 默认读系统/环境代理）在服务启动时捕获系统代理 `127.0.0.1:7688`，代理软件关闭后进程仍走该代理 → 全部外呼 `WinError 10061`。修复：共享 Session 设 `trust_env=False` 强制直连（直连 tuaremax.top 实测 200）。**需重启 web 服务生效**；若必须走代理访问则需保持代理软件运行。

---

## v3 ｜ 2026-09-26 · 数据本地化与性能优化

### 数据本地化（入库 + 评分读库优先）
- **资金流入库**：`data_initializer._init_fund_flow_data` 由空实现改为调用 `FundFlowUpdater` 拉近 60 交易日写 `stock_fund_flow` / `industry_fund_flow` / `sector_fund_flow`；评分保留 `daily_bulk` 按日全市场缓存（表字段为净流入、与 moneyflow_ths 买卖分解不匹配，读库边际收益小）。
- **事件 9 接口入库**（新增 `utils/event_data_fetcher.py`）：forecast / express / repurchase / stk_holdernumber / stk_holdertrade / share_float / stk_seasoned / top_list / top_inst（doc_id 45/46/124/166/175/160/494/106/107）按公告日/交易日单日全市场拉取写 `stock_event`；不支持的接口（stk_seasoned）探测后自动禁用；按 (event_type, event_date) 先删后插幂等；`_init_event_data` 全量拉近 90 天、**增量只拉最新日期之后**。
- **事件应用到评分**（`event_scorer`）：`_query_local_events` 读库优先（forecast / 股东增减持 / 股票回购），新增 4 个 `_check_*`（限售解禁 -10、股东户数环比 ±10、龙虎榜机构 ±10、业绩快报 ±8/15），扩展 `EVENT_VALIDITY`/`POSITIVE_SCORES`/`NEGATIVE_SCORES`/`LOCAL_EVENT_TYPES`；清理读库优先重复注入。
- **基本面财务本地化**（新增 `utils/financial_data_fetcher.py`）：`fina_indicator` 镜像**仅接受 ts_code**（不支持 period 批量）→ `fetch_all_stocks()` 逐股入库 `stock_financial`、增量 `fetch_for_stock`；`fundamental_scorer._fetch_fina_indicator` 读库优先。`_init_financial_data` 改为**后台线程（daemon）**执行，主流程立即返回。
- **验证**：单日事件入库 1296 条；评分读库命中时 Tushare `_pro` 不触发（holdertrade/repurchase/forecast + 4 新事件）；示例股入库 8 期财务、读库评分 80；综合评分冒烟 5 维度正常（示例股=46.1、另一股资金面一票否决=-100）；重复跑幂等。

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
- **现象**：数据管理-基础数据页 某只股票 最新价 ¥0、数据条数 0（其他股票正常）。
- **根因**（日志 + 数据库 + 代码 + 复现实验钉死）：75 只股票 K线=0（`stock_basic` 有记录但 `stock_kline` 无行，排除 symbol↔code 串写）。**两类缺失**：① 15 只不在任何初始化批次 URL（名称如含"无效"、及历史未上市新股等均为 IPO 被否/撤单的申购残留代码，经 Tushare 与 baostock 双重核验不存在——akshare 降级源混入无效申购代码写入 stock_basic）；② 60 只在批次 URL 中却未入库（复现 TickFlow 批次响应在 ~4.4MB 处被截断导致 JSON 解析失败，属网络/响应瞬态，非保存逻辑缺陷）。
- **修复**：① 60 只缺失补拉入库、15 只无效代码从 `stock_basic` 删除；② `get_all_stock_codes` 新增 `invalid_codes` 黑名单 + akshare 分支排除"无效"关键词（Tushare/腾讯/akshare 三源统一过滤）；③ `_init_kline_history_data` 保存失败记 WARNING，初始化完成后校验缺失自动补拉（TickFlow→腾讯降级 1 轮）。**黑名单与自动补拉需重启生效，数据修复即时生效**。

### 修复：狩猎场保存结果与页面计算不一致（保存了旧缓存记录）
- **现象**：狩猎场计算显示 2 只，点"保存"却提示"已保存 1 条"，且狩猎跟踪里查到的不是页面显示的那 2 只。
- **根因**：① 前端 `index.html` 有两个重复 id 的 `#timing-strategy` 下拉框——`calculate()` 读 `#khunter-page #timing-strategy`（顺势宝 macd_bollinger）、`saveResults()` 读 `getElementById`（第一个=turtle），**保存参数与计算不一致**；② 后端 `KHunterAPI.save()` 内部重新调 `process()`，其缓存以 khunter 表已存记录为缓存 → 命中昨天保存的旧记录，**保存了旧缓存而非当前结果**。
- **修复**：① 前端 `saveResults()` 选择器改 `#khunter-page #timing-strategy` 并把当前结果 `currentResults` 传给保存接口（所见即所得）；② 后端 `save()` 新增 `results` 参数——传入则直接保存，未传则 `force_refresh=True` 强制重算再保存；③ `KHunterDataProcessor.process()` 加 `force_refresh` 跳过 `_check_cache`；④ `routes.khunter_save()` 透传 `results`。历史旧记录保留。**需重启服务 + 刷新浏览器生效**。

---

## v4 ｜ 2026-09-27 · 选股池回归全市场（创业板/科创板不再被排除）

### 修复：某创业板股长期不被选股结果命中（3 个策略均缺失）
- **现象**：选股结果里一直没有某只股票，但用户此前见过其被 **2560战法 / 多金叉共振 / 趋势起点** 3 个策略同时选中（信号日 2026-09-24）。
- **根因**（数据库 → 代码 → git blame → 实测钉死）：① 该股 K线 750 条完整、09-24 收盘 13.30/量能 2.58 倍等与用户选股理由**逐字一致**，且 `stock_selection_record` 该股 0 条、当天其他策略正常跑 → **策略与数据无问题**；② git blame 定位 `web_server.py` run_selection 与 `main.py` select 的过滤"仅保留主板（600/601/603/605/000/001/002/003）"由 62c2dc0「选股加速」引入 → **30 开头创业板、68 开头科创板全被排除出选股池**。
- **修复**：两处过滤条件改为 **沪市主板 + 深市主板 + 创业板**（`600/601/603/605/000/001/002/003/300/301`，排除科创板 688、北交所）。
- **验证**：选股池 3357 → **4767 只**（恢复创业板 1411 只），科创板 616 只全排除；该股在池内；编译 + 20 策略加载 OK。**需重启服务生效**。

### 修复：保存选股结果时触发行业数据源拉取导致 ERROR/ProxyError 刷屏
- **现象**：选股结果页点"保存结果"，日志大量出现 `所有数据源都获取失败`、`ProxyError('Unable to connect to proxy', RemoteDisconnected(...))`、tushare `stock_basic` 反复重试（3 源 × 3 次全表拉取）。
- **排查链**：
  1. 保存 66 只选股结果时，`save_selection_result` 对每只股票调 `_get_stock_industry`：**stock_basic 表行业为空则走 `IndustryFetcher` 数据源拉取**（tushare → 东财行业 → 东财行业排名，各 3 次重试）。
  2. 实测 tushare `stock_basic`：返回 5569 行、列名正常，但部分退市/特殊股（如示例代码）匹配行数 = 0（镜像缺失/退市状态股票，**从任何源都拉不到行业**）；本地库 **5385 只中 183 只行业为空**。
  3. 东财行业源经 akshare 底层 requests **走系统代理**（`127.0.0.1:7688`，代理软件已关闭）→ `ProxyError` → 三源全失败 → ERROR 刷屏；同时每次保存对空行业股票做 9 次全表 stock_basic 请求，严重拖慢保存。
- **修复**：`_get_stock_industry` 改为**只读 stock_basic 表**（有则返回，空则返回空字符串），**不再触发任何数据源网络拉取**；行业补全统一由数据初始化流程负责。保存选股结果全链路零网络调用（行业/价格/关键日期均为本地读取）。
- **验证**：示例股 → 'J66货币金融服务'、另一示例股 → 'M74专业技术服务业'（DB 命中）；退市/特殊股 / 不存在代码 → ''（无网络调用）；`selection_record_manager.py` 编译通过。**需重启服务生效**。
- **遗留说明**：183 只行业为空股票如需补全行业，可在数据初始化时补拉（此时仍会触发数据源，东财源代理问题待后续在初始化链路统一处理）；不影响保存功能。

### 修复：市场速览"最热板块"显示未知 + 点股票数量打开为空
- **现象**：市场速览-最热板块排名 1 显示"未知"、股票数量 50、占比 100%；点股票数量打开为空。
- **排查链**（数据库 → 代码 → 接口实测）：
  1. 最热板块数据来自 `stock_selection_record.sector`（保存选股结果后由 ranking_manager 排名更新写入）；2026-09-24 共 68 条 **sector 全空** → 全部归为"未知"。
  2. sector 来源链：排名更新读 `stock_score_detail.sector_details.sector_name` → 评分时板块未产出（sector_name=""）→ 手动实测板块评分链路（ths_member / ths_index / ths_daily / moneyflow_cnt_ths）**Tushare 全部可用**（示例股算 100 分/最优板块"证金持股"），但选股评分批次中部分股票 ths_member 失败/无映射 → 板块详情空。
  3. 板块源实证：部分股票有板块映射（56/80/73 个）；部分退市/特殊股无板块。
- **修复**：
  1. **补算存量板块**：对 09-24 选股记录 68 条复用单个 SectorScorer（预热板块映射缓存）重算板块并回填 `stock_selection_record.sector`——**66 条拿到真实板块**（物联网/新能源汽车/证金持股/一带一路等），仅个别退市股无板块无行业。
  2. **评分链路兜底**：`ranking_manager._get_best_sector` 板块未产出时回退 `stock_basic.industry`，保证 sector 非空、不再出现"未知"。
  3. **接口兜底**：`/api/dashboard/hot-areas` 查询 `SELECT sector, industry`，`sector 为空时用 industry` 兜底展示。
- **验证**：最热板块 top10 现为物联网 8 只(11.8%)/新能源汽车 6 只(8.8%)/证金持股 5 只(7.4%) 等真实板块；点"物联网"股票数量查到 5 只（示例个股）；`ranking_manager.py`/`web_server.py` 编译通过。**存量数据已即时生效，兜底逻辑需重启服务生效**。

### 优化：选股策略"精选手池 + 每策略最多1只"（广撒网 92 -> 精选）
- **目标**：09-27 执行选股 92 只（交集率仅11%），要求收窄到高确定性信号 + 每个策略最多 1 只，结合 A 股主力特性（高开多兑现、利好兑现利空爆拉、一致看好直接兑现、逆人性低吸）。
- **两步骤解耦（架构）**：① 每策略按自身规则筛选 + **假信号过滤**（主力阴招规避：放巨量滞涨/高开低走/加速赶顶/乖离过大/放量假突破/放量断板，龙头豁免加速乖离）；② 命中多只时**策略内评分排序**（强者恒强/该强不强弱/月线位置/连板龙头/超布林·5日线10%/放量滞涨），每策略最多保留 1 只。
- **关键判定细化**：
  - 高开低走分辨洗盘/兑现——放量破MA5=出货、跌幅超"市场情绪动态阈值"=出货（随涨跌家数：极端普跌放宽8%/偏强收紧4%/正常5%）、缩量温和回落=洗盘保留。
  - 加速赶顶升级多维——5日>25% 或 10日>40%，且放量/乖离MA20>15%/高位任一确认，排除缩量低乖离健康上行（补10日+乖离+位置，捕获前期加速现横高位的票）。
  - 强者恒强豁免（持续破新高+量价健康→位置影响小）、该强不强就是弱（放量收阴/断板走弱）、低吸类不套用强强/弱态规则。
- **参数收紧**（params=实际生效，default=出厂值可还原）：阻力位突破 breakout_ratio -0.02->0.02（原负值bug）、涨停回马枪回调2->5天/4%~10%/放量2.5、趋势起点量能1.8、多金叉共振须同日三金叉、龙头 turnover 12/10:00前封板、2560 涨幅7%/量1.5 等。
- **验证**：全市场模拟总去重 18 只、每策略<=1 只；关键对照票（加速剔除/龙头豁免/强度优先）回归通过。`web_server.py` 编译通过，**需重启服务生效**；原参数备份 `config/strategy_params.yaml.bak_20260927`。

### 优化：飞书推送精简美化 + 多策略共振置顶
- **背景**：原推送每策略逐条列出（35 只全列 + 63 只去除全列 + 买入建议重复，冗长难读）；同一股票被多个策略命中时在买入建议里重复出现。
- **`web_server.py` 飞书推送重构**：
  1. **多策略共振置顶**：先构建 股票->策略 映射，命中 >=2 策略的股票在推送开头"⭐ 重点推荐 · 多策略共振"置顶（标注策略组合与价格），按策略数降序。
  2. **各策略合并一行**：同一策略的股票 `code 名称 价格 ・ ...` 合并为一行，替代逐条罗列。
  3. **去除列表精简**：去除股票只列前 10 只 + "…等 N 只"（不再全列 63 只）；新增列表合并一行。
  4. **股票去重**：`_all_stocks` 按 code 去重；买入建议循环加 `_seen_codes` 去重（同股票只 analyze 一次，不再重复出现）。
- **验证**：模拟 `cleaned_results`（5 只、3 只多策略共振）输出正确——共振置顶 3 只（回马枪+W底 / W底+趋势加速 / 2560+多金叉），各策略合并一行，买入去重后只跑 5 次 analyze。`web_server.py` 编译通过，**需重启服务生效**。

- **钉钉推送补全**：项目此前**只有配置（config.yaml 钉钉 webhook/secret）、无任何发送实现**（无 DingTalkNotifier，web_server 只推飞书）→ 新增 `utils/dingtalk_notifier.py`（DingTalkNotifier，含**加签** HMAC-SHA256：timestamp+secret → base64 → `&timestamp=&sign=`），web_server 推送段在飞书后并列推钉钉（未配置时静默跳过）。验证：加签 URL 正确含 timestamp/sign，payload msgtype=text 正确；编译通过。**需重启服务生效**。追加：**钉钉推送增加开关**——`config.yaml` 的 `dingtalk.enabled`（默认 true）；web_server 钉钉段先判断 `enabled`，为 false 时跳过（log "钉钉推送已关闭"），config.yaml.template 同步。验证：config.yaml 读取 enabled=True；编译通过。**需重启服务生效**。**飞书推送同步加开关**——`config.yaml` 的 `feishu.enabled`（默认 true），template 补 feishu 段；web_server 飞书发送段先判断 `enabled`，为 false 时跳过。验证：config.yaml 读取 feishu.enabled=True、dingtalk.enabled=True；编译通过。**需重启服务生效**。

### 修复：历史选股按自然日归档（周末/节假日执行选股当天能查到）
- **现象**：09-27（周日）执行选股 17 只并"保存结果"，但历史选股按 09-26~09-27 查询为空。
- **排查**：保存日志显示"保存:0 更新:17 错误:0"（**保存成功**），但数据库记录 `selection_date=2026-09-24`（selection_time=09-27 17:55:09）——`save_selection` 的 `_get_nearest_kline_date` 把执行日 09-27 映射到最近交易日 **09-24**（因周末），记录归档到 09-24，历史选股按 09-26~27 查不到。
- **修复**：`utils/selection_record_manager.py` 保存选股时改为**按执行选股的自然日归档**（`selection_date = user_date`，不做交易日映射；周末/节假日保存当天日期），`selection_time` 仍记录真实执行时间。历史选股按执行当天即可查询。
- **验证**：改后 end_date=2026-09-27 -> selection_date=2026-09-27（不再映射 09-24）；日期段不再调用 `_get_nearest_kline_date`。编译通过，**需重启服务生效**；重新执行选股并保存后，当天结果将落在执行日。

---

## v5 ｜ 2026-09-28 · SQLite 并发写锁修复

### 修复：批量回测保存 `database is locked`（SQLite 并发写冲突）
- **现象**：批量回测保存 `backtest_result` / 收益曲线时，`db_manager` 反复报 `sqlite3.OperationalError: database is locked`（`db_manager.py:195` / `backtest_dao.py:253`），保存失败。
- **根因**：db_manager 的全局写锁 `_write_lock` 只在事务路径（`begin_transaction`）获取；`insert` / `update` / `delete` / `insert_many` 等非事务写直接 `execute`，**无锁串行化、无锁冲突重试** → 并发写（如回测保存与其他写库）在 WAL 模式下写者互斥，撞锁即抛错。
- **修复**：`utils/db_manager.py` 的 `execute()` 对写语句（INSERT / UPDATE / DELETE / REPLACE）统一走**全局写锁串行化**（事务内已持锁则不重复获取）+ **锁冲突指数退避重试**（0.5s / 1s / 2s，最多 3 次），覆盖所有写路径；读语句不受影响。
- **验证**：临时库 4 线程并发写 → 0 报错、200 行全部入库；`py_compile` 通过。

---

## v6 ｜ 2026-09-28 · 排名跟踪改造 + 选股/推送增强

### 功能
- **排名跟踪页**：按收益率降序；删除重复的"板块"列，新增"评分排名"与"入选策略及说明"两列；新增可选排序字段（默认收益率 / 按入选策略名称）。
- **连续选股统计**：历史选股页新增，统计最近 N 天选股差异——连续 ≥2 天入选的股票重点提醒，并给出每日新增/去除对比。
- **推送操作建议**：飞书/钉钉推送新增"前 3 天推荐股票操作建议"（持有/减仓/卖出，含止损位与止盈参考）。

### 修复

- **保存按天清理**：同一天多次选股保存只保留最后一次（写入前先删除当天旧记录）。
- **飞书开关不生效**：`config.yaml` 改为绝对路径读取（与启动目录无关）；飞书发送校验返回值。
- **前端缓存不生效**：bump `app.js?v=15`；约定——改前端 JS 需同步 bump 版本号。

---

## v10 ｜ 2026-09-29~10-02 · 选股重构（策略持有决策）

### 选股逻辑（最终状态）
- **每策略维护最强持有**：命中后进观察期（短3/中5/长10交易日），强弱对比+评分给「继续持有/切换/舍弃」，每策略0~1只；观察期/舍弃阈值/负面指标在「系统设置-选股参数」可改；初次选股直接首选中；多策略共振整体释放，卖出终止锁定（status=sold 不再更新）；同股连续命中续持、保留最早选入日。
- **强弱/卖出统一路由**：weak/strong/sell 三 API（趋势/量能/动量/相对强弱四维共振）；弱强互斥单值（真/假走强走弱），真走弱才触发换股卖出；卖出走 sell_signal 逆人性五维出货（量能/资金/换手/筹码/技术），卖点按次日开盘价；强弱标志写入选股池，供个股图谱/选股跟踪复用。

### 数据模型分离
- `stock_selection_record`（选股记录=每天明细，历史选股读它，按日期+策略+股票筛选）；`strategy_hold_record/history`（选股池=持有/卖出主表，选股跟踪为主，选入到卖出仅1条，卖出后再选开新记录）；执行选股写池、保存写记录表。

### 选股跟踪与历史选股
- 跟踪合并排名/跟踪：持有+清仓聚合、筛选/排序/分页/重新生成、操作列人工改卖出时间/价格、展示中文策略名+真/假强弱标签+持有/卖出状态+说明（选入信号理由或卖出原因）。
- 历史选股展示每天明细（股票/中文策略/选入价/评分/命中策略数）。

### 推送改版（飞书/钉钉统一）
- `📊 缅A选股机会`+共N只入选｜M只共振；多策略共振置顶、买入建议按评分降序+强弱标识、入选个股策略归属去重、今日卖出建议、尾部前3天操作建议/大盘/新闻；飞书/钉钉开关独立生效。

### 外部数据接口与技术面统一
- 接入 `stk_factor_pro`（doc328，含 MACD/KDJ/RSI/BOLL/MA/EMA）/ 资金流向（doc348/371/343/345）/ 每日筹码（doc294）；选股/个股/评分统一官方技术指标（`_bfq` 复权口径，官方有值用官方、取不到再自算，18h 缓存）；技术面评分=六维指标健康度基础分+策略权重叠加，未命中策略不再一律0分。

### 数据治理与修复
- 退市/暂停股治理（同步源头过滤+刷新黑名单）；SQLite WAL+全局写锁串行化+退避重试（根治 database is locked）；板块强度评分打通申万二级映射（index_member_all 335）；金股评分/板块/排名补齐（技术面分+板块降级链+按累计收益率排名）；策略名筛选归一化（历史选股/回测一致，2560 战法等多策略组合可查）；详情页 K线读库提速。

### 说明
- **需重启服务生效。**

