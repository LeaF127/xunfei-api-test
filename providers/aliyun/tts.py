"""
阿里云语音合成（TTS）- WebSocket 版
"""

import base64
import json
import time
import websocket  # pip install websocket-client

from providers.base import BaseTTS
from providers.aliyun.auth import AliyunAuth

# WebSocket 服务地址
WS_URL = "wss://nls-gateway-ap-southeast-1.aliyuncs.com/ws/v1"


class AliTTS(BaseTTS):
    """
    阿里云语音合成（WebSocket 版）
    
    接口要求：
      协议:    WebSocket Secure (wss)
      地址:    wss://nls-gateway-ap-southeast-1.aliyuncs.com/ws/v1
      音频:    采样率 8k/16k
      格式:    pcm / wav / mp3
      文本:    <= 300 字
    """

    def __init__(self, access_key_id: str, access_key_secret: str):
        super().__init__()
        self.auth = AliyunAuth(access_key_id, access_key_secret)
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
        """
        WebSocket 合成语音
        """
        self.audio_data = b""
        self.ttft = None
        self.total_time = None
        self.rtf = None

        token = self.auth.get_token()

        # 开始计时
        start_time = time.perf_counter()
        first_audio_time = None

        # 建立 WebSocket 连接（同步模式，带 X-NLS-Token header）
        ws = websocket.create_connection(
            WS_URL,
            header={"X-NLS-Token": token},
        )

        try:
            # 1. 发送合成请求
            start_msg = {
                "header": {
                    "namespace": "SpeechSynthesizer",
                    "name": "StartSynthesis",
                    "message_id": str(int(time.time_ns())),
                    "task_id": f"task-{int(time.time_ns())}",
                    "status": 20000000,
                    "status_text": "Gateway:Success:Success."
                },
                "payload": {
                    "format": audio_format.upper(),
                    "sample_rate": sample_rate,
                    "voice": voice,
                    "volume": volume,
                    "speech_rate": speech_rate,
                    "pitch_rate": pitch_rate,
                    "text": text,
                    "enable_subtitle": False,
                    "token": token,
                }
            }
            ws.send(json.dumps(start_msg))

            # 等待 StartSynthesis 响应
            resp = json.loads(ws.recv())
            header = resp.get("header", {})
            if header.get("status", 0) != 20000000:
                raise RuntimeError(f"StartSynthesis 失败: {resp}")

            # 2. 循环接收合成音频数据
            ws.settimeout(30)
            while True:
                msg = ws.recv()
                data = json.loads(msg)
                name = data.get("header", {}).get("name", "")

                if name == "SynthesisResult":
                    payload = data.get("payload", {})
                    audio_b64 = payload.get("audio", "")
                    if audio_b64:
                        if first_audio_time is None:
                            first_audio_time = time.perf_counter() - start_time
                        self.audio_data += base64.b64decode(audio_b64)
                    # status=2 表示合成结束
                    if payload.get("status") == 2:
                        break

                if name == "TaskFailed":
                    raise RuntimeError(f"TTS 失败: {data}")

        finally:
            ws.close()

        # 结束计时
        self.total_time = time.perf_counter() - start_time
        self.ttft = first_audio_time

        # 计算音频时长用于 RTF
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

        return self.audio_data
