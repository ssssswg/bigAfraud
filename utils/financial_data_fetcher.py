# -*- coding: utf-8 -*-
"""
Tushare 财务数据入库器
======================
按报告期（period）拉取全市场财务指标，写入 stock_financial 表，供 fundamental_scorer 评分读库优先使用。

接口：fina_indicator（doc_id 31，财务指标）
  - 支持按 period（报告期）批量返回全市场，如 period='20260331'
  - 评分所需字段：roe（净资产收益率）、netprofit_yoy（净利润同比）、ocf_to_opincome（经营现金流/营业收入）、eps、ocfps

时效：财务指标按报告期发布，每日更新一次；评分取该股最新一期（按 ann_date 降序），数据始终为已发布最新。
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class FinancialDataFetcher:
    """财务数据入库器（按报告期全市场，写 stock_financial）"""

    TABLE = 'stock_financial'

    def __init__(self, db_manager):
        self.db_manager = db_manager
        self._pro = None
        self.stats = {'added': 0, 'updated': 0, 'failed': 0}
        self._ensure_table()

    def _ensure_table(self):
        sql = """
        CREATE TABLE IF NOT EXISTS stock_financial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            ann_date TEXT,
            end_date TEXT,
            roe REAL,
            netprofit_yoy REAL,
            ocfps REAL,
            eps REAL,
            ocf_to_opincome REAL
        )
        """
        cur = self.db_manager.execute(sql)
        try:
            cur.connection.commit()
        except Exception:
            pass

    def _get_pro(self):
        if self._pro is None:
            from utils.tushare_client import get_pro
            self._pro = get_pro()
        return self._pro

    @staticmethod
    def _to_6digit(ts_code: str) -> str:
        return str(ts_code).split('.')[0] if ts_code else ''

    def recent_periods(self, n: int = 8) -> List[str]:
        """返回最近 n 个报告期（季度末 YYYYMMDD）"""
        months = [12, 9, 6, 3]
        now = datetime.now()
        q = (now.month - 1) // 3 + 1
        yy, mm = now.year, months[q - 1]
        periods = []
        while len(periods) < n:
            periods.append("%d%02d31" % (yy, mm))
            if mm == 3:
                mm, yy = 12, yy - 1
            else:
                mm = months[months.index(mm) - 1]
        return periods

    def _save_period(self, df, period: str):
        """将某报告期全市场财务写入 stock_financial（先删后插幂等）"""
        if df is None or df.empty:
            return 0
        sql = """
        INSERT OR REPLACE INTO stock_financial
        (stock_code, ann_date, end_date, roe, netprofit_yoy, ocfps, eps, ocf_to_opincome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            self.db_manager.execute(
                "DELETE FROM stock_financial WHERE end_date=?",
                (period,))
            rows = []
            for _, r in df.iterrows():
                code = self._to_6digit(r.get('ts_code'))
                if not code:
                    continue
                rows.append((
                    code,
                    str(r.get('ann_date') or ''),
                    str(r.get('end_date') or period),
                    self._f(r.get('roe')), self._f(r.get('netprofit_yoy')),
                    self._f(r.get('ocfps')), self._f(r.get('eps')),
                    self._f(r.get('ocf_to_opincome')),
                ))
            cur = None
            for row in rows:
                cur = self.db_manager.execute(sql, row)
            if cur is not None:
                cur.connection.commit()
            self.stats['added'] += len(rows)
            logger.info(f"财务入库 {period}: {len(rows)} 条")
            return len(rows)
        except Exception as e:
            logger.error(f"财务入库 {period} 失败: {e}")
            self.stats['failed'] += 1
            return 0

    @staticmethod
    def _f(v):
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def fetch_recent(self, n_periods: int = 8) -> Dict:
        """拉最近 n 期全市场财务并入库存"""
        pro = self._get_pro()
        if pro is None:
            return dict(self.stats)
        periods = self.recent_periods(n_periods)
        logger.info(f"财务入库：{len(periods)} 个报告期 {periods[0]}~{periods[-1]}")
        for p in periods:
            try:
                df = pro.fina_indicator(
                    period=p,
                    fields="ts_code,ann_date,end_date,roe,netprofit_yoy,ocfps,eps,ocf_to_opincome",
                )
                self._save_period(df, p)
            except Exception as e:
                logger.warning(f"财务接口 fina_indicator({p}) 失败: {e}")
                self.stats['failed'] += 1
        logger.info(f"财务入库完成: 新增 {self.stats['added']} 条, 失败 {self.stats['failed']} 条")
        return dict(self.stats)

    def fetch_all_stocks(self) -> Dict:
        """逐股拉全市场财务并入库存（镜像 fina_indicator 仅支持 ts_code，无法按报告期批量）"""
        try:
            rows = self.db_manager.query("SELECT DISTINCT code FROM stock_basic")
        except Exception as e:
            logger.error(f"读取股票列表失败: {e}")
            return dict(self.stats)
        codes = [r.get('code') for r in (rows or []) if r.get('code')]
        logger.info(f"财务入库：全市场 {len(codes)} 只股票逐股拉取（镜像仅支持 ts_code）")
        for code in codes:
            self.fetch_for_stock(str(code))
        logger.info(f"财务入库完成: 新增 {self.stats['added']} 条, 失败 {self.stats['failed']} 条")
        return dict(self.stats)

    def fetch_for_stock(self, stock_code: str, limit: int = 8) -> int:
        """按单股拉财务入库（增量/新股票用）"""
        pro = self._get_pro()
        if pro is None:
            return 0
        try:
            df = pro.fina_indicator(
                ts_code=self._convert(stock_code),
                fields="ts_code,ann_date,end_date,roe,netprofit_yoy,ocfps,eps,ocf_to_opincome",
            )
            if df is None or df.empty:
                return 0
            cnt = 0
            for _, r in df.head(limit).iterrows():
                code = self._to_6digit(r.get('ts_code'))
                sql = ("INSERT OR REPLACE INTO stock_financial "
                       "(stock_code, ann_date, end_date, roe, netprofit_yoy, ocfps, eps, ocf_to_opincome) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
                cur = self.db_manager.execute(sql, (
                    code, str(r.get('ann_date') or ''), str(r.get('end_date') or ''),
                    self._f(r.get('roe')), self._f(r.get('netprofit_yoy')),
                    self._f(r.get('ocfps')), self._f(r.get('eps')),
                    self._f(r.get('ocf_to_opincome')),
                ))
                cnt += 1
            if cnt:
                cur.connection.commit()
            return cnt
        except Exception as e:
            logger.warning(f"单股财务入库失败 {stock_code}: {e}")
            return 0

    @staticmethod
    def _convert(code: str) -> str:
        return "%s.%s" % (code, 'SH' if code.startswith(('6', '9')) else 'SZ')
