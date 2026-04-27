"""阿里云一句话识别 (ASR) — RESTful API"""

import time

import requests

from providers.base import BaseASR
from providers.aliyun.auth import AliyunAuth


class AliASR(BaseASR):
    """
    阿里云智能语音交互 — 一句话识别

    接口要求:
      协议:    HTTPS (RESTful)
      地址:    https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr
      音频:    采样率 8k/16k, 16bit, 单声道
      格式:    pcm / wav / mp3 / ogg / opus / speex
      时长:    最长 60s
    """

    GATEWAY_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr"

    def __init__(self, access_key_id: str, access_key_secret: str, app_key: str):
        super().__init__()
        self.auth = AliyunAuth(access_key_id, access_key_secret)
        self.app_key = app_key

    def recognize(self, audio_data, audio_format="pcm", sample_rate=16000,
                  enable_punctuation=True,
                  enable_inverse_text_normalization=True) -> str:
        """
        一句话识别

        Args:
            audio_data: 文件路径 (str) 或音频二进制 (bytes)
            audio_format: pcm / wav / mp3
            sample_rate: 采样率
            enable_punctuation: 开启标点预测
            enable_inverse_text_normalization: 开启数字转写
        Returns:
            识别文本
        """
        self.ttft = None
        self.total_time = None
        self.rtf = None

        # 读取音频
        if isinstance(audio_data, str):
            with open(audio_data, "rb") as f:
                audio = f.read()
        elif isinstance(audio_data, bytes):
            audio = audio_data
        else:
            raise TypeError("audio_data 必须是文件路径(str)或音频数据(bytes)")

        audio_duration = len(audio) / (sample_rate * 2)
        token = self.auth.get_token()

        url = (
            f"{self.GATEWAY_URL}"
            f"?appkey={self.app_key}"
            f"&format={audio_format}"
            f"&sample_rate={sample_rate}"
            f"&enable_punctuation_prediction={str(enable_punctuation).lower()}"
            f"&enable_inverse_text_normalization={str(enable_inverse_text_normalization).lower()}"
        )

        headers = {
            "X-NLS-Token": token,
            "Content-Type": "application/octet-stream",
        }

        start = time.perf_counter()
        response = requests.post(url, headers=headers, data=audio, timeout=30)
        self.total_time = time.perf_counter() - start

        if response.status_code != 200:
            raise RuntimeError(f"ASR 请求失败: HTTP {response.status_code}, {response.text}")

        result = response.json()
        if result.get("status") != 200:
            raise RuntimeError(f"ASR 识别失败: {result}")

        self.ttft = self.total_time  # RESTful 模式下 TTFT ≈ 总耗时
        text = result.get("result", "")

        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        return text
