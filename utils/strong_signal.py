"""多维走强共振判定（与 utils/weak_signal.py 对称）：判断标的是否走强（供选股/排名/个股分析复用）

走强四维框架：
① 趋势（权重最高，定方向）：收盘站上 MA20/MA60、短期均线上穿长期(金叉)/多头排列、MACD 金叉红柱放大 / DIF 上穿 0 轴
② 量能（验证真假）：价涨量增、回调缩量(洗盘)、OBV 能量潮上行、换手温和放大；最怕"放量滞涨"(天量价不动=见顶隐患)
③ 动量（看强势程度）：RSI 上穿 50(转多头)、KDJ 金叉、持续创 N 日新高、重心上移
④ 相对强弱（看地位）：持续跑赢大盘；大盘弱它强=α 走强(重点跟踪)，大盘强它也强=β 跟涨

组合判定：单维触发=走强迹象(观察)；趋势转多 + 量能/动量两维共振=确认走强(可介入)；四维齐强=趋势确立(持股为主)。
证真条件：走强是过程，回踩不破关键均线(尤其 MA20) → confirm(证真)。
"""

import logging
logger = logging.getLogger(__name__)

DIM_WEIGHT = {'trend': 40, 'volume': 20, 'momentum': 20, 'relative': 20}


def _empty(level='none', reason=''):
    return {'level': level, 'strong_score': 0, 'trend_up': False, 'confirm': False,
            'signals': [], 'reason': reason,
            'dims': {'trend': {'strong': False, 'signals': []},
                     'volume': {'strong': False, 'signals': []},
                     'momentum': {'strong': False, 'signals': []},
                     'relative': {'strong': False, 'unavailable': True, 'signals': []}}}


def compute_strong_signal(db_manager, stock_code: str, df=None, lookback: int = 20) -> dict:
    """计算标的的多维走强共振信号（对称于 compute_weak_signal）

    Args:
        db_manager: DBManager 实例（须含 read_stock）
        stock_code: 股票代码
        df: 可选，已读入的K线DataFrame（正序/倒序均可，内部统一）
        lookback: 动量"创N日新高"回看窗口

    Returns:
        {'level': none|appear|confirmed|trend_up,
         'strong_score': 0~100,
         'trend_up': bool,   # 趋势方向是否已转多
         'confirm': bool,    # 回踩不破MA20 → 证真（可持股）
         'signals': [触发信号文本],
         'dims': {四维各维度明细}}
    """
    try:
        import pandas as pd
        from utils.weak_signal import _index_recent_return, _to_py
        if df is None:
            df = db_manager.read_stock(stock_code) if db_manager else None
        if df is None or df.empty:
            return _empty(reason='无K线')

        d = pd.to_datetime(df['date'])
        if len(df) > 1 and d.iloc[0] > d.iloc[-1]:
            df = df.iloc[::-1].reset_index(drop=True)

        closes = df['close'].astype(float).tolist()
        if len(closes) < 70:
            return _empty(reason='K线不足70根')
        opens = df['open'].astype(float).tolist()
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

        # ── ① 趋势维度（方向确认是走强根本）──
        trend_signals = []
        above20 = c > ma20.iloc[-1]
        above60 = c > ma60.iloc[-1]
        if above20:
            trend_signals.append('站上MA20')
        if above60:
            trend_signals.append('站上MA60')
        if ma5.iloc[-1] > ma20.iloc[-1]:
            trend_signals.append('MA5上穿MA20(金叉/多头)')
        if ma20.iloc[-1] > ma60.iloc[-1]:
            trend_signals.append('MA20>MA60多头排列')
        _dif = float(macd['macd'].iloc[-1])
        _dea = float(macd['macd_signal'].iloc[-1])
        _hist = float(macd['macd_hist'].iloc[-1])
        _hist_prev = float(macd['macd_hist'].iloc[-2]) if len(macd) > 1 else 0.0
        if _dif > _dea and _hist > 0 and _hist > _hist_prev:
            trend_signals.append('MACD金叉红柱放大')
        elif _dif > 0:
            trend_signals.append('DIF上穿0轴')
        trend_up = bool(trend_signals) and (above20 or above60)

        # ── ② 量能维度（验证真假；最怕放量滞涨）──
        vol_signals = []
        prev_c = closes[-2]
        today_vol, today_c = vols[-1], closes[-1]
        vol5 = (sum(vols[-6:-1]) / 5) if len(vols) >= 6 else None
        if today_c > prev_c and vol5 and today_vol > vol5:
            vol_signals.append('价涨量增')
        if today_c < prev_c and vol5 and today_vol < vol5 * 0.8:
            vol_signals.append('回调缩量(洗盘)')
        if vol5 and today_vol > vol5 and today_vol < vol5 * 3:
            vol_signals.append('换手温和放大')
        # OBV 能量潮最近5日上行（≥3日走高）
        obv, ob = [], 0.0
        for i in range(len(closes)):
            if i == 0:
                ob += vols[0]
            else:
                ob += vols[i] if closes[i] > closes[i - 1] else (-vols[i] if closes[i] < closes[i - 1] else 0)
            obv.append(ob)
        if len(obv) >= 6 and sum(1 for k in range(len(obv) - 5, len(obv)) if obv[k] > obv[k - 1]) >= 3:
            vol_signals.append('OBV能量潮上行')
        # 放量滞涨（天量价不动，见顶隐患，不算走强）
        liangzhi = bool(vol5) and today_vol >= vol5 * 3 and prev_c and (today_c - prev_c) / prev_c < 0.02
        if liangzhi:
            vol_signals.append('⚠放量滞涨(见顶隐患)')
        volume_strong = bool([x for x in vol_signals if not x.startswith('⚠')])

        # ── ③ 动量维度（看强势程度）──
        mom_signals = []
        _r = float(rsi['rsi'].iloc[-1]) if 'rsi' in rsi else None
        _rp = float(rsi['rsi'].iloc[-2]) if 'rsi' in rsi and len(rsi) > 1 else None
        if _r is not None and _r > 50 and (_rp is None or _rp <= 50):
            mom_signals.append(f'RSI{_r:.0f}上穿50(转多头)')
        _K, _D = float(kdj['K'].iloc[-1]), float(kdj['D'].iloc[-1])
        _Kp, _Dp = float(kdj['K'].iloc[-2]), float(kdj['D'].iloc[-2])
        if _K > _D and _Kp <= _Dp:
            mom_signals.append('KDJ金叉')
        hhv = s_close.rolling(lookback).max()
        _nhigh = sum(1 for k in range(max(1, len(closes) - 5), len(closes))
                     if closes[k] >= hhv.iloc[k] and closes[k] > closes[k - 1])
        if _nhigh >= 2:
            mom_signals.append(f'持续创{lookback}日新高')
        if len(closes) >= 10 and sum(closes[-5:]) / 5 > sum(closes[-10:-5]) / 5:
            mom_signals.append('重心上移')
        momentum_strong = bool(mom_signals)

        # ── ④ 相对强弱维度（大盘弱它强=α走强重点，大盘强它也强=β跟涨）──
        rel = {'strong': False, 'unavailable': True, 'signals': []}
        try:
            idx_ret = _index_recent_return(lookback)
            if idx_ret is not None and len(closes) > lookback:
                stock_ret = (closes[-1] / closes[-1 - lookback] - 1)
                rel['unavailable'] = False
                if stock_ret > idx_ret + 0.01:
                    rel['strong'] = True
                    if idx_ret <= 0.01:
                        rel['signals'].append(f'大盘弱它强(α走强,重点): 个股{stock_ret*100:.1f}%>大盘{idx_ret*100:.1f}%')
                    else:
                        rel['signals'].append(f'跑赢大盘(β跟涨偏强): 个股{stock_ret*100:.1f}%>大盘{idx_ret*100:.1f}%')
                else:
                    rel['signals'].append(f'未跑赢大盘(个股{stock_ret*100:.1f}%≤大盘{idx_ret*100:.1f}%)')
        except Exception:
            rel['unavailable'] = True
        rel_hit = rel['strong'] if not rel['unavailable'] else None

        # ── 证真：回踩不破关键均线(尤其MA20) ──
        # 修正(2026-10-01)：原用"最近20日最低价>=最新MA20*0.98"会把走强启动前的旧低点计入，
        #   且用最新(已抬升)MA20 对比历史低点存在时间错位，导致连红急拉的强势股被误判为"假走强"。
        #   改为：当前仍站上MA20 且 最近5日回踩低点未有效跌破MA20，才确认为真走强。
        confirm = False
        if closes[-1] > ma20.iloc[-1]:
            # 用"最近5日收盘持续站上各自当日MA20"证真走强（对称于走弱证伪"连续收回均线"）；
            # 避免用最新(已抬升)MA20 对比历史最低价造成的时间错位，也排除盘中下影线的干扰。
            _seg = max(1, len(closes) - 5)
            confirm = bool(all(closes[k] > ma20.iloc[k] for k in range(_seg, len(closes))))

        # ── 共振级别组合判定 ──
        hit_t, hit_v, hit_m, hit_r = int(trend_up), int(volume_strong), int(momentum_strong), int(bool(rel_hit))
        if trend_up and volume_strong and momentum_strong and (rel_hit is not False):
            level = 'trend_up'  # 四维齐强=趋势确立(持股为主)
        elif trend_up and (volume_strong or momentum_strong):
            level = 'confirmed'  # 趋势转多+量能/动量两维共振=确认走强(可介入)
        elif hit_t + hit_v + hit_m + hit_r >= 1:
            level = 'appear'  # 单维触发=走强迹象(观察)
        else:
            level = 'none'

        strong_score = hit_t * DIM_WEIGHT['trend'] + hit_v * DIM_WEIGHT['volume'] \
                       + hit_m * DIM_WEIGHT['momentum'] + (DIM_WEIGHT['relative'] if hit_r else 0)

        all_signals = trend_signals + vol_signals + mom_signals + rel['signals']
        return _to_py({
            'level': level, 'strong_score': strong_score, 'trend_up': trend_up,
            'confirm': confirm, 'signals': all_signals,
            'dims': {'trend': {'strong': trend_up, 'signals': trend_signals},
                     'volume': {'strong': volume_strong, 'signals': vol_signals},
                     'momentum': {'strong': momentum_strong, 'signals': mom_signals},
                     'relative': {'strong': bool(rel_hit), 'unavailable': rel['unavailable'],
                                  'signals': rel['signals']}},
        })
    except Exception as e:
        logger.warning(f"走强共振判定异常 {stock_code}: {e}")
        return _empty(reason=str(e))
