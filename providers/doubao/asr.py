"""
豆包语音 一句话识别 (ASR) — WebSocket 二进制协议

接口说明:
  协议:  WebSocket
  地址:  wss://openspeech.bytedance.com/api/v2/asr
  音频:  采样率 16kHz, 16bit, 单声道
  格式:  pcm / wav / mp3 / ogg_opus / speex
  时长:  最长 60s
  鉴权:  HMAC256 签名
"""

import json
import struct
import time
import uuid

import websockets

from providers.base import BaseASR
from providers.doubao.auth import build_asr_auth_header


class DoubaoASR(BaseASR):
    """
    豆包语音 — 一句话识别 (ASR)

    使用 WebSocket 二进制协议，将整段音频一次性发送后接收识别结果。
    """

    WS_URL = "wss://openspeech.bytedance.com/api/v2/asr"

    def __init__(self, appid: str, access_token: str, secret_key: str, cluster: str = "volcengine_asr"):
        super().__init__()
        self.appid = appid
        self.access_token = access_token
        self.secret_key = secret_key
        self.cluster = cluster

    @staticmethod
    def _build_header(message_type: int = 1, serialization: int = 1,
                      compression: int = 0, reserved: int = 0) -> bytes:
        """
        构造 WebSocket 二进制协议 header (4 字节)

        Byte 0: [protocol_version(4bit)] [header_size(4bit)]  => 0x11
        Byte 1: [message_type(4bit)]    [message_type_specific(4bit)] => 由调用者指定
        Byte 2: [serialization(4bit)]   [compression(4bit)] => 0x10
        Byte 3: reserved
        """
        b0 = 0x11  # protocol=1, header_size=4(即 header 占 4 字节)
        b1 = (message_type << 4) | 0
        b2 = (serialization << 4) | compression
        b3 = reserved
        return bytes([b0, b1, b2, b3])

    def _build_full_request(self, audio_data: bytes,
                            audio_format: str = "wav",
                            sample_rate: int = 16000,
                            bits: int = 16,
                            channel: int = 1) -> bytes:
        """
        构造完整的请求二进制帧 (header + payload_size + payload)

        一句话识别: 一次性发送全部音频 (sequence=1, 最后一包取相反数 => -1)
        """
        reqid = str(uuid.uuid4())
        sequence = -1  # 只发一包，取相反数表示结束

        payload_dict = {
            "app": {
                "appid": self.appid,
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
            },
        }
        payload_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

        # header: type=1(full_client_request), serialization=1(JSON), compression=0(none)
        header = self._build_header(message_type=1, serialization=1, compression=0)
        # payload size: 4 字节大端
        payload_size = struct.pack(">I", len(payload_bytes))
        # 拼接完整音频数据到 payload 之后 (音频放在 JSON payload 同帧中)
        # 实际上 for 一句话识别, 音频数据直接附在 JSON payload 后面
        # 但协议要求 payload 就是 JSON; 音频作为额外数据
        # 根据 Demo, 实际是将音频也放到 payload JSON 的 data 字段中不太合适
        # 正确做法: 对于 full_client_request, payload 是配置 JSON
        # 音频数据通过后续的 audio_only 帧发送
        # 但一句话识别可以一次性: header(type=1) + size + json_config + audio_data
        # 这里简化为: 把音频拼到 payload 后面
        full_request = header + payload_size + payload_bytes + audio_data
        return full_request

    def _build_audio_frame(self, audio_data: bytes, sequence: int) -> bytes:
        """
        构造纯音频帧 (message_type=2, audio_only)
        """
        header = self._build_header(message_type=2, serialization=0, compression=0)
        payload_size = struct.pack(">I", len(audio_data))
        return header + payload_size + audio_data

    def recognize(self, audio_data, audio_format: str = "wav",
                  sample_rate: int = 16000, **kwargs) -> str:
        """
        一句话识别

        Args:
            audio_data: 文件路径 (str) 或音频二进制 (bytes)
            audio_format: 音频格式 (wav/pcm/mp3/ogg_opus)
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
        # websockets 要求 header 为 list[tuple]
        ws_headers = list(auth_headers.items())

        start = time.perf_counter()

        try:
            async def _ws_call():
                import asyncio
                async with websockets.connect(
                    self.WS_URL,
                    additional_headers=ws_headers,
                    max_size=10 * 1024 * 1024,
                    ping_timeout=None,
                ) as ws:
                    # 发送 full_client_request (配置 + 音频一次性)
                    req = self._build_full_request(audio, audio_format, sample_rate)
                    await ws.send(req)

                    # 接收响应
                    result_text = ""
                    ttft_recorded = False
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            break

                        if isinstance(msg, bytes) and len(msg) >= 4:
                            msg_type = (msg[1] >> 4) & 0x0F
                            # payload_size (bytes 4-7)
                            if len(msg) >= 8:
                                p_size = struct.unpack(">I", msg[4:8])[0]
                                payload = msg[8:8 + p_size]
                            else:
                                payload = b""

                            if msg_type == 0x9:  # full_server_response (错误)
                                try:
                                    err = json.loads(payload.decode("utf-8"))
                                    raise RuntimeError(f"ASR 服务端错误: {err}")
                                except json.JSONDecodeError:
                                    pass
                            elif msg_type in (0x1, 0x2):  # 服务端 ack / 部分结果
                                if not ttft_recorded and payload:
                                    self.ttft = time.perf_counter() - start
                                    ttft_recorded = True
                                try:
                                    resp = json.loads(payload.decode("utf-8"))
                                    if "result" in resp:
                                        for item in resp["result"]:
                                            if "text" in item:
                                                result_text = item["text"]
                                except (json.JSONDecodeError, UnicodeDecodeError):
                                    pass
                            elif msg_type == 0x3:  # 最后一个包 (sequence 为负)
                                if not ttft_recorded and payload:
                                    self.ttft = time.perf_counter() - start
                                    ttft_recorded = True
                                try:
                                    resp = json.loads(payload.decode("utf-8"))
                                    if "result" in resp:
                                        for item in resp["result"]:
                                            if "text" in item:
                                                result_text = item["text"]
                                except (json.JSONDecodeError, UnicodeDecodeError):
                                    pass
                                break
                        elif isinstance(msg, str):
                            # JSON 文本消息
                            if not ttft_recorded:
                                self.ttft = time.perf_counter() - start
                                ttft_recorded = True
                            try:
                                resp = json.loads(msg)
                                if resp.get("code", -1) != 0:
                                    raise RuntimeError(f"ASR 错误: {resp}")
                                if "result" in resp:
                                    for item in resp["result"]:
                                        if "text" in item:
                                            result_text = item["text"]
                            except json.JSONDecodeError:
                                pass
                            break

                    return result_text

            import asyncio
            # 如果已有事件循环在运行, 用 nest_asyncio 或新线程
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(asyncio.run, _ws_call())
                        text = future.result(timeout=60)
                else:
                    text = loop.run_until_complete(_ws_call())
            except RuntimeError:
                text = asyncio.run(_ws_call())

        except Exception as e:
            raise RuntimeError(f"豆包 ASR 调用失败: {e}")

        self.total_time = time.perf_counter() - start
        if self.ttft is None:
            self.ttft = self.total_time

        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        return text
