"""
阿里云语音识别（ASR）- WebSocket 版

文档: https://help.aliyun.com/zh/isi/developer-reference/api-reference-1

交互流程:
  1. 建立 WebSocket 连接（wss://nls-gateway-ap-southeast-1.aliyuncs.com/ws/v1）
     握手时带 X-NLS-Token header
  2. 发送 StartRecognition 消息（含 appkey、format、sample_rate 等）
  3. 发送 SendAudio 消息（分块发送音频，base64 编码）
  4. 发送最后一帧（status=2）
  5. 接收 RecognitionCompleted 消息（含最终识别结果）
"""

import base64
import json
import time
import websocket

from providers.base import BaseASR
from providers.aliyun.auth import AliyunAuth

WS_URL = "wss://nls-gateway-ap-southeast-1.aliyuncs.com/ws/v1"


class AliASR(BaseASR):
    """
    阿里云一句话识别（WebSocket 版）
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
        """WebSocket 一句话识别"""
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

        start_time = time.perf_counter()
        first_result_time = None

        # 建立 WebSocket 连接（带 X-NLS-Token header 鉴权）
        ws = websocket.create_connection(WS_URL, header={"X-NLS-Token": token})

        try:
            # 1. 发送 StartRecognition（必须含 appkey）
            ws.send(json.dumps({
                "header": {
                    "namespace": "SpeechRecognizer",
                    "name": "StartRecognition",
                    "appkey": self.app_key,
                    "message_id": str(int(time.time_ns())),
                    "status": 20000000,
                    "status_text": "Gateway:Success:Success."
                },
                "payload": {
                    "format": audio_format.upper(),
                    "sample_rate": sample_rate,
                    "enable_intermediate_result": False,
                    "enable_punctuation_prediction": enable_punctuation,
                    "enable_inverse_text_normalization": enable_inverse_text_normalization,
                }
            }))

            # 等待服务端确认
            resp = json.loads(ws.recv())
            hdr = resp.get("header", {})
            if hdr.get("name") == "TaskFailed":
                raise RuntimeError(f"StartRecognition 失败: {resp}")

            # 2. 分块发送音频（base64）
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            chunk_size = 3200
            offset = 0
            task_id = hdr.get("task_id", "")

            while offset < len(audio_b64):
                is_last = (offset + chunk_size >= len(audio_b64))
                chunk = audio_b64[offset:offset + chunk_size]

                ws.send(json.dumps({
                    "header": {
                        "namespace": "SpeechRecognizer",
                        "name": "SendAudio",
                        "appkey": self.app_key,
                        "task_id": task_id,
                        "message_id": str(int(time.time_ns())),
                        "status": 20000000,
                        "status_text": "Gateway:Success:Success."
                    },
                    "payload": {
                        "audio": chunk,
                        "status": 2 if is_last else 1
                    }
                }))
                offset += chunk_size
                if not is_last:
                    time.sleep(0.04)

            # 3. 等待最终识别结果
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

        self.total_time = time.perf_counter() - start_time
        self.ttft = first_result_time
        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        return self.result_text
