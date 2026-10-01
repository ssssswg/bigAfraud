# -*- coding: utf-8 -*-
"""统一支撑/压力位 API + 推荐价格量化模块

数据口径：前复权 OHLCV（本地库 stock_kline 由腾讯 qfq 接口写入，即为前复权）+ 官方技术因子
（tushare stk_factor_pro，doc328，经 factor_fetcher / official_factor_scope 在官方有值时优先采用，否则自算）。

支撑/压力位四层方法（多方法共振 + 区间容差）：
  1. 静态位   —— 前高 / 前低（N 日 high/low 极值，多周期）
  2. 回撤位   —— 斐波那契回撤（波段高低点 0.236~0.786）+ 枢轴点 Pivot（P/S1/S2/S3/R1/R2/R3）
  3. 动态位   —— 均线 MA10/20/30/60、布林带上下轨、直行多空线 bull_bear_line
  4. 量能位   —— 筹码分布（cyq_chips）：筹码峰（套牢/获利密集区）+ 平均成本线（chips_metrics）

核心规则：
  - 所有候选价按 ±容差（默认 0.8%，可用 ±0.5%~1%）聚簇成「区间」；
    落在同一区间的候选方法数（resonance）≥2 时，该位才标记为「共振可信」。
  - 支撑 = 现价下方最近的关键区间，压力 = 现价上方最近的关键区间。
  - 推荐价格（买入/止损/止盈）基于共振支撑/压力位生成，供买入、卖出建议统一调用。

统一 API：compute_support_resistance(db, code) —— 支撑 + 压力合并成单一能力。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 区间聚类容差（按价格比例），允许 ±0.5%~1%
CLUSTER_TOLERANCE = 0.008
# 斐波那契回撤档位
FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786)
# 前高低点多周期窗口
STATIC_LOOKBACKS = (10, 20, 60)
# 枢轴点均值窗口
PIVOT_WINDOW = 20


# ==================== 数据加载 ====================

def load_df(db, stock_code: str):
    """从本地核心库读取前复权K线，返回时间升序 DataFrame

    db: DBManager 实例（须含 read_stock）
    stock_code: 股票代码
    返回: DataFrame（升序，含 date/open/high/low/close/volume）或 None
    """
    try:
        df = db.read_stock(stock_code)
        if df is None or df.empty:
            return None
        if 'date' not in df.columns:
            df = df.reset_index()
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f"读取K线失败 {stock_code}: {e}")
        return None


def _to_klines(df):
    """DataFrame -> list of dict（兼容 simple_analyzer 原有逻辑）"""
    return [
        {"date": str(r['date'])[:10], "open": float(r['open']), "close": float(r['close']),
         "high": float(r['high']), "low": float(r['low']), "volume": float(r['volume'])}
        for _, r in df.iterrows()
    ]


# ==================== 基础指标（官方因子优先，取不到自算） ====================

def _ma(df, n: int) -> Optional[float]:
    """MA(n) 最新值，官方 stk_factor_pro 有 ma_qfq_n 时优先"""
    try:
        from utils.technical import MA
        s = MA(df['close'], n)
        v = s.iloc[-1]
        return float(v) if v == v else None
    except Exception:
        try:
            v = float(df['close'].tail(n).mean())
            return v
        except Exception:
            return None


def _macd_dif(df) -> Optional[float]:
    """MACD DIF 最新值（趋势方向辅助）"""
    try:
        from utils.technical import MACD
        dif, dea, macd = MACD(df)
        v = dif.iloc[-1]
        return float(v) if v == v else None
    except Exception:
        return None


# ==================== 四层支撑/压力候选 ====================

def _static_candidates(df, close: float) -> List[float]:
    """① 静态位：前高/前低（多周期高低点极值）"""
    cands = []
    for n in STATIC_LOOKBACKS:
        if len(df) >= n:
            cands.append(float(df['low'].tail(n).min()))   # 前低（支撑）
            cands.append(float(df['high'].tail(n).max()))  # 前高（压力）
    return cands


def _fib_candidates(df, close: float) -> List[float]:
    """②a 回撤位：斐波那契回撤（近60日波段高低点）"""
    cands = []
    if len(df) >= 20:
        win = df.tail(60)
        hh = float(win['high'].max())
        ll = float(win['low'].min())
        rng = hh - ll
        if rng > 0:
            for k in FIB_LEVELS:
                cands.append(hh - rng * k)  # 回撤位（既作支撑也作压力，由现价方向筛选）
    return cands


def _pivot_candidates(df, close: float) -> List[float]:
    """②b 回撤位：枢轴点 Pivot Points（近 N 日 H/L/C 均值，经典公式）"""
    cands = []
    if len(df) >= 5:
        win = df.tail(PIVOT_WINDOW)
        H = float(win['high'].mean())
        L = float(win['low'].mean())
        C = float(win['close'].mean())
        P = (H + L + C) / 3
        cands += [P,                       # P 中枢
                  2 * P - H,               # S1
                  P - (H - L),             # S2
                  L - 2 * (H - P),         # S3
                  2 * P - L,               # R1
                  P + (H - L),             # R2
                  H + 2 * (P - L)]         # R3
    return cands


def _dynamic_candidates(df, close: float) -> List[float]:
    """③ 动态位：均线 MA10/20/30/60、布林上下轨、直行多空线"""
    cands = []
    for n in (10, 20, 30, 60):
        v = _ma(df, n)
        if v and v > 0:
            cands.append(v)
    try:
        from utils.technical import Bollinger
        bb = Bollinger(df)
        if bb is not None and len(bb):
            up = bb['upper'].iloc[-1]
            low = bb['lower'].iloc[-1]
            for v in (up, low):
                if v == v and v > 0:
                    cands.append(float(v))
    except Exception:
        pass
    try:
        from utils.technical import calculate_zhixing_trend
        zx = calculate_zhixing_trend(df)
        if zx is not None and len(zx):
            bbl = zx['bull_bear_line'].iloc[-1]
            stt = zx['short_term_trend'].iloc[-1]
            for v in (bbl, stt):
                if v == v and v > 0:
                    cands.append(float(v))
    except Exception:
        pass
    return cands


def _chips_concentration(df, close: float) -> Optional[Dict]:
    """筹码集中度：(cost_95pct - cost_5pct) / cost_50pct，越小筹码越集中

    返回 {'ratio', 'state'}：
      state ∈ 集中(≤0.25) | 适中(0.25~0.40) | 分散(>0.40)
    筹码越集中，成本位（支撑/压力）越可信；取不到返回 None。
    """
    try:
        from utils import factor_fetcher
        pro = factor_fetcher.get_pro()
        if pro is None:
            return None
        _code = str(df['code'].iloc[0]) if 'code' in df.columns else str(df.iloc[0].get('code', ''))
        ts_code = factor_fetcher.to_ts_code(_code)
        last_date = str(df['date'].iloc[-1])[:10].replace('-', '')
        perf = factor_fetcher.get_chips_perf(pro, ts_code, last_date)
        if perf is None or perf.empty:
            return None
        row = perf.sort_values('trade_date').iloc[-1].to_dict()
        try:
            c5 = float(row['cost_5pct'])
            c50 = float(row['cost_50pct'])
            c95 = float(row['cost_95pct'])
        except Exception:
            return None
        if not c50 or c50 <= 0:
            return None
        ratio = round((c95 - c5) / c50, 3)
        state = '集中' if ratio <= 0.25 else ('适中' if ratio <= 0.40 else '分散')
        return {'ratio': ratio, 'state': state}
    except Exception:
        return None


def _chips_candidates(df, close: float) -> List[float]:
    """④ 量能位：筹码分布量能位

    - cyq_chips（doc294）：各价位占比 → 筹码峰（套牢/获利密集区）
    - cyq_perf（doc293）：成本分位 cost_85pct/cost_95pct、加权平均成本 weight_avg
      → 套牢盘密集区（压力）/ 获利盘成本中枢（支撑）
    """
    cands = []
    try:
        from utils import factor_fetcher
        pro = factor_fetcher.get_pro()
        if pro is None:
            return cands
        _code = str(df['code'].iloc[0]) if 'code' in df.columns else str(df.iloc[0].get('code', ''))
        ts_code = factor_fetcher.to_ts_code(_code)
        last_date = str(df['date'].iloc[-1])[:10].replace('-', '')

        # ① 逐价位占比：筹码峰
        chips = factor_fetcher.get_chips(pro, ts_code, last_date)
        if chips is not None and not chips.empty:
            c = chips[['price', 'percent']].copy()
            c['price'] = pd_num(c['price'])
            c['percent'] = pd_num(c['percent'])
            c = c.dropna()
            if not c.empty and c['percent'].sum() > 0:
                peak = c.loc[c['percent'].idxmax(), 'price']
                cands.append(float(peak))                     # 筹码峰（密集区，双向）

        # ② 成本分位/加权平均成本
        perf = factor_fetcher.get_chips_perf(pro, ts_code, last_date)
        if perf is not None and not perf.empty:
            row = perf.sort_values('trade_date').iloc[-1].to_dict()
            for k in ('cost_85pct', 'cost_95pct', 'cost_50pct', 'weight_avg', 'cost_15pct'):
                v = row.get(k)
                try:
                    v = float(v)
                except Exception:
                    v = None
                if v and v > 0:
                    cands.append(v)
    except Exception:
        pass
    return cands


def pd_num(s):
    try:
        import pandas as pd
        return pd.to_numeric(s, errors='coerce')
    except Exception:
        return s


# ==================== 聚类共振（区间） ====================

def _cluster(prices, tolerance: float = CLUSTER_TOLERANCE) -> List[Dict]:
    """按 ±容差把候选价聚簇成「区间」

    相邻候选与当前簇 mid 差距 < tolerance 视为同一区间（多方法共振）。
    返回簇列表：[{'mid','low','high','count','methods'}]，count=落在该区间的候选方法数
    """
    prices = sorted({round(float(p), 2) for p in prices if p and p > 0})
    clusters: List[Dict] = []
    for p in prices:
        if clusters and p <= clusters[-1]['mid'] * (1 + tolerance):
            cl = clusters[-1]
            cl['count'] += 1
            cl['mid'] = round((cl['mid'] * (cl['count'] - 1) + p) / cl['count'], 2)
            cl['low'] = min(cl['low'], p)
            cl['high'] = max(cl['high'], p)
            cl['methods'].append(p)
        else:
            clusters.append({'mid': round(p, 2), 'low': round(p, 2),
                             'high': round(p, 2), 'count': 1, 'methods': [p]})
    return clusters


def _fmt_cluster(cl, role: str, close: float) -> Dict:
    """把聚簇结果格式化为统一输出条目"""
    return {
        'price': cl['mid'],
        'range_low': cl['low'],
        'range_high': cl['high'],
        'resonance': cl['count'],           # 多方法共振数（≥2 可信）
        'confirmed': cl['count'] >= 2,      # 是否共振可信
        'role': role,                        # support | resist
    }


# ==================== 统一 API：支撑 + 压力 ====================

def compute_support_resistance(db, stock_code: str,
                               tolerance: float = CLUSTER_TOLERANCE,
                               with_chips: bool = True) -> Dict:
    """统一支撑/压力位 API（四层方法 + 共振 + 区间）

    db: DBManager 实例
    stock_code: 股票代码
    tolerance: 区间容差（默认 0.8%，可取 ±0.5%~1%）
    with_chips: 是否尝试拉取筹码分布（量能位），默认 True

    返回:
        {
          'close': float, 'trend': str,
          'supports': [条目...],   # 现价下方的关键区间（按价格降序）
          'resists':  [条目...],   # 现价上方的关键区间（按价格升序）
          'layers': {static, fib, pivot, dynamic, chips},  # 各层原始候选
        }
    数据不足时返回 None。
    """
    df = load_df(db, stock_code)
    if df is None or len(df) < 30:
        return None

    # 在官方因子作用域内计算（官方 stk_factor_pro 优先，取不到自算）
    try:
        from utils.technical import official_factor_scope
        with official_factor_scope(stock_code, df):
            close = float(df['close'].iloc[-1])
            layer_cands = {
                'static': _static_candidates(df, close),
                'fib': _fib_candidates(df, close),
                'pivot': _pivot_candidates(df, close),
                'dynamic': _dynamic_candidates(df, close),
                'chips': _chips_candidates(df, close) if with_chips else [],
            }
            trend = _trend_of(df)
    except Exception as e:
        logger.warning(f"支撑/压力计算失败 {stock_code}: {e}")
        return None

    all_cands = [p for lst in layer_cands.values() for p in lst]
    clusters = _cluster(all_cands, tolerance)

    supports = [_fmt_cluster(c, 'support', close) for c in clusters if c['mid'] < close]
    resists = [_fmt_cluster(c, 'resist', close) for c in clusters if c['mid'] > close]
    # 支撑按"离现价最近"在前（降序），压力按"离现价最近"在前（升序）
    supports.sort(key=lambda x: x['price'], reverse=True)
    resists.sort(key=lambda x: x['price'])

    return {
        'close': round(close, 2),
        'trend': trend,
        'supports': supports,
        'resists': resists,
        'layers': layer_cands,
        'chips_concentration': _chips_concentration(df, close),  # 筹码集中度（None=未取到）
    }


def _trend_of(df) -> str:
    """趋势判定：上升 / 下降 / 震荡（MA5>MA10>MA20 且 DIF>0 → 上升，反向 → 下降）"""
    try:
        ma5 = _ma(df, 5)
        ma10 = _ma(df, 10)
        ma20 = _ma(df, 20)
        dif = _macd_dif(df)
        if ma5 and ma10 and ma20:
            if ma5 > ma10 > ma20 and (dif is None or dif > 0):
                return '上升'
            if ma5 < ma10 < ma20 and (dif is None or dif < 0):
                return '下降'
        return '震荡'
    except Exception:
        return 'unknown'


# ==================== 推荐价格（基于统一支撑/压力 API） ====================

def _pick_level(levels: List[Dict], close: float, confirmed_only: bool = True,
                role: str = 'support'):
    """从支撑/压力区间里挑"离现价最近且共振可信"的关键位

    优先 confirmed（共振≥2）；无共振位时回退到最近一个未共振位；再兜底用现价比例。
    """
    confirmed = [x for x in levels if x['confirmed']]
    pool = confirmed if confirmed and confirmed_only else levels
    if role == 'support':
        # 支撑取"离现价最近"（价格最高的）
        return pool[0] if pool else None
    else:
        return pool[0] if pool else None


def recommend_prices(db, stock_code: str) -> Optional[Dict]:
    """结合趋势与统一支撑/压力位生成推荐价格（买入价/止损/止盈）与风险收益比

    返回:
        {
          'close','trend',
          'buy',           # None 表示不建议买入（下降趋势）
          'stop','target',
          'nearest_support','nearest_resist',   # 关键区间 mid
          'support_range','resist_range',        # 区间 [low, high]
          'rrr','eligible','space_limited',
        }
    """
    sr = compute_support_resistance(db, stock_code)
    if sr is None:
        return None
    close = sr['close']
    trend = sr['trend']

    supports = sr['supports']
    resists = sr['resists']

    s_pick = _pick_level(supports, close, role='support')
    r_pick = _pick_level(resists, close, role='resist')

    nearest_support = s_pick['price'] if s_pick else round(close * 0.97, 2)
    nearest_resist = r_pick['price'] if r_pick else round(close * 1.06, 2)

    buy = stop = target = None

    if trend == '上升':
        buy = round(max(nearest_support * 1.005, close * 0.99), 2)
        if buy > close:
            buy = round(close * 0.99, 2)
        stop = round(nearest_support * 0.98, 2)
        stop = round(max(stop, close * 0.92), 2)          # 强势突破止损贴近现价
        target = round(nearest_resist, 2) if nearest_resist > close else round(close * 1.08, 2)
    elif trend == '震荡':
        buy = round(max(nearest_support * 1.005, close * 0.995), 2)
        if buy > close:
            buy = round(close * 0.995, 2)
        stop = round(nearest_support * 0.97, 2)
        target = round(nearest_resist, 2) if nearest_resist > close else round(close * 1.05, 2)
    elif trend == '下降':
        buy = None
        stop = round(nearest_support * 0.97, 2)
        target = round(nearest_resist, 2) if nearest_resist > close else round(close * 1.04, 2)
    else:
        buy = round(close * 0.99, 2)
        stop = round(close * 0.96, 2)
        target = round(close * 1.05, 2)

    if target and target <= close:
        target = round(close * 1.06, 2)
    if stop and stop >= close:
        stop = round(close * 0.96, 2)

    rrr = 0.0
    if buy and target and stop and (buy - stop) > 0:
        rrr = round((target - buy) / (buy - stop), 2)
    space_limited = bool(buy) and (nearest_resist - close) < close * 0.025
    eligible = rrr >= 1.0

    return {
        'close': close,
        'trend': trend,
        'buy': buy,
        'stop': round(stop, 2) if stop else None,
        'target': round(target, 2) if target else None,
        'nearest_support': round(nearest_support, 2),
        'nearest_resist': round(nearest_resist, 2),
        'support_range': [s_pick['range_low'], s_pick['range_high']] if s_pick else None,
        'resist_range': [r_pick['range_low'], r_pick['range_high']] if r_pick else None,
        'chips_state': (sr.get('chips_concentration') or {}).get('state') if sr.get('chips_concentration') else None,
        'rrr': rrr,
        'eligible': eligible,
        'space_limited': space_limited,
    }


def get_recommend(db, stock_code: str) -> Optional[Dict]:
    """便捷入口：读本地K线并返回推荐价格（供 simple_analyzer / 推送链路复用）"""
    return recommend_prices(db, stock_code)
