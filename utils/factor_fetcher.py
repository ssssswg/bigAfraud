"""Tushare 技术面因子 + 筹码分布 数据获取（统一数据源）

- stk_factor_pro（doc_id=328）：股票技术面因子(专业版)，含换手率/量比/市值及 MACD、KDJ、RSI、BOLL 等大量现成指标
- cyq_chips（doc_id=294）：每日筹码分布（各价位占比）；cyq_perf（doc_id=293）：每日筹码及胜率（成本分位/加权平均成本/胜率）

两者均为项目技术面/筹码维度判定的统一数据源，带进程内 TTL 缓存，失败降级（返回 None）。
"""
import json
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_pro = None


def get_pro():
    """读取 config/tushare_config.json 的 token，返回 Tushare pro API 单例（无 token 返回 None）

    统一走 utils.tushare_client.get_pro()：自动设置 _DataApi__http_url = tuaremax.top（镜像代理）
    与全局限流，避免默认连官方主站 api.waditu.com 导致 token 校验失败（报"您的token不对"）。
    """
    global _pro
    if _pro is not None:
        return _pro
    try:
        from utils.tushare_client import get_pro as _client_get_pro
        _pro = _client_get_pro()
        return _pro
    except Exception as e:
        logger.warning(f'初始化 Tushare pro 失败: {e}')
        return None


def to_ts_code(stock_code: str) -> str:
    """6位数字代码 → ts_code（XX.SH / XX.SZ / XX.BJ）"""
    code = str(stock_code).strip()
    if '.' in code:
        return code
    if len(code) != 6:
        return code
    if code.startswith(('60', '68', '90')):
        return f'{code}.SH'
    if code.startswith(('83', '87', '88', '43', '92')):
        return f'{code}.BJ'
    return f'{code}.SZ'


def get_factor_history(pro, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """拉单股历史技术面因子（stk_factor_pro），TTL 当日缓存"""
    if pro is None:
        return None
    from utils.tushare_bulk_cache import proc_cache
    key = ('stk_factor_pro', ts_code, str(start_date), str(end_date))
    try:
        return proc_cache(key, 18 * 3600,
                          lambda: pro.stk_factor_pro(ts_code=ts_code, start_date=str(start_date),
                                                     end_date=str(end_date)))
    except Exception as e:
        logger.debug(f'stk_factor_pro 获取失败 {ts_code}: {e}')
        return None


def get_all_factors_by_date(pro, trade_date: str) -> Optional[pd.DataFrame]:
    """按交易日批量拉全市场技术因子（stk_factor_pro，doc328），TTL 当日缓存。

    1 次请求返回全市场当日因子（实测 5560 行 / ~3s），替代逐只 400 天拉取。
    用于选股批量预热：预填 proc_cache 后，official_factor_scope 逐只命中缓存，不再发单股请求。
    """
    if pro is None or not trade_date:
        return None
    from utils.tushare_bulk_cache import proc_cache
    key = ('stk_factor_pro_batch', str(trade_date))
    try:
        return proc_cache(key, 18 * 3600,
                          lambda: pro.stk_factor_pro(trade_date=str(trade_date)))
    except Exception as e:
        logger.debug(f'stk_factor_pro 批量获取失败 {trade_date}: {e}')
        return None


def get_stock_main_flow(pro, ts_code: str, trade_date: Optional[str] = None, lookback_days: int = 10) -> Optional[float]:
    """统一个股主力资金净流入额（Tushare moneyflow doc 348，net_mf_amount，单位：万元），取最近一个交易日的值

    供选股/评分/个股分析等处复用；未拉取到返回 None。
    """
    if pro is None or not ts_code:
        return None
    try:
        from datetime import datetime, timedelta
        _end = trade_date or datetime.now().strftime('%Y%m%d')
        _start = (datetime.strptime(_end, '%Y%m%d') - timedelta(days=lookback_days)).strftime('%Y%m%d')
        _df = pro.moneyflow(ts_code=ts_code, start_date=_start, end_date=_end)
        if _df is not None and not _df.empty:
            _df = _df.sort_values('trade_date')
            _row = _df.iloc[-1]
            return float(_row.get('net_mf_amount', 0) or 0)
        return None
    except Exception as e:
        logger.debug(f'moneyflow 获取失败 {ts_code}: {e}')
        return None


def get_chips(pro, ts_code: str, trade_date: str) -> Optional[pd.DataFrame]:
    """拉单股当日筹码分布（cyq_chips），TTL 当日缓存"""
    if pro is None:
        return None
    from utils.tushare_bulk_cache import proc_cache
    key = ('cyq_chips', ts_code, str(trade_date))
    try:
        return proc_cache(key, 18 * 3600,
                          lambda: pro.cyq_chips(ts_code=ts_code, trade_date=str(trade_date)))
    except Exception as e:
        logger.debug(f'cyq_chips 获取失败 {ts_code} {trade_date}: {e}')
        return None


def get_chips_perf(pro, ts_code: str, trade_date: str) -> Optional[pd.DataFrame]:
    """拉单股当日筹码及胜率（cyq_perf，doc_id=293 成本分位/加权平均成本/胜率），TTL 当日缓存

    输出字段：cost_5pct / cost_15pct / cost_50pct / cost_85pct / cost_95pct（成本分位）、
    weight_avg（加权平均成本）、winner_rate（胜率/获利比例%）
    """
    if pro is None:
        return None
    from utils.tushare_bulk_cache import proc_cache
    key = ('cyq_perf', ts_code, str(trade_date))
    try:
        return proc_cache(key, 18 * 3600,
                          lambda: pro.cyq_perf(ts_code=ts_code, trade_date=str(trade_date)))
    except Exception as e:
        logger.debug(f'cyq_perf 获取失败 {ts_code} {trade_date}: {e}')
        return None


def _last_factor(factor_df, date_str: Optional[str] = None):
    """从因子表取目标日期（默认最新）那一行（dict）"""
    if factor_df is None or factor_df.empty:
        return None
    f = factor_df.copy()
    if 'trade_date' not in f.columns:
        return None
    f['trade_date'] = f['trade_date'].astype(str)
    if date_str:
        sub = f[f['trade_date'] == str(date_str)]
        if sub.empty:
            return None
        return sub.iloc[-1].to_dict()
    return f.sort_values('trade_date').iloc[-1].to_dict()


def turnover_rate(factor_df, date_str: Optional[str] = None) -> Optional[float]:
    """目标日期换手率（%）"""
    row = _last_factor(factor_df, date_str)
    if row is None or not row.get('turnover_rate'):
        return None
    try:
        return float(row['turnover_rate'])
    except Exception:
        return None


def recent_turnover(factor_df, n: int = 3):
    """取目标日往前最近 n 日的换手率列表（时间升序，含当日）"""
    if factor_df is None or factor_df.empty or 'trade_date' not in factor_df.columns:
        return []
    f = factor_df[['trade_date', 'turnover_rate']].dropna().sort_values('trade_date')
    f['turnover_rate'] = pd.to_numeric(f['turnover_rate'], errors='coerce')
    return f['turnover_rate'].tail(n).tolist()


def circ_mv(factor_df, date_str: Optional[str] = None) -> Optional[float]:
    """目标日期流通市值（万元）"""
    row = _last_factor(factor_df, date_str)
    if row is None:
        return None
    for k in ('circ_mv', 'circ_mv'):
        if row.get(k):
            try:
                return float(row[k])
            except Exception:
                return None
    return None


def factor_macd_dif(factor_df, date_str: Optional[str] = None) -> Optional[float]:
    """目标日期 MACD DIF（不复权）"""
    row = _last_factor(factor_df, date_str)
    if row is None:
        return None
    for k in ('macd_dif_bfq', 'macd_dif', 'macd_dif_hfq'):
        if row.get(k):
            try:
                return float(row[k])
            except Exception:
                return None
    return None


def factor_rsi(factor_df, date_str: Optional[str] = None) -> Optional[float]:
    """目标日期 RSI(6) 不复权"""
    row = _last_factor(factor_df, date_str)
    if row is None:
        return None
    for k in ('rsi_bfq_6', 'rsi_6'):
        if row.get(k):
            try:
                return float(row[k])
            except Exception:
                return None
    return None


def chips_metrics(chips_df, close: Optional[float]):
    """由筹码分布算获利比例(%) 与 平均成本

    cyq_chips 输出 price + percent（各价位筹码占比%）
    获利比例 = 价格 <= 收盘价的筹码累计占比；平均成本 = 各价位价格的加权平均
    """
    if chips_df is None or chips_df.empty:
        return None, None
    try:
        c = chips_df[['price', 'percent']].copy()
        c['price'] = pd.to_numeric(c['price'], errors='coerce')
        c['percent'] = pd.to_numeric(c['percent'], errors='coerce')
        c = c.dropna()
        if c.empty:
            return None, None
        total = c['percent'].sum()
        if total <= 0:
            return None, None
        winner = c.loc[c['price'] <= (close if close else c['price'].max()), 'percent'].sum() / total * 100
        avg_cost = (c['price'] * c['percent']).sum() / total
        return round(winner, 1), round(float(avg_cost), 3)
    except Exception as e:
        logger.debug(f'筹码指标计算失败: {e}')
        return None, None
