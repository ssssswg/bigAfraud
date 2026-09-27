"""钉钉机器人通知（自定义机器人 + 加签安全设置）

钉钉自定义机器人安全设置支持两种：加签 / 关键词 / IP白名单。
本项目 config.yaml 配置了 secret，采用「加签」方式：
    timestamp + '\\n' + secret -> HMAC-SHA256 -> base64 -> urllib.quote_plus
发送时在 webhook_url 后拼接 &timestamp=xxx&sign=xxx
"""
import time
import hmac
import hashlib
import base64
import urllib.parse
import logging
import requests

logger = logging.getLogger(__name__)


class DingTalkNotifier:
    def __init__(self, webhook_url: str = "", secret: str = ""):
        self.webhook_url = webhook_url
        self.secret = secret

    def _signed_url(self) -> str:
        """带加签的完整请求 URL"""
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

    def send_text(self, text: str) -> bool:
        if not self.webhook_url:
            logger.warning("钉钉 webhook_url 未配置")
            return False
        url = self._signed_url() if self.secret else self.webhook_url
        payload = {"msgtype": "text", "text": {"content": text}}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("钉钉推送成功")
                return True
            logger.error("钉钉推送失败: %s", result)
            return False
        except Exception as e:
            logger.error("钉钉推送异常: %s", e)
            return False

    def send_markdown(self, title: str, markdown_text: str) -> bool:
        if not self.webhook_url:
            logger.warning("钉钉 webhook_url 未配置")
            return False
        url = self._signed_url() if self.secret else self.webhook_url
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": markdown_text},
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("钉钉推送成功(markdown)")
                return True
            logger.error("钉钉推送失败(markdown): %s", result)
            return False
        except Exception as e:
            logger.error("钉钉推送异常(markdown): %s", e)
            return False
