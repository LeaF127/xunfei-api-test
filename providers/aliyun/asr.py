"""
阿里云语音识别（ASR）- WebSocket 版
"""

import base64
import json
import re
import time
import threading
import websocket  # pip install websocket-client

from providers.base import BaseASR
from providers.aliyun.auth import AliyunAuth

# WebSocket 服务地址
WS_URL = "wss://nls-gateway-ap-southeast-1.aliyuncs.com/ws/v1"


class AliASR(BaseASR):
    """
    阿里云一句话识别（WebSocket 版）
    
    接口要求：
      协议:    WebSocket Secure (wss)
      地址:    wss://nls-gateway-ap-southeast-1.aliyuncs.com/ws/v1
      音频:    PCM 编码、16bit 采样位数、单声道
      采样率:   8000Hz / 16000Hz
      时长:    不超过 60s
    """

    def __init__(self, access_key_id: str, access_key_secret: str):
        super().__init__()
        self.auth = AliyunAuth(access_key_id, access_key_secret)
        self.result_text = ""
        
        # 性能指标
        self.ttft = None
        self.total_time = None
        self.rtf = None
        # 内部计时变量
        self._asr_start = None
        self._first_result_time = None

    def recognize(self, audio_file_path: str,
                  audio_format: str = "pcm",
                  sample_rate: int = 16000,
                  enable_punctuation: bool = False,
                  enable_inverse_text_normalization: bool = True) -> str:
        """
        WebSocket 一句话识别
        
        Args:
            audio_file_path: 音频文件路径或音频数据
            audio_format: 音频格式（默认 pcm）
            sample_rate: 采样率（8000 或 16000）
            enable_punctuation: 是否在后处理中添加标点
            enable_inverse_text_normalization: 是否将中文数字转为阿拉伯数字
        
        Returns:
            识别文本结果
        """
        self.result_text = ""
        self.ttft = None
        self.total_time = None
        self.rtf = None
        self._first_result_time = None

        # 读取音频文件
        if isinstance(audio_file_path, str):
            with open(audio_file_path, "rb") as f:
                audio_data = f.read()
        elif isinstance(audio_file_path, bytes):
            audio_data = audio_file_path
        else:
            raise TypeError("audio_file_path 必须是文件路径(str)或音频数据(bytes)")

        # 计算音频时长（PCM）
        audio_duration = len(audio_data) / (sample_rate * 2) if audio_format == "pcm" else 0

        token = self.auth.get_token()

        # WebSocket 事件
        ws = None
        recognition_completed = threading.Event()

        def on_open(ws_obj):
            """WebSocket 连接建立"""
            nonlocal ws
            ws = ws_obj
            
            # 发送开始识别指令
            start_message = {
                "header": {
                    "namespace": "SpeechRecognizer",
                    "name": "StartRecognition",
                    "message_id": str(int(time.time_ns())),
                    "task_id": f"task-{int(time.time_ns())}",
                    "status": 20000000,
                    "status_text": "Gateway:Success:Success."
                },
                "payload": {
                    "format": audio_format.upper(),
                    "sample_rate": sample_rate,
                    "enable_intermediate_result": False,
                    "enable_punctuation_prediction": enable_punctuation,
                    "enable_inverse_text_normalization": enable_inverse_text_normalization,
                    "token": token,
                }
            }
            ws.send(json.dumps(start_message))

        def on_message(ws_obj, message):
            """接收识别结果"""
            nonlocal ws
            data = json.loads(message)

            # 处理中间识别结果（暂不使用）
            if data.get("header", {}).get("name") == "RecognitionResultChanged":
                result = data.get("payload", {}).get("result", "")
                if result and self._first_result_time is None:
                    self._first_result_time = time.perf_counter() - self._asr_start

            # 处理最终识别结果
            if data.get("header", {}).get("name") == "RecognitionCompleted":
                result = data.get("payload", {}).get("result", "")
                if result:
                    self.result_text = result
                    if self._first_result_time is None:
                        self._first_result_time = time.perf_counter() - self._asr_start
                recognition_completed.set()
                ws.close()

        def on_error(ws_obj, error):
            """WebSocket 错误"""
            print(f"[阿里云ASR] WebSocket 错误: {error}")
            recognition_completed.set()

        def on_close(ws_obj, close_status, close_msg):
            """WebSocket 连接关闭"""
            recognition_completed.set()

        # 创建 WebSocket 连接
        ws_app = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        # 开始计时
        self._asr_start = time.perf_counter()

        # 在独立线程中运行 WebSocket
        def run_ws():
            ws_app.run_forever()

        ws_thread = threading.Thread(target=run_ws, daemon=True)
        ws_thread.start()

        # 等待 WebSocket 连接建立
        time.sleep(0.5)

        # 发送音频数据（需要 base64 编码，按照 WebSocket 消息格式）
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")

        # 分块发送音频数据（模拟流式发送）
        chunk_size = 3200  # 每块约 100ms 音频（16000Hz PCM）
        offset = 0
        while offset < len(audio_b64):
            chunk = audio_b64[offset:offset + chunk_size]
            
            data_message = {
                "header": {
                    "namespace": "SpeechRecognizer",
                    "name": "SendAudio",
                    "message_id": str(int(time.time_ns())),
                    "task_id": f"task-{int(time.time_ns())}",
                    "status": 20000000,
                    "status_text": "Gateway:Success:Success."
                },
                "payload": {
                    "audio": chunk,
                    "status": 1  # 1=中间数据
                }
            }
            ws_app.send(json.dumps(data_message))
            offset += chunk_size
            time.sleep(0.04)  # 模拟实时发送间隔

        # 发送最后一帧
        if offset >= len(audio_b64):
            end_message = {
                "header": {
                    "namespace": "SpeechRecognizer",
                    "name": "SendAudio",
                    "message_id": str(int(time.time_ns())),
                    "task_id": f"task-{int(time.time_ns())}",
                    "status": 20000000,
                    "status_text": "Gateway:Success:Success."
                },
                "payload": {
                    "audio": "",
                    "status": 2  # 2=最后一帧，结束识别
                }
            }
            ws_app.send(json.dumps(end_message))

        # 等待识别完成
        recognition_completed.wait(timeout=30)

        # 结束计时，计算指标
        self.total_time = time.perf_counter() - self._asr_start
        self.ttft = self._first_result_time
        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        return self.result_text
