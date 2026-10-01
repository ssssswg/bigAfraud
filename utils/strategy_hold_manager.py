"""
策略持有决策管理器 - 选股逻辑重构核心

将「每日广撒网产出大量候选」重构为「每个策略维护一个当前最强持有标的」：

1. 策略投资周期分类：short(短线) / mid(中线) / long(长线)
2. 观察期机制：标的命中策略后进入观察期（按短/中/长不同），观察期内跟踪，
   超过观察期仍不符合预期（评分低于阈值）则舍弃，重新选择
3. 历史持有持久化：记录每个策略当前持有标的及历史最强候选与评分
4. 强弱对比决策：当天新候选强度 > 当前持有标的强度 → 切换；
   否则 → 继续持有旧标的

强度口径：综合评分 score（0~100，复用 stock_score_api.calculate_stock_score）。
"""
from pathlib import Path
from datetime import datetime, timedelta
import logging
from typing import Dict, Optional, List, Callable

from utils.global_db import get_global_db
from utils.trade_date_utils import get_trading_days_between

logger = logging.getLogger(__name__)

# 策略投资周期默认分类（可被 config/strategy_params.yaml 的 investment_horizon 覆盖）
DEFAULT_HORIZON: Dict[str, str] = {
    # 短线
    'MultiPartyCannonStrategy': 'short',
    'LimitUpPullbackStrategy': 'short',
    'LimitUpSidewaysStrategy': 'short',
    'GoldenCrossNotGreenStrategy': 'short',
    'LeaderStrategy': 'short',
    'ImmortalGuidanceStrategy': 'short',
    'StrongWashWeakToStrongStrategy': 'short',
    # 中线
    'Strategy2560Selection': 'mid',
    'TrendStartStrategy': 'mid',
    'TrendAccelerationInflectionStrategy': 'mid',
    'MainUptrendDipBuyStrategy': 'mid',
    'ResistanceBreakoutStrategy': 'mid',
    'MultiGoldenCrossStrategy': 'mid',
    'GoldenTriangleStrategy': 'mid',
    # 长线
    'WBottomStrategy': 'long',
    'BottomTrendInflectionStrategy': 'long',
    'LowTD9Strategy': 'long',
    'OversoldReboundStrategy': 'long',
    'TrendResonanceReversalStrategy': 'long',
    'MorningStarStrategy': 'long',
}

# 各投资周期默认观察期（交易日）
DEFAULT_OBSERVE_DAYS: Dict[str, int] = {'short': 3, 'mid': 5, 'long': 10}

# 观察期超期后，当前评分低于该阈值视为不符合预期 → 舍弃
SCORE_ABANDON_THRESHOLD = 60.0


class StrategyHoldManager:
    """策略持有决策管理器"""

    def __init__(self, db=None):
        self.db = db or get_global_db()
        self._create_tables()
        self._config = self._load_config()

    # ==================== 配置 ====================

    def _load_config(self) -> Dict:
        """加载策略持有配置

        优先级（低→高）：
        1. 内置默认（DEFAULT_HORIZON / DEFAULT_OBSERVE_DAYS）
        2. config/strategy_params.yaml 顶层 strategy_hold 段（horizon / observe_period_days / abandon_score_threshold）
        3. 各策略段内 investment_horizon / observe_period_days 覆盖
        """
        cfg = {'horizon': dict(DEFAULT_HORIZON),
               'observe_per_horizon': dict(DEFAULT_OBSERVE_DAYS),
               'observe_per_strategy': {},
               'abandon_score_threshold': SCORE_ABANDON_THRESHOLD}
        params_file = Path(__file__).resolve().parent.parent / 'config' / 'strategy_params.yaml'
        try:
            if params_file.exists():
                with open(params_file, 'r', encoding='utf-8') as f:
                    import yaml
                    data = yaml.safe_load(f) or {}

                # 顶层集中配置段 strategy_hold
                hold = data.get('strategy_hold', {}) or {}
                for sname, v in (hold.get('horizon') or {}).items():
                    if v in ('short', 'mid', 'long'):
                        cfg['horizon'][sname] = v
                opd = hold.get('observe_period_days') or {}
                for k, v in opd.items():
                    if k in ('short', 'mid', 'long') and isinstance(v, (int, float)) and v > 0:
                        cfg['observe_per_horizon'][k] = int(v)
                    elif isinstance(v, (int, float)) and v > 0:
                        cfg['observe_per_strategy'][k] = int(v)
                thr = hold.get('abandon_score_threshold')
                if isinstance(thr, (int, float)) and thr > 0:
                    cfg['abandon_score_threshold'] = float(thr)

                # 各策略段内覆盖
                strategies = data.get('strategies', {}) or {}
                for sname, scfg in strategies.items():
                    if not isinstance(scfg, dict):
                        continue
                    hz = scfg.get('investment_horizon')
                    if hz in ('short', 'mid', 'long'):
                        cfg['horizon'][sname] = hz
                    od = scfg.get('observe_period_days')
                    if isinstance(od, (int, float)) and od > 0:
                        cfg['observe_per_strategy'][sname] = int(od)
        except Exception as e:
            logger.warning(f"加载策略持有配置失败，使用默认: {e}")
        return cfg

    def get_horizon(self, strategy_name: str) -> str:
        return self._config['horizon'].get(strategy_name, 'short')

    def get_observe_days(self, strategy_name: str) -> int:
        if strategy_name in self._config['observe_per_strategy']:
            return self._config['observe_per_strategy'][strategy_name]
        horizon = self.get_horizon(strategy_name)
        return self._config['observe_per_horizon'].get(horizon, 20)

    # ==================== 建表 ====================

    def _create_tables(self):
        sql_hold = """
            CREATE TABLE IF NOT EXISTS strategy_hold_record (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name VARCHAR(100) NOT NULL UNIQUE,
                stock_code VARCHAR(20),
                stock_name VARCHAR(50),
                hold_price DECIMAL(10,2),
                hit_date DATE,
                observe_end_date DATE,
                score DECIMAL(5,2),
                horizon VARCHAR(10),
                status VARCHAR(20) DEFAULT 'observing',
                reason TEXT,
                select_reason TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        sql_history = """
            CREATE TABLE IF NOT EXISTS strategy_hold_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name VARCHAR(100) NOT NULL,
                stock_code VARCHAR(20),
                stock_name VARCHAR(50),
                select_date DATE,
                evaluated_date DATE,
                score DECIMAL(5,2),
                outcome VARCHAR(20),
                sell_reason TEXT,
                sell_price DECIMAL(10,2),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        self.db.execute_with_retry(sql_hold)
        self.db.execute_with_retry(sql_history)
        # 迁移：老库补列（仅当列不存在，避免 duplicate column）
        try:
            cur = self.db.execute_with_retry("PRAGMA table_info(strategy_hold_record)")
            cols_hold = {r[1] for r in cur.fetchall()}
            if 'hold_price' not in cols_hold:
                self.db.execute_with_retry("ALTER TABLE strategy_hold_record ADD COLUMN hold_price DECIMAL(10,2)")
            if 'select_reason' not in cols_hold:
                self.db.execute_with_retry("ALTER TABLE strategy_hold_record ADD COLUMN select_reason TEXT")
        except Exception as e:
            logger.warning(f"策略持有表迁移(hold_price/select_reason)失败: {e}")
        try:
            cur = self.db.execute_with_retry("PRAGMA table_info(strategy_hold_history)")
            cols_hist = {r[1] for r in cur.fetchall()}
            if 'sell_reason' not in cols_hist:
                self.db.execute_with_retry("ALTER TABLE strategy_hold_history ADD COLUMN sell_reason TEXT")
            if 'select_date' not in cols_hist:
                self.db.execute_with_retry("ALTER TABLE strategy_hold_history ADD COLUMN select_date DATE")
            if 'sell_price' not in cols_hist:
                self.db.execute_with_retry("ALTER TABLE strategy_hold_history ADD COLUMN sell_price DECIMAL(10,2)")
        except Exception as e:
            logger.warning(f"策略持有表迁移(sell_reason)失败: {e}")
        logger.info("策略持有记录表初始化完成")

    # ==================== 读写持有记录 ====================

    def _get_hold(self, strategy_name: str) -> Optional[Dict]:
        rows = self.db.query(
            "SELECT * FROM strategy_hold_record WHERE strategy_name = ?",
            (strategy_name,),
        )
        return rows[0] if rows else None

    def _upsert_hold(self, strategy_name: str, stock_code: str, stock_name: str,
                     hit_date: str, observe_end_date: str, score: float,
                     horizon: str, status: str, reason: str, hold_price: float = None,
                     select_reason: str = None):
        self.db.execute_with_retry(
            """
            INSERT INTO strategy_hold_record
                (strategy_name, stock_code, stock_name, hit_date, observe_end_date, score, horizon, status, reason, hold_price, select_reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(strategy_name) DO UPDATE SET
                stock_code=excluded.stock_code, stock_name=excluded.stock_name,
                hit_date=excluded.hit_date, observe_end_date=excluded.observe_end_date,
                score=excluded.score, horizon=excluded.horizon, status=excluded.status,
                reason=excluded.reason, hold_price=excluded.hold_price,
                select_reason=excluded.select_reason, updated_at=datetime('now')
            """,
            (strategy_name, stock_code, stock_name, hit_date, observe_end_date, score, horizon, status, reason, hold_price, select_reason),
        )

    def _add_history(self, strategy_name: str, stock_code: str, stock_name: str,
                     evaluated_date: str, score: float, outcome: str, sell_reason: str = None,
                     select_date: str = None, sell_price: float = None):
        self.db.execute_with_retry(
            """
            INSERT INTO strategy_hold_history
                (strategy_name, stock_code, stock_name, select_date, evaluated_date, score, outcome, sell_reason, sell_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (strategy_name, stock_code, stock_name, select_date, evaluated_date, score, outcome, sell_reason, sell_price),
        )

    # ==================== 核心决策 ====================

    def decide(self, strategy_name: str, candidate: Optional[Dict],
               current_date: str, score_fn: Optional[Callable] = None) -> Dict:
        """对单个策略执行持有决策

        Args:
            strategy_name: 策略名称（与策略注册一致，建议英文类名）
            candidate: 当天该策略选出的最优候选 {'code','name','score'}；无候选传 None
            current_date: 决策日期 YYYY-MM-DD
            score_fn: 评分函数 callable(stock_code, date)->float，用于补算缺失评分 / 重算当前强度

        Returns:
            {'action': new|switch|hold|discard|empty,
             'recommend': {'code','name','score'} 或 None,
             'reason': str, 'horizon': str}
        """
        horizon = self.get_horizon(strategy_name)
        observe_days = self.get_observe_days(strategy_name)

        # 补齐候选评分
        if candidate and (candidate.get('score') is None):
            candidate['score'] = self._current_score(candidate['code'], current_date, score_fn)

        hold = self._get_hold(strategy_name)
        if hold and hold.get('status') == 'sold':
            # 已卖出=终止：视为无持有，本轮重新决策（新候选则重新选入=开新记录，不参与新旧比较）
            hold = None

        # ---- 无持有：首次选中 ----
        if hold is None:
            if not candidate:
                return {'action': 'empty', 'recommend': None,
                        'reason': '无候选且无持有，空仓等待', 'horizon': horizon}
            return self._start_hold(strategy_name, candidate, current_date, horizon, observe_days, '首选中')

        hold_code = hold['stock_code']
        hold_name = hold['stock_name']
        hold_score = float(hold['score'] or 0)

        # ---- 历史回扫：持有标的是否触发卖出规则（命中则打上规则，并直接用今日新候选替换）----
        sell = self._rescan_sell(hold)
        if sell and sell.get('sell_status') == '卖出':
            self.release_stock_all(hold['stock_code'], sell['sell_reason'], current_date)
            if candidate:
                return self._start_hold(strategy_name, candidate, current_date,
                                        horizon, observe_days,
                                        f'历史持有触发卖出({sell["sell_reason"]})，替换为新候选')
            return {'action': 'sold', 'recommend': None,
                    'reason': f'历史持有触发卖出({sell["sell_reason"]})，无新候选，空仓', 'horizon': horizon}

        # ---- 同一股票连续命中同一策略：视为续持（保留最早选入日 hit_date，不切换/重选/舍弃）----
        if candidate and candidate.get('code') == hold_code:
            new_score = float(candidate.get('score') or hold_score)
            _traded = get_trading_days_between(hold['hit_date'], current_date)
            _st = 'holding' if _traded >= observe_days else (hold['status'] or 'observing')
            _r = f'同一股票连续命中，续持不切换，评分更新{new_score:.0f}'
            self._update_hold_score(strategy_name, new_score, _st, _r)
            return {'action': 'hold',
                    'recommend': {'code': hold_code, 'name': hold_name, 'score': new_score},
                    'reason': _r, 'horizon': horizon}

        # ---- 旧持有多维弱转共振判定（趋势/量能/动量/相对强弱；方向没破不轻易判弱）----
        try:
            from utils.weak_signal import compute_weak_signal
            _weak = compute_weak_signal(self.db, hold_code)
        except Exception as _e:
            logger.warning(f"弱转共振判定失败 {hold_code}: {_e}")
            _weak = None
        _wlevel = (_weak or {}).get('level', 'none')
        _wsig = (_weak or {}).get('signals', []) or []
        if (_weak or {}).get('falsify'):
            _wlevel = 'none'  # 连续3日收回关键均线 → 走弱证伪，恢复正常强弱对比
        if _wlevel in ('confirmed', 'trend_weak'):
            # 命中确认/趋势性走弱（真走弱）→ 评分驱动决策（唯一走弱换股卖出点）：
            # 1) 用综合评分引擎重新计算该股票当前评分，并更新到该策略该股票的持有记录
            # 2) 若该策略本轮新候选评分 > 重算评分 → 触发卖出规则：卖出旧票、推荐新票
            # 3) 否则（无新候选 或 新候选未超越）→ 继续保留该策略选股（持有旧票），仅更新评分
            old_score_now = self._current_score(hold_code, current_date, score_fn)
            if old_score_now <= 0:
                old_score_now = hold_score  # 重算失败回退历史分
            _wsig_str = ('、'.join(_wsig)) if _wsig else _wlevel
            self._update_hold_score(strategy_name, old_score_now, 'holding',
                                    f'走弱标志命中({_wlevel}: {_wsig_str})，重算评分{old_score_now:.0f}保留')
            if candidate:
                s_new = float(candidate.get('score') or 0)
                if s_new > old_score_now:
                    self.release_stock_all(hold_code, f'走弱换股卖出({_wsig_str})', current_date)
                    self._switch(strategy_name, hold, candidate, current_date, horizon, observe_days,
                                 s_new, old_score_now,
                                 f'旧持有走弱({_wsig_str})，新候选更强({s_new:.0f}>{old_score_now:.0f})')
                    return {'action': 'switch',
                            'recommend': {'code': candidate['code'], 'name': candidate.get('name', ''),
                                          'score': s_new},
                            'reason': f'旧持有走弱({_wsig_str})，新候选更强({s_new:.0f}>{old_score_now:.0f})，卖出旧票推荐新票',
                            'horizon': horizon}
            # 无候选 或 新候选评分未超越重算评分 → 继续保留该策略选股
            return {'action': 'hold',
                    'recommend': {'code': hold_code, 'name': hold_name, 'score': old_score_now},
                    'reason': f'旧持有走弱({_wsig_str})但无更强新候选，保留该策略选股并更新评分{old_score_now:.0f}',
                    'horizon': horizon}
        elif _wlevel == 'warning':
            # 单维预警（未达确认走弱）→ 只预警观察，不触发换股卖出，继续走观察期/强弱对比
            _wsig_str = ('、'.join(_wsig)) if _wsig else '单维预警'
            self._update_hold_score(strategy_name, hold_score, 'holding',
                                    f'走弱预警({_wsig_str})，暂不换股，继续观察')

        traded_days = get_trading_days_between(hold['hit_date'], current_date)

        # ---- 观察期超期校验 ----
        if traded_days >= observe_days:
            cur_score = self._current_score(hold_code, current_date, score_fn)
            if cur_score < self._config.get('abandon_score_threshold', SCORE_ABANDON_THRESHOLD):
                # 不符合预期 → 舍弃
                self._discard(strategy_name, hold, cur_score, '观察期超期评分不达标', current_date)
                if candidate:
                    return self._start_hold(strategy_name, candidate, current_date,
                                             horizon, observe_days, '舍弃旧标的，切换新候选')
                return {'action': 'discard', 'recommend': None,
                        'reason': f'观察期超期评分{cur_score}<{self._config.get("abandon_score_threshold", SCORE_ABANDON_THRESHOLD)}，已舍弃，无新候选',
                        'horizon': horizon}
            # 达标 → 确认持有，并更新为最新强度
            hold_score = cur_score
            self._update_hold_score(strategy_name, cur_score, 'holding', '观察期达标，确认持有')

        # ---- 强弱对比 ----
        if candidate:
            s_new = float(candidate.get('score') or 0)
            if s_new > hold_score:
                # 新候选更强 → 切换
                self._switch(strategy_name, hold, candidate, current_date,
                             horizon, observe_days, s_new, hold_score)
                return {'action': 'switch', 'recommend': {'code': candidate['code'],
                                                          'name': candidate.get('name', ''),
                                                          'score': s_new},
                        'reason': f'新候选更强({s_new:.0f}>{hold_score:.0f})，切换持有', 'horizon': horizon}

        # ---- 继续持有旧标的 ----
        return {'action': 'hold', 'recommend': {'code': hold_code, 'name': hold_name, 'score': hold_score},
                'reason': f'继续持有，新候选未超越当前持有(当前{hold_score:.0f})', 'horizon': horizon}

    # ==================== 历史回扫卖出 ====================

    def _rescan_sell(self, hold: Dict) -> Optional[Dict]:
        """历史回扫：持有标的是否触发卖出规则（从命中日扫描到最新交易日）"""
        price = float(hold.get('hold_price') or 0)
        if not price:
            # 无命中价则从K线取命中日收盘价
            try:
                df = self.db.read_stock(hold['stock_code'])
                if df is not None and not df.empty:
                    import pandas as pd
                    dcol = df['date'] if 'date' in df.columns else df.index
                    hit = df[pd.to_datetime(dcol) == pd.to_datetime(hold['hit_date'])]
                    if not hit.empty:
                        price = float(hit.iloc[0]['close'])
            except Exception as e:
                logger.warning(f"历史回扫取价失败 {hold['stock_code']}: {e}")
                price = 0
        if not price:
            return None
        try:
            from utils.sell_signal import compute_sell_signal
            return compute_sell_signal(self.db, hold['stock_code'], price, hold['hit_date'])
        except Exception as e:
            logger.warning(f"历史回扫卖出信号失败 {hold['stock_code']}: {e}")
            return None

    def _mark_sold(self, strategy_name: str, hold: Dict, sell_reason: str, current_date: str) -> None:
        """记录历史持有因触发卖出规则而被替换/舍弃"""
        score = float(hold.get('score') or 0)
        self._add_history(strategy_name, hold['stock_code'], hold['stock_name'],
                          current_date, score, 'sold', sell_reason, select_date=hold['hit_date'])
        logger.info(f"策略 {strategy_name} 历史持有 {hold['stock_code']} 触发卖出: {sell_reason}")

    def release_stock_all(self, stock_code: str, reason: str, current_date: str) -> int:
        """自动卖出整体释放：该股名下所有策略的活跃持有全部置 sold 并记历史，释放后可被重新选股"""
        rows = self.db.query(
            "SELECT * FROM strategy_hold_record WHERE stock_code=? AND status IN ('observing','holding')",
            (stock_code,)) or []
        for h in rows:
            try:
                self._mark_sold(h['strategy_name'], h, reason, current_date)
                self.db.execute_with_retry(
                    "UPDATE strategy_hold_record SET status='sold', reason=?, updated_at=datetime('now') WHERE strategy_name=? AND stock_code=?",
                    (f'触发卖出: {reason}', h['strategy_name'], stock_code))
            except Exception as e:
                logger.warning(f"整体释放卖出失败 {h.get('strategy_name')}/{stock_code}: {e}")
        return len(rows)

    def _manual_sell(self, strategy_name: str, hold: Dict, sell_date: str, sell_price: float = None) -> None:
        """人工标记卖出：写 history(sold) + 将持有置为 sold（终止锁定，后续不再自动更新）"""
        try:
            self._add_history(
                strategy_name, hold.get('stock_code', ''), hold.get('stock_name', ''),
                sell_date or (hold.get('hit_date') or ''), float(hold.get('score') or 0),
                'sold', '人工标记卖出', select_date=hold.get('hit_date'), sell_price=sell_price,
            )
            self.db.execute_with_retry(
                "UPDATE strategy_hold_record SET status='sold', reason=?, updated_at=datetime('now') WHERE strategy_name=?",
                (f"人工标记卖出（{sell_date or ''}）", strategy_name),
            )
            logger.info(f"策略 {strategy_name} 人工标记卖出 {hold.get('stock_code')}（日期={sell_date}, 价格={sell_price}）")
        except Exception as e:
            logger.warning(f"人工标记卖出失败 {strategy_name}/{hold.get('stock_code')}: {e}")

    # ==================== 内部动作 ====================

    def _current_score(self, stock_code: str, current_date: str, score_fn: Optional[Callable]) -> float:
        if score_fn is None:
            return 0.0
        try:
            return float(score_fn(stock_code, current_date) or 0)
        except Exception as e:
            logger.warning(f"重算评分失败 {stock_code}@{current_date}: {e}")
            return 0.0

    def _start_hold(self, strategy_name: str, candidate: Dict, current_date: str,
                    horizon: str, observe_days: int, reason: str) -> Dict:
        code = candidate['code']
        name = candidate.get('name', '')
        score = float(candidate.get('score') or 0)
        price = candidate.get('price')
        observe_end = self._add_trading_days(current_date, observe_days)
        self._upsert_hold(strategy_name, code, name, current_date, observe_end,
                          score, horizon, 'observing', reason, price,
                          candidate.get('select_reason') or '')
        self._add_history(strategy_name, code, name, current_date, score, 'selected', select_date=current_date)
        logger.info(f"策略 {strategy_name} 首选中持有 {code} {name}，观察期 {observe_days} 交易日")
        return {'action': 'new', 'recommend': {'code': code, 'name': name, 'score': score},
                'reason': reason, 'horizon': horizon}

    def _switch(self, strategy_name: str, old_hold: Dict, candidate: Dict, current_date: str,
                horizon: str, observe_days: int, s_new: float, s_hold: float,
                reason: str = None) -> None:
        # 记录旧标的被切换
        self._add_history(strategy_name, old_hold['stock_code'], old_hold['stock_name'],
                          current_date, s_hold, 'replaced', select_date=old_hold['hit_date'])
        code = candidate['code']
        name = candidate.get('name', '')
        price = candidate.get('price')
        observe_end = self._add_trading_days(current_date, observe_days)
        _reason = reason or f'新候选更强，切换持有({s_new:.0f}>{s_hold:.0f})'
        self._upsert_hold(strategy_name, code, name, current_date, observe_end,
                          s_new, horizon, 'observing', _reason, price,
                          candidate.get('select_reason') or '')
        self._add_history(strategy_name, code, name, current_date, s_new, 'selected', select_date=current_date)
        logger.info(f"策略 {strategy_name} 切换持有 {old_hold['stock_code']} -> {code}")

    def _discard(self, strategy_name: str, hold: Dict, cur_score: float, reason: str, current_date: str) -> None:
        self._add_history(strategy_name, hold['stock_code'], hold['stock_name'],
                          current_date, cur_score, 'discarded', select_date=hold['hit_date'])
        self.db.execute_with_retry(
            "UPDATE strategy_hold_record SET status='discarded', reason=?, updated_at=datetime('now') WHERE strategy_name=?",
            (reason, strategy_name),
        )
        logger.info(f"策略 {strategy_name} 舍弃持有 {hold['stock_code']}: {reason}")

    def _update_hold_score(self, strategy_name: str, score: float, status: str, reason: str) -> None:
        self.db.execute_with_retry(
            "UPDATE strategy_hold_record SET score=?, status=?, reason=?, updated_at=datetime('now') WHERE strategy_name=?",
            (score, status, reason, strategy_name),
        )

    @staticmethod
    def _add_trading_days(start_date: str, days: int) -> str:
        """从 start_date 向后推 days 个交易日（含当天），返回截止日期"""
        from utils.trade_date_utils import get_trading_days
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
        except Exception:
            return start_date
        # 从当天起向后扫 days 个工作日作为近似交易日（简化，周末跳过）
        d = start
        count = 0
        while count < days:
            d = datetime(d.year, d.month, d.day) + timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return d.strftime('%Y-%m-%d')

    # ==================== 查询 ====================

    def get_all_holds(self) -> List[Dict]:
        return list(self.db.query(
            "SELECT * FROM strategy_hold_record WHERE status IN ('observing','holding') "
            "ORDER BY horizon, strategy_name"
        ))

    def get_hold(self, strategy_name: str) -> Optional[Dict]:
        return self._get_hold(strategy_name)

    def get_history(self, strategy_name: str = None, limit: int = 100) -> List[Dict]:
        if strategy_name:
            return list(self.db.query(
                "SELECT * FROM strategy_hold_history WHERE strategy_name = ? ORDER BY evaluated_date DESC LIMIT ?",
                (strategy_name, limit),
            ))
        return list(self.db.query(
            "SELECT * FROM strategy_hold_history ORDER BY evaluated_date DESC LIMIT ?", (limit,)
        ))
