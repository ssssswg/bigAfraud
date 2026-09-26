# -*- coding: utf-8 -*-
"""
Tushare 事件数据入库器
======================
将 9 类事件接口（均支持按公告日/交易日单日全市场取数）批量拉取并写入 stock_event 表，
供 event_scorer 评分读库优先使用。

事件接口（Tushare 官方规范）：
  - forecast      业绩预告   (ann_date)
  - express       业绩快报   (ann_date)
  - repurchase    股票回购   (ann_date)
  - stk_holdernumber 股东人数 (ann_date)
  - stk_holdertrade 股东增减持 (ann_date)
  - share_float   限售股解禁 (ann_date)
  - stk_seasoned  股票增发   (ann_date)
  - top_list      龙虎榜每日明细 (trade_date)
  - top_inst      龙虎榜机构明细 (trade_date)

时效保证：
  - 按日期入库，评分按 stock_code + 有效期窗口查询；
  - 增量更新只拉最近一段交易日，数据始终为 Tushare 当日最新。
"""
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class EventDataFetcher:
    """事件数据入库器（统一写 stock_event）"""

    # event_type -> (接口名, 日期参数, 需要字段)
    APIS: Dict[str, tuple] = {
        '业绩预告': ('forecast', 'ann_date',
                  'ts_code,ann_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max'),
        '业绩快报': ('express', 'ann_date',
                  'ts_code,ann_date,revenue,n_income,yoy_net_profit,diluted_eps'),
        '股票回购': ('repurchase', 'ann_date',
                  'ts_code,ann_date,proc,amount,vol'),
        '股东人数': ('stk_holdernumber', 'ann_date',
                  'ts_code,ann_date,holder_num'),
        '股东增减持': ('stk_holdertrade', 'ann_date',
                    'ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,avg_price'),
        '限售股解禁': ('share_float', 'ann_date',
                   'ts_code,ann_date,float_date,float_share,float_ratio,holder_name'),
        '股票增发': ('stk_seasoned', 'ann_date',
                  'ts_code,ann_date,fo_type,cur_stage,fo_raise_total'),
        '龙虎榜': ('top_list', 'trade_date',
                'trade_date,ts_code,name,close,pct_change,net_amount,amount,reason'),
        '龙虎榜机构': ('top_inst', 'trade_date',
                   'trade_date,ts_code,exalter,side,buy,sell,net_buy,reason'),
    }

    # 已知镜像不支持的接口（已探测确认），默认禁用，避免每次初始化重复探测并刷警告；
    # 若镜像后续支持，从该集合移除对应接口名即可自动恢复
    KNOWN_UNAVAILABLE: set = {'stk_seasoned'}

    def __init__(self, db_manager):
        self.db_manager = db_manager
        self._pro = None
        self.stats = {'added': 0, 'updated': 0, 'failed': 0}
        # 探测确认不可用的接口（如镜像不支持的 stk_seasoned），避免每次报错
        # 初始即禁用已知不可用接口；运行时探测到其他不可用接口再动态加入
        self._disabled = set(self.KNOWN_UNAVAILABLE)

    def _get_pro(self):
        if self._pro is None:
            from utils.tushare_client import get_pro
            self._pro = get_pro()
        return self._pro

    @staticmethod
    def _to_6digit(ts_code: str) -> str:
        """'000001.SZ' -> '000001'"""
        return str(ts_code).split('.')[0] if ts_code else ''

    def _save_events(self, df, event_type: str, date_param: str):
        """将某接口返回的全市场事件写入 stock_event"""
        if df is None or df.empty:
            return
        rows = []
        now = json.dumps({'ts': 'now'})
        for _, r in df.iterrows():
            ts_code = str(r.get('ts_code', ''))
            stock_code = self._to_6digit(ts_code)
            if not stock_code:
                continue
            event_date = str(r.get(date_param, ''))
            # 事件标题
            title = event_type
            # 事件内容：保留关键字段（转 JSON）
            content = json.dumps({k: str(v) if v is not None else ''
                                  for k, v in r.items() if k != date_param},
                                 ensure_ascii=False)
            rows.append((stock_code, event_type, event_date, title, content, 'tushare', ''))
        if rows:
            # stock_event 无唯一索引，按 (event_type, event_date) 先删后插，保证幂等
            del_date = str(rows[0][2])
            sql = """
            INSERT OR REPLACE INTO stock_event
            (stock_code, event_type, event_date, event_title, event_content, event_source, event_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            try:
                self.db_manager.execute(
                    "DELETE FROM stock_event WHERE event_type=? AND event_date=?",
                    (event_type, del_date))
                cursor = None
                for row in rows:
                    cursor = self.db_manager.execute(sql, row)
                if cursor is not None:
                    cursor.connection.commit()
                self.stats['added'] += len(rows)
                logger.debug(f"写入 {event_type} {len(rows)} 条事件")
            except Exception as e:
                logger.error(f"写入 {event_type} 事件失败: {e}")
                self.stats['failed'] += len(rows)

    def fetch_and_save(self, date: str):
        """拉取某交易日/公告日的全市场事件并入库存"""
        pro = self._get_pro()
        if pro is None:
            logger.warning("Tushare 未就绪，跳过事件入库")
            return
        for event_type, (api, date_param, fields) in self.APIS.items():
            if api in self._disabled:
                continue
            try:
                fn = getattr(pro, api)
                df = fn(**{date_param: date}, fields=fields)
                self._save_events(df, event_type, date_param)
            except Exception as e:
                # 接口不存在/不支持（如镜像缺 stk_seasoned）：标记禁用，只报一次
                msg = str(e)
                if ('不存在' in msg or 'NoneType' in msg or 'has no attribute' in msg
                        or 'AttributeError' in msg):
                    self._disabled.add(api)
                    logger.warning(f"事件接口 {api} 不可用，已禁用: {msg}")
                else:
                    logger.warning(f"事件接口 {api}({date}) 失败: {msg}")
                    self.stats['failed'] += 1

    def fetch_range(self, start_date: str, end_date: str):
        """按日期范围批量入库事件（start_date/end_date 为 YYYYMMDD）"""
        from utils.trade_date_utils import get_trading_days
        days = get_trading_days(start_date, end_date)  # YYYY-MM-DD
        logger.info(f"事件入库：{len(days)} 个交易日 ({start_date}~{end_date})")
        for d in days:
            self.fetch_and_save(d.replace('-', ''))
        logger.info(f"事件入库完成: 新增 {self.stats['added']} 条, 失败 {self.stats['failed']} 条")
        return dict(self.stats)
