"""出货(派发)信号五维加权评分 — 逆人性卖出引擎的资金/量能/技术面判定

五大量化维度与权重（参考 A股主力出货特征）：
| 维度 | 权重 | 依据 | 数据源 |
|------|------|------|--------|
| 量能背离 | 25 | 放量滞涨 / 天量见顶 / 缩量新高(量价背离) | K线 |
| 资金净流出 | 25 | 主力(超大单+大单)连续净流出≥3日 | moneyflow |
| 换手异常 | 20 | 高位换手异常放大 / 换手与涨幅背离 | stk_factor_pro |
| 筹码派发 | 15 | 获利盘>90% / 平均成本上移股价滞涨 | cyq_chips |
| 技术顶背离 | 15 | MACD顶背离 / RSI高位钝化 | K线 + stk_factor_pro |

量能25+资金25+换手20+筹码15+技术15=100 加权。任一维度缺数据（接口失败/无 token）时按可得维度归一化。

分级：
  < 30   正常/洗盘
  30-60  警惕
  60-85  高度警惕
  ≥ 85   确认出货
"""
import logging
from typing import Optional, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

DIM_WEIGHTS = {'量能': 25, '资金': 25, '换手': 20, '筹码': 15, '技术': 15}


def _read_kline(db_manager, stock_code):
    df = db_manager.read_stock(stock_code)
    if df is None or df.empty:
        return None
    if 'date' not in df.columns:
        df = df.reset_index()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return df


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _volume_dimension(closes, highs, lows, opens, vols, i):
    """量能维度：放量滞涨 / 天量见顶 / 缩量新高（0-100）"""
    if i < 1:
        return 0.0, []
    vol5 = sum(vols[max(0, i - 5):i]) / min(5, i) if i >= 5 else None
    if not vol5:
        return 0.0, []
    prev = closes[i - 1]
    pct = (closes[i] - prev) / prev * 100 if prev else 0
    vol_ratio = vols[i] / vol5
    vol_signals = []
    score = 0.0
    # 天量见顶：量能 ≥3倍5日均量 且 收阴（天量天价）
    if vol_ratio >= 3.0 and closes[i] < opens[i]:
        vol_signals.append(f'天量见顶(量{vol_ratio:.1f}倍收阴)')
        score = max(score, 100.0)
    # 放量滞涨：量 1.5~2倍 且 涨幅<2% 且 收阴或长上影 → 高概率出货
    long_upper_shadow = (highs[i] - max(opens[i], closes[i])) > 2 * abs(closes[i] - opens[i]) if closes[i] != opens[i] else False
    if 1.5 <= vol_ratio <= 2.0 and pct < 2 and (closes[i] < opens[i] or long_upper_shadow):
        vol_signals.append(f'放量滞涨(量{vol_ratio:.1f}倍)')
        score = max(score, 80.0)
    # 量价背离（缩量新高）：创近10日新高 但 量能 <0.8倍5日均量 → 上攻动能衰竭
    near_high = max(highs[max(0, i - 9):i + 1])
    if closes[i] >= near_high and vols[i] < vol5 * 0.8:
        vol_signals.append(f'缩量新高(量{vol_ratio:.1f}倍)')
        score = max(score, 70.0)
    return score, vol_signals


def _turnover_dimension(factor_df, circ_mv_val, closes, i):
    """换手维度：高位换手异常放大（中小盘>10% / 大盘>5%，持续3日以上）、换手与涨幅背离（0-100）"""
    if factor_df is None or factor_df.empty:
        return 0.0, [], False
    try:
        from utils import factor_fetcher
        to = factor_fetcher.recent_turnover(factor_df, 3)
        if not to:
            return 0.0, [], False
        big = (circ_mv_val or 0) >= 2_000_000  # 流通市值≥200亿(万元)视为大盘股
        high_th = 5.0 if big else 10.0
        # 持续高换手天数（从最新往前）
        days = 0
        for t in reversed(to):
            if t and t >= high_th:
                days += 1
            else:
                break
        score = 0.0
        signs = []
        if days >= 3:
            score = max(score, 100.0)
            signs.append(f'高位换手{to[-1]:.1f}%持续{days}日')
        elif days == 2:
            score = max(score, 70.0)
            signs.append(f'高位换手{to[-1]:.1f}%持续{days}日')
        elif days == 1:
            score = max(score, 40.0)
        # 换手与涨幅背离：高换手但当日滞涨（筹码高位快速易手）
        if to[-1] and to[-1] >= high_th and i >= 1:
            prev = closes[i - 1]
            pct = (closes[i] - prev) / prev * 100 if prev else 0
            if pct < 2:
                score = max(score, 60.0)
                signs.append(f'高换手{to[-1]:.1f}%涨幅滞涨')
        return score, signs, True
    except Exception as e:
        logger.debug(f'换手维度计算失败: {e}')
        return 0.0, [], False


def _chips_dimension(chips_df, close, pct):
    """筹码维度：获利盘>90%高位钝化 / 平均成本上移股价滞涨（0-100）"""
    if chips_df is None or chips_df.empty:
        return 0.0, [], False
    try:
        from utils import factor_fetcher
        winner, avg_cost = factor_fetcher.chips_metrics(chips_df, close)
        if winner is None:
            return 0.0, [], False
        score = 0.0
        signs = []
        if winner > 90:
            score = max(score, 80.0)
            signs.append(f'获利盘{winner:.0f}%高位钝化')
        elif winner > 85:
            score = max(score, 60.0)
        # 获利盘高位 + 当日滞涨（人人都赚钱→没人接盘，派发特征）
        if winner > 85 and pct < 3:
            score = max(score, 90.0)
            signs.append('获利盘高位+滞涨(派发)')
        return score, signs, True
    except Exception as e:
        logger.debug(f'筹码维度计算失败: {e}')
        return 0.0, [], False


def _tech_dimension(closes, highs, vols, i, factor_df=None, date_str=None):
    """技术维度：MACD顶背离 / RSI高位钝化（0-100），优先用 stk_factor_pro 现成值"""
    if i < 1:
        return 0.0, []
    tech_signals = []
    score = 0.0
    # RSI(14) > 80 高位钝化（优先 stk_factor_pro 的 rsi，回退自算）
    rsi = None
    if factor_df is not None and not factor_df.empty:
        try:
            from utils import factor_fetcher
            rsi = factor_fetcher.factor_rsi(factor_df, date_str)
        except Exception:
            rsi = None
    if rsi is None:
        rsi = _rsi(closes[:i + 1])
    if rsi is not None and rsi > 80:
        tech_signals.append(f'RSI高位({rsi:.0f})')
        score = max(score, 60.0)
    # MACD 顶背离：价格创新高 但 DIF 不再创新高
    if i >= 20:
        _s = pd.Series(closes[:i + 1])
        dif = (_s.ewm(span=12, adjust=False).mean() - _s.ewm(span=26, adjust=False).mean()).tolist()
        seg = closes[max(0, i - 9):i + 1]
        dif_seg = dif[max(0, i - 9):i + 1]
        if len(seg) >= 3:
            price_peak_idx = seg.index(max(seg))
            if price_peak_idx == len(seg) - 1 and dif_seg[price_peak_idx] < max(dif_seg[:price_peak_idx]) - 1e-9:
                tech_signals.append('MACD顶背离')
                score = max(score, 100.0)
    return score, tech_signals


def _fund_dimension(moneyflow_df):
    """资金维度：主力(超大单+大单)连续净流出天数（0-100）"""
    if moneyflow_df is None or moneyflow_df.empty:
        return 0.0, [], False
    fund_signals = []
    try:
        mf = moneyflow_df.copy()
        if 'trade_date' in mf.columns:
            mf = mf.sort_values('trade_date', ascending=False)
        cols = ['buy_elg_amount', 'sell_elg_amount', 'buy_lg_amount', 'sell_lg_amount']
        if not all(c in mf.columns for c in cols):
            return 0.0, [], False
        nets = []
        for _, r in mf.iterrows():
            elg = float(r['buy_elg_amount'] or 0) - float(r['sell_elg_amount'] or 0)
            lg = float(r['buy_lg_amount'] or 0) - float(r['sell_lg_amount'] or 0)
            nets.append(elg + lg)
        outflow_days = 0
        for n in nets:
            if n < 0:
                outflow_days += 1
            else:
                break
        if outflow_days >= 3:
            score, sign = 100.0, f'主力连续净流出{outflow_days}日'
        elif outflow_days == 2:
            score, sign = 70.0, f'主力连续净流出{outflow_days}日'
        elif outflow_days == 1:
            score, sign = 40.0, '主力净流出1日'
        else:
            score, sign = 0.0, '主力净流入'
        if sign:
            fund_signals.append(sign)
        return score, fund_signals, True
    except Exception as e:
        logger.debug(f"资金维度计算失败: {e}")
        return 0.0, [], False


def _fetch_factor_chips(db_manager, stock_code, date):
    """按需获取单股技术因子 + 当日筹码（factor_fetcher，失败降级）"""
    factor_df = None
    chips_df = None
    try:
        from utils import factor_fetcher
        pro = factor_fetcher.get_pro()
        if pro is None:
            return None, None
        ts_code = factor_fetcher.to_ts_code(stock_code)
        df = _read_kline(db_manager, stock_code)
        if df is None or df.empty:
            return None, None
        last_date = df['date'].max()
        end = last_date.strftime('%Y%m%d')
        start = (last_date - pd.Timedelta(days=45)).strftime('%Y%m%d')
        factor_df = factor_fetcher.get_factor_history(pro, ts_code, start, end)
        # 目标日期（date 或最新交易日）
        target = pd.to_datetime(date) if date else last_date
        chips_df = factor_fetcher.get_chips(pro, ts_code, target.strftime('%Y%m%d'))
    except Exception as e:
        logger.debug(f'因子/筹码获取失败 {stock_code}: {e}')
    return factor_df, chips_df


def compute_distribution_score(db_manager, stock_code: str,
                               date: Optional[str] = None,
                               moneyflow_df: Optional[pd.DataFrame] = None,
                               factor_df: Optional[pd.DataFrame] = None,
                               chips_df: Optional[pd.DataFrame] = None) -> Dict:
    """五维出货(派发)信号加权评分

    Args:
        db_manager: DBManager 实例（含 read_stock）
        stock_code: 股票代码
        date: 目标日期 YYYY-MM-DD，默认最新交易日
        moneyflow_df: 可选，Tushare moneyflow 逐日数据（资金维度）
        factor_df: 可选，stk_factor_pro 单股历史（换手/技术维度，缺省内部获取）
        chips_df: 可选，cyq_chips 当日筹码（筹码维度，缺省内部获取）

    Returns:
        {'score': 0-100, 'level': str, 'dimensions': {维度: 得分},
         'signals': [命中信号], 'available': {维度: bool}}
    """
    df = _read_kline(db_manager, stock_code)
    if df is None or df.empty:
        return {'score': 0.0, 'level': '正常/洗盘', 'dimensions': {},
                'signals': [], 'available': {d: False for d in DIM_WEIGHTS}}
    rows = df.to_dict('records')
    closes = [float(r['close']) for r in rows]
    highs = [float(r['high']) for r in rows]
    lows = [float(r['low']) for r in rows]
    opens = [float(r['open']) for r in rows]
    vols = [float(r.get('volume') or 0) for r in rows]
    idx = len(closes) - 1
    target_date = None
    if date:
        d = pd.to_datetime(date)
        for k, r in enumerate(rows):
            if pd.to_datetime(r['date']) <= d:
                idx = k
        target_date = pd.to_datetime(date)
    else:
        target_date = pd.to_datetime(rows[-1]['date'])

    # 换手/筹码数据：优先用传入，否则按需获取
    if factor_df is None or chips_df is None:
        _f, _c = _fetch_factor_chips(db_manager, stock_code, target_date.strftime('%Y-%m-%d'))
        factor_df = factor_df if factor_df is not None else _f
        chips_df = chips_df if chips_df is not None else _c

    date_str = target_date.strftime('%Y%m%d')
    vol_score, vol_signals = _volume_dimension(closes, highs, lows, opens, vols, idx)
    fund_score, fund_signals, fund_avail = _fund_dimension(moneyflow_df)

    circ_mv_val = None
    if factor_df is not None and not factor_df.empty:
        try:
            from utils import factor_fetcher
            circ_mv_val = factor_fetcher.circ_mv(factor_df, date_str)
        except Exception:
            circ_mv_val = None
    turnover_score, turnover_signals, turnover_avail = _turnover_dimension(factor_df, circ_mv_val, closes, idx)
    prev = closes[idx - 1] if idx >= 1 else closes[idx]
    pct = (closes[idx] - prev) / prev * 100 if prev else 0
    chips_score, chips_signals, chips_avail = _chips_dimension(chips_df, closes[idx], pct)
    tech_score, tech_signals = _tech_dimension(closes, highs, vols, idx, factor_df, date_str)

    dims = {'量能': vol_score, '资金': fund_score, '换手': turnover_score,
            '筹码': chips_score, '技术': tech_score}
    avail = {'量能': True, '资金': fund_avail, '换手': turnover_avail,
             '筹码': chips_avail, '技术': True}

    total_w = sum(w for d, w in DIM_WEIGHTS.items() if avail.get(d))
    if total_w <= 0:
        return {'score': 0.0, 'level': '正常/洗盘', 'dimensions': dims,
                'signals': [], 'available': avail}
    score = sum(dims[d] * DIM_WEIGHTS[d] for d in DIM_WEIGHTS if avail.get(d)) / total_w
    score = round(score, 1)

    if score >= 85:
        level = '确认出货'
    elif score >= 60:
        level = '高度警惕'
    elif score >= 30:
        level = '警惕'
    else:
        level = '正常/洗盘'

    signals = vol_signals + fund_signals + turnover_signals + chips_signals + tech_signals
    return {'score': score, 'level': level, 'dimensions': dims,
            'signals': signals, 'available': avail}
