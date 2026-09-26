# -*- coding: utf-8 -*-
"""
Tushare 批量缓存层
==================
减少回测/评分中的重复 Tushare 请求。

时效保证（对应 Tushare 每日更新一次的更新频率）：
- 以 trade_date 为缓存维度：同一交易日的全市场数据只在首次拉取，之后命中缓存；
  跨日时 trade_date 变化 -> 缓存 key 变化 -> 自动重新拉取，因此当日数据始终是
  Tushare 当日最新值。
- 低频/不定期更新的接口（财务指标、业绩预告、回购等）使用进程内 TTL 缓存，
  过期即重新拉取，避免拿过期数据。

线程安全：所有操作受全局 RLock 保护，支持并发评分。
"""
import threading
from datetime import datetime

__all__ = ['DailyBulkCache', 'daily_bulk', 'proc_cache', 'clear_cache']

_lock = threading.RLock()
_daily_store = {}   # {api_name: {trade_date: DataFrame}}
_fetch_time = {}    # {(api_name, trade_date): datetime}
_proc_store = {}    # {key: (value, expire_at)}


class DailyBulkCache:
    """
    按交易日缓存某接口的全市场数据（每日更新接口专用）。
    用法:
        df = daily_bulk.get(pro, 'top_list', trade_date='20260924',
                            fields='ts_code,trade_date,name,buy,sell,net_buy')
    同一 trade_date 首次调用会拉取全市场并缓存，后续调用直接命中。
    """

    def get(self, pro, api, trade_date, force=False, **kwargs):
        """取某接口某交易日的全市场数据。kwargs 透传给接口（如 fields）。"""
        trade_date = str(trade_date)
        with _lock:
            bucket = _daily_store.setdefault(api, {})
            if not force and trade_date in bucket:
                return bucket[trade_date]
            fn = getattr(pro, api)
            df = fn(trade_date=trade_date, **kwargs)
            # 仅缓存非空数据：若当日 Tushare 尚无数据（如盘前），不缓存，下次重拉以保证最新
            if df is not None and not df.empty:
                bucket[trade_date] = df
                _fetch_time[(api, trade_date)] = datetime.now()
            return df

    def invalidate(self, api, trade_date=None):
        """强制使某接口（或某接口某日）缓存失效。"""
        with _lock:
            if trade_date is not None:
                _daily_store.get(api, {}).pop(str(trade_date), None)
                _fetch_time.pop((api, str(trade_date)), None)
            else:
                _daily_store.pop(api, None)
                for k in list(_fetch_time):
                    if k[0] == api:
                        _fetch_time.pop(k, None)

    def __contains__(self, api):
        return api in _daily_store


def proc_cache(key, ttl_seconds, fetch_fn, force=False):
    """
    进程内通用 TTL 缓存（低频/不定期更新接口用）。
    :param key: 规范化缓存键（如 ('fina_indicator', '000001.SZ')）
    :param ttl_seconds: 有效秒数，过期后重新调用 fetch_fn
    :param fetch_fn: 无参可调用，返回缓存数据
    """
    with _lock:
        now = datetime.now()
        hit = _proc_store.get(key)
        if not force and hit is not None and hit[1] > now:
            return hit[0]
    value = fetch_fn()
    with _lock:
        _proc_store[key] = (value, datetime.now() + __import__('datetime').timedelta(seconds=ttl_seconds))
    return value


def clear_cache():
    """清空全部缓存（进程重启用）。"""
    with _lock:
        _daily_store.clear()
        _fetch_time.clear()
        _proc_store.clear()


daily_bulk = DailyBulkCache()
