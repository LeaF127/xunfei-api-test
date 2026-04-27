"""阿里云 NLS Token 管理"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import requests


class AliyunAuth:
    """阿里云 AccessKey 签名 → 获取 NLS Token"""

    TOKEN_API = "https://nls-meta.cn-shanghai.aliyuncs.com/"

    def __init__(self, access_key_id: str, access_key_secret: str):
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self._token = None
        self._token_expire_time = 0

    @staticmethod
    def _percent_encode(s: str) -> str:
        """阿里云 OpenAPI 编码规则"""
        return (
            urllib.parse.quote(s, safe="")
            .replace("+", "%20")
            .replace("*", "%2A")
            .replace("%7E", "~")
        )

    def _sign(self, params: dict) -> str:
        """计算 HMAC-SHA1 签名"""
        sorted_params = sorted(params.items())
        canonical_query = "&".join(
            f"{self._percent_encode(k)}={self._percent_encode(v)}"
            for k, v in sorted_params
        )
        string_to_sign = f"GET&%2F&{self._percent_encode(canonical_query)}"
        signature = base64.b64encode(
            hmac.new(
                (self.access_key_secret + "&").encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        return signature

    def get_token(self) -> str:
        """获取 NLS Token（自动缓存，到期前 60s 刷新）"""
        if self._token and time.time() < self._token_expire_time - 60:
            return self._token

        params = {
            "Action": "CreateToken",
            "Version": "2019-04-12",
            "AccessKeyId": self.access_key_id,
            "Format": "JSON",
            "RegionId": "cn-shanghai",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(time.time_ns()),
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        params["Signature"] = self._sign(params)

        resp = requests.get(self.TOKEN_API, params=params, timeout=10)
        data = resp.json()

        if "Token" not in data:
            raise RuntimeError(f"获取阿里云 Token 失败: {data}")

        self._token = data["Token"]["Id"]
        self._token_expire_time = data["Token"]["ExpireTime"]
        return self._token
