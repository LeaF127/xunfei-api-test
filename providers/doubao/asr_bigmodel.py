"""
豆包语音 大模型流式语音识别 (ASR) — WebSocket V3 二进制协议

默认使用 bigmodel_nostream (流式输入模式):
  - 等全部音频发完后返回识别结果, 准确率更高
  - 平均 5s 音频可在 300~400ms 内返回
  - 适合批量测试场景

也可切换到 bigmodel (双向流式模式):
  - 逐包返回识别结果, 延迟更低
  - 适合实时流式场景
"""

import gzip
import json
import struct
import time
import uuid

import websocket

from providers.base import BaseASR, Metrics

import os
_DEBUG = os.getenv("DOUBAO_ASR_DEBUG", "").strip() in ("1", "true", "True")


def _debug(msg: str):
    if _DEBUG:
        print(f"[BigModel ASR DEBUG] {msg}")


def _extract_text(payload: dict) -> str:
    """从 payload 中提取识别文本"""
    if not isinstance(payload, dict):
        return ""
    result_obj = payload.get("result", {})
    if isinstance(result_obj, dict) and "text" in result_obj:
        return result_obj["text"]
    if isinstance(result_obj, list):
        for item in result_obj:
            if isinstance(item, dict) and "text" in item:
                return item["text"]
    return ""


class DoubaoBigModelASR(BaseASR):
    """豆包语音 — 大模型流式语音识别"""

    # 流式输入模式 (准确率更高, 适合批量测试)
    WS_URL_NOSTREAM = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    # 双向流式 (标准版, 实时性更好)
    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    # 双向流式 (优化版)
    WS_URL_ASYNC = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"

    def __init__(self, api_key: str, resource_id: str,
                 ws_url: str | None = None):
        super().__init__()
        self.api_key = api_key
        self.resource_id = resource_id
        # 默认用 nostream, 准确率更高
        self.ws_url = ws_url or self.WS_URL_NOSTREAM

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
        """解析 V3 服务端响应帧"""
        if len(data) < 4:
            return {"msg_type": -1, "msg_specific": 0, "sequence": 0, "payload": {}}

        msg_type = (data[1] >> 4) & 0x0F
        msg_specific = data[1] & 0x0F
        serialization = (data[2] >> 4) & 0x0F
        compression = (data[2]) & 0x0F

        # 错误帧
        if msg_type == 0x0F:
            if len(data) < 12:
                return {"msg_type": msg_type, "msg_specific": msg_specific,
                        "sequence": 0, "payload": {"code": -1, "message": data.hex()}}
            error_code = struct.unpack(">I", data[4:8])[0]
            error_size = struct.unpack(">I", data[8:12])[0]
            error_msg = data[12:12 + error_size].decode("utf-8", errors="replace")
            return {"msg_type": msg_type, "msg_specific": msg_specific,
                    "sequence": 0, "payload": {"code": error_code, "message": error_msg}}

        # 正常帧: header(4) + sequence(4) + payload_size(4) + payload
        if len(data) < 12:
            return {"msg_type": msg_type, "msg_specific": msg_specific,
                    "sequence": 0, "payload": {}}

        sequence = struct.unpack(">i", data[4:8])[0]
        payload_size = struct.unpack(">I", data[8:12])[0]
        payload_raw = data[12:12 + payload_size]

        if compression == 1 and payload_raw:
            try:
                payload_raw = gzip.decompress(payload_raw)
            except Exception as e:
                _debug(f"gzip解压失败: {e}")
                return {"msg_type": msg_type, "msg_specific": msg_specific,
                        "sequence": sequence, "payload": {}}

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
                            bits=16, channel=1, language="zh-CN") -> bytes:
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
        # nostream 接口支持 language 参数
        if self.ws_url.endswith("bigmodel_nostream"):
            config["audio"]["language"] = language

        payload = json.dumps(config, ensure_ascii=False).encode("utf-8")
        header = self._build_header(1, 0b0000, 1, 0)
        frame = header + struct.pack(">I", len(payload)) + payload
        _debug(f"Config: {json.dumps(config, ensure_ascii=False)[:300]}")
        return frame

    @staticmethod
    def _build_audio_frame(audio_data: bytes, is_last: bool = False) -> bytes:
        """
        构造 audio_only_request (不带 sequence)

        中间包: flags=0b0000, header(4) + payload_size(4) + payload
        最后一包: flags=0b0010, header(4) + payload_size(4) + payload
        """
        if is_last:
            header = DoubaoBigModelASR._build_header(2, 0b0010, 0, 0)
        else:
            header = DoubaoBigModelASR._build_header(2, 0b0000, 0, 0)
        frame = header + struct.pack(">I", len(audio_data)) + audio_data
        _debug(f"Audio chunk: {len(audio_data)}B{' [LAST]' if is_last else ''} header={header.hex()}")
        return frame

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
        _debug(f"音频: {len(audio)}B ≈{audio_duration:.2f}s format={audio_format} rate={sample_rate}")
        _debug(f"URL: {self.ws_url}")

        request_id = str(uuid.uuid4())
        ws_headers = [
            f"X-Api-Key: {self.api_key}",
            f"X-Api-Resource-Id: {self.resource_id}",
            f"X-Api-Request-Id: {request_id}",
            "X-Api-Sequence: -1",
        ]

        start = time.perf_counter()
        result_text = ""
        connection_alive = True

        try:
            ws = websocket.WebSocket()
            _debug("连接 WebSocket...")
            ws.connect(self.ws_url, header=ws_headers,
                       max_size=10 * 1024 * 1024, timeout=60)
            _debug("连接成功!")

            try:
                # ---- Step 1: 发送 config ----
                ws.send_binary(self._build_config_frame(
                    audio_format=audio_format, sample_rate=sample_rate))

                # ---- Step 2: 接收 config ack ----
                try:
                    ack_data = ws.recv()
                    if isinstance(ack_data, bytes) and len(ack_data) >= 4:
                        ack = self._parse_response(ack_data)
                        p = ack.get("payload", {})
                        _debug(f"Config ack: {json.dumps(p, ensure_ascii=False)[:300]}")
                        if isinstance(p, dict) and p.get("code") is not None:
                            if p["code"] not in (0, 20000000):
                                raise RuntimeError(f"配置错误: code={p['code']}, msg={p.get('message','')}")
                except websocket.WebSocketTimeoutException:
                    pass

                # ---- Step 3: 快速发送全部音频 (不等中间响应) ----
                bytes_per_chunk = int(sample_rate * 2 * 0.2)  # 200ms = 6400B
                total_chunks = max(1, (len(audio) + bytes_per_chunk - 1) // bytes_per_chunk)
                _debug(f"发送 {total_chunks} 个音频包...")

                for i in range(total_chunks):
                    chunk = audio[i * bytes_per_chunk : (i + 1) * bytes_per_chunk]
                    is_last = (i == total_chunks - 1)
                    ws.send_binary(self._build_audio_frame(chunk, is_last=is_last))

                _debug(f"全部 {total_chunks} 包已发送, 等待识别结果...")
                self.last_metrics.ttft = time.perf_counter() - start

                # ---- Step 4: 接收识别结果 ----
                recv_count = 0
                while connection_alive:
                    try:
                        resp_data = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        _debug("recv 超时")
                        break
                    except (websocket.WebSocketConnectionClosedException,
                            ConnectionResetError, OSError) as e:
                        _debug(f"连接关闭: {type(e).__name__}")
                        connection_alive = False
                        break

                    recv_count += 1
                    if not isinstance(resp_data, bytes) or len(resp_data) < 4:
                        continue

                    resp = self._parse_response(resp_data)
                    msg_type = resp["msg_type"]
                    msg_specific = resp.get("msg_specific", 0)
                    payload = resp.get("payload", {})

                    _debug(f"响应 #{recv_count}: msg_type={msg_type} "
                           f"specific={msg_specific:04b} seq={resp['sequence']} "
                           f"payload={json.dumps(payload, ensure_ascii=False)[:300]}")

                    if msg_type == 0x0F:
                        raise RuntimeError(f"ASR 错误: {payload}")

                    text = _extract_text(payload)
                    if text:
                        result_text = text

                    if msg_specific in (0b0010, 0b0011) or resp.get("sequence", 0) < 0:
                        _debug("收到最终响应")
                        break

                self.last_metrics.total_time = time.perf_counter() - start
                _debug(f"完成: {recv_count} 个响应, 连接={'存活' if connection_alive else '已关闭'}")

            finally:
                try:
                    ws.close()
                except Exception:
                    pass

        except RuntimeError:
            raise
        except Exception as e:
            _debug(f"❌ 异常: {type(e).__name__}: {e}")
            raise RuntimeError(f"豆包大模型 ASR 调用失败: {e}")

        if self.last_metrics.ttft is None:
            self.last_metrics.ttft = self.last_metrics.total_time
        if audio_duration > 0 and self.last_metrics.total_time:
            self.last_metrics.rtf = self.last_metrics.total_time / audio_duration

        _debug(f"结果: '{result_text[:100]}' ttft={self.last_metrics.ttft:.3f}s "
               f"total={self.last_metrics.total_time:.3f}s rtf={self.last_metrics.rtf:.3f}")
        return result_text, self.last_metrics
