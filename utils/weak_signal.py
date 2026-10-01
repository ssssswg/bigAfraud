"""多维弱转共振判定：判断旧持有标的是否走弱（供策略持有决策的切换/舍弃前置）

核心思路（逆人性、不轻易判弱）：
- 方向没破，不轻易判弱 —— 趋势维度必须跌破关键均线（MA20/MA60）才算"方向转空"。
- 单维触发 = 预警（减仓观察）；趋势转空 + 量能/动量转弱（两维共振）＝确认走弱；
  四维齐弱 ＝ 趋势性走弱（反抽即减，别抢反弹）。
- 证伪条件：收盘价连续 3 日以上收回关键均线 → 走弱被证伪，恢复正常强弱对比。

四个维度：
  ① 趋势（权重最高，定方向）：跌破MA20/MA60、MA20<MA60空头排列、MACD死叉且绿柱放大 / DIF破零轴
  ② 量能（验证真假）：下跌放量、反弹缩量、OBV能量潮下行、换手/量能持续萎缩
  ③ 动量（看衰竭）：RSI<50（多空分水岭）、KDJ死叉、反复创N日新低
  ④ 相对强弱（看地位）：持续跑输大盘，且大盘强个股弱才是真正的α走弱（大盘弱个股弱是普跌β）

相对强弱依赖大盘指数数据，缺失时降级为"unavailable"（不阻断，三维共振也可确认趋势性走弱）。
"""

import logging
logger = logging.getLogger(__name__)

# 维度权重（用于弱转强度评分，0~100）
DIM_WEIGHT = {'trend': 40, 'volume': 20, 'momentum': 20, 'relative': 20}

# 大盘近 N 日收益的模块级缓存（避免每只股票重复拉取大盘）
_INDEX_RET_CACHE = {'data': None, 'lookback': 0}


def _empty(level='none', reason=''):
    return {'level': level, 'weak_score': 0, 'trend_bear': False, 'falsify': False,
            'signals': [], 'reason': reason,
            'dims': {'trend': {'weak': False, 'signals': []},
                     'volume': {'weak': False, 'signals': []},
                     'momentum': {'weak': False, 'signals': []},
                     'relative': {'weak': False, 'unavailable': True, 'signals': []}}}


def _to_py(o):
    """递归把 numpy 标量转成原生 Python 类型（保证 JSON 可序列化）"""
    import numpy as np
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, dict):
        return {k: _to_py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_py(x) for x in o]
    return o


def _index_recent_return(lookback: int):
    """大盘近 lookback 个交易日累计对数收益（模块级缓存）"""
    if _INDEX_RET_CACHE['lookback'] == lookback and _INDEX_RET_CACHE['data'] is not None:
        return _INDEX_RET_CACHE['data']
    try:
        from utils.index_data_fetcher import IndexDataFetcher
        rets = IndexDataFetcher().fetch_index_returns(end_date=None, lookback_days=lookback)
        val = float(sum(rets[-lookback:])) if (rets is not None and len(rets)) else None
    except Exception:
        val = None
    _INDEX_RET_CACHE.update({'data': val, 'lookback': lookback})
    return val


def compute_weak_signal(db_manager, stock_code: str, df=None, lookback: int = 20) -> dict:
    """计算旧持有标的的多维弱转共振信号

    Args:
        db_manager: DBManager 实例（须含 read_stock）
        stock_code: 股票代码
        df: 可选，已读入的K线DataFrame（正序/倒序均可，内部统一）
        lookback: 动量"创N日新低"的回看窗口

    Returns:
        {'level': none|warning|confirmed|trend_weak,
         'weak_score': 0~100,
         'trend_bear': bool,   # 趋势方向是否已转空
         'falsify': bool,      # 连续3日收回关键均线 → 走弱证伪
         'signals': [触发信号文本],
         'dims': {趋势/量能/动量/相对 各维度明细}}
    """
    try:
        import pandas as pd
        if df is None:
            df = db_manager.read_stock(stock_code) if db_manager else None
        if df is None or df.empty:
            return _empty(reason='无K线')

        # 统一正序（从早到晚）
        d = pd.to_datetime(df['date'])
        if len(df) > 1 and d.iloc[0] > d.iloc[-1]:
            df = df.iloc[::-1].reset_index(drop=True)

        closes = df['close'].astype(float).tolist()
        if len(closes) < 70:
            return _empty(reason='K线不足70根')
        opens = df['open'].astype(float).tolist()
        highs = df['high'].astype(float).tolist()
        lows = df['low'].astype(float).tolist()
        vols = [float(v or 0) for v in df['volume']]
        s_close = pd.Series(closes)

        ma5 = s_close.rolling(5).mean()
        ma20 = s_close.rolling(20).mean()
        ma60 = s_close.rolling(60).mean()

        from utils import technical
        # 技术因子优先官方接口（stk_factor_pro），获取不到才自算兜底
        with technical.official_factor_scope(stock_code, df):
            macd = technical.MACD(df)
            rsi = technical.RSI(df, 14)
            kdj = technical.KDJ(df)
            _om = technical._official_aligned('ma_bfq_5')
            if _om: ma5 = technical._merge_series(ma5, _om)
            _om = technical._official_aligned('ma_bfq_20')
            if _om: ma20 = technical._merge_series(ma20, _om)
            _om = technical._official_aligned('ma_bfq_60')
            if _om: ma60 = technical._merge_series(ma60, _om)

        c = closes[-1]

        # ── ① 趋势维度（方向没破，不轻易判弱）──
        trend_signals = []
        brk20 = c < ma20.iloc[-1]
        brk60 = c < ma60.iloc[-1]
        if brk20:
            trend_signals.append('跌破MA20')
        if brk60:
            trend_signals.append('跌破MA60')
        if ma20.iloc[-1] < ma60.iloc[-1]:
            trend_signals.append('MA20<MA60空头排列')
        _dif = float(macd['macd'].iloc[-1])
        _dea = float(macd['macd_signal'].iloc[-1])
        _hist = float(macd['macd_hist'].iloc[-1])
        _hist_prev = float(macd['macd_hist'].iloc[-2]) if len(macd) > 1 else 0.0
        if _dif < _dea and _hist < 0 and _hist < _hist_prev:
            trend_signals.append('MACD死叉绿柱放大')
        elif _dif < 0:
            trend_signals.append('DIF破零轴')
        trend_bear = bool(trend_signals) and bool(brk20 or brk60)

        # ── ② 量能维度（验证真假）──
        vol_signals = []
        vol5 = (sum(vols[-6:-1]) / 5) if len(vols) >= 6 else None
        today_vol, today_o, today_c = vols[-1], opens[-1], closes[-1]
        if today_c < today_o and vol5 and today_vol > vol5 * 1.2:
            vol_signals.append('下跌放量')
        elif today_c > today_o and vol5 and today_vol < vol5 * 0.8:
            vol_signals.append('反弹缩量')
        # OBV 能量潮最近5日下行（≥3日走低）
        obv, ob = [], 0.0
        for i in range(len(closes)):
            if i == 0:
                ob += vols[0]
            else:
                ob += vols[i] if closes[i] > closes[i - 1] else (-vols[i] if closes[i] < closes[i - 1] else 0)
            obv.append(ob)
        if len(obv) >= 6 and sum(1 for k in range(len(obv) - 5, len(obv)) if obv[k] < obv[k - 1]) >= 3:
            vol_signals.append('OBV能量潮下行')
        # 换手/量能持续萎缩（近5日量 < 前5日量0.9，无换手数据用成交量近似）
        if len(vols) >= 10 and sum(vols[-5:]) < sum(vols[-10:-5]) * 0.9:
            vol_signals.append('量能持续萎缩')
        volume_weak = bool(vol_signals)

        # ── ③ 动量维度（看衰竭）──
        mom_signals = []
        _r = float(rsi['rsi'].iloc[-1]) if 'rsi' in rsi else None
        if _r is not None and _r < 50:
            mom_signals.append(f'RSI{_r:.0f}<50(多空分水岭)')
        _K, _D = float(kdj['K'].iloc[-1]), float(kdj['D'].iloc[-1])
        _Kp, _Dp = float(kdj['K'].iloc[-2]), float(kdj['D'].iloc[-2])
        if _K < _D and _Kp > _Dp:
            mom_signals.append('KDJ死叉')
        llv = s_close.rolling(lookback).min()
        _nlow = sum(1 for k in range(max(1, len(closes) - 5), len(closes))
                    if closes[k] <= llv.iloc[k] and closes[k] < closes[k - 1])
        if _nlow >= 2:
            mom_signals.append(f'反复创{lookback}日新低')
        momentum_weak = bool(mom_signals)

        # ── ④ 相对强弱维度（看地位；大盘强个股弱=α走弱，大盘弱个股弱=普跌β不判弱）──
        rel = {'weak': False, 'unavailable': True, 'signals': []}
        try:
            idx_ret = _index_recent_return(lookback)
            if idx_ret is not None and len(closes) > lookback:
                stock_ret = (closes[-1] / closes[-1 - lookback] - 1)
                rel['unavailable'] = False
                if idx_ret > 0.01 and stock_ret < idx_ret - 0.01:
                    rel['weak'] = True
                    rel['signals'].append(f'跑输大盘(个股{stock_ret * 100:.1f}%<大盘{idx_ret * 100:.1f}%)')
                elif idx_ret <= 0:
                    rel['signals'].append('大盘弱(普跌β)，非α走弱')
                else:
                    rel['signals'].append(f'跑赢大盘(个股{stock_ret * 100:.1f}%≥大盘{idx_ret * 100:.1f}%)')
        except Exception:
            rel['unavailable'] = True
        rel_hit = rel['weak'] if not rel['unavailable'] else None  # None=无数据

        # ── 证伪：连续3日以上收回关键均线(MA20/MA60) ──
        falsify = False
        if len(closes) >= 3 and len(ma60) >= 1:
            _reclaim = all(closes[k] > ma20.iloc[k] for k in range(len(closes) - 3, len(closes)))
            _reclaim60 = all(closes[k] > ma60.iloc[k] for k in range(len(closes) - 3, len(closes)))
            falsify = bool(_reclaim) and bool(_reclaim60)

        # ── 共振级别组合判定 ──
        hit_t = int(trend_bear)
        hit_v = int(volume_weak)
        hit_m = int(momentum_weak)
        hit_r = int(bool(rel_hit))  # None→0，False→0，True→1
        if falsify:
            level = 'none'  # 走弱被证伪
        elif trend_bear and volume_weak and momentum_weak and (rel_hit is not False):
            level = 'trend_weak'  # 四维齐弱（相对无数据时三维齐弱亦视为趋势性走弱）
        elif trend_bear and (volume_weak or momentum_weak):
            level = 'confirmed'  # 趋势转空 + 量能/动量转弱（两维共振）＝确认走弱
        elif hit_t + hit_v + hit_m + hit_r >= 1:
            level = 'warning'  # 单维触发＝预警减仓观察
        else:
            level = 'none'

        weak_score = hit_t * DIM_WEIGHT['trend'] + hit_v * DIM_WEIGHT['volume'] \
                     + hit_m * DIM_WEIGHT['momentum'] + (DIM_WEIGHT['relative'] if hit_r else 0)

        all_signals = trend_signals + vol_signals + mom_signals + rel['signals']
        return _to_py({
            'level': level, 'weak_score': weak_score, 'trend_bear': trend_bear,
            'falsify': falsify, 'signals': all_signals,
            'dims': {'trend': {'weak': trend_bear, 'signals': trend_signals},
                     'volume': {'weak': volume_weak, 'signals': vol_signals},
                     'momentum': {'weak': momentum_weak, 'signals': mom_signals},
                     'relative': {'weak': bool(rel_hit), 'unavailable': rel['unavailable'],
                                  'signals': rel['signals']}},
        })
    except Exception as e:
        logger.warning(f"弱转共振判定异常 {stock_code}: {e}")
        return _empty(reason=str(e))
