"""讯飞在线语音合成 (TTS)"""

import base64
import json
import re
import time

import websocket

from providers.base import BaseTTS, Metrics
from providers.xunfei.auth import create_auth_url
from utils.audio import estimate_mp3_duration, get_audio_duration


class XunFeiTTS(BaseTTS):
    """
    讯飞在线语音合成（流式版）WebAPI

    接口要求:
      协议:    wss
      地址:    wss://tts-api.xfyun.cn/v2/tts
      音频:    采样率 16k/8k
      格式:    pcm / mp3 / opus / speex
      文本:    单次 < 8000 字节 (约 2000 汉字)
    """

    BASE_URL = "wss://tts-api.xfyun.cn/v2/tts"

    def __init__(self, app_id: str, api_key: str, api_secret: str):
        super().__init__()
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.audio_data = b""
        self._tts_start = None
        self._first_audio_time = None

    def synthesize(self, text: str,
                   vcn: str = "xiaoyan",
                   speed: int = 50,
                   volume: int = 50,
                   pitch: int = 50,
                   aue: str = "lame",
                   auf: str = "audio/L16;rate=16000",
                   tte: str = "UTF8",
                   output_file: str = None) -> bytes:
        """
        同步合成语音

        Args:
            text:        要合成的文本
            vcn:         发音人
            speed:       语速 [0-100], 默认50
            volume:      音量 [0-100], 默认50
            pitch:       音高 [0-100], 默认50
            aue:         音频编码 raw=pcm, lame=mp3
            auf:         采样率
            tte:         文本编码
            output_file: 输出文件路径
        Returns:
            合成的音频二进制数据
        """
        self.audio_data = b""
        self.ttft = None
        self.total_time = None
        self.rtf = None
        self._first_audio_time = None

        url = create_auth_url(self.BASE_URL, self.api_key, self.api_secret)

        text_b64 = base64.b64encode(text.encode("utf-8")).decode("utf-8")

        request_body = {
            "common": {"app_id": self.app_id},
            "business": {
                "aue": aue,
                "sfl": 1,
                "auf": auf,
                "vcn": vcn,
                "speed": speed,
                "volume": volume,
                "pitch": pitch,
                "tte": tte,
            },
            "data": {
                "status": 2,
                "text": text_b64,
            },
        }

        self._request_body = json.dumps(request_body)
        self._aue = aue
        self._auf = auf
        self._output_file = output_file

        self._tts_start = time.perf_counter()

        ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.run_forever()

        self.total_time = time.perf_counter() - self._tts_start
        self.ttft = self._first_audio_time

        if self.audio_data:
            if self._aue == "lame":
                audio_duration = estimate_mp3_duration(self.audio_data)
            elif self._aue == "raw":
                sr_match = re.search(r"rate=(\d+)", self._auf)
                sr = int(sr_match.group(1)) if sr_match else 16000
                audio_duration = len(self.audio_data) / (sr * 2)
            else:
                audio_duration = 0.0
            if audio_duration > 0:
                self.rtf = self.total_time / audio_duration

        if self.audio_data and output_file:
            with open(output_file, "wb") as f:
                f.write(self.audio_data)
            print(f"[TTS] 音频已保存: {output_file} ({len(self.audio_data)} bytes)")

        m = Metrics(ttft=self.ttft, total_time=self.total_time, rtf=self.rtf)
        self.last_metrics = m
        return self.audio_data, m

    def _on_open(self, ws):
        ws.send(self._request_body)

    def _on_message(self, ws, message):
        resp = json.loads(message)
        code = resp.get("code", -1)
        if code != 0:
            print(f"[TTS Error] code={code}, message={resp.get('message', '')}")
            ws.close()
            return

        data = resp.get("data")
        if data is None:
            return

        audio_b64 = data.get("audio", "")
        if audio_b64:
            if self._first_audio_time is None:
                self._first_audio_time = time.perf_counter() - self._tts_start
            self.audio_data += base64.b64decode(audio_b64)

        if data.get("status") == 2:
            ws.close()

    def _on_error(self, ws, error):
        print(f"[TTS WebSocket Error] {error}")

    def _on_close(self, ws, close_status, close_msg):
        pass
