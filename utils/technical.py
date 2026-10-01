"""
技术指标计算模块 - 通达信公式函数实现
"""
import pandas as pd
import numpy as np
import contextvars

# ===== 官方技术指标（stk_factor_pro doc 328）优先注入 =====
# 由 BaseStrategy.execute_selection 在每只股票计算指标前注入；取不到官方值则自动回退自算。
_OFFICIAL_CTX = contextvars.ContextVar('_OFFICIAL_FACTOR', default=None)

# 当前作用域股票代码（由 official_factor_scope 设置），供指标函数缓存复用
_CURRENT_CODE = contextvars.ContextVar('_CURRENT_CODE', default=None)

# 每股技术指标结果缓存：{(ts_code, fname, params, order) -> 指标结果}
# 同轮选股内跨策略复用（同一股票同一指标同一参数只算一次，官方值同注入），选股轮开始清空。
_IND_CACHE = {}

def set_official_factor(dates_series, factor_df):
    """注入当前股票官方因子：dates_series=策略df的date列(与df同index)，factor_df=stk_factor_pro正序df"""
    _OFFICIAL_CTX.set({'dates': dates_series, 'factor': factor_df})

def clear_official_factor():
    _OFFICIAL_CTX.set(None)

def clear_indicator_cache():
    """清空每股指标结果缓存（选股入口每次选股前调用刷新，避免跨轮串扰）"""
    _IND_CACHE.clear()

def _ctx_order():
    """当前官方作用域 df 的顺序：True=倒序(最新在前)，False=正序，None=未知(不缓存)"""
    ctx = _OFFICIAL_CTX.get()
    if ctx and ctx.get('dates') is not None:
        try:
            d = ctx['dates']
            if d is not None and len(d) > 1:
                return bool(d.iloc[0] > d.iloc[-1])
        except Exception:
            pass
    return None

def _cache_key(fname, params):
    """缓存 key：绑定 (code, fname, params, 顺序)。顺序未知则返回 None（不缓存，保守防错位）"""
    code = _CURRENT_CODE.get()
    if not code:
        return None
    order = _ctx_order()
    if order is None:
        return None
    return (code, fname, params, order)

# 批量预热缓存：{ts_code: 当日因子行 dict}，由 prefetch_official_factor_batch 填充。
# official_factor_scope 优先命中，命中则该股不再发 400 天单股请求（选股提速核心）。
_OFFICIAL_BATCH = {}

# 候选股 400 天官方历史缓存：{ts_code: 历史官方因子 df}，由 prefetch_official_factor_history 填充。
# official_factor_scope 命中时历史 100% 官方（优先于批量当日），供最终判定/推荐价格。
_OFFICIAL_HISTORY = {}

def clear_official_factor_batch():
    """清空批量预热缓存（选股入口每次选股前调用刷新）"""
    _OFFICIAL_BATCH.clear()

def prefetch_official_factor_batch(pro=None, trade_date=None):
    """批量预热全市场当日官方因子（1 次请求替代逐只 400 天拉取）。

    用 stk_factor_pro(trade_date=当日) 一次拉全市场当日因子（实测 5560 行 / ~3s），
    按 ts_code 填充 _OFFICIAL_BATCH，供 official_factor_scope 命中。
    失败静默回退原逐只拉取。返回命中股票数。
    """
    try:
        from utils import factor_fetcher
        if pro is None:
            pro = factor_fetcher.get_pro()
        if pro is None or not trade_date:
            return 0
        _td = str(trade_date).replace('-', '').replace(':', '').replace(' ', '')
        df = factor_fetcher.get_all_factors_by_date(pro, _td)
        if df is None or df.empty:
            return 0
        _OFFICIAL_BATCH.clear()
        for _r in df.to_dict('records'):
            _ts = _r.get('ts_code')
            if _ts:
                _OFFICIAL_BATCH[_ts] = _r
        return len(_OFFICIAL_BATCH)
    except Exception:
        return 0

def prefetch_official_factor_history(pro=None, codes=None):
    """对候选股补拉 400 天官方历史（方案C：历史 100% 官方）。

    逐只 get_factor_history 拉 400 天官方因子填 _OFFICIAL_HISTORY，
    供后续 official_factor_scope 命中（历史官方优先于批量当日）。候选股少（≤30），补拉快。
    失败静默回退。返回命中数。
    """
    try:
        from utils import factor_fetcher
        from datetime import datetime, timedelta
        if pro is None:
            pro = factor_fetcher.get_pro()
        if pro is None or not codes:
            return 0
        _end = datetime.now().strftime('%Y%m%d')
        _start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
        n = 0
        for c in codes:
            _ts = factor_fetcher.to_ts_code(str(c))
            _fdf = factor_fetcher.get_factor_history(pro, _ts, _start, _end)
            if _fdf is not None and not _fdf.empty:
                _OFFICIAL_HISTORY[_ts] = _fdf
                n += 1
        return n
    except Exception:
        return 0

def _official_aligned(col, dates=None):
    """按日期把官方列对齐到策略 df 的行，返回 {index: 值}；无官方/列不存在返回 None

    dates: 传入 None 时用当前官方作用域的 date 列（index 对齐）；
           df 型指标函数应显式传 df['date']，按 date 值对齐 → 与 df 重排序无关，官方值盖到正确日期行。
    实现为向量化（pandas map），避免 Python 逐行循环拖慢全市场选股。
    """
    ctx = _OFFICIAL_CTX.get()
    if not ctx:
        return None
    factor = ctx.get('factor')
    if factor is None or dates is None or 'trade_date' not in factor.columns or col not in factor.columns:
        return None
    # 反向遍历官方行（批量当日1行 / 候选≤400行，远少于策略df行数），
    # 对每官方日做 datetime 布尔定位 → 完全避开全量 strftime/map，选股提速关键。
    _dates = pd.to_datetime(dates)
    out = {}
    for _td, _v in zip(factor['trade_date'], factor[col]):
        try:
            _ts = pd.Timestamp(str(_td).strip())
            _hit = (_dates == _ts)
            if _hit.any():
                for _i in _dates.index[_hit]:
                    out[_i] = float(_v)
        except Exception:
            pass
    return out or None

def _merge_series(series_self, official_map):
    """自算为基础，官方有值则覆盖（同 index）。向量化覆盖，避免逐行 Python 赋值拖慢全市场。"""
    if not official_map:
        return series_self
    s = series_self.copy()
    idx = [i for i in official_map if i in s.index]
    if idx:
        s.loc[idx] = [official_map[i] for i in idx]
    return s

from contextlib import contextmanager

@contextmanager
def official_factor_scope(stock_code, df):
    """
    统一官方技术因子上下文（选股 / 个股分析 / 详情页共用）：
    在作用域内为当前股票注入 stk_factor_pro(doc328) 官方技术指标 context，
    utils.technical 的 MA/EMA/KDJ/RSI/MACD 在官方有对应周期时优先用官方值；
    作用域结束时自动清除，未拉取到官方数据则整体回退自算。
    """
    clear_official_factor()
    _code_tok = _CURRENT_CODE.set(str(stock_code) if stock_code else None)
    try:
        if stock_code and df is not None and 'date' in df.columns:
            try:
                from utils import factor_fetcher
                _ts = factor_fetcher.to_ts_code(stock_code)
                _hist = _OFFICIAL_HISTORY.get(_ts)
                if _hist is not None and not _hist.empty:
                    # 1) 候选股：400 天官方历史 → 历史 100% 官方（最高优先）
                    set_official_factor(df['date'], _hist)
                elif _OFFICIAL_BATCH.get(_ts) is not None:
                    # 2) 非候选股：批量预热当日因子 → 单日一行，只对齐最新交易日官方值
                    _row = _OFFICIAL_BATCH[_ts]
                    _fdf = pd.DataFrame([_row])
                    if not _fdf.empty:
                        set_official_factor(df['date'], _fdf)
                else:
                    # 3) 兜底：逐只拉 400 天官方历史
                    from datetime import datetime, timedelta
                    _pro = factor_fetcher.get_pro()
                    if _pro is not None:
                        _now = datetime.now()
                        _fdf = factor_fetcher.get_factor_history(
                            _pro, _ts,
                            (_now - timedelta(days=400)).strftime('%Y%m%d'),
                            _now.strftime('%Y%m%d'))
                        if _fdf is not None and not _fdf.empty:
                            set_official_factor(df['date'], _fdf)
            except Exception:
                pass
        yield
    finally:
        clear_official_factor()
        _CURRENT_CODE.reset(_code_tok)


def prefetch_official_factor(stock_code, df):
    """后台预热 stk_factor_pro 官方因子缓存（不阻塞调用方）。

    K线浏览等快速场景先自算返回，同时异步拉取官方因子填充 proc_cache，
    供后续评分 / 再次查看命中官方优先。尽力而为，失败静默。
    """
    try:
        if stock_code and df is not None and 'date' in df.columns:
            import threading
            threading.Thread(target=_prefetch_official_factor_job,
                             args=(stock_code, df), daemon=True).start()
    except Exception:
        pass


def _prefetch_official_factor_job(stock_code, df):
    try:
        from utils import factor_fetcher
        from datetime import datetime, timedelta
        _pro = factor_fetcher.get_pro()
        if _pro is None:
            return
        _ts = factor_fetcher.to_ts_code(stock_code)
        _now = datetime.now()
        factor_fetcher.get_factor_history(
            _pro, _ts,
            (_now - timedelta(days=400)).strftime('%Y%m%d'),
            _now.strftime('%Y%m%d'))
    except Exception:
        pass




def MA(series, n):
    """
    简单移动平均 - 正确处理倒序排列的数据
    
    对于倒序数据，MA(n)应该取当前及之后n-1个数据的平均值
    实现方式：反转数据 -> 计算rolling -> 反转回来
    """
    key = _cache_key('MA', n)
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    # 反转数据，使数据按时间正序排列
    reversed_series = series.iloc[::-1]
    
    # 在正序数据上计算MA（向前看n个值）
    ma_reversed = reversed_series.rolling(window=n, min_periods=1).mean()
    
    # 反转回来，恢复倒序，保持原始索引
    ma_self = ma_reversed.iloc[::-1]
    _om = _official_aligned('ma_bfq_%d' % n)
    result = _merge_series(ma_self, _om)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def EMA(series, n):
    """
    指数移动平均 - 正确处理倒序排列的数据
    """
    key = _cache_key('EMA', n)
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    reversed_series = series.iloc[::-1]
    ema_reversed = reversed_series.ewm(span=n, adjust=False, min_periods=1).mean()
    ema_self = ema_reversed.iloc[::-1]
    _om = _official_aligned('ema_bfq_%d' % n)
    result = _merge_series(ema_self, _om)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def ma_norm(df, n):
    """顺序无关 MA：输入含 close 的 df（正序/倒序均可），输出与 df 同序同 index。
    官方 ma_bfq_%d 优先，自算兜底；每股缓存复用。"""
    if df is None or df.empty or 'close' not in df.columns:
        return pd.Series([], dtype=float)
    key = _cache_key('MA', n)
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    close = df['close']
    try:
        desc = bool(df['date'].iloc[0] > df['date'].iloc[-1])
    except Exception:
        desc = False
    if desc:
        ma = close.iloc[::-1].rolling(window=n, min_periods=1).mean().iloc[::-1]
    else:
        ma = close.rolling(window=n, min_periods=1).mean()
    _om = _official_aligned('ma_bfq_%d' % n, df['date'])
    result = _merge_series(ma, _om)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def ema_norm(df, n):
    """顺序无关 EMA：输入含 close 的 df（正序/倒序均可），输出与 df 同序同 index。
    官方 ema_bfq_%d 优先，自算兜底；每股缓存复用。"""
    if df is None or df.empty or 'close' not in df.columns:
        return pd.Series([], dtype=float)
    key = _cache_key('EMA', n)
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    close = df['close']
    try:
        desc = bool(df['date'].iloc[0] > df['date'].iloc[-1])
    except Exception:
        desc = False
    if desc:
        ema = close.iloc[::-1].ewm(span=n, adjust=False, min_periods=1).mean().iloc[::-1]
    else:
        ema = close.ewm(span=n, adjust=False, min_periods=1).mean()
    _om = _official_aligned('ema_bfq_%d' % n)
    result = _merge_series(ema, _om)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def LLV(series, n):
    """
    N周期最低值 - 正确处理倒序排列的数据
    """
    key = _cache_key('LLV', n)
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    reversed_series = series.iloc[::-1]
    llv_reversed = reversed_series.rolling(window=n, min_periods=1).min()
    result = llv_reversed.iloc[::-1]
    if key is not None:
        _IND_CACHE[key] = result
    return result


def HHV(series, n):
    """
    N周期最高值 - 正确处理倒序排列的数据
    """
    key = _cache_key('HHV', n)
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    reversed_series = series.iloc[::-1]
    hhv_reversed = reversed_series.rolling(window=n, min_periods=1).max()
    result = hhv_reversed.iloc[::-1]
    if key is not None:
        _IND_CACHE[key] = result
    return result


def SMA(X, n, m):
    """
    移动平均 - 通达信风格
    SMA(X,N,M): X的N日移动平均, M为权重
    公式: Y = (X*M + Y'*(N-M)) / N
    向量化：Y[i] = alpha*X[i] + (1-alpha)*Y[i-1]，alpha=M/N，即 ewm(alpha=alpha, adjust=False).mean()，与递归精确等价
    """
    alpha = m / n
    return X.ewm(alpha=alpha, adjust=False).mean()


def REF(series, n):
    """
    向前引用N周期 - 正确处理倒序排列的数据
    
    对于倒序数据（最新在前），REF(series, 1)应该获取"前一天"的数据
    实现方式：反转数据 -> shift -> 反转回来
    """
    reversed_series = series.iloc[::-1]
    ref_reversed = reversed_series.shift(n)
    return ref_reversed.iloc[::-1]


def EXIST(cond, n):
    """
    N周期内是否存在满足COND的情况 - 正确处理倒序排列的数据
    """
    reversed_cond = cond.iloc[::-1]
    exist_reversed = reversed_cond.rolling(window=n, min_periods=1).max().astype(bool)
    return exist_reversed.iloc[::-1]


def FINANCE(df, field_code):
    """
    财务数据获取
    39: 总市值（注意：原通达信39是流通市值，本项目使用总市值）
    """
    if field_code == 39:
        return df.get('market_cap', pd.Series([0] * len(df), index=df.index))
    return pd.Series([0] * len(df), index=df.index)


def Bollinger(df, period=20, std_dev=2):
    """布林带 - 官方 boll_upper/mid/lower_bfq 优先，自算兜底"""
    key = _cache_key('Bollinger', (period, std_dev))
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    mid = df['close'].rolling(window=period, min_periods=1).mean()
    std = df['close'].rolling(window=period, min_periods=1).std()
    upper = mid + std * std_dev
    lower = mid - std * std_dev
    om_up = _official_aligned('boll_upper_bfq', df['date'])
    om_mid = _official_aligned('boll_mid_bfq', df['date'])
    om_low = _official_aligned('boll_lower_bfq', df['date'])
    if om_up:
        upper = _merge_series(upper, om_up)
    if om_mid:
        mid = _merge_series(mid, om_mid)
    if om_low:
        lower = _merge_series(lower, om_low)
    result = pd.DataFrame({'bb_mid': mid, 'bb_upper': upper, 'bb_lower': lower})
    if key is not None:
        _IND_CACHE[key] = result
    return result


def KDJ(df, n=9, m1=3, m2=3):
    """
    KDJ指标计算 - 标准实现
    通达信公式：
    RSV = (CLOSE - LLV(LOW,N)) / (HHV(HIGH,N) - LLV(LOW,N)) * 100
    K = SMA(RSV,M1,1)
    D = SMA(K,M2,1)
    J = 3*K - 2*D
    
    注意：数据可能是倒序（最新在前）或正序，需要自动检测并处理
    """
    # 检查数据是否为空
    if df is None or df.empty:
        return pd.DataFrame({'K': [], 'D': [], 'J': []}, index=df.index if df is not None else [])
    
    key = _cache_key('KDJ', (n, m1, m2))
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    
    # 检测数据顺序
    try:
        is_descending = df['date'].iloc[0] > df['date'].iloc[-1]
    except (IndexError, KeyError):
        # 如果无法检测顺序，默认按正序处理
        is_descending = False
    
    # 统一转换为正序计算（从早到晚）
    if is_descending:
        df_calc = df.iloc[::-1].copy().reset_index(drop=True)
    else:
        df_calc = df.copy().reset_index(drop=True)
    
    # 计算RSV
    low_min = df_calc['low'].rolling(window=n, min_periods=1).min()
    high_max = df_calc['high'].rolling(window=n, min_periods=1).max()
    
    range_val = high_max - low_min
    # RSV 向量化：前 n-1 周期不足或 range=0 时填 50，否则 (close-low_min)/range*100
    _ok = (np.arange(len(df_calc)) >= n - 1) & (range_val.values != 0)
    _denom = np.where(range_val.values == 0, np.nan, range_val.values)
    rsv = pd.Series(
        np.where(_ok, (df_calc['close'].values - low_min.values) / _denom * 100, 50.0),
        index=df_calc.index, dtype=float)
    rsv = rsv.fillna(50.0)

    # K/D：通达信 SMA(RSV,M1,1) / SMA(K,M2,1)，即 ewm(alpha=1/M, adjust=False)，与递归精确等价（首值=50）
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()

    # 计算J值
    j = 3 * k - 2 * d
    
    # 构建结果
    result = pd.DataFrame({
        'K': k,
        'D': d,
        'J': j
    })
    
    # 恢复原始顺序
    if is_descending:
        result = result.iloc[::-1].reset_index(drop=True)
    
    result.index = df.index
    if (n, m1, m2) == (9, 3, 3):
        for _cs, _co in (('K', 'kdj_k_bfq'), ('D', 'kdj_d_bfq'), ('J', 'kdj_bfq')):
            _om = _official_aligned(_co, df['date'])
            if _om:
                result[_cs] = _merge_series(result[_cs], _om)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def RSI(df, period=14):
    """
    RSI指标计算 - 相对强弱指标
    
    通达信公式：
    LC := REF(CLOSE,1);
    RSI:SMA(MAX(CLOSE-LC,0),N,1)/SMA(ABS(CLOSE-LC),N,1)*100;
    
    参数：
        df: DataFrame，必须包含'close'列
        period: RSI周期，默认14
    
    返回：
        DataFrame，包含'rsi'列
    """
    # 检查数据是否为空
    if df is None or df.empty:
        return pd.DataFrame({'rsi': []}, index=df.index if df is not None else [])
    
    key = _cache_key('RSI', period)
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    
    # 检测数据顺序
    try:
        is_descending = df['date'].iloc[0] > df['date'].iloc[-1]
    except (IndexError, KeyError):
        is_descending = False
    
    # 统一转换为正序计算（从早到晚）
    if is_descending:
        df_calc = df.iloc[::-1].copy().reset_index(drop=True)
    else:
        df_calc = df.copy().reset_index(drop=True)
    
    # 计算价格变化
    close = df_calc['close']
    delta = close.diff()
    
    # 分离上涨和下跌
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    # 计算平均上涨和下跌（使用EMA）
    avg_gain = gain.ewm(com=period-1, adjust=False, min_periods=1).mean()
    avg_loss = loss.ewm(com=period-1, adjust=False, min_periods=1).mean()
    
    # 计算RS和RSI
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    # 处理除零情况
    rsi = rsi.fillna(50)  # 当avg_loss为0时，RSI设为50
    
    # 构建结果
    result = pd.DataFrame({'rsi': rsi})
    
    # 恢复原始顺序
    if is_descending:
        result = result.iloc[::-1].reset_index(drop=True)
    
    result.index = df.index
    if period in (6, 12, 24):
        _om = _official_aligned('rsi_bfq_%d' % period, df['date'])
        if _om:
            result['rsi'] = _merge_series(result['rsi'], _om)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def calculate_zhixing_trend(df, m1=14, m2=28, m3=57, m4=114):
    """
    计算知行趋势线指标
    
    指标定义:
    - 知行短期趋势线 = EMA(EMA(CLOSE,10),10)
      对收盘价连续做两次10日指数移动平均
    
    - 知行多空线 = (MA(CLOSE,m1) + MA(CLOSE,m2) + MA(CLOSE,m3) + MA(CLOSE,m4)) / 4
      四条均线平均值，默认使用 14, 28, 57, 114
    
    参数:
        m1, m2, m3, m4: 多空线计算用的MA周期，默认14, 28, 57, 114
    """
    # 知行短期趋势线 = EMA(EMA(CLOSE,10),10)
    key = _cache_key('zhixing', (m1, m2, m3, m4))
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    # 知行短期趋势线 = EMA(EMA(CLOSE,10),10)（顺序无关，官方优先）
    _s1 = ema_norm(df, 10)
    _df2 = df.copy()
    _df2['close'] = _s1
    short_term_trend = ema_norm(_df2, 10)

    # 知行多空线 = (MA(m1) + MA(m2) + MA(m3) + MA(m4)) / 4（顺序无关，官方优先）
    bull_bear_line = (ma_norm(df, m1) + ma_norm(df, m2) +
                      ma_norm(df, m3) + ma_norm(df, m4)) / 4

    result = pd.DataFrame({
        'short_term_trend': short_term_trend,
        'bull_bear_line': bull_bear_line
    }, index=df.index)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def MACD(df, fastperiod=12, slowperiod=26, signalperiod=9):
    """
    MACD指标计算 - 标准实现
    通达信公式：
    DIF: EMA(CLOSE,12) - EMA(CLOSE,26)
    DEA: EMA(DIF,9)
    MACD: 2*(DIF-DEA)
    
    注意：数据可能是倒序（最新在前）或正序，需要自动检测并处理
    """
    # 检查数据是否为空
    if df is None or df.empty:
        return pd.DataFrame({'macd': [], 'macd_signal': [], 'macd_hist': []}, index=df.index if df is not None else [])
    
    key = _cache_key('MACD', (fastperiod, slowperiod, signalperiod))
    if key is not None and key in _IND_CACHE:
        return _IND_CACHE[key]
    
    # 检测数据顺序
    try:
        is_descending = df['date'].iloc[0] > df['date'].iloc[-1]
    except (IndexError, KeyError):
        # 如果无法检测顺序，默认按正序处理
        is_descending = False
    
    # 统一转换为正序计算（从早到晚）
    if is_descending:
        df_calc = df.iloc[::-1].copy().reset_index(drop=True)
    else:
        df_calc = df.copy().reset_index(drop=True)
    
    # 计算快速和慢速EMA
    ema_fast = df_calc['close'].ewm(span=fastperiod, adjust=False, min_periods=1).mean()
    ema_slow = df_calc['close'].ewm(span=slowperiod, adjust=False, min_periods=1).mean()
    
    # 计算DIF（MACD线）
    dif = ema_fast - ema_slow
    
    # 计算DEA（信号线）
    dea = dif.ewm(span=signalperiod, adjust=False, min_periods=1).mean()
    
    # 计算MACD柱状图
    macd = 2 * (dif - dea)
    
    # 构建结果
    result = pd.DataFrame({
        'macd': dif,       # DIF线
        'macd_signal': dea, # DEA线
        'macd_hist': macd   # MACD柱状图
    })
    
    # 恢复原始顺序
    if is_descending:
        result = result.iloc[::-1].reset_index(drop=True)
    
    result.index = df.index
    if (fastperiod, slowperiod, signalperiod) == (12, 26, 9):
        for _cs, _co in (('macd', 'macd_dif_bfq'), ('macd_signal', 'macd_dea_bfq'), ('macd_hist', 'macd_bfq')):
            _om = _official_aligned(_co, df['date'])
            if _om:
                result[_cs] = _merge_series(result[_cs], _om)
    if key is not None:
        _IND_CACHE[key] = result
    return result


def calculate_price_change(df, method='prev_close'):
    """
    计算价格变化率 - 统一的涨幅计算函数
    
    参数：
        df: DataFrame，必须包含'open'和'close'列
        method: 计算方法
            - 'prev_close': 相对于前一天收盘价的涨幅（标准定义）
            - 'open': 相对于当天开盘价的涨幅（日内涨幅）
    
    返回：
        Series，包含价格变化率
    
    说明：
        - 数据可能是倒序（最新在前）或正序，函数会自动处理
        - 对于倒序数据，前一天是下一行（iloc[i+1]）
        - 对于正序数据，前一天是上一行（iloc[i-1]）
    """
    if df is None or df.empty:
        return pd.Series(dtype=float)
    
    # 检测数据顺序
    try:
        is_descending = df['date'].iloc[0] > df['date'].iloc[-1]
    except (IndexError, KeyError):
        is_descending = False
    
    if method == 'prev_close':
        # 相对于前一天收盘价的涨幅（标准定义）
        if is_descending:
            # 倒序数据：前一天是下一行
            prev_close = df['close'].shift(-1)
        else:
            # 正序数据：前一天是上一行
            prev_close = df['close'].shift(1)
        
        # 计算涨幅
        price_change = (df['close'] - prev_close) / prev_close
        
    elif method == 'open':
        # 相对于当天开盘价的涨幅（日内涨幅）
        price_change = (df['close'] - df['open']) / df['open']
    
    else:
        raise ValueError(f"不支持的计算方法: {method}")
    
    return price_change


def calculate_daily_return(df):
    """
    计算日收益率 - 相对于前一天收盘价的涨幅
    
    这是 calculate_price_change(df, method='prev_close') 的简化版本
    
    参数：
        df: DataFrame，必须包含'close'列
    
    返回：
        Series，包含日收益率
    """
    return calculate_price_change(df, method='prev_close')


def calculate_intraday_return(df):
    """
    计算日内收益率 - 相对于当天开盘价的涨幅
    
    这是 calculate_price_change(df, method='open') 的简化版本
    
    参数：
        df: DataFrame，必须包含'open'和'close'列
    
    返回：
        Series，包含日内收益率
    """
    return calculate_price_change(df, method='open')
