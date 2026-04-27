"""
阿里云语音合成（TTS）- WebSocket 版
"""

import base64
import json
import time
import threading
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
        # 内部计时变量
        self._tts_start = None
        self._first_audio_time = None

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
        
        Args:
            text:        要合成的文本（UTF-8，<=300 字）
            voice:       发音人（xiaoyun, xiaoyan, xiaowei 等）
            volume:      音量 [0-100]
            speech_rate: 语速 [-500, 500]
            pitch_rate:  音高 [-500, 500]
            audio_format: 音频格式（pcm / wav / mp3）
            sample_rate: 采样率（8000 或 16000）
            output_file: 输出文件路径
        
        Returns:
            合成的音频二进制数据
        """
        self.audio_data = b""
        self.ttft = None
        self.total_time = None
        self.rtf = None
        self._first_audio_time = None

        token = self.auth.get_token()

        # WebSocket 事件
        ws = None
        synthesis_completed = threading.Event()
        ws_connected = threading.Event()

        def on_open(ws_obj):
            """WebSocket 连接建立"""
            nonlocal ws
            ws = ws_obj
            ws_connected.set()

        def on_message(ws_obj, message):
            """接收合成音频数据"""
            nonlocal ws
            data = json.loads(message)

            # 处理音频数据
            if data.get("header", {}).get("name") == "SynthesisResult":
                payload = data.get("payload", {})
                audio_b64 = payload.get("audio", "")
                if audio_b64:
                    # 记录首次音频数据时间（TTFT）
                    if self._first_audio_time is None:
                        self._first_audio_time = time.perf_counter() - self._tts_start
                    self.audio_data += base64.b64decode(audio_b64)
                    status = payload.get("status")
                    # status=2 表示合成结束
                    if status == 2:
                        synthesis_completed.set()

            # 处理异常
            if data.get("header", {}).get("name") == "TaskFailed":
                error_msg = data.get("payload", {}).get("message", "Unknown error")
                print(f"[阿里云TTS] 合成失败: {error_msg}")
                synthesis_completed.set()

        def on_error(ws_obj, error):
            """WebSocket 错误"""
            print(f"[阿里云TTS] WebSocket 错误: {error}")
            synthesis_completed.set()
            ws_connected.set()

        def on_close(ws_obj, close_status, close_msg):
            """WebSocket 连接关闭"""
            ws_connected.set()
            synthesis_completed.set()

        # 创建 WebSocket 连接（使用 create_connection 支持添加 header）
        self._tts_start = time.perf_counter()

        try:
            ws = websocket.create_connection(
                WS_URL,
                header={"X-NLS-Token": token},
                enable_trace=True,
            )
            on_open(ws)
            
            # 设置回调
            ws.set_on_message(on_message)
            ws.set_on_error(on_error)
            ws.set_on_close(on_close)

            # 等待连接建立
            ws_connected.wait(timeout=10)

            # 发送合成请求
            message = {
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
            ws.send(json.dumps(message))

        except Exception as e:
            print(f"[阿里云TTS] WebSocket 连接失败: {e}")
            raise

        # 等待合成完成
        synthesis_completed.wait(timeout=30)

        # 结束计时，计算指标
        self.total_time = time.perf_counter() - self._tts_start
        self.ttft = self._first_audio_time

        # 计算音频时长用于 RTF
        if self.audio_data:
            if audio_format == "mp3":
                # 简化的 MP3 时长估算（可根据实际情况调整）
                # 这里假设平均 128kbps，每个字约 1/16000 秒
                audio_duration = len(self.audio_data) / 16000
            else:  # pcm / wav
                audio_duration = len(self.audio_data) / (sample_rate * 2)
            if audio_duration > 0:
                self.rtf = self.total_time / audio_duration

        # 保存到文件
        if output_file and self.audio_data:
            with open(output_file, "wb") as f:
                f.write(self.audio_data)
            print(f"[TTS] 音频已保存: {output_file} ({len(self.audio_data)} bytes)")

        return self.audio_data
