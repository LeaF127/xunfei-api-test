"""
豆包语音 一句话识别 (ASR) — WebSocket 二进制协议

接口说明:
  协议:  WebSocket
  地址:  wss://openspeech.bytedance.com/api/v2/asr
  音频:  采样率 16kHz, 16bit, 单声道
  格式:  wav / pcm / mp3 / ogg / speex
  时长:  最长 60s
  鉴权:  HMAC256 签名
"""

import gzip
import json
import struct
import time
import uuid

import websocket

from providers.base import BaseASR
from providers.doubao.auth import build_asr_auth_header, build_tts_headers_bearer


class DoubaoASR(BaseASR):
    """豆包语音 — 一句话识别 (ASR)"""

    WS_URL = "wss://openspeech.bytedance.com/api/v2/asr"

    def __init__(self, appid: str, access_token: str, secret_key: str, cluster: str = "volcengine_asr"):
        super().__init__()
        self.appid = appid
        self.access_token = access_token
        self.secret_key = secret_key
        self.cluster = cluster

    # ==================== 二进制协议 ====================

    @staticmethod
    def _build_header(message_type: int, message_type_specific: int = 0,
                      serialization: int = 1, compression: int = 0) -> bytes:
        return bytes([
            0x11,
            (message_type << 4) | (message_type_specific & 0x0F),
            (serialization << 4) | (compression & 0x0F),
            0x00,
        ])

    @staticmethod
    def _parse_response(data: bytes) -> dict:
        """
        解析服务端响应帧

        两种格式:
          msg_type != 0x0F (正常响应):
            header(4) + payload_size(4) + payload(可能 gzip 压缩 + JSON 序列化)
          msg_type == 0x0F (错误响应):
            header(4) + error_code(4) + error_size(4) + error_message(UTF8)
        """
        if len(data) < 4:
            return {"msg_type": -1, "payload": {}}

        msg_type = (data[1] >> 4) & 0x0F
        serialization = (data[2] >> 4) & 0x0F
        compression = (data[2]) & 0x0F

        # ===== 错误消息: header(4) + error_code(4) + error_size(4) + error_msg =====
        if msg_type == 0x0F:
            if len(data) < 12:
                return {"msg_type": msg_type, "payload": {"code": -1, "message": f"raw: {data.hex()}"}}
            error_code = struct.unpack(">I", data[4:8])[0]
            error_size = struct.unpack(">I", data[8:12])[0]
            error_msg = data[12:12 + error_size].decode("utf-8", errors="replace")
            return {"msg_type": msg_type, "payload": {"code": error_code, "message": error_msg}}

        # ===== 正常响应: header(4) + payload_size(4) + payload =====
        if len(data) < 8:
            return {"msg_type": msg_type, "payload": {}}

        payload_size = struct.unpack(">I", data[4:8])[0]
        payload_raw = data[8:8 + payload_size]

        # 解压
        if compression == 1 and payload_raw:
            try:
                payload_raw = gzip.decompress(payload_raw)
            except Exception as e:
                print(f"[ASR debug] gzip解压失败: {e}, raw_len={len(payload_raw)}")
                return {"msg_type": msg_type, "payload": {}}

        # 反序列化
        if serialization == 1 and payload_raw:
            try:
                payload = json.loads(payload_raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"[ASR debug] JSON解析失败: {e}")
                return {"msg_type": msg_type, "payload": {}}
        elif payload_raw:
            payload = {"_raw_size": len(payload_raw)}
        else:
            payload = {}

        return {"msg_type": msg_type, "payload": payload}

    # ==================== 帧构造 ====================

    def _build_config_frame(self, audio_format="wav", sample_rate=16000,
                            bits=16, channel=1, use_gzip=False):
        config = {
            "app": {
                "appid": self.appid,
                "token": self.access_token,
                "cluster": self.cluster,
            },
            "user": {"uid": "benchmark_test"},
            "audio": {
                "format": audio_format,
                "rate": sample_rate,
                "bits": bits,
                "channel": channel,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "sequence": 1,
                "nbest": 1,
                "workflow": "audio_in,resample,partition,vad,fe,decode",
            },
        }
        payload = json.dumps(config, ensure_ascii=False).encode("utf-8")
        if use_gzip:
            payload = gzip.compress(payload)
            header = self._build_header(1, 0, 1, 1)
        else:
            header = self._build_header(1, 0, 1, 0)
        return header + struct.pack(">I", len(payload)) + payload

    def _build_audio_frame(self, audio_data, is_last=True, use_gzip=True):
        if use_gzip:
            compressed = gzip.compress(audio_data)
            header = self._build_header(2, 0b0010 if is_last else 0, 0, 1)
        else:
            compressed = audio_data
            header = self._build_header(2, 0b0010 if is_last else 0, 0, 0)
        return header + struct.pack(">I", len(compressed)) + compressed

    # ==================== 识别 ====================

    def recognize(self, audio_data, audio_format="wav", sample_rate=16000, **kwargs):
        self.ttft = None
        self.total_time = None
        self.rtf = None

        if isinstance(audio_data, str):
            with open(audio_data, "rb") as f:
                audio = f.read()
        elif isinstance(audio_data, bytes):
            audio = audio_data
        else:
            raise TypeError("audio_data 必须是文件路径(str)或音频数据(bytes)")

        audio_duration = len(audio) / (sample_rate * 2)

        # auth_headers = build_asr_auth_header(
        #     access_token=self.access_token,
        #     secret_key=self.secret_key,
        #     method="GET", path="/api/v2/asr",
        #     host="openspeech.bytedance.com",
        # )
        auth_headers = build_tts_headers_bearer(self.access_token)

        start = time.perf_counter()

        try:
            ws = websocket.WebSocket()
            ws.connect(
                self.WS_URL,
                header=[f"{k}: {v}" for k, v in auth_headers.items()],
                max_size=10 * 1024 * 1024,
                timeout=30,
            )

            try:
                # Step 1: 发送 config
                ws.send_binary(self._build_config_frame(
                    audio_format=audio_format, sample_rate=sample_rate))

                # Step 2: 接收 ack
                ack_data = ws.recv()
                if isinstance(ack_data, bytes) and len(ack_data) >= 4:
                    ack = self._parse_response(ack_data)
                    p = ack.get("payload", {})
                    if isinstance(p, dict) and p.get("code", 1000) != 1000:
                        raise RuntimeError(
                            f"ASR 配置阶段错误: code={p.get('code')}, "
                            f"message={p.get('message', '')}")
                    self.ttft = time.perf_counter() - start

                # Step 3: 发送音频
                ws.send_binary(self._build_audio_frame(audio, is_last=True))

                # Step 4: 接收结果
                result_text = ""
                while True:
                    try:
                        resp_data = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        break
                    if not isinstance(resp_data, bytes):
                        continue

                    resp = self._parse_response(resp_data)
                    p = resp.get("payload", {})
                    if not isinstance(p, dict) or not p:
                        continue

                    code = p.get("code")
                    if code is not None and code != 1000:
                        raise RuntimeError(
                            f"ASR 错误: code={code}, message={p.get('message', '')}")

                    for item in p.get("result", []):
                        if "text" in item:
                            result_text = item["text"]

                    if p.get("sequence", 0) < 0:
                        break

                self.total_time = time.perf_counter() - start
                if self.ttft is None:
                    self.ttft = self.total_time

            finally:
                ws.close()

        except Exception as e:
            raise RuntimeError(f"豆包 ASR 调用失败: {e}")

        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        return result_text
