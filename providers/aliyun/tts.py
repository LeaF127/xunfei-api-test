"""
阿里云语音合成（TTS）- RESTful API

文档: https://help.aliyun.com/zh/isi/developer-reference/restful-api-3

交互流程：
  1. 构造 HTTP POST 请求，Body 为 JSON（含 appkey/token/text/format 等）
  2. Header 设置 Content-Type: application/json
  3. 响应 Content-Type 为 audio/* 时，Body 为音频二进制数据
  4. 响应 Content-Type 为 application/json 时，为错误信息
"""

import json
import time
import http.client

from providers.base import BaseTTS, Metrics
from providers.aliyun.auth import AliyunAuth

# RESTful 服务地址
HOST = "nls-gateway-ap-southeast-1.aliyuncs.com"
TTS_URL = "/stream/v1/tts"


class AliTTS(BaseTTS):
    """
    阿里云语音合成（RESTful API）
    """

    def __init__(self, access_key_id: str, access_key_secret: str, app_key: str):
        super().__init__()
        self.auth = AliyunAuth(access_key_id, access_key_secret)
        self.app_key = app_key
        self.audio_data = b""

        # 性能指标
        self.ttft = None
        self.total_time = None
        self.rtf = None

    def synthesize(self, text: str,
                   voice: str = "xiaoyun",
                   volume: int = 50,
                   speech_rate: int = 0,
                   pitch_rate: int = 0,
                   audio_format: str = "mp3",
                   sample_rate: int = 16000,
                   output_file: str = None) -> bytes:
        """RESTful 语音合成"""
        self.audio_data = b""
        self.ttft = None
        self.total_time = None
        self.rtf = None

        token = self.auth.get_token()

        # 构造请求 Body（JSON）
        body = json.dumps({
            "appkey": self.app_key,
            "token": token,
            "text": text,
            "format": audio_format,
            "sample_rate": sample_rate,
            "voice": voice,
            "volume": volume,
            "speech_rate": speech_rate,
            "pitch_rate": pitch_rate,
        })

        # 设置 HTTP 请求头
        http_headers = {
            "Content-Type": "application/json",
        }

        # 开始计时
        start_time = time.perf_counter()

        # 发送 HTTP POST 请求
        conn = http.client.HTTPConnection(HOST)
        conn.request(method="POST", url=TTS_URL, body=body, headers=http_headers)

        response = conn.getresponse()
        content_type = response.getheader("Content-Type", "")
        resp_body = response.read()

        self.total_time = time.perf_counter() - start_time
        self.ttft = self.total_time  # RESTful 一次性返回

        conn.close()

        # 解析响应
        if response.status != 200:
            raise RuntimeError(f"HTTP 请求失败: {response.status} {response.reason}")

        if content_type.startswith("audio/"):
            # 成功：返回音频数据
            self.audio_data = resp_body
        else:
            # 失败：返回 JSON 错误信息
            error_info = json.loads(resp_body)
            raise RuntimeError(f"合成失败: {error_info}")

        # 计算 RTF
        if self.audio_data:
            if audio_format == "mp3":
                from utils.audio import estimate_mp3_duration
                audio_duration = estimate_mp3_duration(self.audio_data)
            else:
                audio_duration = len(self.audio_data) / (sample_rate * 2)
            if audio_duration > 0:
                self.rtf = self.total_time / audio_duration

        # 保存到文件
        if output_file and self.audio_data:
            with open(output_file, "wb") as f:
                f.write(self.audio_data)
            print(f"[TTS] 音频已保存: {output_file} ({len(self.audio_data)} bytes)")

        m = Metrics(ttft=self.ttft, total_time=self.total_time, rtf=self.rtf)
        self.last_metrics = m
        return self.audio_data, m
