"""
豆包语音 语音合成 (TTS) — HTTP 接口 (一次性合成-非流式)

接口说明:
  协议:  HTTPS (HTTP POST)
  地址:  https://openspeech.bytedance.com/api/v1/tts
  格式:  mp3 / wav / ogg_opus / pcm
  文本:  单次 <= 1024 字节 (UTF-8), 建议 <= 300 字符
  鉴权:  Bearer Token
  注意:  返回 JSON, 音频经 base64 编码
"""

import base64
import json
import time
import uuid

import requests

from providers.base import BaseTTS
from providers.doubao.auth import build_tts_headers_bearer
from utils.audio import estimate_mp3_duration


class DoubaoTTS(BaseTTS):
    """
    豆包语音 — 在线语音合成 (TTS)

    使用 HTTP POST 一次性合成, 返回 base64 编码的音频。
    """

    API_URL = "https://openspeech.bytedance.com/api/v1/tts"

    def __init__(self, appid: str, access_token: str, cluster: str = "volcano_tts"):
        super().__init__()
        self.appid = appid
        self.access_token = access_token
        self.cluster = cluster

    def synthesize(self, text: str,
                   voice_type: str = "BV001_streaming",
                   audio_format: str = "mp3",
                   sample_rate: int = 24000,
                   speed_ratio: float = 1.0,
                   volume_ratio: float = 1.0,
                   pitch_ratio: float = 1.0,
                   output_file: str = None,
                   **kwargs) -> bytes:
        """
        合成语音

        Args:
            text: 要合成的文本
            voice_type: 音色类型 (如 BV001_streaming)
            audio_format: 音频编码格式 (mp3/wav/ogg_opus/pcm)
            sample_rate: 采样率 (8000/16000/24000/48000)
            speed_ratio: 语速比例
            volume_ratio: 音量比例
            pitch_ratio: 音高比例
            output_file: 输出文件路径
        Returns:
            音频二进制数据
        """
        self.ttft = None
        self.total_time = None
        self.rtf = None

        reqid = str(uuid.uuid4())

        body = {
            "app": {
                "appid": self.appid,
                "token": "placeholder",  # 无实际鉴权作用, 传任意非空字符串
                "cluster": self.cluster,
            },
            "user": {
                "uid": "benchmark_test",
            },
            "audio": {
                "voice_type": voice_type,
                "encoding": audio_format,
                "speed_ratio": speed_ratio,
                "volume_ratio": volume_ratio,
                "pitch_ratio": pitch_ratio,
                "sample_rate": sample_rate,
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

        headers = build_tts_headers_bearer(self.access_token)

        start = time.perf_counter()
        response = requests.post(self.API_URL, json=body, headers=headers, timeout=30)
        self.total_time = time.perf_counter() - start

        if response.status_code != 200:
            raise RuntimeError(f"TTS 请求失败: HTTP {response.status_code}, {response.text}")

        result = response.json()

        # 检查业务状态码
        code = result.get("code", -1)
        if code != 3000:
            raise RuntimeError(f"TTS 合成失败: code={code}, message={result.get('message', '')}")

        # 解码 base64 音频
        audio_b64 = result.get("data", "")
        if not audio_b64:
            raise RuntimeError(f"TTS 合成返回空音频: {result}")

        audio_data = base64.b64decode(audio_b64)
        self.ttft = self.total_time  # HTTP 非流式模式, TTFT ≈ 总耗时

        # 计算 RTF
        if audio_format == "mp3":
            audio_duration = estimate_mp3_duration(audio_data)
        elif audio_format == "pcm":
            audio_duration = len(audio_data) / (sample_rate * 2)
        else:
            # wav: 去掉 header (44 bytes) 后按 pcm 计算
            audio_duration = max(0, len(audio_data) - 44) / (sample_rate * 2)

        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        if output_file and audio_data:
            with open(output_file, "wb") as f:
                f.write(audio_data)
            print(f"[TTS] 音频已保存: {output_file} ({len(audio_data)} bytes)")

        return audio_data
