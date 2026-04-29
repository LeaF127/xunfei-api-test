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

V3 帧格式:
  config请求:   header(4) + payload_size(4) + payload
  audio请求:    header(4) + payload_size(4) + payload  (flags=0b0000/0b0010, 无sequence)
                或 header(4) + sequence(4) + payload_size(4) + payload (flags=0b0001/0b0011, 有sequence)
  服务端响应:   header(4) + sequence(4) + payload_size(4) + payload
  错误帧:      header(4) + error_code(4) + error_size(4) + error_msg

msg_specific flags:
  0b0000 - header后不是sequence (中间包)
  0b0001 - header后是正数sequence (中间包)
  0b0010 - header后不是sequence, 指示最后一包
  0b0011 - header后是负数sequence (最后一包)
"""

import gzip
import json
import struct
import time
import uuid

import websocket

from providers.base import BaseASR, Metrics

# 全局 debug 开关, 设置环境变量 DOUBAO_ASR_DEBUG=1 开启
import os
_DEBUG = os.getenv("DOUBAO_ASR_DEBUG", "").strip() in ("1", "true", "True")


def _debug(msg: str):
    if _DEBUG:
        print(f"[BigModel ASR DEBUG] {msg}")


class DoubaoBigModelASR(BaseASR):
    """豆包语音 — 大模型流式语音识别"""

    # 双向流式 (优化版)
    WS_URL_ASYNC = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    # 双向流式 (标准版)
    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    # 流式输入 (非流式返回, 准确率更高)
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
        """
        解析 V3 服务端响应帧

        正常: header(4) + sequence(4) + payload_size(4) + payload
        错误: header(4) + error_code(4) + error_size(4) + error_msg
        """
        if len(data) < 4:
            return {"msg_type": -1, "msg_specific": 0, "sequence": 0,
                    "payload": {}, "raw_hex": data.hex()}

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
                    "sequence": 0, "payload": {}, "raw_len": len(data)}

        sequence = struct.unpack(">i", data[4:8])[0]
        payload_size = struct.unpack(">I", data[8:12])[0]
        payload_raw = data[12:12 + payload_size]

        if compression == 1 and payload_raw:
            try:
                payload_raw = gzip.decompress(payload_raw)
            except Exception as e:
                _debug(f"gzip解压失败: {e}, raw_len={len(payload_raw)}")
                return {"msg_type": msg_type, "msg_specific": msg_specific,
                        "sequence": sequence, "payload": {"_gzip_error": str(e)}}

        if serialization == 1 and payload_raw:
            try:
                payload = json.loads(payload_raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                _debug(f"JSON解析失败: {e}")
                payload = {"_json_error": str(e), "_raw_preview": payload_raw[:200].decode("utf-8", errors="replace")}
        else:
            payload = {"_raw_size": len(payload_raw) if payload_raw else 0}

        return {"msg_type": msg_type, "msg_specific": msg_specific,
                "sequence": sequence, "payload": payload}

    # ==================== 帧构造 ====================

    def _build_config_frame(self, audio_format="wav", sample_rate=16000,
                            bits=16, channel=1, use_gzip=False) -> bytes:
        """
        构造 full_client_request (JSON 配置)
        格式: header(4) + payload_size(4) + payload
        """
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
        _debug(f"Config frame: header={header.hex()} payload_size={len(payload)} total={len(frame)}")
        return frame

    @staticmethod
    def _build_audio_frame(audio_data: bytes, seq: int, is_last: bool = False,
                           use_gzip: bool = False, use_sequence: bool = True) -> bytes:
        """
        构造 audio_only_request

        use_sequence=True  (flags=0b0001/0b0011):
          格式: header(4) + sequence(4) + payload_size(4) + payload
        use_sequence=False (flags=0b0000/0b0010):
          格式: header(4) + payload_size(4) + payload
        """
        if use_gzip:
            compressed = gzip.compress(audio_data)
            comp_flag = 1
        else:
            compressed = audio_data
            comp_flag = 0

        if use_sequence:
            if is_last:
                header = DoubaoBigModelASR._build_header(2, 0b0011, 0, comp_flag)
                seq_bytes = struct.pack(">i", -seq)
            else:
                header = DoubaoBigModelASR._build_header(2, 0b0001, 0, comp_flag)
                seq_bytes = struct.pack(">i", seq)
            frame = header + seq_bytes + struct.pack(">I", len(compressed)) + compressed
            _debug(f"Audio frame seq={seq}{'(LAST)' if is_last else ''}: "
                   f"header={header.hex()} seq_bytes={seq_bytes.hex()} "
                   f"payload_size={len(compressed)} total={len(frame)}")
        else:
            if is_last:
                header = DoubaoBigModelASR._build_header(2, 0b0010, 0, comp_flag)
            else:
                header = DoubaoBigModelASR._build_header(2, 0b0000, 0, comp_flag)
            frame = header + struct.pack(">I", len(compressed)) + compressed
            _debug(f"Audio frame seq={seq}{'(LAST)' if is_last else ''} [no-seq]: "
                   f"header={header.hex()} payload_size={len(compressed)} total={len(frame)}")

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
        _debug(f"音频: {len(audio)} bytes, 时长≈{audio_duration:.2f}s, format={audio_format}, rate={sample_rate}")
        _debug(f"URL: {self.ws_url}")

        # 鉴权 Header
        request_id = str(uuid.uuid4())
        ws_headers = [
            f"X-Api-Key: {self.api_key}",
            f"X-Api-Resource-Id: {self.resource_id}",
            f"X-Api-Request-Id: {request_id}",
            "X-Api-Sequence: -1",
        ]
        _debug(f"WS Headers: {ws_headers}")

        start = time.perf_counter()

        try:
            ws = websocket.WebSocket()
            _debug("正在连接 WebSocket...")
            ws.connect(
                self.ws_url,
                header=ws_headers,
                max_size=10 * 1024 * 1024,
                timeout=60,
            )
            _debug("WebSocket 连接成功!")

            try:
                # ---- Step 1: 发送 config ----
                _debug("=== Step 1: 发送 config ===")
                config_frame = self._build_config_frame(
                    audio_format=audio_format, sample_rate=sample_rate)
                ws.send_binary(config_frame)
                _debug(f"Config 已发送 ({len(config_frame)} bytes)")

                # ---- Step 2: 接收 config ack ----
                _debug("=== Step 2: 等待 config ack ===")
                try:
                    ack_data = ws.recv()
                    _debug(f"收到 ack: {type(ack_data).__name__} {len(ack_data)} bytes")
                    if isinstance(ack_data, bytes):
                        _debug(f"  raw hex (前40B): {ack_data[:40].hex()}")
                        ack = self._parse_response(ack_data)
                        _debug(f"  msg_type={ack['msg_type']} msg_specific={ack['msg_specific']} "
                               f"sequence={ack['sequence']}")
                        p = ack.get("payload", {})
                        _debug(f"  payload: {json.dumps(p, ensure_ascii=False)[:500]}")
                        if isinstance(p, dict) and p.get("code") is not None:
                            if p["code"] not in (0, 20000000):
                                raise RuntimeError(
                                    f"BigModel ASR 配置阶段错误: code={p['code']}, "
                                    f"message={p.get('message', '')}")
                except websocket.WebSocketTimeoutException:
                    _debug("  ack 超时, 继续")

                # ---- Step 3: 分包发送音频 ----
                _debug("=== Step 3: 分包发送音频 ===")
                bytes_per_chunk = sample_rate * 2 * 2  # 200ms
                total_chunks = max(1, (len(audio) + bytes_per_chunk - 1) // bytes_per_chunk)
                _debug(f"分包: {total_chunks} 包, 每包≈{bytes_per_chunk} bytes")

                # 使用带 sequence 的格式 (flags=0b0001/0b0011)
                use_seq = kwargs.get("use_sequence", True)
                _debug(f"use_sequence={use_seq}")

                for i in range(total_chunks):
                    chunk_start = i * bytes_per_chunk
                    chunk_end = min(chunk_start + bytes_per_chunk, len(audio))
                    chunk = audio[chunk_start:chunk_end]
                    is_last = (i == total_chunks - 1)

                    frame = self._build_audio_frame(
                        chunk, seq=i + 1, is_last=is_last,
                        use_gzip=False, use_sequence=use_seq)
                    ws.send_binary(frame)
                    _debug(f"  已发送 chunk {i+1}/{total_chunks} ({len(chunk)} bytes)"
                           f"{' [LAST]' if is_last else ''}")

                    # 每包之间 sleep, 模拟实时流
                    if not is_last:
                        time.sleep(0.1)

                    # 尝试接收中间响应
                    try:
                        ws.settimeout(1)
                        while True:
                            try:
                                resp_data = ws.recv()
                                if isinstance(resp_data, bytes) and len(resp_data) >= 4:
                                    resp = self._parse_response(resp_data)
                                    _debug(f"  中间响应: msg_type={resp['msg_type']} "
                                           f"seq={resp['sequence']} "
                                           f"payload_keys={list(resp.get('payload',{}).keys())[:5]}")
                                    if self.last_metrics.ttft is None:
                                        self.last_metrics.ttft = time.perf_counter() - start
                            except websocket.WebSocketTimeoutException:
                                break
                    finally:
                        ws.settimeout(60)

                # ---- Step 4: 接收最终结果 ----
                _debug("=== Step 4: 接收最终结果 ===")
                result_text = ""
                recv_count = 0
                while True:
                    try:
                        resp_data = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        _debug("  最终接收超时, 退出")
                        break

                    recv_count += 1
                    if not isinstance(resp_data, bytes):
                        _debug(f"  忽略非binary响应: {type(resp_data).__name__}")
                        continue

                    resp = self._parse_response(resp_data)
                    msg_type = resp["msg_type"]
                    msg_specific = resp["msg_specific"]
                    payload = resp.get("payload", {})

                    _debug(f"  响应 #{recv_count}: msg_type={msg_type} "
                           f"msg_specific={msg_specific:04b} "
                           f"seq={resp['sequence']} "
                           f"payload_keys={list(payload.keys())[:5] if isinstance(payload, dict) else 'N/A'}")

                    # 错误
                    if msg_type == 0x0F:
                        _debug(f"  ❌ 错误帧: {payload}")
                        raise RuntimeError(
                            f"BigModel ASR 错误: code={payload.get('code')}, "
                            f"message={payload.get('message', '')}")

                    # 提取文本
                    if isinstance(payload, dict):
                        result_obj = payload.get("result", {})
                        if isinstance(result_obj, dict) and "text" in result_obj:
                            result_text = result_obj["text"]
                            _debug(f"  text: {result_text[:100]}")
                        elif isinstance(result_obj, list) and result_obj:
                            for item in result_obj:
                                if isinstance(item, dict) and "text" in item:
                                    result_text = item["text"]
                                    _debug(f"  text (list): {result_text[:100]}")

                    if self.last_metrics.ttft is None:
                        self.last_metrics.ttft = time.perf_counter() - start

                    # 结束判断
                    if msg_specific in (0b0010, 0b0011) or resp.get("sequence", 0) < 0:
                        _debug(f"  收到最终响应, 退出循环")
                        break

                _debug(f"共收到 {recv_count} 个响应")

                self.last_metrics.total_time = time.perf_counter() - start
                if self.last_metrics.ttft is None:
                    self.last_metrics.ttft = self.last_metrics.total_time

            finally:
                ws.close()
                _debug("WebSocket 已关闭")

        except Exception as e:
            _debug(f"❌ 异常: {type(e).__name__}: {e}")
            raise RuntimeError(f"豆包大模型 ASR 调用失败: {e}")

        if audio_duration > 0 and self.last_metrics.total_time:
            self.last_metrics.rtf = self.last_metrics.total_time / audio_duration

        _debug(f"结果: text='{result_text[:100]}' ttft={self.last_metrics.ttft:.3f}s "
               f"total={self.last_metrics.total_time:.3f}s rtf={self.last_metrics.rtf:.3f}")
        return result_text, self.last_metrics
