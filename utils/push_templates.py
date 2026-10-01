# -*- coding: utf-8 -*-
"""缅A推送模板（唯一模板，供 web_server.py 与 main.py 共用）

只维护这一份模板：模板二「缅A持仓操作」。
飞书 / 钉钉推送内容统一由本模块构建，任何入口调用结果完全一致，避免维护多套模板。
"""
from datetime import datetime as _dt


def _mutual_strength(weak_dict, strong_dict):
    """走强/走弱互斥判定：返回 weak | strong | none（二者不同时非 none 输出，只记录一个状态）

    规则：按等级权重比较（弱 none=0 warning=1 confirmed=2 trend_weak=3；
    强 none=0 appear=1 confirmed=2 trend_up=3），等级高者胜；
    等级相同再按趋势方向（趋势转空→weak，趋势转多→strong）；否则 none。
    """
    wl = (weak_dict or {}).get('level', 'none')
    sl = (strong_dict or {}).get('level', 'none')
    wk = {'none': 0, 'warning': 1, 'confirmed': 2, 'trend_weak': 3}
    sk = {'none': 0, 'appear': 1, 'confirmed': 2, 'trend_up': 3}
    wR = wk.get(wl, 0)
    sR = sk.get(sl, 0)
    tbear = bool((weak_dict or {}).get('trend_bear'))
    tup = bool((strong_dict or {}).get('trend_up'))
    if wR > sR:
        return 'weak:' + (wl if wl != 'none' else 'warning')
    if sR > wR:
        return 'strong:' + (sl if sl != 'none' else 'appear')
    if tbear and not tup:
        return 'weak:warning'
    if tup and not tbear:
        return 'strong:appear'
    return 'none'


def strength_label(db, code):
    """计算单只股票的强弱真伪标识（真走强/假走强/真走弱/假走弱/空），弱强互斥"""
    try:
        from utils.weak_signal import compute_weak_signal
        from utils.strong_signal import compute_strong_signal
        w = compute_weak_signal(db, code)
        s = compute_strong_signal(db, code)
        lv = _mutual_strength(w, s)
        if lv.startswith('weak:'):
            return '假走弱' if (w or {}).get('falsify') else '真走弱'
        if lv.startswith('strong:'):
            return '真走强' if (s or {}).get('confirm') else '假走强'
        return ''
    except Exception:
        return ''


def _pxs_of(prices, code):
    px = prices.get(code, '-')
    return f"｜{px}" if px and px != '-' else ''


def _fetch_prev_holds(today_str):
    """读取前3个选股日的历史纳入持仓（去重）：
    [{code, name, strat, date, price}]，失败返回 []
    """
    prev_holds = []
    try:
        from utils.selection_record_manager import SelectionRecordManager
        _srm = SelectionRecordManager()
        _hist = _srm.get_selection_history({'end_date': today_str}, page=1, limit=5000)
        _rec_by_date = {}
        for _r in (_hist.get('data') or []):
            _d = _r.get('selection_date', '')
            if _d and _d < today_str:
                _rec_by_date.setdefault(_d, []).append(_r)
        _last3 = sorted(_rec_by_date.keys())[-3:]
        for _d in _last3:
            for _r in _rec_by_date[_d]:
                _c = _r.get('stock_code', '')
                if _c and all(x['code'] != _c for x in prev_holds):
                    prev_holds.append({
                        'code': _c,
                        'name': _r.get('stock_name', ''),
                        'strat': _r.get('strategy_name', ''),
                        'date': _d,
                        'price': _r.get('selection_price') or _r.get('price') or 0,
                    })
    except Exception:
        pass
    return prev_holds


def build_hold_message(all_stocks, stock_strategies, stock_prices,
                       strength_fn, analyze_fn, prev_hold_records, hold_fn):
    """模板二：缅A持仓操作（唯一推送模板）

    all_stocks:      [{code, name}] 今日入选（去重）
    stock_strategies:{code -> [策略名]}
    stock_prices:    {code -> 价格}
    strength_fn(code) -> 强弱字符串（可空）
    analyze_fn(code, name) -> {score, ...} 用于股票评分
    prev_hold_records:[{code, name, strat, date, price}] 历史纳入持仓
    hold_fn(code, name, date, price) -> {level, current, stop, target} 或 None
    """
    lines = [f"📈 缅A持仓操作 · {_dt.now().strftime('%Y-%m-%d %H:%M')}"]

    def _score_of(code, nm):
        try:
            a = analyze_fn(code, nm)
            sc = (a or {}).get('score', 0)
            return f"{sc}分" if sc and sc > 0 else '--'
        except Exception:
            return '--'

    # ◆ 今日买入（新纳入策略）：多策略共振置顶强调
    lines.append("")
    lines.append("◆ 今日买入（新纳入策略）")
    if all_stocks:
        # 今日新纳入 = 今天入选且此前未纳入的股票（旧有持仓不在此列，仅归入持仓跟踪）
        prev_codes = {r.get('code') for r in prev_hold_records}
        new_items = {c: s for c, s in stock_strategies.items() if c not in prev_codes}
        if new_items:
            multi = {c: s for c, s in new_items.items() if len(s) >= 2}
            if multi:
                _multi_names = ' / '.join(
                    next((s['name'] for s in all_stocks if s['code'] == c), c)
                    for c in multi)
                lines.append(f"⭐ 多策略共振 {len(multi)} 只｜优先关注：{_multi_names}")

            rows = []
            for code, strats in new_items.items():
                nm = next((s['name'] for s in all_stocks if s['code'] == code), '')
                st = '·'.join(strats) or '--'
                sl = strength_fn(code) or '--'
                score_s = _score_of(code, nm)
                stop_s, tgt_s = '｜止损 --', '｜止盈 --'
                try:
                    h = hold_fn(code, nm, None, None)
                    if h:
                        stop_s = f"｜止损 {h['stop']:.2f}" if h.get('stop') else '｜止损 --'
                        tgt_s = f"｜止盈 {h['target']:.2f}" if h.get('target') else '｜止盈 --'
                except Exception:
                    pass
                is_multi = code in multi
                rows.append((0 if is_multi else 1,
                             f"{'⭐' if is_multi else '🟢'} 买入｜{nm}{_pxs_of(stock_prices, code)}｜{st}｜{score_s}｜{sl}{stop_s}{tgt_s}"))
            for _, line in sorted(rows, key=lambda x: x[0]):
                lines.append(line)
        else:
            lines.append("　今日无新纳入")
    else:
        lines.append("　今日无新纳入")

    # ◆ 持仓跟踪（历史纳入：持有/减仓/卖出）
    if prev_hold_records:
        lvl_icon = {'持有': '🟢 持有', '减仓': '🟡 减仓', '卖出': '🔴 卖出'}
        lines.append("")
        lines.append("◆ 持仓跟踪（历史纳入）")
        rows = []
        for info in prev_hold_records:
            try:
                a = hold_fn(info['code'], info['name'], info.get('date'), info.get('price'))
            except Exception:
                a = None
            if a:
                tip = lvl_icon.get(a['level'], a['level'])
                strat = info.get('strat') or '--'
                _nm = info.get('name') or a.get('name', '')
                sl = strength_fn(info['code']) or '--'
                score_s = _score_of(info['code'], _nm)
                stop_s = f"｜止损 {a['stop']:.2f}" if a.get('stop') else '｜止损 --'
                tgt_s = f"｜止盈 {a['target']:.2f}" if a.get('target') else '｜止盈 --'
                rows.append((a['level'],
                             f"{tip}｜{_nm}｜{a['current']:.2f}｜{strat}｜{score_s}｜{sl}{stop_s}{tgt_s}"))
        order = {'卖出': 0, '减仓': 1, '持有': 2}
        for lv, line in sorted(rows, key=lambda x: order.get(x[0], 9)):
            lines.append(line)

    return "\n".join(lines)


def build_message(all_stocks, stock_strategies, stock_prices,
                  strength_fn, analyze_fn, hold_fn):
    """一键构建唯一推送消息（模板二）"""
    prev_holds = _fetch_prev_holds(_dt.now().strftime('%Y-%m-%d'))
    return build_hold_message(all_stocks, stock_strategies, stock_prices,
                              strength_fn, analyze_fn, prev_holds, hold_fn)
