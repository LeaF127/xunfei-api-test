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

    def recognize(self, audio_file_path: str,
                  audio_format: str = "pcm",
                  sample_rate: int = 16000,
                  enable_punctuation: bool = False,
                  enable_inverse_text_normalization: bool = True) -> str:
        """
        WebSocket 一句话识别
        """
        self.result_text = ""
        self.ttft = None
        self.total_time = None
        self.rtf = None

        # 读取音频文件
        if isinstance(audio_file_path, str):
            with open(audio_file_path, "rb") as f:
                audio_data = f.read()
        elif isinstance(audio_file_path, bytes):
            audio_data = audio_file_path
        else:
            raise TypeError("audio_file_path 必须是文件路径(str)或音频数据(bytes)")

        # 计算音频时长
        audio_duration = len(audio_data) / (sample_rate * 2) if audio_format == "pcm" else 0

        token = self.auth.get_token()

        # 开始计时
        start_time = time.perf_counter()
        first_result_time = None

        # 建立 WebSocket 连接（同步模式，带 X-NLS-Token header）
        ws = websocket.create_connection(
            WS_URL,
            header={"X-NLS-Token": token},
        )

        try:
            # 1. 发送开始识别指令
            start_msg = {
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
            ws.send(json.dumps(start_msg))

            # 等待 StartRecognition 响应
            resp = json.loads(ws.recv())
            header = resp.get("header", {})
            if header.get("status", 0) != 20000000:
                raise RuntimeError(f"StartRecognition 失败: {resp}")

            # 2. 分块发送音频数据
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            chunk_size = 3200
            offset = 0
            task_id = header.get("task_id", "")

            while offset < len(audio_b64):
                chunk = audio_b64[offset:offset + chunk_size]
                is_last = (offset + chunk_size >= len(audio_b64))

                data_msg = {
                    "header": {
                        "namespace": "SpeechRecognizer",
                        "name": "SendAudio",
                        "message_id": str(int(time.time_ns())),
                        "task_id": task_id,
                        "status": 20000000,
                        "status_text": "Gateway:Success:Success."
                    },
                    "payload": {
                        "audio": chunk,
                        "status": 2 if is_last else 1
                    }
                }
                ws.send(json.dumps(data_msg))
                offset += chunk_size

                # 非最后一帧时，短暂间隔 + 检查是否有中间结果
                if not is_last:
                    time.sleep(0.04)
                    # 非阻塞检查中间结果
                    ws.settimeout(0.01)
                    try:
                        while True:
                            msg = ws.recv()
                            data = json.loads(msg)
                            name = data.get("header", {}).get("name", "")
                            if name == "RecognitionResultChanged" and first_result_time is None:
                                first_result_time = time.perf_counter() - start_time
                            if name == "RecognitionCompleted":
                                self.result_text = data.get("payload", {}).get("result", "")
                                if first_result_time is None:
                                    first_result_time = time.perf_counter() - start_time
                                self._finish(start_time, first_result_time, audio_duration)
                                return self.result_text
                    except websocket.WebSocketTimeoutException:
                        pass
                    ws.settimeout(30)

            # 3. 等待最终结果
            ws.settimeout(30)
            while True:
                msg = ws.recv()
                data = json.loads(msg)
                name = data.get("header", {}).get("name", "")

                if name == "RecognitionResultChanged" and first_result_time is None:
                    first_result_time = time.perf_counter() - start_time

                if name == "RecognitionCompleted":
                    self.result_text = data.get("payload", {}).get("result", "")
                    if first_result_time is None:
                        first_result_time = time.perf_counter() - start_time
                    break

                if name == "TaskFailed":
                    raise RuntimeError(f"ASR 失败: {data}")

        finally:
            ws.close()

        self._finish(start_time, first_result_time, audio_duration)
        return self.result_text

    def _finish(self, start_time, first_result_time, audio_duration):
        self.total_time = time.perf_counter() - start_time
        self.ttft = first_result_time
        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration
