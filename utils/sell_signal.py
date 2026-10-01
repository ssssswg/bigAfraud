"""卖出信号计算（逆人性，与散户相反）— 统一卖出路由

供多处复用：
1. 选股排名卖点（RankingManager._compute_sell_advice）
2. 选股逻辑重构的持有标的历史回扫（StrategyHoldManager）
3. 技术面负面指标评分扣分（TechnicalScorer）

口径：盘后确认信号 → 次日开盘价卖出（可执行）；信号在最新交易日触发 → 先给出卖出建议、收益待次日收盘后回填。

逆人性逻辑（与散户相反）：
- 散户破位死扛 → 我们跌破MA5(该强不强)/MA10 即止损卖出
- 散户放量滞涨舍不得卖 → 放量且滞涨 即卖出(出货)
- 散户放量大跌还抄底 → 放量且大跌 即卖出(出货)
- 散户高位贪婪追涨 → 偏离月线>10% / 触布林上轨 即止盈卖出(兑现)
- 散户忽视连续超涨 → 连续2天离5日线阈值 / 连续2天超布林上轨阈值 即卖出(兑现回调风险)
- 强者恒强豁免：强势创新高(MA5>MA10多头且逼近近10日高点)时不因位置高卖出（放量大跌除外）
- 健康上涨豁免：量价齐升(涨幅>0且放量) + 温和放量 + MACD无顶背离 → 放量滞涨/高位止盈不触发，继续持有（跌破均线的破位/放量大跌信号除外）

负面指标参数可在「系统设置-选股参数」页配置（写回 config/strategy_params.yaml 的 negative_signals 段，保存后调用 reload_negative_config 即时生效）。
"""
import logging
from pathlib import Path
from typing import Optional, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# 负面技术指标参数默认值（可在「选股参数」页配置覆盖）
NEGATIVE_DEFAULTS = {
    'break_score': -20,        # 破位（跌破5日线）扣分
    'off5_pct': 10.0,          # 连续2天收盘距5日线阈值（%）
    'off5_score': -20,         # 连续离5日线扣分
    'overboll_pct': 10.0,      # 连续2天收盘超布林上轨阈值（%）
    'overboll_score': -15,     # 连续超布林上轨扣分
    'stagnation_pct': 2.0,     # 放量滞涨：涨幅低于此阈值（%）
    'stagnation_score': -20,   # 放量滞涨扣分
    'bigdrop_pct': 3.0,        # 放量大跌：跌幅达到此阈值（%）
    'bigdrop_score': -30,      # 放量大跌扣分
    'vol_mult': 1.5,           # 放量判定倍数（量 > N倍 5日均量）
}

# 当前生效的负面指标参数（模块加载时从 yaml 读取，可 reload 刷新）
NEGATIVE_CONFIG = dict(NEGATIVE_DEFAULTS)


def _config_path() -> Path:
    p = Path(__file__).parent.parent / "config" / "strategy_params.yaml"
    if not p.exists():
        p = Path.cwd() / "config" / "strategy_params.yaml"
    return p


def reload_negative_config() -> Dict:
    """从 config/strategy_params.yaml 的 negative_signals 段重新加载负面指标参数（保存配置后调用，即时生效）"""
    global NEGATIVE_CONFIG
    cfg = dict(NEGATIVE_DEFAULTS)
    try:
        import yaml
        with open(_config_path(), 'r', encoding='utf-8') as f:
            doc = yaml.safe_load(f) or {}
        neg = (doc.get('negative_signals') or {}) if isinstance(doc, dict) else {}
        for k in cfg:
            if k in neg and isinstance(neg[k], (int, float)):
                cfg[k] = float(neg[k]) if not k.endswith('_score') else int(neg[k])
    except Exception as e:
        logger.warning(f"加载负面指标配置失败: {e}，使用默认值")
    NEGATIVE_CONFIG = cfg
    return cfg


def _negative_score_map() -> Dict[str, int]:
    c = NEGATIVE_CONFIG
    return {
        '破位': c['break_score'],
        '连续离5日线10%': c['off5_score'],
        '连续超布林上轨10%': c['overboll_score'],
        '放量滞涨': c['stagnation_score'],
        '放量大跌': c['bigdrop_score'],
    }


def _prep_df(db_manager, stock_code):
    """读取并规范K线，返回按日期升序的 DataFrame（含 date 列）"""
    df = db_manager.read_stock(stock_code)
    if df is None or df.empty:
        return None
    if 'date' not in df.columns:
        df = df.reset_index()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return df


def _day_negative(closes: List[float], highs: List[float], vols: List[float], i: int) -> Dict:
    """计算第 i 个交易日的负面技术指标（不含豁免逻辑，评分扣分与卖出共用）

    返回：
        {'破位': bool, '连续离5日线10%': bool, '连续超布林上轨10%': bool,
         '放量滞涨': bool, '放量大跌': bool, 'list': [命中项...]}
    """
    c = NEGATIVE_CONFIG
    res = {'破位': False, '连续离5日线10%': False, '连续超布林上轨10%': False,
           '放量滞涨': False, '放量大跌': False, 'list': []}
    if i < 1:
        return res
    ma5 = sum(closes[i - 4:i + 1]) / 5 if i >= 4 else None
    prev = closes[i - 1]
    pct = (closes[i] - prev) / prev * 100 if prev else 0
    vol5 = sum(vols[max(0, i - 5):i]) / min(5, i) if i >= 5 else None
    vol_expand = bool(vol5) and vols[i] > vol5 * c['vol_mult']

    # 破位：收盘跌破5日线
    if ma5 and closes[i] < ma5:
        res['破位'] = True
        res['list'].append('破位')

    # 连续2天收盘距5日线 > 阈值%（超涨偏离，兑现回调风险）
    if i >= 5:
        m5 = sum(closes[i - 5:i]) / 5
        m5p = sum(closes[i - 6:i - 1]) / 5
        if m5 and m5p and (closes[i] - m5) / m5 > c['off5_pct'] / 100 and (closes[i - 1] - m5p) / m5p > c['off5_pct'] / 100:
            res['连续离5日线10%'] = True
            res['list'].append('连续离5日线10%')

    # 连续2天收盘超布林上轨 > 阈值%
    if i >= 20:
        w_i = closes[i - 19:i + 1]
        m_i = sum(w_i) / len(w_i)
        std_i = (sum((x - m_i) ** 2 for x in w_i) / len(w_i)) ** 0.5
        w_p = closes[i - 20:i]
        m_p = sum(w_p) / len(w_p)
        std_p = (sum((x - m_p) ** 2 for x in w_p) / len(w_p)) ** 0.5
        if closes[i] > m_i + 2 * std_i * (1 + c['overboll_pct'] / 100) and closes[i - 1] > m_p + 2 * std_p * (1 + c['overboll_pct'] / 100):
            res['连续超布林上轨10%'] = True
            res['list'].append('连续超布林上轨10%')

    # 放量大跌优先（同一放量K线不重复记放量滞涨）
    if vol_expand and pct <= -c['bigdrop_pct']:
        res['放量大跌'] = True
        res['list'].append('放量大跌')
    elif vol_expand and pct < c['stagnation_pct']:
        res['放量滞涨'] = True
        res['list'].append('放量滞涨')

    return res


def compute_negative_signals(db_manager, stock_code: str, date: Optional[str] = None) -> Dict:
    """返回最新/指定交易日命中的负面技术指标（评分扣分 + 卖出信号统一入口）

    Args:
        db_manager: DBManager 实例（含 read_stock）
        stock_code: 股票代码
        date: 目标日期 YYYY-MM-DD，默认取最新交易日

    Returns:
        {'list': ['破位', ...], 'score': 扣分合计(≤0), '破位': bool, ...}
    """
    df = _prep_df(db_manager, stock_code)
    if df is None or df.empty:
        return {'list': [], 'score': 0, '破位': False, '连续离5日线10%': False,
                '连续超布林上轨10%': False, '放量滞涨': False, '放量大跌': False}
    rows = df.to_dict('records')
    closes = [float(r['close']) for r in rows]
    highs = [float(r['high']) for r in rows]
    vols = [float(r.get('volume') or 0) for r in rows]
    idx = len(closes) - 1
    if date:
        d = pd.to_datetime(date)
        for k, r in enumerate(rows):
            if pd.to_datetime(r['date']) <= d:
                idx = k
    neg = _day_negative(closes, highs, vols, idx)
    smap = _negative_score_map()
    score = sum(smap.get(s, 0) for s in neg['list'])
    neg['score'] = score
    return neg


def compute_sell_signal(db_manager, stock_code: str, selection_price: float,
                        selection_date: str) -> Optional[Dict]:
    """从选股日到最新交易日逐日扫描，返回卖出信号（统一卖出路由）

    Args:
        db_manager: DBManager 实例（须含 read_stock）
        stock_code: 股票代码
        selection_price: 选股/命中日价格（用于计算收益）
        selection_date: 选股/命中日期 YYYY-MM-DD

    Returns:
        命中卖出: {'sell_price', 'sell_reason', 'sell_yield', 'sell_status': '卖出'}
        未命中: {'sell_status': '持有', ...}
        异常/K线不足: None 或 '持有'
    """
    try:
        c = NEGATIVE_CONFIG
        df = _prep_df(db_manager, stock_code)
        if df is None or df.empty or not selection_price:
            return None
        rows = df.to_dict('records')
        closes = [float(r['close']) for r in rows]
        opens = [float(r['open']) for r in rows]
        highs = [float(r['high']) for r in rows]
        vols = [float(r.get('volume') or 0) for r in rows]
        # MACD 预计算（12/26/9），用于顶背离判定
        _s = pd.Series(closes)
        _ema_fast = _s.ewm(span=12, adjust=False).mean()
        _ema_slow = _s.ewm(span=26, adjust=False).mean()
        dif_arr = (_ema_fast - _ema_slow).tolist()
        dea_arr = (_ema_fast - _ema_slow).ewm(span=9, adjust=False).mean().tolist()
        sd = pd.to_datetime(selection_date)
        # 选股日之后起始索引
        start = None
        for k, r in enumerate(rows):
            if r['date'] > sd:
                start = k
                break
        if start is None or start + 9 >= len(rows):
            # K线不足10根：无足够信号，仅标记持有
            return {'sell_price': None, 'sell_reason': '', 'sell_yield': None, 'sell_status': '持有'}
        last_high_price = -float('inf')
        last_high_dif = -float('inf')
        for i in range(start, len(rows)):
            ma5 = sum(closes[i - 4:i + 1]) / 5 if i >= 4 else None
            ma10 = sum(closes[i - 9:i + 1]) / 10 if i >= 9 else None
            ma20 = sum(closes[i - 19:i + 1]) / 20 if i >= 19 else None
            std20 = None
            if i >= 19:
                w = closes[i - 19:i + 1]
                m = sum(w) / len(w)
                std20 = (sum((x - m) ** 2 for x in w) / len(w)) ** 0.5
            prev = closes[i - 1]
            pct = (closes[i] - prev) / prev * 100 if prev else 0
            vol5 = sum(vols[max(0, i - 5):i]) / min(5, i) if i >= 5 else None
            vol_expand = bool(vol5) and vols[i] > vol5 * c['vol_mult']
            near_high = max(highs[max(0, i - 9):i + 1])
            strong = ma5 and ma10 and ma5 > ma10 and closes[i] >= near_high * 0.98
            # 健康上涨（继续持有）：量价齐升 + 温和放量 + MACD无顶背离
            price_up = pct > 0
            vol_up = (i > 0) and vols[i] > vols[i - 1]
            moderate_vol = bool(vol5) and vols[i] > vol5 and vols[i] <= vol5 * 3.0
            top_div = False
            if closes[i] > last_high_price:
                if dif_arr[i] < last_high_dif - 1e-9:
                    top_div = True
                last_high_price = closes[i]
                last_high_dif = dif_arr[i]
            healthy = price_up and vol_up and moderate_vol and (not top_div)

            neg = _day_negative(closes, highs, vols, i)
            # 信号判定（放量大跌最高优先、不豁免）
            reason = None
            if neg['放量大跌']:
                reason = '放量大跌(出货)'
            elif vol5 and vols[i] >= vol5 * 3.0 and closes[i] < opens[i]:
                reason = '天量见顶(出货)'
            elif (vol5 and vols[i] < vol5 * 0.8) and closes[i] >= near_high * 0.98:
                reason = '缩量新高(动能衰竭)'
            elif ma10 and closes[i] < ma5 and closes[i] < ma10:
                reason = '跌破5/10日线(该强不强)'
            elif ma5 and closes[i] < ma5:
                reason = '跌破5日线(该强不强)'
            elif neg['连续离5日线10%'] and not strong:
                reason = f'连续2天离5日线>{c["off5_pct"]:g}%(超涨兑现)'
            elif neg['连续超布林上轨10%'] and not strong:
                reason = f'连续2天超布林上轨>{c["overboll_pct"]:g}%(超涨兑现)'
            elif neg['放量滞涨'] and not healthy:
                reason = '放量滞涨(出货信号)'
            elif ma20 and (closes[i] > ma20 * 1.10 or (std20 and closes[i] > ma20 + 2 * std20)) and not strong and not healthy:
                reason = '高位止盈(偏离月线>10%)'
            if reason:
                # 盘后确认信号 → 次日开盘价卖出（可执行口径）
                if i + 1 < len(rows):
                    sell = opens[i + 1]
                    return {'sell_price': round(sell, 2), 'sell_reason': reason,
                            'sell_yield': round((sell - selection_price) / selection_price * 100, 2),
                            'sell_status': '卖出'}
                # 信号在最新交易日触发：卖出建议提前给出，收益率待次日收盘后回填
                return {'sell_price': None,
                        'sell_reason': f'{reason}（次日开盘卖出，收益待收盘后计算）',
                        'sell_yield': None,
                        'sell_status': '卖出'}
        # 未触发 → 持有
        return {'sell_price': None, 'sell_reason': '', 'sell_yield': None, 'sell_status': '持有'}
    except Exception as e:
        logger.warning(f"计算卖出建议失败 {stock_code}: {e}")
        return None
