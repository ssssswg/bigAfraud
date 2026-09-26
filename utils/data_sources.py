"""
数据源实现模块

集中管理 bigApush 的所有数据源，每个数据源实现 DataFetcher（utils/data_fetcher.py）
所需的统一接口：

    fetch_stock_basic()                                  -> Optional[List[Dict]]
    fetch_industry_data()                                -> Optional[List[Dict]]
    fetch_fund_flow(stock_code)                          -> Optional[Dict]
    fetch_stock_history(stock_code, years=1)             -> Optional[Any]
    fetch_stock_events(stock_code)                       -> Optional[List[Dict]]
    is_available()                                       -> bool

当前数据源：
    - tushare_pro : Tushare Pro（需 token，配置于 config/tushare_config.json）
    - tencent     : 腾讯财经免费 HTTP 接口
    - eastmoney   : 东方财富（复用 utils/eastmoney_fetcher 的 Cookie/代理通道）
    - baostock    : 免费开源行情库（实现参考 Sequoia-X/sequoia_x/data/engine.py）

说明：baostock 免费且无需 token，但只提供行情/K线/股票列表/行业数据，
资金流向与事件数据其接口不提供，相应方法返回 None，交由 fallback 机制落到其他数据源。

东财相关接口（历史K线/资金流/行业）统一走 utils/eastmoney_fetcher.py 的请求通道：
该通道带多 Cookie 轮换 + 代理，可缓解东财行情接口（push2his/push2）的反爬断连，
避免直接依赖 akshare 默认请求头被断开。
"""

from utils.tushare_client import get_pro
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# 复用项目内 eastmoney_fetcher 的 Cookie/代理/多Cookie轮换通道
from utils.eastmoney_fetcher import fetcher as _em_fetcher

# 配置日志
logger = logging.getLogger(__name__)

# 东财接口各域的 ut 参数（与 akshare 中使用的固定值一致）
_EM_UT_KLINER = "7eea3edcaed734bea9cbfc24409ed989"
_EM_UT_FFLOW = "b2884a393a59ad64002292a3e90d46a5"
_EM_UT_CLIST = "bd1d9ddb04089700cf9c27f6f7426281"


def _to_baostock_code(symbol: str) -> str:
    """将纯数字股票代码转为 baostock 格式：6/9 开头 -> sh，其余 -> sz。"""
    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
    return f"{prefix}.{symbol}"


def _em_market(code: str) -> str:
    """东财 secid 市场号：沪市=1，深市/北交所=0。"""
    return "1" if code.startswith("6") else "0"


def _em_fetch_history(code: str, years: int = 1) -> Optional[pd.DataFrame]:
    """东财历史日K线（前复权），复用 eastmoney_fetcher 的 Cookie/代理通道。"""
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": _EM_UT_KLINER,
            "klt": "101",       # 日K
            "fqt": "1",         # 前复权
            "secid": f"{_em_market(code)}.{code}",
            "beg": start,
            "end": end,
            "lmt": "1000000",
        }
        resp = _em_fetcher.make_request(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params=params,
            timeout=15,
        )
        data = resp.json().get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            return None
        rows = []
        for line in klines:
            p = line.split(",")
            if len(p) < 11:
                continue
            rows.append({
                "date": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "volume": float(p[5]),
                "turnover": float(p[6]),
            })
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        return df
    except Exception as e:
        logger.warning(f"东财(em通道)获取 {code} 历史行情失败: {e}")
        return None


def _em_fetch_fund_flow(code: str) -> Optional[Dict]:
    """东财个股资金流向（当日），复用 eastmoney_fetcher 的 Cookie/代理通道。"""
    try:
        params = {
            "lmt": "0",
            "klt": "101",
            "secid": f"{_em_market(code)}.{code}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": _EM_UT_FFLOW,
        }
        resp = _em_fetcher.make_request(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params=params,
            timeout=15,
        )
        data = resp.json().get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            return None
        p = klines[0].split(",")
        # p = [日期, 主力净流入-净额, 小单净流入, 中单净流入, 大单净流入, 超大单净流入, 主力净占比, ...]
        main_inflow = float(p[1]) if len(p) > 1 else 0.0
        return {
            "code": code,
            "date": p[0],
            "main_inflow": main_inflow,
            "large_inflow": float(p[4]) if len(p) > 4 else 0.0,
            "super_large_inflow": float(p[5]) if len(p) > 5 else 0.0,
            "source": "eastmoney",
        }
    except Exception as e:
        logger.warning(f"东财(em通道)获取 {code} 资金流向失败: {e}")
        return None


def _em_fetch_industry() -> Optional[List[Dict]]:
    """东财行业板块列表，复用 eastmoney_fetcher 的 Cookie/代理通道。"""
    try:
        params = {
            "pn": "1",
            "pz": "200",
            "po": "1",
            "np": "1",
            "ut": _EM_UT_CLIST,
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90 t:2 f:!50",
            "fields": "f12,f14,f104,f105",  # 板块代码, 板块名称, 上涨家数, 下跌家数
        }
        resp = _em_fetcher.make_request(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params=params,
            timeout=15,
        )
        data = resp.json().get("data") or {}
        diff = data.get("diff") or []
        out = []
        for item in diff:
            name = str(item.get("f14", ""))
            if not name:
                continue
            up = int(item.get("f104") or 0)
            down = int(item.get("f105") or 0)
            # 成分股数量近似 = 上涨家数 + 下跌家数（不含平盘）
            out.append({"industry": name, "count": up + down})
        return out or None
    except Exception as e:
        logger.warning(f"东财(em通道)获取行业数据失败: {e}")
        return None


class DataSource(ABC):
    """数据源基类 - 定义统一的数据采集接口"""

    def __init__(self, name: str, priority: int = 0):
        self.name = name
        self.priority = priority

    def is_available(self) -> bool:
        """数据源是否可用（默认可用）"""
        return True

    @abstractmethod
    def fetch_stock_basic(self) -> Optional[List[Dict]]:
        """获取全市场股票基本信息（每项含 code/name/industry/market 等）"""

    @abstractmethod
    def fetch_industry_data(self) -> Optional[List[Dict]]:
        """获取行业数据"""

    @abstractmethod
    def fetch_fund_flow(self, stock_code: str) -> Optional[Dict]:
        """获取个股资金流向"""

    @abstractmethod
    def fetch_stock_history(self, stock_code: str, years: int = 1) -> Optional[Any]:
        """获取个股历史行情（DataFrame 或列表，需支持 len()）"""

    @abstractmethod
    def fetch_stock_events(self, stock_code: str) -> Optional[List[Dict]]:
        """获取个股事件信息"""


# ============================================================
# Tushare Pro
# ============================================================

class TushareProDataSource(DataSource):
    """Tushare Pro 数据源（需 token，配置于 config/tushare_config.json）"""

    def __init__(self):
        super().__init__("tushare_pro", priority=1)

    # -- 私有工具 --------------------------------------------------

    def _get_pro(self):
        """创建 Tushare Pro API 实例；无 token 返回 None"""
        try:
            import tushare as ts

            config_path = Path("config/tushare_config.json")
            if not config_path.exists():
                logger.warning("tushare_config.json 不存在，Tushare 数据源不可用")
                return None
            with open(config_path, "r", encoding="utf-8") as f:
                import json
                tushare_config = json.load(f)
            token = tushare_config.get("token") or tushare_config.get("api_key")
            if not token:
                logger.warning("未找到 Tushare token 配置，Tushare 数据源不可用")
                return None
            return get_pro(token)
        except Exception as e:
            logger.warning(f"Tushare API 初始化失败: {e}")
            return None

    def _to_ts_code(self, stock_code: str) -> str:
        """6位数字代码转 Tushare 格式（.SH/.SZ）"""
        return f"{stock_code}.SH" if stock_code.startswith("6") else f"{stock_code}.SZ"

    def is_available(self) -> bool:
        return self._get_pro() is not None

    # -- 接口实现 --------------------------------------------------

    def fetch_stock_basic(self) -> Optional[List[Dict]]:
        pro = self._get_pro()
        if pro is None:
            return None
        try:
            df = pro.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code,name,industry,area,market",
            )
            if df is None or df.empty:
                return None
            return [
                {
                    "code": row["ts_code"].split(".")[0],
                    "name": row.get("name", ""),
                    "industry": row.get("industry", ""),
                    "area": row.get("area", ""),
                    "market": row.get("market", ""),
                }
                for _, row in df.iterrows()
            ]
        except Exception as e:
            logger.warning(f"Tushare 获取股票列表失败: {e}")
            return None

    def fetch_industry_data(self) -> Optional[List[Dict]]:
        pro = self._get_pro()
        if pro is None:
            return None
        try:
            df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,industry")
            if df is None or df.empty:
                return None
            # 按行业聚合统计
            grouped = df.groupby("industry").size().reset_index(name="count")
            return [
                {"industry": str(row["industry"]), "count": int(row["count"])}
                for _, row in grouped.iterrows()
            ]
        except Exception as e:
            logger.warning(f"Tushare 获取行业数据失败: {e}")
            return None

    def fetch_fund_flow(self, stock_code: str) -> Optional[Dict]:
        pro = self._get_pro()
        if pro is None:
            return None
        try:
            df = pro.moneyflow(ts_code=self._to_ts_code(stock_code))
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            return {
                "code": stock_code,
                "trade_date": str(row.get("trade_date", "")),
                "main_net_inflow": float(row.get("net_mf_amount", 0) or 0),  # 主力净流入额
                "super_large_buy_amount": float(row.get("buy_elg_amount", 0) or 0),  # 特大单买入额
                "large_buy_amount": float(row.get("buy_lg_amount", 0) or 0),  # 大单买入额
                "source": "tushare_pro",
            }
        except Exception as e:
            logger.warning(f"Tushare 获取 {stock_code} 资金流向失败: {e}")
            return None

    def fetch_stock_history(self, stock_code: str, years: int = 1) -> Optional[pd.DataFrame]:
        pro = self._get_pro()
        if pro is None:
            return None
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")
            df = pro.daily(ts_code=self._to_ts_code(stock_code), start_date=start, end_date=end)
            if df is None or df.empty:
                return None
            df = df.rename(columns={
                "trade_date": "date",
                "vol": "volume",
                "amount": "turnover",
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            return df
        except Exception as e:
            logger.warning(f"Tushare 获取 {stock_code} 历史行情失败: {e}")
            return None

    def fetch_stock_events(self, stock_code: str) -> Optional[List[Dict]]:
        # Tushare Pro 事件接口需额外权限，统一交由其他数据源
        return None


# ============================================================
# 腾讯财经（免费 HTTP 接口）
# ============================================================

class TencentDataSource(DataSource):
    """腾讯财经数据源（qt.gtimg.cn 实时行情 / ifzq.gtimg.cn 历史K线）

    腾讯免费接口仅提供行情/K线/快照，不提供个股资金流向与行业板块数据，
    因此 fetch_fund_flow / fetch_industry_data 明确返回 None，
    由 fallback 机制落到 eastmoney 源取这两个字段。
    """

    def __init__(self):
        super().__init__("tencent", priority=2)
        self.timeout = 15
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    # -- 私有工具 --------------------------------------------------

    def _query_code(self, stock_code: str) -> str:
        return f"sh{stock_code}" if stock_code.startswith(("6", "8")) else f"sz{stock_code}"

    def _get_stock_list(self) -> Optional[List[Dict]]:
        """通过 AKShare 获取 A 股代码-名称列表（免费）"""
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            if df is None or df.empty:
                return None
            return [
                {"code": str(row["code"]), "name": str(row["name"])}
                for _, row in df.iterrows()
            ]
        except Exception as e:
            logger.warning(f"腾讯数据源获取股票列表失败: {e}")
            return None

    # -- 接口实现 --------------------------------------------------

    def fetch_stock_basic(self) -> Optional[List[Dict]]:
        return self._get_stock_list()

    def fetch_industry_data(self) -> Optional[List[Dict]]:
        # 腾讯不提供行业板块数据，交由 eastmoney 源兜底
        return None

    def fetch_fund_flow(self, stock_code: str) -> Optional[Dict]:
        # 腾讯不提供个股资金流向，交由 eastmoney 源兜底
        return None

    def fetch_stock_history(self, stock_code: str, years: int = 1) -> Optional[pd.DataFrame]:
        try:
            import requests
            market_code = self._query_code(stock_code)
            fetch_days = min(int(years) * 365, 1000)  # 腾讯接口单次最多 1000 天
            url = (
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={market_code},day,,,{fetch_days},qfq"
            )
            resp = requests.get(url, timeout=self.timeout, headers=self.headers)
            data = resp.json()
            data_level = data.get("data", {})
            stock_data = data_level.get(market_code, {}) if isinstance(data_level, dict) else {}
            klines = stock_data.get("qfqday", []) or stock_data.get("day", []) if isinstance(stock_data, dict) else []
            if not klines:
                return None
            records = []
            for item in klines:
                if len(item) >= 6 and isinstance(item, list):
                    records.append({
                        "date": str(item[0]),
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": int(float(item[5])),
                    })
            if not records:
                return None
            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            return df
        except Exception as e:
            logger.warning(f"腾讯数据源获取 {stock_code} 历史行情失败: {e}")
            return None

    def fetch_stock_events(self, stock_code: str) -> Optional[List[Dict]]:
        try:
            import akshare as ak
            # 使用个股公告接口（ak.stock_zh_a_notice 在新版 akshare 已移除）
            notices = ak.stock_individual_notice_report(security=stock_code, symbol="全部")
            if notices is None or notices.empty:
                return []
            events = []
            for _, row in notices.head(10).iterrows():
                # akshare 不同版本公告列名有差异，用候选字段兜底，避免取到空串假数据
                content = (
                    row.get("公告标题") or row.get("标题") or row.get("title") or ""
                )
                date = (
                    row.get("公告日期") or row.get("发布日期") or row.get("date") or ""
                )
                events.append({
                    "type": "公告",
                    "content": str(content),
                    "date": str(date),
                })
            return events
        except Exception as e:
            logger.warning(f"腾讯数据源获取 {stock_code} 事件失败: {e}")
            return None


# ============================================================
# 东方财富（复用 eastmoney_fetcher 的 Cookie/代理通道）
# ============================================================

class EastMoneyDataSource(DataSource):
    """东方财富数据源（复用 utils/eastmoney_fetcher 通道，覆盖行情/资金流/行业）"""

    def __init__(self):
        super().__init__("eastmoney", priority=3)

    def fetch_stock_basic(self) -> Optional[List[Dict]]:
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            if df is None or df.empty:
                return None
            return [
                {"code": str(row["code"]), "name": str(row["name"])}
                for _, row in df.iterrows()
            ]
        except Exception as e:
            logger.warning(f"东方财富获取股票列表失败: {e}")
            return None

    def fetch_industry_data(self) -> Optional[List[Dict]]:
        return _em_fetch_industry()

    def fetch_fund_flow(self, stock_code: str) -> Optional[Dict]:
        return _em_fetch_fund_flow(stock_code)

    def fetch_stock_history(self, stock_code: str, years: int = 1) -> Optional[pd.DataFrame]:
        return _em_fetch_history(stock_code, years=years)

    def fetch_stock_events(self, stock_code: str) -> Optional[List[Dict]]:
        try:
            import akshare as ak
            # 使用个股公告接口（ak.stock_zh_a_notice 在新版 akshare 已移除）
            notices = ak.stock_individual_notice_report(security=stock_code, symbol="全部")
            if notices is None or notices.empty:
                return []
            events = []
            for _, row in notices.head(10).iterrows():
                # akshare 不同版本公告列名有差异，用候选字段兜底，避免取到空串假数据
                content = (
                    row.get("公告标题") or row.get("标题") or row.get("title") or ""
                )
                date = (
                    row.get("公告日期") or row.get("发布日期") or row.get("date") or ""
                )
                events.append({
                    "type": "公告",
                    "content": str(content),
                    "date": str(date),
                })
            return events
        except Exception as e:
            logger.warning(f"东方财富获取 {stock_code} 事件失败: {e}")
            return None


# ============================================================
# Baostock（免费开源行情库）
# 实现参考：F:/WorkSpace/Sequoia-X/sequoia_x/data/engine.py
# ============================================================

class BaostockDataSource(DataSource):
    """Baostock 免费数据源（无需 token，提供行情/股票列表/行业数据）"""

    def __init__(self):
        super().__init__("baostock", priority=4)

    # -- 私有工具 --------------------------------------------------

    def _login(self) -> bool:
        """登录 baostock，成功返回 True"""
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code != "0":
                logger.warning(f"baostock 登录失败: {lg.error_msg}")
                return False
            return True
        except Exception as e:
            logger.warning(f"baostock 初始化失败: {e}")
            return False

    def is_available(self) -> bool:
        return self._login()

    # -- 接口实现 --------------------------------------------------

    def fetch_stock_basic(self) -> Optional[List[Dict]]:
        """通过 baostock 获取全市场 A 股代码列表（参考 engine.py get_all_symbols）"""
        try:
            import baostock as bs
            if not self._login():
                return None
            try:
                rs = bs.query_stock_basic(code_name="", code="")
                stocks = []
                while rs.next():
                    row = rs.get_row_data()
                    if len(row) < 6:
                        continue
                    code = row[0]       # "sh.600000" or "sz.000001"
                    status = row[4]     # "1" = 上市
                    stock_type = row[5] # "1" = 股票
                    if status == "1" and stock_type == "1":
                        stocks.append({
                            "code": code.split(".")[1],
                            "name": row[1] if len(row) > 1 else "",
                            "market": "沪市" if code.startswith("sh") else "深市",
                            "source": "baostock",
                        })
                logger.info(f"baostock 获取股票列表完成，共 {len(stocks)} 只")
                return stocks or None
            finally:
                bs.logout()
        except Exception as e:
            logger.warning(f"baostock 获取股票列表失败: {e}")
            return None

    def fetch_industry_data(self) -> Optional[List[Dict]]:
        """通过 baostock 获取股票所属行业"""
        try:
            import baostock as bs
            if not self._login():
                return None
            try:
                rs = bs.query_stock_industry()
                # 返回字段顺序：updateDate, code, code_name, industry, industryClassification
                # 通过 rs.fields 定位列索引，避免硬编码错位（行业名在 index 3，非 index 1）
                fields = list(rs.fields) if getattr(rs, "fields", None) else []
                industry_idx = fields.index("industry") if "industry" in fields else 3
                industries = {}
                while rs.next():
                    row = rs.get_row_data()
                    if industry_idx >= len(row):
                        continue
                    industry = row[industry_idx]
                    if not industry:
                        continue
                    industries[industry] = industries.get(industry, 0) + 1
                if not industries:
                    return None
                return [
                    {"industry": name, "count": count}
                    for name, count in sorted(industries.items(), key=lambda x: -x[1])
                ]
            finally:
                bs.logout()
        except Exception as e:
            logger.warning(f"baostock 获取行业数据失败: {e}")
            return None

    def fetch_stock_industry_map(self) -> Optional[dict]:
        """通过 baostock 获取每只股票的申万一级行业映射 {code: industry}"""
        try:
            import baostock as bs
            if not self._login():
                return None
            try:
                rs = bs.query_stock_industry()
                fields = list(rs.fields) if getattr(rs, "fields", None) else []
                code_idx = fields.index("code") if "code" in fields else 0
                industry_idx = fields.index("industry") if "industry" in fields else 3
                mapping = {}
                while rs.next():
                    row = rs.get_row_data()
                    if max(code_idx, industry_idx) >= len(row):
                        continue
                    raw_code = row[code_idx]  # "sh.600000"
                    industry = row[industry_idx]
                    if not raw_code or not industry:
                        continue
                    code = raw_code.split(".")[-1]
                    mapping[code] = industry
                return mapping or None
            finally:
                bs.logout()
        except Exception as e:
            logger.warning(f"baostock 获取行业映射失败: {e}")
            return None

    def fetch_fund_flow(self, stock_code: str) -> Optional[Dict]:
        # baostock 不提供个股资金流向接口，交由其他数据源
        return None

    def fetch_stock_history(self, stock_code: str, years: int = 1) -> Optional[pd.DataFrame]:
        """通过 baostock 获取日 K 线（后复权，参考 engine.py backfill）

        engine.py 约定：
            adjustflag="1" 表示后复权；字段 date,open,high,low,close,volume,amount
        """
        try:
            import baostock as bs
            if not self._login():
                return None
            try:
                end = datetime.now().strftime("%Y-%m-%d")
                start = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
                bs_code = _to_baostock_code(stock_code)
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount",
                    start_date=start,
                    end_date=end,
                    frequency="d",
                    adjustflag="1",  # 后复权
                )
                if rs.error_code != "0":
                    logger.warning(f"baostock 查询 {stock_code} 失败: {rs.error_msg}")
                    return None
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    return None
                df = pd.DataFrame(rows, columns=rs.fields)
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                df = df[df["volume"] > 0]
                if df.empty:
                    return None
                df = df.rename(columns={"amount": "turnover"})
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date")
                df = df[["date", "open", "high", "low", "close", "volume", "turnover"]]
                return df
            finally:
                bs.logout()
        except Exception as e:
            logger.warning(f"baostock 获取 {stock_code} 历史行情失败: {e}")
            return None

    def fetch_stock_events(self, stock_code: str) -> Optional[List[Dict]]:
        # baostock 不提供事件/公告接口，交由其他数据源
        return None
