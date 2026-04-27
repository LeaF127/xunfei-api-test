"""
阿里云语音识别（ASR）- RESTful API

文档: https://help.aliyun.com/zh/isi/developer-reference/restful-api-1

交互流程：
  1. 构造 HTTP POST 请求，URL 包含 appkey/format/sample_rate 等参数
  2. Header 携带 X-NLS-Token 鉴权
  3. Body 直接发送音频二进制数据（application/octet-stream）
  4. 解析 JSON 响应获取识别结果
"""

import json
import time
import http.client

from providers.base import BaseASR
from providers.aliyun.auth import AliyunAuth

# RESTful 服务地址
HOST = "nls-gateway-ap-southeast-1.aliyuncs.com"
ASR_URL = "/stream/v1/asr"


class AliASR(BaseASR):
    """
    阿里云一句话识别（RESTful API）
    """

    def __init__(self, access_key_id: str, access_key_secret: str, app_key: str):
        super().__init__()
        self.auth = AliyunAuth(access_key_id, access_key_secret)
        self.app_key = app_key
        self.result_text = ""

        # 性能指标
        self.ttft = None
        self.total_time = None
        self.rtf = None

    def recognize(self, audio_file_path: str,
                  audio_format: str = "pcm",
                  sample_rate: int = 16000,
                  enable_punctuation: bool = False,
                  enable_inverse_text_normalization: bool = True) -> str:
        """RESTful 一句话识别"""
        self.result_text = ""
        self.ttft = None
        self.total_time = None
        self.rtf = None

        # 读取音频
        if isinstance(audio_file_path, str):
            with open(audio_file_path, "rb") as f:
                audio_data = f.read()
        elif isinstance(audio_file_path, bytes):
            audio_data = audio_file_path
        else:
            raise TypeError("audio_file_path 必须是文件路径(str)或音频数据(bytes)")

        audio_duration = len(audio_data) / (sample_rate * 2) if audio_format == "pcm" else 0
        token = self.auth.get_token()

        # 构造请求 URL（参数拼接）
        request_url = f"{ASR_URL}?appkey={self.app_key}"
        request_url += f"&format={audio_format}"
        request_url += f"&sample_rate={sample_rate}"

        if enable_punctuation:
            request_url += "&enable_punctuation_prediction=true"

        if enable_inverse_text_normalization:
            request_url += "&enable_inverse_text_normalization=true"

        # 设置 HTTP 请求头
        http_headers = {
            "X-NLS-Token": token,
            "Content-Type": "application/octet-stream",
            "Content-Length": len(audio_data),
        }

        # 开始计时
        start_time = time.perf_counter()

        # 发送 HTTP POST 请求
        conn = http.client.HTTPConnection(HOST)
        conn.request(method="POST", url=request_url, body=audio_data, headers=http_headers)

        response = conn.getresponse()
        body = response.read()

        self.total_time = time.perf_counter() - start_time
        self.ttft = self.total_time  # RESTful 是一次性返回，TTFT = 总耗时

        conn.close()

        # 解析响应
        if response.status != 200:
            raise RuntimeError(f"HTTP 请求失败: {response.status} {response.reason}")

        data = json.loads(body)
        status = data.get("status", -1)

        if status == 20000000:
            self.result_text = data.get("result", "")
        else:
            raise RuntimeError(f"识别失败: status={status}, message={data}")

        # 计算 RTF
        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        return self.result_text
