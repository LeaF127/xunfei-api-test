"""
豆包语音 一句话识别 (ASR) — WebSocket 二进制协议

接口说明:
  协议:  WebSocket
  地址:  wss://openspeech.bytedance.com/api/v2/asr
  音频:  采样率 16kHz, 16bit, 单声道
  格式:  wav / pcm / mp3 / ogg / speex
  时长:  最长 60s
  鉴权:  HMAC256 签名

协议流程:
  1. 建立 WebSocket 连接（带鉴权 Header）
  2. 发送 full_client_request（JSON 配置, message_type=1, sequence=1）
  3. 接收 server ack
  4. 发送 audio_only_request（音频数据, message_type=2, 最后一包 flags=0b0010）
  5. 接收 full_server_response（识别结果）
"""

import gzip
import json
import struct
import time
import uuid

import websocket

from providers.base import BaseASR
from providers.doubao.auth import build_asr_auth_header


class DoubaoASR(BaseASR):
    """
    豆包语音 — 一句话识别 (ASR)

    使用 WebSocket 二进制协议，按官方文档流程发送请求。
    """

    WS_URL = "wss://openspeech.bytedance.com/api/v2/asr"

    def __init__(self, appid: str, access_token: str, secret_key: str, cluster: str = "volcengine_asr"):
        super().__init__()
        self.appid = appid
        self.access_token = access_token
        self.secret_key = secret_key
        self.cluster = cluster

    # ==================== 二进制协议工具 ====================

    @staticmethod
    def _build_header(message_type: int, message_type_specific: int = 0,
                      serialization: int = 1, compression: int = 0) -> bytes:
        """
        构造 4 字节二进制协议 header

        Byte 0: [protocol_version=0b0001] [header_size=0b0001(4字节)]
        Byte 1: [message_type(4bit)] [message_type_specific_flags(4bit)]
        Byte 2: [serialization(4bit)] [compression(4bit)]
        Byte 3: reserved = 0x00
        """
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

        Returns:
            {"msg_type": int, "payload": dict|bytes}
        """
        if len(data) < 4:
            return {"msg_type": -1, "payload": {}}

        msg_type = (data[1] >> 4) & 0x0F
        serialization = (data[2] >> 4) & 0x0F
        compression = (data[2]) & 0x0F

        if msg_type == 0x0F:
            # Error message from server: header(4) + error_code(4) + error_size(4) + error_msg
            if len(data) >= 12:
                error_code = struct.unpack(">I", data[4:8])[0]
                error_size = struct.unpack(">I", data[8:12])[0]
                error_msg = data[12:12 + error_size].decode("utf-8", errors="replace")
                return {"msg_type": msg_type, "payload": {"code": error_code, "message": error_msg}}
            return {"msg_type": msg_type, "payload": {"code": -1, "message": "unknown error"}}

        if len(data) < 8:
            return {"msg_type": msg_type, "payload": {}}

        payload_size = struct.unpack(">I", data[4:8])[0]
        payload_raw = data[8:8 + payload_size]

        # 解压
        if compression == 1 and payload_raw:
            payload_raw = gzip.decompress(payload_raw)

        # 反序列化
        if serialization == 1 and payload_raw:
            try:
                payload = json.loads(payload_raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {"raw": payload_raw}
        else:
            payload = {"raw_size": len(payload_raw)}

        return {"msg_type": msg_type, "payload": payload}

    # ==================== 帧构造 ====================

    def _build_config_frame(self, audio_format: str = "wav",
                            sample_rate: int = 16000,
                            bits: int = 16,
                            channel: int = 1,
                            sequence: int = 1,
                            use_gzip: bool = True) -> bytes:
        """
        构造 full_client_request 帧（JSON 配置，不含音频）

        message_type = 1 (full_client_request)
        message_type_specific = 0
        """
        reqid = str(uuid.uuid4())

        config = {
            "app": {
                "appid": self.appid,
                "token": "access_token",  # 必填，填任意非空值即可
                "cluster": self.cluster,
            },
            "user": {
                "uid": "benchmark_test",
            },
            "audio": {
                "format": audio_format,
                "rate": sample_rate,
                "bits": bits,
                "channel": channel,
            },
            "request": {
                "reqid": reqid,
                "sequence": sequence,
                "nbest": 1,
                "workflow": "audio_in,resample,partition,vad,fe,decode",
            },
        }

        payload_bytes = json.dumps(config, ensure_ascii=False).encode("utf-8")

        if use_gzip:
            payload_bytes = gzip.compress(payload_bytes)
            header = self._build_header(
                message_type=1, message_type_specific=0,
                serialization=1, compression=1,
            )
        else:
            header = self._build_header(
                message_type=1, message_type_specific=0,
                serialization=1, compression=0,
            )

        payload_size = struct.pack(">I", len(payload_bytes))
        return header + payload_size + payload_bytes

    def _build_audio_frame(self, audio_data: bytes,
                           is_last: bool = True,
                           use_gzip: bool = True) -> bytes:
        """
        构造 audio_only_request 帧

        message_type = 2 (audio_only)
        message_type_specific = 0b0000 (非最后一包) 或 0b0010 (最后一包)
        serialization = 0 (raw bytes)
        """
        if use_gzip:
            compressed = gzip.compress(audio_data)
            header = self._build_header(
                message_type=2,
                message_type_specific=0b0010 if is_last else 0b0000,
                serialization=0, compression=1,
            )
        else:
            compressed = audio_data
            header = self._build_header(
                message_type=2,
                message_type_specific=0b0010 if is_last else 0b0000,
                serialization=0, compression=0,
            )

        payload_size = struct.pack(">I", len(compressed))
        return header + payload_size + compressed

    # ==================== 识别主逻辑 ====================

    def recognize(self, audio_data, audio_format: str = "wav",
                  sample_rate: int = 16000, **kwargs) -> str:
        """
        一句话识别

        Args:
            audio_data: 文件路径 (str) 或音频二进制 (bytes)
            audio_format: 音频格式 (wav/pcm/mp3/ogg)
            sample_rate: 采样率
        Returns:
            识别文本
        """
        self.ttft = None
        self.total_time = None
        self.rtf = None

        # 读取音频
        if isinstance(audio_data, str):
            with open(audio_data, "rb") as f:
                audio = f.read()
        elif isinstance(audio_data, bytes):
            audio = audio_data
        else:
            raise TypeError("audio_data 必须是文件路径(str)或音频数据(bytes)")

        audio_duration = len(audio) / (sample_rate * 2)

        # 构建鉴权 header
        auth_headers = build_asr_auth_header(
            access_token=self.access_token,
            secret_key=self.secret_key,
            method="GET",
            path="/api/v2/asr",
            host="openspeech.bytedance.com",
        )

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
                # ---- Step 1: 发送 full_client_request (JSON 配置) ----
                config_frame = self._build_config_frame(
                    audio_format=audio_format,
                    sample_rate=sample_rate,
                    sequence=-1,  # 一句话识别，只有一包
                    use_gzip=True,
                )
                ws.send_binary(config_frame)

                # ---- Step 2: 接收 server ack ----
                ack_data = ws.recv()
                if isinstance(ack_data, bytes):
                    ack = self._parse_response(ack_data)
                    if ack["msg_type"] == 0x0F:
                        raise RuntimeError(f"ASR 配置阶段服务端错误: {ack['payload']}")
                    ack_payload = ack.get("payload", {})
                    if isinstance(ack_payload, dict) and ack_payload.get("code", 1000) != 1000:
                        raise RuntimeError(f"ASR 配置阶段错误: code={ack_payload.get('code')}, "
                                           f"message={ack_payload.get('message')}")
                    self.ttft = time.perf_counter() - start

                # ---- Step 3: 发送 audio_only_request (音频数据, 最后一包) ----
                audio_frame = self._build_audio_frame(audio, is_last=True, use_gzip=True)
                ws.send_binary(audio_frame)

                # ---- Step 4: 接收识别结果 ----
                result_text = ""
                while True:
                    try:
                        resp_data = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        break

                    if not isinstance(resp_data, bytes):
                        continue

                    resp = self._parse_response(resp_data)
                    msg_type = resp["msg_type"]
                    payload = resp["payload"]

                    if msg_type == 0x0F:
                        raise RuntimeError(f"ASR 识别阶段服务端错误: {payload}")

                    if msg_type == 0x09:
                        # full_server_response
                        if isinstance(payload, dict):
                            code = payload.get("code", -1)
                            if code != 1000:
                                raise RuntimeError(
                                    f"ASR 错误: code={code}, message={payload.get('message', '')}"
                                )
                            # 提取识别文本
                            results = payload.get("result", [])
                            for item in results:
                                if "text" in item:
                                    result_text = item["text"]

                            # sequence 为负数表示最后一包结果
                            seq = payload.get("sequence", 0)
                            if seq < 0:
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
