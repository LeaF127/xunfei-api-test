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

from providers.base import BaseASR, Metrics
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

        if not audio_data:
            raise ValueError("音频数据为空")

        # 估算音频时长（WAV 格式需要减去 header 大小）
        if audio_format == "pcm":
            audio_duration = len(audio_data) / (sample_rate * 2)
        elif audio_format == "wav":
            # WAV 有 44 字节 header
            audio_duration = max(0, (len(audio_data) - 44)) / (sample_rate * 2)
        else:
            audio_duration = 0

        token = self.auth.get_token()

        # 构造请求 URL（参数拼接，严格按照官方示例）
        request_url = f"{ASR_URL}?appkey={self.app_key}"
        request_url += f"&format={audio_format}"
        request_url += f"&sample_rate={str(sample_rate)}"

        if enable_punctuation:
            request_url += "&enable_punctuation_prediction=true"

        if enable_inverse_text_normalization:
            request_url += "&enable_inverse_text_normalization=true"

        # 调试：打印请求信息
        print(f"[阿里云ASR] Request URL: {request_url}")
        print(f"[阿里云ASR] Audio size: {len(audio_data)} bytes, format: {audio_format}, sample_rate: {sample_rate}")

        # 设置 HTTP 请求头
        http_headers = {
            "X-NLS-Token": token,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(audio_data)),
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
        print(f"[阿里云ASR] Response: {response.status} {response.reason}")
        print(f"[阿里云ASR] Body: {body[:500]}")

        if response.status != 200:
            try:
                error_data = json.loads(body)
                raise RuntimeError(
                    f"HTTP {response.status} {response.reason}: "
                    f"task_id={error_data.get('task_id')}, "
                    f"status={error_data.get('status')}, "
                    f"message={error_data.get('message', '')}"
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise RuntimeError(f"HTTP {response.status} {response.reason}: {body}")

        data = json.loads(body)
        status = data.get("status", -1)

        if status == 20000000:
            self.result_text = data.get("result", "")
        else:
            raise RuntimeError(f"识别失败: status={status}, message={data.get('message', '')}, response={data}")

        # 计算 RTF
        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        m = Metrics(ttft=self.ttft, total_time=self.total_time, rtf=self.rtf)
        self.last_metrics = m
        return self.result_text, m
