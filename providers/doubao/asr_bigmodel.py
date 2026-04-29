"""
豆包语音 大模型流式语音识别 (ASR) — WebSocket V3 二进制协议

接口说明:
  协议:  WebSocket
  地址:  wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
  鉴权:  X-Api-Key + X-Api-Resource-Id (Header)
  音频:  采样率 16kHz, 16bit, 单声道
  格式:  wav / pcm / ogg / mp3
  分包:  建议每包 200ms, 间隔 100~200ms
  文档:  https://www.volcengine.com/docs/6561/1354869
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

    WS_URL_ASYNC = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    WS_URL_NOSTREAM = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"

    def __init__(self, api_key: str, resource_id: str,
                 ws_url: str | None = None):
        super().__init__()
        self.api_key = api_key
        self.resource_id = resource_id
        self.ws_url = ws_url or self.WS_URL

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

        # 错误帧: header(4) + error_code(4) + error_size(4) + error_msg
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
                            bits=16, channel=1, use_gzip=False) -> bytes:
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
        _debug(f"Config JSON ({len(payload)} bytes): {config}")
        if use_gzip:
            payload = gzip.compress(payload)
            header = self._build_header(1, 0b0000, 1, 1)
        else:
            header = self._build_header(1, 0b0000, 1, 0)
        frame = header + struct.pack(">I", len(payload)) + payload
        _debug(f"Config frame: header={header.hex()} size={len(payload)} total={len(frame)}")
        return frame

    @staticmethod
    def _build_audio_frame(audio_data: bytes, seq: int, is_last: bool = False,
                           use_gzip: bool = False, use_sequence: bool = False) -> bytes:
        compressed = gzip.compress(audio_data) if use_gzip else audio_data
        comp_flag = 1 if use_gzip else 0

        if use_sequence:
            if is_last:
                header = DoubaoBigModelASR._build_header(2, 0b0011, 0, comp_flag)
                seq_bytes = struct.pack(">i", -seq)
            else:
                header = DoubaoBigModelASR._build_header(2, 0b0001, 0, comp_flag)
                seq_bytes = struct.pack(">i", seq)
            frame = header + seq_bytes + struct.pack(">I", len(compressed)) + compressed
        else:
            if is_last:
                header = DoubaoBigModelASR._build_header(2, 0b0010, 0, comp_flag)
            else:
                header = DoubaoBigModelASR._build_header(2, 0b0000, 0, comp_flag)
            frame = header + struct.pack(">I", len(compressed)) + compressed

        _debug(f"Audio seq={seq}{'(LAST)' if is_last else ''}: "
               f"header={header.hex()} size={len(compressed)} total={len(frame)}")
        return frame

    # ==================== recv 工具 ====================

    def _recv_response(self, ws):
        """
        安全 recv, 返回 (resp_dict, connection_alive)
        连接关闭时返回 (None, False)
        """
        try:
            data = ws.recv()
            if isinstance(data, bytes) and len(data) >= 4:
                resp = self._parse_response(data)
                _debug(f"  RECV: msg_type={resp['msg_type']} "
                       f"msg_specific={resp['msg_specific']:04b} "
                       f"seq={resp['sequence']} "
                       f"payload={json.dumps(resp.get('payload',{}), ensure_ascii=False)[:300]}")
                return resp, True
            return None, True
        except websocket.WebSocketTimeoutException:
            return None, True
        except (websocket.WebSocketConnectionClosedException, ConnectionResetError, OSError) as e:
            _debug(f"  连接关闭: {type(e).__name__}: {e}")
            return None, False

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
        _debug(f"音频: {len(audio)} bytes, 时长≈{audio_duration:.2f}s, format={audio_format}, rate={sample_rate}")
        _debug(f"URL: {self.ws_url}")

        request_id = str(uuid.uuid4())
        ws_headers = [
            f"X-Api-Key: {self.api_key}",
            f"X-Api-Resource-Id: {self.resource_id}",
            f"X-Api-Request-Id: {request_id}",
            "X-Api-Sequence: -1",
        ]
        _debug(f"Headers: {ws_headers}")

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
                _debug("=== Step 1: 发送 config ===")
                ws.send_binary(self._build_config_frame(
                    audio_format=audio_format, sample_rate=sample_rate))

                # ---- Step 2: 接收 config ack ----
                _debug("=== Step 2: config ack ===")
                resp, connection_alive = self._recv_response(ws)
                if resp and isinstance(resp.get("payload"), dict):
                    p = resp["payload"]
                    if p.get("code") is not None and p["code"] not in (0, 20000000):
                        raise RuntimeError(f"配置阶段错误: code={p['code']}, message={p.get('message','')}")
                if self.last_metrics.ttft is None:
                    self.last_metrics.ttft = time.perf_counter() - start

                # ---- Step 3: 分包发送音频 ----
                _debug("=== Step 3: 分包发送音频 ===")
                bytes_per_chunk = int(sample_rate * 2 * 0.2)  # 200ms = 6400 bytes
                total_chunks = max(1, (len(audio) + bytes_per_chunk - 1) // bytes_per_chunk)
                _debug(f"分包: {total_chunks} 包, 每包≈{bytes_per_chunk} bytes")

                for i in range(total_chunks):
                    chunk = audio[i * bytes_per_chunk : (i + 1) * bytes_per_chunk]
                    is_last = (i == total_chunks - 1)

                    frame = self._build_audio_frame(chunk, seq=i + 1, is_last=is_last)
                    ws.send_binary(frame)
                    _debug(f"  已发送 chunk {i+1}/{total_chunks} ({len(chunk)}B){' [LAST]' if is_last else ''}")

                    if not is_last:
                        time.sleep(0.1)

                    # 非阻塞接收中间响应
                    if connection_alive:
                        try:
                            ws.settimeout(1)
                            while connection_alive:
                                resp, connection_alive = self._recv_response(ws)
                                if resp is None:
                                    break
                                if resp["msg_type"] == 0x0F:
                                    raise RuntimeError(f"发送阶段服务端错误: {resp.get('payload',{})}")
                                # 提取中间结果
                                text = _extract_text(resp.get("payload", {}))
                                if text:
                                    result_text = text
                        finally:
                            ws.settimeout(60)

                # ---- Step 4: 接收最终结果 ----
                _debug("=== Step 4: 接收最终结果 ===")
                recv_count = 0
                while connection_alive:
                    resp, connection_alive = self._recv_response(ws)
                    if resp is None:
                        if not connection_alive:
                            _debug("  连接已关闭 (正常结束)")
                            break
                        continue  # timeout

                    recv_count += 1

                    # 错误帧
                    if resp["msg_type"] == 0x0F:
                        raise RuntimeError(f"ASR 错误: {resp.get('payload',{})}")

                    # 提取文本
                    text = _extract_text(resp.get("payload", {}))
                    if text:
                        result_text = text
                        _debug(f"  text: {text[:100]}")

                    if self.last_metrics.ttft is None:
                        self.last_metrics.ttft = time.perf_counter() - start

                    # 判断是否最终包
                    msg_specific = resp.get("msg_specific", 0)
                    if msg_specific in (0b0010, 0b0011) or resp.get("sequence", 0) < 0:
                        _debug("  收到最终响应")
                        break

                _debug(f"共收到 {recv_count} 个响应 (connection_alive={connection_alive})")

                self.last_metrics.total_time = time.perf_counter() - start
                if self.last_metrics.ttft is None:
                    self.last_metrics.ttft = self.last_metrics.total_time

            finally:
                try:
                    ws.close()
                except Exception:
                    pass
                _debug("WebSocket 已关闭")

        except RuntimeError:
            raise
        except Exception as e:
            _debug(f"❌ 异常: {type(e).__name__}: {e}")
            raise RuntimeError(f"豆包大模型 ASR 调用失败: {e}")

        if audio_duration > 0 and self.last_metrics.total_time:
            self.last_metrics.rtf = self.last_metrics.total_time / audio_duration

        _debug(f"结果: text='{result_text[:100]}' ttft={self.last_metrics.ttft:.3f}s "
               f"total={self.last_metrics.total_time:.3f}s rtf={self.last_metrics.rtf:.3f}")
        return result_text, self.last_metrics
