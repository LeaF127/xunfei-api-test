"""
豆包语音 大模型流式语音识别 (ASR) — WebSocket V3 二进制协议

接口说明:
  协议:  WebSocket
  地址:  wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
  鉴权:  X-Api-Key + X-Api-Resource-Id (Header)
  音频:  采样率 16kHz, 16bit, 单声道
  格式:  wav / pcm / ogg / mp3
  分包:  建议每包 200ms, 间隔 100~200ms
  文档:  https://www.volcengine.com/docs/6561/1354869

协议流程:
  1. 建立 WebSocket 连接（鉴权 Header）
  2. 发送 full_client_request (JSON 配置)
  3. 按包发送 audio_only_request (200ms/包), 最后一包 flags=0b0010
  4. 接收 full_server_response (含 sequence), 取最终结果

V3 帧格式 (与 V2 区别):
  请求: header(4) + payload_size(4) + payload
  响应: header(4) + sequence(4) + payload_size(4) + payload  ← 多了 sequence
"""

import gzip
import json
import struct
import time
import uuid

import websocket

from providers.base import BaseASR, Metrics


class DoubaoBigModelASR(BaseASR):
    """豆包语音 — 大模型流式语音识别"""

    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"

    def __init__(self, api_key: str, resource_id: str):
        super().__init__()
        self.api_key = api_key
        self.resource_id = resource_id

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
        解析 V3 服务端响应帧

        格式: header(4) + sequence(4) + payload_size(4) + payload
        错误帧: header(4) + error_code(4) + error_size(4) + error_msg
        """
        if len(data) < 4:
            return {"msg_type": -1, "msg_specific": 0, "sequence": 0, "payload": {}}

        msg_type = (data[1] >> 4) & 0x0F
        msg_specific = data[1] & 0x0F
        serialization = (data[2] >> 4) & 0x0F
        compression = (data[2]) & 0x0F

        # ===== 错误帧: header(4) + error_code(4) + error_size(4) + error_msg =====
        if msg_type == 0x0F:
            if len(data) < 12:
                return {"msg_type": msg_type, "msg_specific": msg_specific,
                        "sequence": 0, "payload": {"code": -1, "message": f"raw: {data.hex()}"}}
            error_code = struct.unpack(">I", data[4:8])[0]
            error_size = struct.unpack(">I", data[8:12])[0]
            error_msg = data[12:12 + error_size].decode("utf-8", errors="replace")
            return {"msg_type": msg_type, "msg_specific": msg_specific,
                    "sequence": 0, "payload": {"code": error_code, "message": error_msg}}

        # ===== 正常帧: header(4) + sequence(4) + payload_size(4) + payload =====
        if len(data) < 12:
            return {"msg_type": msg_type, "msg_specific": msg_specific,
                    "sequence": 0, "payload": {}}

        sequence = struct.unpack(">i", data[4:8])[0]  # signed int32
        payload_size = struct.unpack(">I", data[8:12])[0]
        payload_raw = data[12:12 + payload_size]

        # 解压
        if compression == 1 and payload_raw:
            try:
                payload_raw = gzip.decompress(payload_raw)
            except Exception as e:
                print(f"[BigModel ASR] gzip解压失败: {e}")
                return {"msg_type": msg_type, "msg_specific": msg_specific,
                        "sequence": sequence, "payload": {}}

        # 反序列化
        if serialization == 1 and payload_raw:
            try:
                payload = json.loads(payload_raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
        else:
            payload = {}

        return {"msg_type": msg_type, "msg_specific": msg_specific,
                "sequence": sequence, "payload": payload}

    # ==================== 帧构造 ====================

    def _build_config_frame(self, audio_format="wav", sample_rate=16000,
                            bits=16, channel=1, use_gzip=False) -> bytes:
        """构造 full_client_request (JSON 配置)"""
        config = {
            "user": {"uid": "benchmark_test"},
            "audio": {
                "format": audio_format,
                "rate": sample_rate,
                "bits": bits,
                "channel": channel,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "result_type": "full",
            },
        }
        payload = json.dumps(config, ensure_ascii=False).encode("utf-8")
        if use_gzip:
            payload = gzip.compress(payload)
            header = self._build_header(1, 0, 1, 1)
        else:
            header = self._build_header(1, 0, 1, 0)
        return header + struct.pack(">I", len(payload)) + payload

    @staticmethod
    def _build_audio_frame(audio_data: bytes, seq: int, is_last: bool = False,
                           use_gzip: bool = True) -> bytes:
        """
        构造 audio_only_request

        msg_specific: 0b0001=正数sequence, 0b0010=最后一包(负包)
        """
        if use_gzip:
            compressed = gzip.compress(audio_data)
            if is_last:
                header = DoubaoBigModelASR._build_header(2, 0b0010, 0, 1)
            else:
                header = DoubaoBigModelASR._build_header(2, 0b0001, 0, 1)
        else:
            compressed = audio_data
            if is_last:
                header = DoubaoBigModelASR._build_header(2, 0b0010, 0, 0)
            else:
                header = DoubaoBigModelASR._build_header(2, 0b0001, 0, 0)

        # sequence (signed int32, 最后一包为负数)
        seq_bytes = struct.pack(">i", -seq if is_last else seq)
        return header + seq_bytes + struct.pack(">I", len(compressed)) + compressed

    # ==================== 识别 ====================

    def recognize(self, audio_data, audio_format="wav", sample_rate=16000, **kwargs):
        self.last_metrics = Metrics()

        if isinstance(audio_data, str):
            with open(audio_data, "rb") as f:
                audio = f.read()
        elif isinstance(audio_data, bytes):
            audio = audio_data
        else:
            raise TypeError("audio_data 必须是文件路径(str)或音频数据(bytes)")

        audio_duration = len(audio) / (sample_rate * 2)

        # 鉴权 Header
        ws_headers = [
            f"X-Api-Key: {self.api_key}",
            f"X-Api-Resource-Id: {self.resource_id}",
            f"X-Api-Request-Id: {uuid.uuid4()}",
            "X-Api-Sequence: -1",
        ]

        start = time.perf_counter()

        try:
            ws = websocket.WebSocket()
            ws.connect(
                self.WS_URL,
                header=ws_headers,
                max_size=10 * 1024 * 1024,
                timeout=60,
            )

            try:
                # ---- Step 1: 发送 config ----
                ws.send_binary(self._build_config_frame(
                    audio_format=audio_format, sample_rate=sample_rate))

                # ---- Step 2: 接收 config ack ----
                ack_data = ws.recv()
                if isinstance(ack_data, bytes) and len(ack_data) >= 4:
                    ack = self._parse_response(ack_data)
                    p = ack.get("payload", {})
                    if isinstance(p, dict) and p.get("code") is not None:
                        if p["code"] not in (0, 20000000):
                            raise RuntimeError(
                                f"BigModel ASR 配置阶段错误: code={p['code']}, "
                                f"message={p.get('message', '')}")

                # ---- Step 3: 分包发送音频 (200ms/包) ----
                bytes_per_200ms = sample_rate * 2 * 2  # 16bit * 0.2s
                total_chunks = max(1, (len(audio) + bytes_per_200ms - 1) // bytes_per_200ms)

                for i in range(total_chunks):
                    chunk_start = i * bytes_per_200ms
                    chunk_end = min(chunk_start + bytes_per_200ms, len(audio))
                    chunk = audio[chunk_start:chunk_end]
                    is_last = (i == total_chunks - 1)

                    frame = self._build_audio_frame(chunk, seq=i + 1, is_last=is_last)
                    ws.send_binary(frame)

                    # 接收服务端中间响应
                    try:
                        ws.settimeout(1)
                        while True:
                            try:
                                resp_data = ws.recv()
                                if isinstance(resp_data, bytes):
                                    resp = self._parse_response(resp_data)
                                    # 记录 TTFT
                                    if self.last_metrics.ttft is None:
                                        self.last_metrics.ttft = time.perf_counter() - start
                            except websocket.WebSocketTimeoutException:
                                break
                    finally:
                        ws.settimeout(60)

                # ---- Step 4: 接收最终结果 ----
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
                    msg_specific = resp["msg_specific"]
                    payload = resp.get("payload", {})

                    # 错误
                    if msg_type == 0x0F:
                        raise RuntimeError(
                            f"BigModel ASR 错误: code={payload.get('code')}, "
                            f"message={payload.get('message', '')}")

                    # 提取文本
                    if isinstance(payload, dict):
                        result_obj = payload.get("result", {})
                        if isinstance(result_obj, dict) and "text" in result_obj:
                            result_text = result_obj["text"]
                        elif isinstance(result_obj, list) and result_obj:
                            # 兼容 list 格式
                            for item in result_obj:
                                if isinstance(item, dict) and "text" in item:
                                    result_text = item["text"]

                    # TTFT
                    if self.last_metrics.ttft is None:
                        self.last_metrics.ttft = time.perf_counter() - start

                    # 最后一包 (msg_specific=0b0011 或 sequence<0)
                    if msg_specific in (0b0010, 0b0011) or resp.get("sequence", 0) < 0:
                        break

                self.last_metrics.total_time = time.perf_counter() - start
                if self.last_metrics.ttft is None:
                    self.last_metrics.ttft = self.last_metrics.total_time

            finally:
                ws.close()

        except Exception as e:
            raise RuntimeError(f"豆包大模型 ASR 调用失败: {e}")

        if audio_duration > 0 and self.last_metrics.total_time:
            self.last_metrics.rtf = self.last_metrics.total_time / audio_duration

        return result_text, self.last_metrics
