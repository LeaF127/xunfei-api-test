"""讯飞 WebSocket 鉴权"""

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode, urlparse


def create_auth_url(host_url: str, api_key: str, api_secret: str) -> str:
    """
    生成讯飞 WebSocket 鉴权 URL（ASR 和 TTS 通用）

    鉴权流程:
      1. 拼接签名原文: host: $host\\ndate: $date\\n$request-line
      2. 使用 api_secret 对签名原文做 hmac-sha256 签名
      3. 将签名结果 base64 编码得到 signature
      4. 拼接 authorization_origin 并 base64 编码得到 authorization
      5. 将 host / date / authorization 作为 URL 参数拼接到请求地址
    """
    parsed = urlparse(host_url)

    # RFC1123 格式的 GMT 时间戳
    date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

    # 拼接签名原文
    signature_origin = f"host: {parsed.hostname}\ndate: {date}\nGET {parsed.path} HTTP/1.1"

    # hmac-sha256 签名
    signature_sha = hmac.new(
        api_secret.encode("utf-8"),
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    signature = base64.b64encode(signature_sha).decode("utf-8")

    # 拼接 authorization_origin
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )

    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")

    # 拼接最终 URL
    params = {"authorization": authorization, "date": date, "host": parsed.hostname}
    return host_url + "?" + urlencode(params)
