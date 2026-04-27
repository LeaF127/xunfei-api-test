"""
豆包语音 (火山引擎) 鉴权模块

支持两种鉴权方式:
  1. 旧版: Bearer Token (appid + token + cluster)
  2. 新版: X-Api-Key + X-Api-Resource-Id

TTS HTTP V1 接口使用 Bearer Token 方式.
ASR WebSocket V2 接口使用 HMAC256 签名方式.
"""

import base64
import hashlib
import hmac
import uuid


def build_tts_headers_bearer(token: str) -> dict:
    """
    构建 TTS HTTP V1 接口请求头 (Bearer Token 鉴权)

    Args:
        token: 火山引擎控制台获取的 access_token
    Returns:
        请求头 dict
    """
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer;{token}",
    }


def build_asr_auth_header(access_token: str, secret_key: str,
                          method: str = "GET", path: str = "/api/v2/asr",
                          host: str = "openspeech.bytedance.com",
                          body: bytes = b"") -> dict:
    """
    构建 ASR WebSocket V2 接口的 Authorization 请求头 (HMAC256 签名)

    签名格式:
      待加密字符串 = "{method} {path} HTTP/1.1\\n{host}\\n{body}"

    Args:
        access_token: 控制台获取的 access_token
        secret_key: 控制台获取的 secret_key
        method: HTTP 方法
        path: 请求路径
        host: 主机名
        body: 请求体 (二进制)
    Returns:
        包含 Authorization 的请求头 dict
    """
    # 拼接待加密字符串
    string_to_sign = f"{method} {path} HTTP/1.1\n{host}\n"
    if body:
        string_to_sign += body.decode("utf-8", errors="replace")

    # HMAC-SHA256 签名 + base64url 编码
    mac = hmac.new(
        secret_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    mac_b64 = base64.urlsafe_b64encode(mac).decode("utf-8").rstrip("=")

    authorization = (
        f'HMAC256; access_token="{access_token}"; '
        f'mac="{mac_b64}"; h="host"'
    )
    return {
        "Authorization": authorization,
        "Host": host,
    }
