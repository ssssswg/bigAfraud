# -*- coding: utf-8 -*-
"""
Tushare Pro 统一客户端（含全局限流）

按 config/tushare.md 规范：
  1) 从 config/tushare_config.json 读取 token
  2) 调用 ts.pro_api 之后必须设置 _DataApi__token 与 _DataApi__http_url
     （自定义镜像地址，否则无法获取数据）
  3) 调用频率控制在每分钟 300~400 次

所有需要 Tushare Pro 的地方统一通过 get_pro() 获取实例，保证：
  - 接口地址一致
  - 全局频率限流（默认 300 次/分钟，可配置）
"""
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 自定义 Tushare 接口地址（见 config/tushare.md，必须设置否则无法获取数据）
TUSHARE_HTTP_URL = "https://tuaremax.top"

# 全局限流默认值：每分钟 300 次（规范区间 300~400 的下界，保守安全）
DEFAULT_MAX_CALLS = 380
DEFAULT_PERIOD = 60.0


class RateLimiter:
    """基于滑动时间窗的线程安全限流器"""

    def __init__(self, max_calls: int, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self._lock = threading.Lock()
        self._times: list = []

    def acquire(self) -> None:
        """请求一次调用许可；若窗口内已达上限则阻塞等待"""
        with self._lock:
            now = time.time()
            self._times = [t for t in self._times if now - t < self.period]
            if len(self._times) >= self.max_calls:
                wait = self.period - (now - self._times[0])
                if wait > 0:
                    time.sleep(wait)
                    now = time.time()
                    self._times = [t for t in self._times if now - t < self.period]
            self._times.append(now)


class _ThrottledPro:
    """对 Tushare pro 实例的限流代理：所有接口调用前先经过全局限流器"""

    def __init__(self, pro, limiter: RateLimiter):
        self._pro = pro
        self._limiter = limiter

    def __getattr__(self, name: str):
        pro = object.__getattribute__(self, "_pro")
        limiter = object.__getattribute__(self, "_limiter")
        attr = getattr(pro, name)
        if callable(attr) and not name.startswith("_"):
            def throttled(*args, **kwargs):
                limiter.acquire()
                return attr(*args, **kwargs)
            return throttled
        return attr


# 全局限流器单例
_LIMITER = None
_LIMITER_LOCK = threading.Lock()


def _get_global_limiter() -> RateLimiter:
    global _LIMITER
    if _LIMITER is None:
        with _LIMITER_LOCK:
            if _LIMITER is None:
                _LIMITER = RateLimiter(max_calls=DEFAULT_MAX_CALLS, period=DEFAULT_PERIOD)
    return _LIMITER


# 共享 HTTP 会话（连接池复用），降低频繁新建 HTTPS 连接的开销
_SESSION_POOL = None
_SESSION_LOCK = threading.Lock()


def _get_global_session():
    global _SESSION_POOL
    if _SESSION_POOL is None:
        with _SESSION_LOCK:
            if _SESSION_POOL is None:
                import requests
                _sess = requests.Session()
                # 不读系统代理/环境代理（trust_env=False），强制直连：
                # 修复 web 服务启动时系统代理(如 127.0.0.1:7688)被缓存、代理关闭后仍走代理
                # 导致 Tushare 请求 WinError 10061 的问题（直连已实测可通）。
                _sess.trust_env = False
                _SESSION_POOL = _sess
    return _SESSION_POOL


def _inject_session_pool():
    """
    将 Tushare 库内部请求改用共享 Session（连接池复用），幂等。

    Tushare pro client 每次调用都执行模块级 requests.post，无连接复用。
    此处仅替换 tushare.pro.client 模块内绑定的 requests 引用，隔离在该库内部，
    不影响项目其他代码。失败时仅告警、不影响功能。
    """
    try:
        from tushare.pro import client as _tclient
        if getattr(_tclient, '_session_injected', False):
            return _get_global_session()
        _real = _tclient.requests
        _session = _get_global_session()

        class _SessionRequests:
            """仅将 post 转发到共享 Session，其余属性透传真实 requests 模块。"""
            def post(self, *a, **k):
                return _session.post(*a, **k)
            def __getattr__(self, name):
                return getattr(_real, name)

        _tclient.requests = _SessionRequests()
        _tclient._session_injected = True
        logger.debug("已为 Tushare 启用连接池复用")
        return _session
    except Exception as e:
        logger.warning(f"注入 Tushare 连接池失败（不影响功能）: {e}")
        return None


def _load_token() -> str:
    """从 config/tushare_config.json 读取 token"""
    try:
        p = Path("config/tushare_config.json")
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            return cfg.get("token") or cfg.get("api_key") or ""
    except Exception as e:
        logger.warning(f"读取 tushare_config.json 失败: {e}")
    return ""


def get_pro(token: str = None):
    """创建并返回配置好自定义接口地址、带全局限流的 Tushare Pro API 实例

    参数：
        token: Tushare token。为空时自动从 config/tushare_config.json 读取。

    返回：
        限流代理实例（调用方式与原生 pro 完全一致）；
        未配置 token 或初始化失败时返回 None，调用方应自行降级处理。
    """
    import tushare as ts
    tk = token if token else _load_token()
    if not tk:
        logger.warning("未找到 Tushare token，返回 None")
        return None
    try:
        pro = ts.pro_api(tk)
        # 规范要求必须设置这两行（见 config/tushare.md）
        pro._DataApi__token = tk
        pro._DataApi__http_url = TUSHARE_HTTP_URL
        # 启用连接池复用（幂等）
        _inject_session_pool()
        # 返回限流代理，全局统一限速
        return _ThrottledPro(pro, _get_global_limiter())
    except Exception as e:
        logger.error(f"初始化 Tushare Pro 失败: {e}")
        return None
