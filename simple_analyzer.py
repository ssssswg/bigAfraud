"""
简单买入建议分析模块
基于技术指标给股票打分，提供买入建议
数据源统一为本地核心库K线（utils.db_manager / utils.price_levels），不依赖外部行情接口。
"""


def calculate_ma(klines, period):
    """计算移动平均线"""
    if len(klines) < period:
        return None
    closes = [k["close"] for k in klines[-period:]]
    return sum(closes) / period


def calculate_rsi(klines, period=14):
    """计算RSI"""
    if len(klines) < period + 1:
        return None
    
    closes = [k["close"] for k in klines[-(period + 1):]]
    gains = []
    losses = []
    
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.0001
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(klines):
    """计算MACD"""
    if len(klines) < 26:
        return None, None, None
    
    closes = [k["close"] for k in klines]
    
    # EMA12
    ema12 = closes[0]
    for i in range(1, len(closes)):
        ema12 = closes[i] * (2/13) + ema12 * (11/13)
    
    # EMA26
    ema26 = closes[0]
    for i in range(1, len(closes)):
        ema26 = closes[i] * (2/27) + ema26 * (25/27)
    
    dif = ema12 - ema26
    
    # 简化计算DEA
    dea = dif * 0.2
    
    macd = (dif - dea) * 2
    
    return dif, dea, macd


def analyze_stock(code, name, date=None):
    """分析单只股票，返回评分和建议（数据源：本地核心库K线；date可选，按该日期截取K线，供历史选股追溯）"""
    try:
        from utils.global_db import get_global_db
        from utils.price_levels import load_df
        _db = get_global_db()
        _df = load_df(_db, code) if not date else _db.read_stock(code, end_date=date, order='asc')
    except Exception:
        return {"code": code, "name": name, "score": 0, "advice": "数据不足", "reasons": []}
    if _df is None or len(_df) < 20:
        return {"code": code, "name": name, "score": 0, "advice": "数据不足", "reasons": []}
    klines = [
        {"date": str(r['date'])[:10], "open": float(r['open']), "close": float(r['close']),
         "high": float(r['high']), "low": float(r['low']), "volume": float(r['volume'])}
        for _, r in _df.iterrows()
    ]
    
    score = 50  # 基础分
    reasons = []
    
    current = klines[-1]["close"]
    
    # 1. 均线分析
    ma5 = calculate_ma(klines, 5)
    ma10 = calculate_ma(klines, 10)
    ma20 = calculate_ma(klines, 20)
    
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            score += 15
            reasons.append("均线多头排列")
        elif current > ma5:
            score += 5
            reasons.append("站上5日均线")
    
    # 2. RSI分析
    rsi = calculate_rsi(klines)
    if rsi:
        if 30 < rsi < 70:
            score += 10
            reasons.append(f"RSI {rsi:.0f}")
        elif rsi < 30:
            score += 15
            reasons.append(f"RSI超卖 {rsi:.0f}")
        elif rsi > 70:
            score -= 10
            reasons.append(f"RSI超买 {rsi:.0f}")
    
    # 3. MACD分析
    dif, dea, macd = calculate_macd(klines)
    if dif is not None:
        if dif > 0 and macd > 0:
            score += 10
            reasons.append("MACD多头")
        elif dif < 0 and macd < 0:
            score -= 5
            reasons.append("MACD空头")
    
    # 4. 成交量分析
    if len(klines) >= 5:
        avg_vol = sum(k["volume"] for k in klines[-5:]) / 5
        last_vol = klines[-1]["volume"]
        if last_vol > avg_vol * 1.5:
            score += 5
            reasons.append("放量上涨")
        elif last_vol < avg_vol * 0.5:
            score -= 5
            reasons.append("缩量调整")
    
    # 5. 涨跌幅分析
    if len(klines) >= 2:
        pct = (current - klines[-2]["close"]) / klines[-2]["close"] * 100
        if 0 < pct < 3:
            score += 5
            reasons.append("温和上涨")
        elif pct > 5:
            score -= 5
            reasons.append("涨幅过大")
    
    # 限制分数范围
    score = max(0, min(100, score))
    
    # 给出建议
    if score >= 70:
        advice = "建议买入"
    elif score >= 55:
        advice = "可以关注"
    elif score >= 40:
        advice = "建议观望"
    else:
        advice = "建议回避"
    
    return {
        "code": code,
        "name": name,
        "score": score,
        "advice": advice,
        "reasons": reasons
    }


def format_analysis_message(analyses):
    """格式化分析结果为飞书消息"""
    if not analyses:
        return ""
    
    lines = ["━━━━━━━━━━━━━━━━━━━━"]
    lines.append("🎯 买入建议分析")
    lines.append("")
    
    # 按分数排序
    sorted_analyses = sorted(analyses, key=lambda x: x["score"], reverse=True)
    
    for a in sorted_analyses:
        emoji = "🟢" if a["score"] >= 70 else "🟡" if a["score"] >= 55 else "⚪" if a["score"] >= 40 else "🔴"
        lines.append(f"{emoji} {a['code']} {a['name']}")
        lines.append(f"  评分: {a['score']}分 | {a['advice']}")
        if a["reasons"]:
            lines.append(f"  理由: {', '.join(a['reasons'][:3])}")
        lines.append("")
    
    return "\n".join(lines)


def generate_advice_for_hold(code, name, entry_date=None, entry_price=None, date=None):
    """对已推荐股票生成持仓操作建议：持有/减仓/卖出 + 止损/止盈价位
    date: 可选，按该日期截取K线（供历史选股追溯），空则用最新

    优先复用统一卖出路由 compute_sell_signal（与选股持有决策/排名卖点同一套规则）：
    传入推荐日 entry_date（及推荐价 entry_price）后，若历史回扫触发卖出信号，
    直接判定为「卖出」；否则再走本函数的技术强弱/高位偏离/放量滞涨判断。
    """
    # ── 数据源：本地核心库K线（不再依赖外部行情接口）──
    try:
        from utils.global_db import get_global_db
        from utils.price_levels import load_df
        _db = get_global_db()
        _df = load_df(_db, code) if not date else _db.read_stock(code, end_date=date, order='asc')
    except Exception:
        return None
    if _df is None or len(_df) < 25:
        return None
    # 统一转成 klines(list of dict) 结构，兼容后续技术判断逻辑
    klines = [
        {"date": str(r['date'])[:10], "open": float(r['open']), "close": float(r['close']),
         "high": float(r['high']), "low": float(r['low']), "volume": float(r['volume'])}
        for _, r in _df.iterrows()
    ]

    current = klines[-1]["close"]
    prev = klines[-2]["close"] if len(klines) >= 2 else current
    pct = (current - prev) / prev * 100 if prev else 0

    # ── 优先复用统一卖出路由（避免与选股持有决策/排名卖点多套卖出逻辑不一致）──
    if entry_date:
        try:
            from utils.sell_signal import compute_sell_signal
            _px = entry_price
            if not _px:
                # 无推荐价时，从K线取推荐日收盘价作为命中价基准
                _target = None
                for _k in klines:
                    if str(_k['date'])[:10] <= str(entry_date)[:10]:
                        _target = _k['close']
                _px = _target
            if _px:
                _sell = compute_sell_signal(_db, code, float(_px), str(entry_date)[:10])
                if _sell and _sell.get('sell_status') == '卖出':
                    _reason = _sell.get('sell_reason') or '触发卖出规则'
                    _stop = _sell.get('sell_price')
                    return {"code": code, "name": name, "current": round(current, 2),
                            "pct": round(pct, 2), "level": "卖出",
                            "signals": [f"触发卖出规则：{_reason}"],
                            "stop": round(float(_stop), 2) if _stop else None,
                            "target": None}
        except Exception:
            pass

    ma5 = calculate_ma(klines, 5)
    ma10 = calculate_ma(klines, 10)
    ma20 = calculate_ma(klines, 20)

    # 布林上轨（月线 ±2σ）
    import statistics
    closes20 = [k["close"] for k in klines[-20:]]
    std20 = statistics.stdev(closes20) if len(closes20) > 1 else 0
    boll_up = (ma20 + 2 * std20) if ma20 else None

    # 近期高低
    high20 = max(k["high"] for k in klines[-20:])
    low10 = min(k["low"] for k in klines[-10:])

    # 量能
    vol5 = sum(k["volume"] for k in klines[-5:]) / 5
    last_vol = klines[-1]["volume"]
    vol_expand = last_vol > vol5 * 1.5 if vol5 else False

    level = "持有"
    signals = []
    stop = None

    # 1) 强弱（MA5 / MA10）
    if ma5 and ma10:
        if current < ma5 and current < ma10:
            level = "卖出"
            signals.append("跌破MA5/MA10")
        elif current < ma5:
            level = "减仓"
            signals.append("跌破MA5(该强不强)")
        else:
            level = "持有"
            signals.append("站上MA5")
    elif ma5:
        if current < ma5:
            level = "减仓"
            signals.append("跌破MA5")
        else:
            level = "持有"
            signals.append("站上MA5")

    # 2) 高位偏离（月线 / 布林上轨 / 5日线）——强者恒强豁免
    dev5 = (current - ma5) / ma5 * 100 if ma5 else 0
    dev20 = (current - ma20) / ma20 * 100 if ma20 else 0
    strong_breakout = ma5 and ma10 and ma5 > ma10 and current >= high20 * 0.98
    if dev20 > 10 or (boll_up and current > boll_up) or dev5 > 10:
        signals.append(f"高位偏离(月线{dev20:.0f}%)")
        if not strong_breakout and level == "持有":
            level = "减仓"

    # 3) 放量滞涨 → 出货信号
    if vol_expand and pct < 2:
        signals.append("放量滞涨,警惕出货")
        if level == "持有":
            level = "减仓"

    # 4) 止损/止盈：用「趋势 + 多层支撑/压力位」量化（本地核心库），替代原简单 MA10 / high20*1.02
    _rec = None
    try:
        from utils.price_levels import get_recommend
        _rec = get_recommend(_db, code, date=date)
    except Exception:
        pass
    if _rec:
        stop = _rec['stop']
        target = _rec['target']
        # 卖出/减仓（转弱）时止损更贴近：用最近支撑下方 3%，与支撑位止损取更近者
        if level in ("卖出", "减仓") and _rec['nearest_support']:
            _tight = round(_rec['nearest_support'] * 0.97, 2)
            stop = _tight if (stop is None or _tight < stop) else stop
    else:
        # 兜底：退化到原逻辑（数据不足时）
        stop = ma10 if ma10 else (ma5 * 0.97 if ma5 else None)
        target = round(high20 * 1.02, 2) if high20 > current else round(current * 1.10, 2)

    return {
        "code": code,
        "name": name,
        "current": round(current, 2),
        "pct": round(pct, 2),
        "level": level,
        "signals": signals,
        "stop": round(stop, 2) if stop else None,
        "target": target,
    }
