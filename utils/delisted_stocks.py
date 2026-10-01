# -*- coding: utf-8 -*-
"""
退市/暂停上市股票黑名单加载工具

黑名单来源：Tushare `stock_basic` 接口的 list_status 字段
  - 'D'：退市
  - 'P'：暂停上市
数据文件：config/delisted_stocks.json（可自动从 Tushare 刷新）

关键设计（顺序理清 + 杜绝空跑）：
  1. 调用方（如数据同步 get_all_stock_codes）在获取股票清单前，先确保退市股黑名单是"最新"的，
     再拉股票清单并用黑名单过滤 —— 顺序是：先黑名单、后清单。
  2. 若黑名单文件缺失 / 为空 / 超过 max_age_days 未更新，本工具会主动从 Tushare 拉取并写回，
     不会返回空集假装过滤成功（避免"空跑"导致退市股漏进股票池）。
  3. Tushare 拉取失败时回退使用现有文件（即使过期也至少保留旧名单）；文件也不存在时才告警。
"""
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DELISTED: set = None          # 进程内缓存
_DELISTED_LOADED_AT: float = 0.0  # 缓存加载时间戳

_DELISTED_FILE = Path(__file__).resolve().parent.parent / 'config' / 'delisted_stocks.json'

_DEFAULT_MAX_AGE_DAYS = 1  # 默认：超过 1 天未更新则主动刷新（Tushare stock_basic 每日更新退市状态）


def _fetch_from_tushare():
    """从 Tushare 拉取退市/暂停上市股票代码，返回集合；失败返回 None。

    拉取失败不抛异常，由调用方决定回退策略。
    """
    try:
        from utils.tushare_client import get_pro
        pro = get_pro()
        codes = set()
        for status in ('D', 'P'):
            try:
                df = pro.stock_basic(exchange='', list_status=status, fields='ts_code')
                if df is not None and not df.empty:
                    codes |= {str(x).split('.')[0] for x in df['ts_code']}
            except Exception:
                logger.debug(f"从 Tushare 拉取 list_status={status} 失败")
        return codes if codes else None
    except Exception as e:
        logger.warning(f"从 Tushare 拉取退市股黑名单失败: {e}")
        return None


def _read_file():
    """读取黑名单文件，返回集合；文件缺失/损坏返回 None"""
    try:
        if _DELISTED_FILE.exists():
            with open(_DELISTED_FILE, encoding='utf-8') as f:
                data = json.load(f)
            if data:
                return set(data)
    except Exception:
        pass
    return None


def _file_is_fresh(max_age_days):
    """文件是否存在且未超过 max_age_days 未更新"""
    try:
        if _DELISTED_FILE.exists():
            mtime = _DELISTED_FILE.stat().st_mtime
            return (time.time() - mtime) < max_age_days * 86400
    except Exception:
        pass
    return False


def refresh_delisted_stocks() -> set:
    """强制从 Tushare 拉取退市/暂停股并写回文件，返回最新黑名单。

    拉取成功则更新文件并返回新集合；拉取失败则回退现有文件并返回（可能为空）。
    """
    global _DELISTED, _DELISTED_LOADED_AT
    codes = _fetch_from_tushare()
    if codes is not None:
        try:
            with open(_DELISTED_FILE, 'w', encoding='utf-8') as f:
                json.dump(sorted(codes), f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning(f"退市股黑名单写文件失败: {e}")
        _DELISTED = codes
        _DELISTED_LOADED_AT = time.time()
        logger.info(f"退市股黑名单已从 Tushare 刷新: {len(codes)} 只")
        return codes
    # 拉取失败：回退现有文件
    fallback = _read_file()
    _DELISTED = fallback if fallback is not None else set()
    _DELISTED_LOADED_AT = time.time()
    if not _DELISTED:
        logger.warning("退市股黑名单刷新失败：Tushare 不可用且无本地文件，退市股过滤不可用")
    return _DELISTED


def load_delisted_stocks(max_age_days: int = _DEFAULT_MAX_AGE_DAYS) -> set:
    """加载退市/暂停上市股黑名单，保证尽可能最新且非空。

    顺序：
      1) 内存缓存新鲜（未超 max_age_days）→ 直接返回
      2) 文件新鲜 → 读文件
      3) 文件缺失 / 为空 / 过期 → 主动从 Tushare 刷新（写回文件）
      4) Tushare 失败 → 回退现有文件（即使过期）
      5) 文件也没有 → 告警并返回空集（明确告知过滤不可用，而非静默）

    Args:
        max_age_days: 超过该天数未更新则主动刷新，默认 1 天
    """
    global _DELISTED, _DELISTED_LOADED_AT
    now = time.time()

    # 1) 内存缓存新鲜
    if _DELISTED is not None and (now - _DELISTED_LOADED_AT) < max_age_days * 86400:
        return _DELISTED

    # 2) 文件新鲜
    if _file_is_fresh(max_age_days):
        data = _read_file()
        if data is not None:
            _DELISTED = data
            _DELISTED_LOADED_AT = now
            return _DELISTED

    # 3) 文件缺失/为空/过期 -> 主动刷新（避免空跑）
    logger.info(f"退市股黑名单需更新（缺失/过期），从 Tushare 拉取...")
    codes = _fetch_from_tushare()
    if codes is not None:
        try:
            with open(_DELISTED_FILE, 'w', encoding='utf-8') as f:
                json.dump(sorted(codes), f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning(f"退市股黑名单写文件失败: {e}")
        _DELISTED = codes
        _DELISTED_LOADED_AT = now
        logger.info(f"退市股黑名单已更新: {len(codes)} 只")
        return codes

    # 4) Tushare 失败 -> 回退现有文件
    fallback = _read_file()
    if fallback is not None:
        _DELISTED = fallback
        _DELISTED_LOADED_AT = now
        logger.warning(f"Tushare 拉取退市股失败，回退使用本地黑名单 {len(fallback)} 只")
        return _DELISTED

    # 5) 文件也没有 -> 明确告警
    _DELISTED = set()
    _DELISTED_LOADED_AT = now
    logger.warning("退市股黑名单不可用：本地无文件且 Tushare 拉取失败，退市股过滤未生效")
    return _DELISTED


def is_delisted(code: str) -> bool:
    """判断某只股票是否在退市/暂停上市黑名单中"""
    return code in load_delisted_stocks()
