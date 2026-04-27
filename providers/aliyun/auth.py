"""
阿里云鉴权 - AccessKey 签名 → NLS Token
"""

import json
import time
import hmac
import hashlib
import base64
import urllib.parse

# 使用阿里云 SDK 方式（推荐）
try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


class AliyunAuth:
    """
    阿里云 AccessKey → NLS Token
    
    支持两种方式：
    1. 使用阿里云 Python SDK（推荐）
    2. 手动 HTTP 签名方式（SDK 不可用时的后备方案）
    """

    # CreateToken API 配置
    DOMAIN = "nlsmeta.ap-southeast-1.aliyuncs.com"
    REGION_ID = "ap-southeast-1"
    VERSION = "2019-07-17"
    ACTION = "CreateToken"

    def __init__(self, access_key_id: str, access_key_secret: str):
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self._token = None
        self._token_expire_time = 0

    @staticmethod
    def _percent_encode(s: str) -> str:
        """阿里云 OpenAPI 百分号编码（RFC 3986）"""
        return (
            urllib.parse.quote(s, safe="")
            .replace("+", "%20")
            .replace("*", "%2A")
            .replace("%7E", "~")
        )

    def _sign_common_request(self, params: dict) -> str:
        """
        手动 HTTP 签名方式（不使用 SDK 时的后备方案）
        CommonRequest 签名算法
        """
        # 构造规范化的参数字符串
        sorted_params = sorted(params.items())
        canonical_query = "&".join(
            f"{self._percent_encode(k)}={self._percent_encode(v)}"
            for k, v in sorted_params
        )
        # 构造待签名字符串
        string_to_sign = f"GET&%2F&{self._percent_encode(canonical_query)}"
        # HMAC-SHA1 签名
        signature = base64.b64encode(
            hmac.new(
                (self.access_key_secret + "&").encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        return signature

    def _get_token_sdk(self) -> str:
        """
        使用阿里云 SDK 获取 Token（推荐）
        """
        if not HAS_SDK:
            raise RuntimeError("阿里云 SDK 未安装，请运行: pip install aliyun-python-sdk-core")

        client = AcsClient(
            self.access_key_id,
            self.access_key_secret,
            self.REGION_ID,
        )
        request = CommonRequest()
        request.set_method("POST")
        request.set_domain(self.DOMAIN)
        request.set_version(self.VERSION)
        request.set_action_name(self.ACTION)

        response = client.do_action_with_exception(request)

        # SDK 返回的格式可能是 dict 或 bytes
        if isinstance(response, dict):
            if response.get("status") != 200:
                raise RuntimeError(f"获取 Token 失败: {response}")
            data = json.loads(response.get("body", "{}"))
        elif isinstance(response, bytes):
            data = json.loads(response)
        else:
            raise RuntimeError(f"未知响应类型: {type(response)}")

        if "Token" not in data:
            raise RuntimeError(f"Token 响应格式错误: {data}")

        self._token = data["Token"]["Id"]
        self._token_expire_time = data["Token"]["ExpireTime"]
        return self._token

    def _get_token_manual(self) -> str:
        """
        手动 HTTP 签名方式获取 Token（SDK 不可用时的后备方案）
        """
        import requests

        params = {
            "Action": self.ACTION,
            "Version": self.VERSION,
            "AccessKeyId": self.access_key_id,
            "Format": "JSON",
            "RegionId": self.REGION_ID,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(time.time_ns()),
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        params["Signature"] = self._sign_common_request(params)

        url = f"https://{self.DOMAIN}/"

        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if "Token" not in data:
            raise RuntimeError(f"获取 Token 失败: {data}")

        self._token = data["Token"]["Id"]
        self._token_expire_time = data["Token"]["ExpireTime"]
        return self._token

    def get_token(self) -> str:
        """
        获取 NLS Token（自动缓存）
        
        Token 有效期内可多次使用（跨线程、跨进程、跨机器）。
        获取 Token 不宜频繁，否则可能会被限流。
        """
        if self._token and time.time() < self._token_expire_time - 60:
            return self._token

        # 优先使用 SDK，失败则使用手动签名
        try:
            return self._get_token_sdk()
        except Exception as e:
            print(f"[阿里云] SDK 方式失败，尝试手动签名: {e}")
            return self._get_token_manual()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # 自动将项目根目录加入 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

    from config import ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET
    auth = AliyunAuth(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET)
    token = auth.get_token()
    print(f"Token: {token}")
