"""讯飞在线语音听写 (ASR)"""

import base64
import json
import re
import time
import threading

import websocket

from providers.base import BaseASR, Metrics
from providers.xunfei.auth import create_auth_url
from utils.audio import get_audio_duration


class XunFeiASR(BaseASR):
    """
    讯飞在线语音听写（流式版）WebAPI

    接口要求:
      协议:    wss
      地址:    wss://iat-api.xfyun.cn/v2/iat
      音频:    采样率 16k/8k, 16bit, 单声道
      格式:    pcm / speex / speex-wb / mp3(仅中英文)
      时长:    最长 60s
    """

    BASE_URL = "wss://iat-api.xfyun.cn/v2/iat"

    def __init__(self, app_id: str, api_key: str, api_secret: str):
        super().__init__()
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.result_text = ""
        self._asr_start = None
        self._first_result_time = None

    def _build_first_frame(self, audio_format="audio/L16;rate=16000",
                           encoding="raw", language="zh_cn",
                           domain="iat", accent="mandarin"):
        """构建首帧请求（含 common + business + data）"""
        return {
            "common": {"app_id": self.app_id},
            "business": {
                "language": language,
                "domain": domain,
                "accent": accent,
            },
            "data": {
                "status": 0,
                "format": audio_format,
                "encoding": encoding,
                "audio": "",
            },
        }

    def _build_audio_frame(self, audio_base64: str, status: int,
                           audio_format="audio/L16;rate=16000", encoding="raw"):
        """构建中间帧 / 最后一帧 (status: 1=中间帧, 2=最后一帧)"""
        return {
            "data": {
                "status": status,
                "format": audio_format,
                "encoding": encoding,
                "audio": audio_base64,
            }
        }

    def recognize(self, audio_data, audio_format="audio/L16;rate=16000",
                  encoding="raw") -> str:
        """
        同步识别音频文件

        Args:
            audio_data: 文件路径 (str) 或音频二进制 (bytes)
            audio_format: 音频采样率格式
            encoding: 音频编码 raw=pcm, lame=mp3
        Returns:
            识别文本结果
        """
        self.result_text = ""
        self.ttft = None
        self.total_time = None
        self.rtf = None
        self._first_result_time = None

        url = create_auth_url(self.BASE_URL, self.api_key, self.api_secret)

        if isinstance(audio_data, str):
            with open(audio_data, "rb") as f:
                audio_bytes = f.read()
        elif isinstance(audio_data, bytes):
            audio_bytes = audio_data
        else:
            raise TypeError("audio_data 必须是文件路径(str)或音频数据(bytes)")

        sr_match = re.search(r"rate=(\d+)", audio_format)
        sample_rate = int(sr_match.group(1)) if sr_match else 16000
        audio_duration = get_audio_duration(audio_bytes, encoding, sample_rate)

        frame_size = 1280
        self._asr_start = time.perf_counter()

        ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: self._on_open(ws, audio_bytes, frame_size, audio_format, encoding),
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.run_forever()

        self.total_time = time.perf_counter() - self._asr_start
        self.ttft = self._first_result_time
        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        m = Metrics(ttft=self.ttft, total_time=self.total_time, rtf=self.rtf)
        self.last_metrics = m
        return self.result_text, m

    def _on_open(self, ws, audio_data, frame_size, audio_format, encoding):
        """连接建立后发送音频数据"""
        def send():
            first = self._build_first_frame(audio_format, encoding)
            ws.send(json.dumps(first))

            offset = 0
            while offset < len(audio_data):
                chunk = audio_data[offset: offset + frame_size]
                b64 = base64.b64encode(chunk).decode("utf-8")
                is_last = (offset + frame_size >= len(audio_data))
                frame = self._build_audio_frame(b64, status=2 if is_last else 1,
                                                audio_format=audio_format, encoding=encoding)
                ws.send(json.dumps(frame))
                offset += frame_size
                time.sleep(0.04)
            time.sleep(1)

        threading.Thread(target=send, daemon=True).start()

    def _on_message(self, ws, message):
        """处理返回的识别结果"""
        resp = json.loads(message)
        code = resp.get("code", -1)
        if code != 0:
            print(f"[ASR Error] code={code}, message={resp.get('message', '')}")
            ws.close()
            return

        data = resp.get("data", {})
        result = data.get("result", {})
        ws_list = result.get("ws", [])
        for w in ws_list:
            for cw in w.get("cw", []):
                word = cw.get("w", "")
                if word:
                    if self._first_result_time is None:
                        self._first_result_time = time.perf_counter() - self._asr_start
                    self.result_text += word

        if data.get("status") == 2:
            ws.close()

    def _on_error(self, ws, error):
        print(f"[ASR WebSocket Error] {error}")

    def _on_close(self, ws, close_status, close_msg):
        pass
