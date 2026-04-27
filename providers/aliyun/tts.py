"""阿里云语音合成 (TTS) — RESTful API"""

import time
from urllib.parse import quote

import requests

from providers.base import BaseTTS
from providers.aliyun.auth import AliyunAuth
from utils.audio import estimate_mp3_duration


class AliTTS(BaseTTS):
    """
    阿里云智能语音交互 — 语音合成

    接口要求:
      协议:    HTTPS (RESTful)
      地址:    https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts
      格式:    pcm / wav / mp3
      文本:    单次 <= 300 字
    """

    GATEWAY_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"

    def __init__(self, access_key_id: str, access_key_secret: str, app_key: str):
        super().__init__()
        self.auth = AliyunAuth(access_key_id, access_key_secret)
        self.app_key = app_key

    def synthesize(self, text: str,
                   voice: str = "zhixiaoxia",
                   volume: int = 50,
                   speech_rate: int = 0,
                   pitch_rate: int = 0,
                   audio_format: str = "mp3",
                   sample_rate: int = 16000,
                   output_file: str = None) -> bytes:
        """
        合成语音

        Args:
            text: 要合成的文本
            voice: 发音人 (zhixiaoxia, zhixiaoyun, zhiyan_emo 等)
            volume: 音量 [0-100]
            speech_rate: 语速 [-500, 500]
            pitch_rate: 音高 [-500, 500]
            audio_format: mp3 / wav / pcm
            sample_rate: 采样率
            output_file: 输出文件路径
        Returns:
            音频二进制数据
        """
        self.ttft = None
        self.total_time = None
        self.rtf = None

        token = self.auth.get_token()

        url = (
            f"{self.GATEWAY_URL}"
            f"?appkey={self.app_key}"
            f"&text={quote(text)}"
            f"&format={audio_format}"
            f"&sample_rate={sample_rate}"
            f"&voice={voice}"
            f"&volume={volume}"
            f"&speech_rate={speech_rate}"
            f"&pitch_rate={pitch_rate}"
        )

        headers = {
            "X-NLS-Token": token,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        start = time.perf_counter()
        response = requests.post(url, headers=headers, timeout=30)
        self.total_time = time.perf_counter() - start

        content_type = response.headers.get("Content-Type", "")

        if "audio" in content_type or "octet-stream" in content_type:
            audio_data = response.content
            self.ttft = self.total_time

            if audio_format == "mp3":
                audio_duration = estimate_mp3_duration(audio_data)
            else:
                audio_duration = len(audio_data) / (sample_rate * 2)

            if audio_duration > 0:
                self.rtf = self.total_time / audio_duration

            if output_file and audio_data:
                with open(output_file, "wb") as f:
                    f.write(audio_data)
                print(f"[TTS] 音频已保存: {output_file} ({len(audio_data)} bytes)")

            return audio_data
        else:
            raise RuntimeError(f"TTS 合成失败: {response.text}")
