# -*- coding: utf-8 -*-
"""
技术面评分器模块

基于选股策略命中情况计算技术面得分。
数据来源：stock_selection_record 表
评分规则：技术面得分 = Σ(策略权重 × 命中标志)
一票否决：M头策略 + 多死叉共振同时命中 → -100分
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

# 导入数据库管理器
from utils.db_manager import DBManager
# 导入技术面详情模型和策略类名映射
from trading.stock_score_models import TechnicalDetail, STRATEGY_CLASS_NAME_MAP

# 配置日志记录器
logger = logging.getLogger(__name__)

# 全局 DBManager 实例
from utils.global_db import get_global_db
from utils.sell_signal import compute_negative_signals
global_db_manager = get_global_db()


# ============================================================
# 从配置文件加载策略权重
# ============================================================
def _load_strategy_weights() -> Dict[str, int]:
    """
    从配置文件加载策略权重
    
    配置文件路径：config/strategy_weights.json
    
    返回:
        Dict[str, int]: 策略名称到权重的映射字典
    """
    # 配置文件路径
    config_path = Path(__file__).parent.parent / "config" / "strategy_weights.json"
    
    # 确保路径正确
    if not config_path.exists():
        # 尝试使用当前工作目录
        config_path = Path.cwd() / "config" / "strategy_weights.json"
    
    # 默认权重（配置文件不存在时使用）
    default_weights = {
        "底部趋势拐点": 50,
        "趋势加速拐点": 40,
        "阻力位突破策略": 40,
        "缩量回调策略": 30,
        "W底策略": 30,
        "多金叉共振策略": 20,
        "启明星策略": 20,
        "多方炮策略": 15,
        "多死叉共振策略": -30,
        "M头策略": -50,
        "趋势共振反转策略": 35,
    }
    
    # 检查配置文件是否存在
    if not config_path.exists():
        logger.warning(f"策略权重配置文件不存在: {config_path}，使用默认权重")
        return default_weights
    
    try:
        # 读取配置文件
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 解析策略权重
        weights = {}
        for strategy in config.get('strategies', []):
            name = strategy.get('name')
            weight = strategy.get('weight', 0)
            if name:
                weights[name] = weight
        
        logger.info(f"从配置文件加载策略权重成功: {config_path}，共 {len(weights)} 个策略")
        return weights
    
    except Exception as e:
        logger.error(f"加载策略权重配置文件失败: {e}，使用默认权重")
        return default_weights


def _load_veto_config() -> Tuple[bool, int, List[str]]:
    """
    从配置文件加载一票否决配置
    
    返回:
        Tuple[bool, int, List[str]]: (是否启用, 否决分数, 触发否决的策略列表)
    """
    # 配置文件路径
    config_path = Path(__file__).parent.parent / "config" / "strategy_weights.json"
    
    # 确保路径正确
    if not config_path.exists():
        # 尝试使用当前工作目录
        config_path = Path.cwd() / "config" / "strategy_weights.json"
    
    # 默认配置
    default_enabled = False
    default_score = -100
    default_strategies = ["M头策略", "多死叉共振策略"]
    
    # 检查配置文件是否存在
    if not config_path.exists():
        return default_enabled, default_score, default_strategies
    
    try:
        # 读取配置文件
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 解析一票否决配置
        veto_config = config.get('veto_config', {})
        enabled = veto_config.get('enabled', default_enabled)
        score = veto_config.get('score', default_score)
        strategies = veto_config.get('strategies', default_strategies)
        
        logger.info(f"加载一票否决配置: enabled={enabled}, score={score}, strategies={strategies}")
        return enabled, score, strategies
    
    except Exception as e:
        logger.error(f"加载一票否决配置失败: {e}，使用默认配置")
        return default_enabled, default_score, default_strategies


# 加载策略权重配置
STRATEGY_WEIGHTS = _load_strategy_weights()

# 加载一票否决配置
VETO_ENABLED, VETO_SCORE, VETO_STRATEGIES = _load_veto_config()


class TechnicalScorer:
    """
    技术面评分器

    根据 stock_selection_record 表中的策略命中记录，
    按策略权重计算技术面得分，并检查一票否决条件。
    """

    def __init__(self, db_manager: DBManager = None):
        """
        初始化技术面评分器

        参数:
            db_manager: 数据库管理器实例，为 None 时使用全局实例
        """
        # 使用传入的 db_manager 或全局实例
        self.db = db_manager or global_db_manager
        # 记录初始化日志
        logger.info("技术面评分器初始化完成")
        # 记录使用的 DBManager 实例
        logger.info(f"使用的 DBManager 实例: {id(self.db)}")

    def _query_hit_strategies(
        self, stock_code: str, score_date: str
    ) -> List[str]:
        """
        查询指定股票在指定日期命中的策略列表

        参数:
            stock_code: 股票代码（6位数字）
            score_date: 评分日期，格式 YYYY-MM-DD 或 YYYYMMDD
        返回:
            List[str]: 命中的策略名称列表
        """
        # 统一日期格式为 YYYY-MM-DD（数据库中存储格式）
        formatted_date = self._format_date(score_date)
        logger.info(
            f"查询策略命中情况: stock_code={stock_code}, date={formatted_date}"
        )

        # 直接使用 SQLite 连接查询，避免 DBManager 实例之间的冲突
        import sqlite3
        from utils.db_config import get_db_path
        
        strategies = []
        try:
            # 获取数据库路径
            db_path = get_db_path()
            logger.info(f"使用数据库路径: {db_path}")
            
            # 创建新的 SQLite 连接
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 先查询所有记录，验证数据是否存在
            test_sql = """
                SELECT *
                FROM stock_selection_record
                WHERE stock_code = ?
            """
            cursor.execute(test_sql, (stock_code,))
            test_results = cursor.fetchall()
            logger.info(f"测试查询结果: {test_results}")
            
            # 从 stock_selection_record 表查询命中策略
            # 使用selection_date限制日期，确保只查询指定日期的策略
            sql = """
                SELECT DISTINCT strategy_name
                FROM stock_selection_record
                WHERE stock_code = ?
                  AND selection_date = ?
                  AND is_active = 1
            """
            # 执行查询
            logger.info(f"执行查询: {sql}, 参数: ({stock_code}, {formatted_date})")
            cursor.execute(sql, (stock_code, formatted_date))
            
            # 获取查询结果
            results = cursor.fetchall()
            logger.info(f"查询结果: {results}")
            
            # 提取策略名称列表
            strategies = [
                row[0]
                for row in results
                if row[0]
            ]
            
            # 关闭连接
            conn.close()
        except Exception as e:
            logger.error(f"查询策略命中情况失败: {e}")
        
        # 记录查询结果
        logger.info(
            f"股票 {stock_code} 在 {formatted_date} 命中 {len(strategies)} 个策略: {strategies}"
        )
        return strategies

    def _format_date(self, date_str: str) -> str:
        """
        将日期字符串统一转换为 YYYY-MM-DD 格式

        参数:
            date_str: 日期字符串，支持 YYYYMMDD 或 YYYY-MM-DD
        返回:
            str: YYYY-MM-DD 格式的日期字符串
        """
        # 去除空白字符
        date_str = date_str.strip()
        # 如果是 YYYYMMDD 格式（8位纯数字），转换为 YYYY-MM-DD
        if len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        # 已经是 YYYY-MM-DD 格式，直接返回
        return date_str

    def _expand_combined_strategies(
        self, strategies: List[str]
    ) -> List[str]:
        """
        将组合策略拆分为单个策略

        如果策略名称中包含"+"，则拆分为多个单个策略。
        例如："多金叉共振策略+W底策略" -> ["多金叉共振策略", "W底策略"]

        参数:
            strategies: 策略名称列表（可能包含组合策略）
        返回:
            List[str]: 拆分后的单个策略列表
        """
        individual_strategies = []
        for strategy in strategies:
            # 检查是否为组合策略（包含"+"）
            if "+" in strategy:
                # 拆分组合策略
                parts = strategy.split("+")
                # 添加每个单个策略
                for part in parts:
                    part = part.strip()
                    if part:
                        individual_strategies.append(part)
                # 记录拆分日志
                logger.debug(
                    f"拆分组合策略: {strategy} -> {parts}"
                )
            else:
                # 单个策略，直接添加
                individual_strategies.append(strategy)
        return individual_strategies

    def _score_by_indicators(self, close, ma5, ma10, ma20, ma60, dif, dea,
                            k, d, j, rsi, vol_ratio, pct_chg,
                            boll_up, boll_mid, boll_low):
        """六维技术指标健康度评分（stk_factor_pro 字段，缺省回退自算），clip [-20, +30]

        维度：①均线趋势(方向) ②MACD(动能) ③KDJ(超买超卖钝化) ④RSI(健康度)
              ⑤BOLL(位置：温和突破/严重超买/破位) ⑥量能(验证真假)
        """
        def _f(v):
            try:
                return float(v)
            except Exception:
                return None
        close = _f(close); ma5 = _f(ma5); ma10 = _f(ma10); ma20 = _f(ma20); ma60 = _f(ma60)
        dif = _f(dif); dea = _f(dea)
        k = _f(k); d = _f(d); j = _f(j)
        rsi = _f(rsi); vr = _f(vol_ratio); pc = _f(pct_chg)
        bu = _f(boll_up); bm = _f(boll_mid); bl = _f(boll_low)

        score = 0.0
        sig = []
        # ① 均线趋势（定方向，权重最高）
        if close and ma5 and ma10 and ma20:
            if ma5 > ma10 > ma20:
                score += 8; sig.append('均线多头排列')
            elif ma5 < ma10 < ma20:
                score -= 6; sig.append('均线空头排列')
        if close and ma20 is not None:
            if close > ma20:
                score += 5; sig.append('站上MA20')
            else:
                score -= 8; sig.append('跌破MA20')
        if close and ma60 is not None:
            if close > ma60:
                score += 3; sig.append('站上MA60')
            else:
                score -= 4; sig.append('跌破MA60')
        # ② MACD（动能）
        if dif is not None and dea is not None:
            if dif > dea:
                if dif > 0:
                    score += 6; sig.append('MACD多头')
                else:
                    score += 2; sig.append('MACD水下金叉')
            else:
                if dea > 0:
                    score -= 6; sig.append('MACD高位死叉')
                else:
                    score -= 4; sig.append('MACD空头')
        # ③ KDJ（超买超卖钝化）
        if k is not None and d is not None:
            if k > d:
                if d < 20:
                    score += 6; sig.append('KDJ低位金叉')
                elif d < 80:
                    score += 4; sig.append('KDJ金叉')
            else:
                if d > 80:
                    score -= 6; sig.append('KDJ高位死叉')
                else:
                    score -= 3; sig.append('KDJ死叉')
            if j is not None:
                if k > 80 and j > 100:
                    score -= 4; sig.append('KDJ超买钝化')
                elif k < 20 and j < 0:
                    score += 3; sig.append('KDJ超卖')
        # ④ RSI（健康度）
        if rsi is not None:
            if 45 <= rsi <= 70:
                score += 5; sig.append('RSI适中')
            elif 70 < rsi <= 80:
                score += 1
            elif rsi > 80:
                score -= 6; sig.append('RSI超买')
            elif 25 <= rsi < 45:
                pass
            else:
                score += 3; sig.append('RSI超卖')
        # ⑤ BOLL（位置）
        if close and bu is not None:
            if close > bu * 1.03:
                score -= 6; sig.append('突破布林上轨超3%')
            elif close > bu:
                score += 3; sig.append('突破布林上轨(强势)')
            elif bm is not None and close > bm:
                score += 2
            elif bl is not None and close < bl:
                score -= 4; sig.append('跌破布林下轨')
        # ⑥ 量能（验证真假）
        if vr is not None:
            if vr > 3 and pc is not None and pc < 0:
                score -= 5; sig.append('天量下跌')
            elif vr > 3 and pc is not None and pc > 0:
                score += 2; sig.append('放量突破')
            elif 1.0 <= vr <= 2.5 and pc is not None and pc > 0:
                score += 3; sig.append('温和放量')
            elif vr < 0.8 and pc is not None and pc > 0:
                score -= 2; sig.append('缩量上涨(动能不足)')
        return max(-20.0, min(30.0, score)), sig

    def _factor_health_score(self, stock_code: str, date_str: str):
        """技术指标健康度评分，供技术面评分复用。

        数据源优先 stk_factor_pro（Tushare 专业技术面因子 doc 328：MA/MACD/KDJ/RSI/BOLL/量比/涨跌幅），
        字段缺失时回退到 stock_kline 自算（同 distribution 策略）。
        返回 (健康度分, 信号列表)；取不到数据返回 (0, [])。
        """
        d = date_str.replace('-', '')
        # ---- 优先 stk_factor_pro 现成技术指标 ----
        try:
            from utils import factor_fetcher
            pro = factor_fetcher.get_pro()
            if pro is not None:
                from datetime import datetime, timedelta
                ts = factor_fetcher.to_ts_code(stock_code)
                end_dt = datetime.strptime(d, '%Y%m%d')
                start = (end_dt - timedelta(days=90)).strftime('%Y%m%d')
                df = factor_fetcher.get_factor_history(pro, ts, start, d)
                row = factor_fetcher._last_factor(df, d)
                if row and row.get('close') is not None and row.get('macd_dif_bfq') is not None and row.get('boll_upper_bfq') is not None:
                    # 官方 kdj_bfq 即 KDJ 的 J 值
                    _j_ = row.get('kdj_bfq')
                    return self._score_by_indicators(
                        row.get('close'), row.get('ma_bfq_5'), row.get('ma_bfq_10'),
                        row.get('ma_bfq_20'), row.get('ma_bfq_60'),
                        row.get('macd_dif_bfq'), row.get('macd_dea_bfq'),
                        row.get('kdj_k_bfq'), row.get('kdj_d_bfq'), _j_,
                        row.get('rsi_bfq_12'), row.get('volume_ratio'), row.get('pct_chg'),
                        row.get('boll_upper_bfq'), row.get('boll_mid_bfq'), row.get('boll_lower_bfq'))
        except Exception as e:
            logger.debug(f"stk_factor_pro 因子获取失败 {stock_code}: {e}")
        # ---- 回退：stock_kline 自算 ----
        try:
            import pandas as pd
            from datetime import datetime
            rows = self.db.query(
                "SELECT date, open, high, low, close, volume FROM stock_kline "
                "WHERE code = ? ORDER BY date DESC LIMIT 200", (stock_code,))
            if not rows:
                return 0.0, []
            def _pd(t):
                t = str(t)
                try:
                    return datetime.strptime(t[:10], '%Y-%m-%d') if '-' in t else datetime.strptime(t[:8], '%Y%m%d')
                except Exception:
                    return None
            out = []
            for r in rows:
                dt = _pd(r.get('date'))
                if dt is None:
                    continue
                out.append((dt, r))
            out.sort(key=lambda x: x[0])
            target = datetime.strptime(d, '%Y%m%d')
            out = [x for x in out if x[0] <= target]
            if not out:
                return 0.0, []
            out = out[-120:]
            df = pd.DataFrame([r for _, r in out],
                              columns=['date', 'open', 'high', 'low', 'close', 'volume'])
            cl = df['close'].astype(float)
            high = df['high'].astype(float)
            low = df['low'].astype(float)
            vol = df['volume'].astype(float)
            if len(cl) < 20:
                return 0.0, []
            close = float(cl.iloc[-1])
            ma5 = float(cl.rolling(5).mean().iloc[-1])
            ma10 = float(cl.rolling(10).mean().iloc[-1])
            ma20 = float(cl.rolling(20).mean().iloc[-1])
            ma60 = float(cl.rolling(60).mean().iloc[-1]) if len(cl) >= 60 else None
            ema12 = cl.ewm(span=12, adjust=False).mean()
            ema26 = cl.ewm(span=26, adjust=False).mean()
            dif = float((ema12 - ema26).iloc[-1])
            dea = float((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])
            delta = cl.diff()
            up = delta.clip(lower=0)
            down = (-delta.clip(upper=0))
            rs = up.rolling(14).mean() / (down.rolling(14).mean() + 1e-9)
            rsi = float((100 - 100 / (1 + rs)).iloc[-1])
            llv = low.rolling(9).min()
            hhv = high.rolling(9).max()
            rsv = (cl - llv) / (hhv - llv + 1e-9) * 100
            k_ = rsv.ewm(com=2, adjust=False).mean()
            d_ = k_.ewm(com=2, adjust=False).mean()
            k = float(k_.iloc[-1]); dd = float(d_.iloc[-1]); j = float(3 * k - 2 * dd)
            mid = cl.rolling(20).mean()
            std = cl.rolling(20).std()
            boll_up = float((mid + 2 * std).iloc[-1])
            boll_mid = float(mid.iloc[-1])
            boll_lo = float((mid - 2 * std).iloc[-1])
            vol_ratio = float(vol.iloc[-1] / (vol.rolling(5).mean().iloc[-1] + 1e-9))
            pct_chg = float(cl.pct_change().iloc[-1] * 100) if len(cl) > 1 else 0.0
            return self._score_by_indicators(
                close, ma5, ma10, ma20, ma60, dif, dea, k, dd, j, rsi,
                vol_ratio, pct_chg, boll_up, boll_mid, boll_lo)
        except Exception as e:
            logger.debug(f"K线自算技术指标失败 {stock_code}: {e}")
            return 0.0, []

    def calculate_score(
        self, stock_code: str, score_date: str, hit_strategies: List[str] = None
    ) -> Tuple[float, TechnicalDetail]:
        """
        计算指定股票在指定日期的技术面得分

        如果传入 hit_strategies 参数，则使用传入的策略列表计算；
        否则从 stock_selection_record 表查询命中策略。

        参数:
            stock_code: 股票代码（6位数字）
            score_date: 评分日期，格式 YYYY-MM-DD 或 YYYYMMDD
            hit_strategies: 命中的策略列表（可选，为 None 时从数据库查询）
        返回:
            Tuple[float, TechnicalDetail]: (技术面得分, 技术面详情对象)
        """
        logger.info(f"开始计算技术面得分: {stock_code}, 日期: {score_date}")

        # 初始化技术面详情对象
        detail = TechnicalDetail()

        # 统一日期格式
        formatted_date = self._format_date(score_date)

        # 计算技术面负面指标（破位/离5日线10%/超布林上轨10%/放量滞涨/放量大跌），用于评分扣分
        try:
            _neg = compute_negative_signals(self.db, stock_code, formatted_date)
            _neg_score = _neg.get('score', 0)
            detail.negative_signals = _neg.get('list', [])
        except Exception:
            _neg_score = 0
            detail.negative_signals = []

        # 如果没有传入策略列表，从数据库查询
        if hit_strategies is None:
            hit_strategies = self._query_hit_strategies(stock_code, formatted_date)
        
        if hit_strategies:
            # 有策略命中记录，直接计算评分
            # 拆分组合策略
            individual_strategies = self._expand_combined_strategies(hit_strategies)
            # 构建策略详情列表
            strategy_list = []
            total_score = 0.0
            
            # 使用全局策略类名到中文名称的映射
            class_name_map = STRATEGY_CLASS_NAME_MAP
            
            # 构建策略名称映射列表，用于一票否决检查
            mapped_strategies = []
            
            for strategy in individual_strategies:
                # 直接使用策略名称（已经是中文名称）
                # 尝试直接匹配策略名称
                weight = STRATEGY_WEIGHTS.get(strategy, 0)
                # 如果直接匹配失败，尝试添加策略后缀
                if weight == 0 and not strategy.endswith('策略'):
                    name_with_suffix = strategy + '策略'
                    weight = STRATEGY_WEIGHTS.get(name_with_suffix, 0)
                # 如果仍然失败，尝试去掉策略后缀
                if weight == 0 and strategy.endswith('策略'):
                    name_without_suffix = strategy[:-2]
                    weight = STRATEGY_WEIGHTS.get(name_without_suffix, 0)
                # 记录策略匹配过程
                logger.info(f"策略: {strategy}, 直接匹配权重: {STRATEGY_WEIGHTS.get(strategy, 0)}, 添加后缀后权重: {STRATEGY_WEIGHTS.get(strategy + '策略', 0) if not strategy.endswith('策略') else 0}, 去掉后缀后权重: {STRATEGY_WEIGHTS.get(strategy[:-2], 0) if strategy.endswith('策略') else 0}, 最终权重: {weight}")
                # 添加到映射策略列表
                mapped_strategies.append(strategy)
                # 构建策略详情
                strategy_list.append({"name": strategy, "weight": weight})
                
                total_score += weight
            
            # 检查一票否决，使用映射后的策略名称
            veto, veto_reason = self.check_veto(stock_code, formatted_date, mapped_strategies)
            
            # 设置详情
            detail.strategies = strategy_list
            detail.veto = veto
            detail.veto_reason = veto_reason
            
            total_score += _neg_score
            # stk_factor_pro 技术因子健康度（MACD/KDJ/RSI/BOLL/均线）并入技术面评分
            _fac, _fac_sig = self._factor_health_score(stock_code, formatted_date)
            total_score += _fac
            detail.factor_score = _fac
            detail.factor_signals = _fac_sig
            logger.info(
                f"股票 {stock_code} 从stock_selection_record表计算技术面得分: {total_score}, "
                f"策略数: {len(strategy_list)}, 否决: {veto}, 负面指标: {detail.negative_signals}"
            )
            return total_score, detail
        else:
            # 未命中策略：仍给技术指标健康度基础分（stk_factor_pro / K线自算），
            # 避免"没有命中策略就一律 0 分"导致技术面失真
            _fac, _fac_sig = self._factor_health_score(stock_code, formatted_date)
            detail.factor_score = _fac
            detail.factor_signals = _fac_sig
            logger.info(
                f"股票 {stock_code} 在 {formatted_date} 未命中策略，技术面=基础指标分 {_fac} 信号={_fac_sig}"
            )
            return _fac, detail

        # 下面的代码暂时保留，但不会被执行（作为备用逻辑）
        # 从stock_score_detail表查询技术面评分数据
        sql = """
            SELECT technical_strategies
            FROM stock_score_detail
            WHERE stock_code = ?
              AND score_date = ?
        """
        results = self.db.query(sql, (stock_code, formatted_date))

        if not results:
            logger.warning(f"股票 {stock_code} 在 {formatted_date} 没有评分记录")
            return 0.0, detail

        # 解析技术面策略数据
        technical_strategies = results[0].get('technical_strategies')
        if not technical_strategies:
            logger.warning(f"股票 {stock_code} 在 {formatted_date} 没有技术面策略数据")
            # 从stock_selection_record表查询策略命中情况
            hit_strategies = self._query_hit_strategies(stock_code, formatted_date)
            if hit_strategies:
                # 拆分组合策略
                individual_strategies = self._expand_combined_strategies(hit_strategies)
                # 构建策略详情列表
                strategy_list = []
                total_score = 0.0
                
                for strategy in individual_strategies:
                    # 尝试直接匹配策略名称
                    weight = STRATEGY_WEIGHTS.get(strategy, 0)
                    # 如果直接匹配失败，尝试去掉策略后缀
                    if weight == 0 and strategy.endswith('策略'):
                        name_without_suffix = strategy[:-2]
                        weight = STRATEGY_WEIGHTS.get(name_without_suffix, 0)
                    # 如果仍然失败，尝试使用策略类名映射
                    if weight == 0:
                        if strategy in STRATEGY_CLASS_NAME_MAP:
                            chinese_name = STRATEGY_CLASS_NAME_MAP[strategy]
                            weight = STRATEGY_WEIGHTS.get(chinese_name, 0)
                    total_score += weight
                    strategy_list.append({"name": strategy, "weight": weight})
                
                # 检查一票否决
                veto, veto_reason = self.check_veto(stock_code, formatted_date, individual_strategies)
                
                # 设置详情
                detail.strategies = strategy_list
                detail.veto = veto
                detail.veto_reason = veto_reason
                
                logger.info(
                    f"股票 {stock_code} 从stock_selection_record表计算技术面得分: {total_score}, "
                    f"策略数: {len(strategy_list)}, 否决: {veto}"
                )
                return total_score, detail
            else:
                return 0.0, detail

        try:
            # 解析JSON格式的策略数据
            import json
            strategies_data = json.loads(technical_strategies)
            
            # 计算总分
            total_score = 0.0
            strategy_list = []
            veto = False
            veto_reason = ""
            
            # 检查是否为字典格式（标准格式）
            if isinstance(strategies_data, dict):
                # 提取策略列表
                strategies = strategies_data.get('strategies', [])
                if isinstance(strategies, list):
                    for strategy in strategies:
                        if isinstance(strategy, dict):
                            name = strategy.get('name', '')
                            weight = strategy.get('weight', 0)
                            total_score += weight
                            strategy_list.append({"name": name, "weight": weight})
                
                # 提取否决信息
                veto = strategies_data.get('veto', False)
                veto_reason = strategies_data.get('veto_reason', "")
            elif isinstance(strategies_data, list):
                # 列表格式
                for strategy in strategies_data:
                    if isinstance(strategy, dict):
                        name = strategy.get('name', '')
                        weight = strategy.get('weight', 0)
                        total_score += weight
                        strategy_list.append({"name": name, "weight": weight})
                    elif isinstance(strategy, str):
                        # 尝试直接匹配策略名称
                        weight = STRATEGY_WEIGHTS.get(strategy, 0)
                        # 如果直接匹配失败，尝试去掉策略后缀
                        if weight == 0 and strategy.endswith('策略'):
                            name_without_suffix = strategy[:-2]
                            weight = STRATEGY_WEIGHTS.get(name_without_suffix, 0)
                        # 如果仍然失败，尝试使用策略类名映射
                        if weight == 0:
                            if strategy in STRATEGY_CLASS_NAME_MAP:
                                chinese_name = STRATEGY_CLASS_NAME_MAP[strategy]
                                weight = STRATEGY_WEIGHTS.get(chinese_name, 0)
                        total_score += weight
                        strategy_list.append({"name": strategy, "weight": weight})
            elif isinstance(strategies_data, str):
                # 字符串格式，按逗号分割
                strategy_names = [s.strip() for s in strategies_data.split(',') if s.strip()]
                for name in strategy_names:
                    # 尝试直接匹配策略名称
                    weight = STRATEGY_WEIGHTS.get(name, 0)
                    # 如果直接匹配失败，尝试去掉策略后缀
                    if weight == 0 and name.endswith('策略'):
                        name_without_suffix = name[:-2]
                        weight = STRATEGY_WEIGHTS.get(name_without_suffix, 0)
                    # 如果仍然失败，尝试使用策略类名映射
                    if weight == 0:
                        # 完整的策略类名到中文名称的映射
                        class_name_map = {
                            'BottomTrendInflectionStrategy': '底部趋势拐点',
                            'TrendAccelerationInflectionStrategy': '趋势加速拐点',
                            'TrendResonanceReversalStrategy': '趋势共振反转策略',
                            'ResistanceBreakoutStrategy': '阻力位突破策略',
                            'WBottomStrategy': 'W底策略',
                            'MultiGoldenCrossStrategy': '多金叉共振策略',
                            'MorningStarStrategy': '启明星策略',
                            'MultiPartyCannonStrategy': '多方炮策略',
                            'MultiDeathCrossStrategy': '多死叉共振策略',
                            'MTopStrategy': 'M头策略',
                            'StrongWashWeakToStrongStrategy': '强势洗盘弱转强策略',
                            'LimitUpPullbackStrategy': '涨停回马枪策略',
                            'LimitUpSidewaysStrategy': '涨停横盘策略',
                            'GoldenTriangleStrategy': '金三角策略'
                        }
                        if name in class_name_map:
                            chinese_name = class_name_map[name]
                            weight = STRATEGY_WEIGHTS.get(chinese_name, 0)
                    total_score += weight
                    strategy_list.append({"name": name, "weight": weight})
            
            # 如果strategies为空，从stock_selection_record表查询
            if not strategy_list:
                logger.warning(f"股票 {stock_code} 在 {formatted_date} 的strategies为空，从stock_selection_record表查询")
                hit_strategies = self._query_hit_strategies(stock_code, formatted_date)
                if hit_strategies:
                    # 拆分组合策略
                    individual_strategies = self._expand_combined_strategies(hit_strategies)
                    # 构建策略详情列表
                    strategy_list = []
                    total_score = 0.0
                    
                    for strategy in individual_strategies:
                        # 尝试直接匹配策略名称
                        weight = STRATEGY_WEIGHTS.get(strategy, 0)
                        # 如果直接匹配失败，尝试去掉策略后缀
                        if weight == 0 and strategy.endswith('策略'):
                            name_without_suffix = strategy[:-2]
                            weight = STRATEGY_WEIGHTS.get(name_without_suffix, 0)
                        # 如果仍然失败，尝试使用策略类名映射
                        if weight == 0:
                            if strategy in STRATEGY_CLASS_NAME_MAP:
                                chinese_name = STRATEGY_CLASS_NAME_MAP[strategy]
                                weight = STRATEGY_WEIGHTS.get(chinese_name, 0)
                        total_score += weight
                        strategy_list.append({"name": strategy, "weight": weight})
                    
                    # 检查一票否决
                    veto, veto_reason = self.check_veto(stock_code, formatted_date, individual_strategies)
                    
                    # 设置详情
                    detail.strategies = strategy_list
                    detail.veto = veto
                    detail.veto_reason = veto_reason
                    
                    logger.info(
                        f"股票 {stock_code} 从stock_selection_record表计算技术面得分: {total_score}, "
                        f"策略数: {len(strategy_list)}, 否决: {veto}"
                    )
                    return total_score, detail
                else:
                    return 0.0, detail
            
            # 设置详情
            detail.strategies = strategy_list
            detail.veto = veto
            detail.veto_reason = veto_reason
            
            # 记录最终得分
            logger.info(
                f"股票 {stock_code} 技术面得分: {total_score}, "
                f"策略数: {len(strategy_list)}, 否决: {veto}"
            )
            return total_score, detail
        except Exception as e:
            logger.error(f"解析技术面策略数据失败: {e}")
            return 0.0, detail

    def check_veto(
        self,
        stock_code: str,
        score_date: str,
        hit_strategies: List[str] = None,
    ) -> Tuple[bool, str]:
        """
        检查一票否决条件

        一票否决条件：M头策略 + 多死叉共振策略同时命中时，
        技术面得分直接为 -100 分。

        参数:
            stock_code: 股票代码（6位数字）
            score_date: 评分日期
            hit_strategies: 已查询的命中策略列表（可选，为 None 时重新查询）
        返回:
            Tuple[bool, str]: (是否触发一票否决, 否决原因)
        """
        # 如果未启用一票否决，直接返回
        if not VETO_ENABLED:
            return False, ""
        
        # 如果未传入策略列表，重新查询
        if hit_strategies is None:
            hit_strategies = self._query_hit_strategies(
                stock_code, score_date
            )

        # 检查是否同时命中所有否决策略
        hit_veto_strategies = [s for s in VETO_STRATEGIES if s in hit_strategies]
        
        # 所有否决策略同时命中才触发一票否决
        if len(hit_veto_strategies) == len(VETO_STRATEGIES):
            reason = f"一票否决：同时命中 {', '.join(VETO_STRATEGIES)}"
            # 记录否决日志
            logger.warning(f"股票 {stock_code} {reason}")
            return True, reason

        # 未触发一票否决
        return False, ""

    def _build_strategy_list(
        self, hit_strategies: List[str]
    ) -> List[dict]:
        """
        构建策略详情列表，包含策略名称和对应权重

        参数:
            hit_strategies: 命中的策略名称列表
        返回:
            List[dict]: 策略详情列表，每个元素为 {"name": 策略名, "weight": 权重}
        """
        strategy_list = []
        for name in hit_strategies:
            # 尝试从配置文件中获取策略的中文名称
            import yaml
            from pathlib import Path
            
            config_path = Path(__file__).parent.parent / "config" / "strategy_params.yaml"
            # 确保路径正确
            if not config_path.exists():
                # 尝试使用当前工作目录
                config_path = Path.cwd() / "config" / "strategy_params.yaml"
            
            strategy_display_name = name
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = yaml.safe_load(f) or {}
                    
                    # 从配置中提取每个策略的 display_name
                    strategies_config = config.get('strategies', {})
                    
                    # 尝试匹配策略名称
                    matched = False
                    for strategy_key, strategy_config in strategies_config.items():
                        # 情况1: 策略名称带下划线（如 'golden_triangle'）
                        if '_' in name:
                            strategy_class_name = ''.join(word.capitalize() for word in name.split('_')) + 'Strategy'
                            if strategy_class_name == strategy_key:
                                strategy_display_name = strategy_config.get('display_name', name)
                                matched = True
                                break
                        # 情况2: 策略名称已经是类名格式（如 'GoldenTriangleStrategy'）
                        elif name == strategy_key:
                            strategy_display_name = strategy_config.get('display_name', name)
                            matched = True
                            break
                except Exception as e:
                    logger.warning(f"读取策略配置失败: {e}")
            
            # 获取策略权重
            weight = STRATEGY_WEIGHTS.get(strategy_display_name, 0)
            # 添加到详情列表
            strategy_list.append({"name": strategy_display_name, "weight": weight})
        return strategy_list
    
    @staticmethod
    def reload_config():
        """
        重新加载配置文件
        
        当用户修改配置文件后，可以调用此方法重新加载配置，
        无需重启服务。
        """
        global STRATEGY_WEIGHTS, VETO_ENABLED, VETO_SCORE, VETO_STRATEGIES
        
        # 重新加载策略权重
        STRATEGY_WEIGHTS = _load_strategy_weights()
        
        # 重新加载一票否决配置
        VETO_ENABLED, VETO_SCORE, VETO_STRATEGIES = _load_veto_config()
        
        logger.info("策略权重配置已重新加载")
