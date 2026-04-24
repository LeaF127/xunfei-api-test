#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
讯飞在线语音听写(ASR) + 在线语音合成(TTS) Python Demo
官方文档:
  ASR: https://www.xfyun.cn/doc/asr/voicedictation/API.html
  TTS: https://www.xfyun.cn/doc/tts/online_tts/API.html

依赖安装: pip install websocket-client
"""

import base64
import hashlib
import hmac
import json
import re
import time
import websocket  # pip install websocket-client
from urllib.parse import urlencode, urlparse

from config import APP_ID, API_KEY, API_SECRET

# ===================== 音频时长工具函数 =====================

def _estimate_mp3_duration(data: bytes) -> float:
    """通过解析 MP3 帧头来估算音频时长（秒）"""
    if len(data) < 4:
        return 0.0

    # 跳过 ID3v2 标签（如果有）
    i = 0
    if data[:3] == b"ID3":
        if len(data) >= 10:
            tag_size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
                       ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
            i = 10 + tag_size

    total_samples = 0
    sample_rate = 0

    # MPEG1 Layer III 比特率表 (kbps)
    bitrate_v1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    # MPEG2/2.5 Layer III 比特率表 (kbps)
    bitrate_v2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
    sr_v1  = [44100, 48000, 32000]
    sr_v2  = [22050, 24000, 16000]
    sr_v25 = [11025, 12000, 8000]

    while i <= len(data) - 4:
        if data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
            i += 1
            continue

        b1, b2 = data[i + 1], data[i + 2]
        version_bits = (b1 >> 3) & 3   # 0=2.5, 1=reserved, 2=2, 3=1
        layer_bits   = (b1 >> 1) & 3   # 0=reserved, 1=III, 2=II, 3=I
        br_idx       = (b2 >> 4) & 0xF
        sr_idx       = (b2 >> 2) & 3
        pad          = (b2 >> 1) & 1

        if version_bits == 1 or layer_bits == 0 or sr_idx == 3 or br_idx in (0, 15):
            i += 1
            continue

        if version_bits == 3:  # MPEG1
            bitrate     = bitrate_v1[br_idx] * 1000
            sample_rate = sr_v1[sr_idx]
            spf         = 1152 if layer_bits in (1, 2) else 384
        else:  # MPEG2 / MPEG2.5
            bitrate     = bitrate_v2[br_idx] * 1000
            sample_rate = (sr_v2 if version_bits == 2 else sr_v25)[sr_idx]
            spf         = 576 if layer_bits == 1 else (1152 if layer_bits == 2 else 384)

        if bitrate == 0 or sample_rate == 0:
            i += 1
            continue

        frame_size = (spf * bitrate // (8 * sample_rate)) + pad
        total_samples += spf
        i += frame_size

    return total_samples / sample_rate if sample_rate > 0 else 0.0


def _get_audio_duration(audio_data: bytes, encoding: str, sample_rate: int) -> float:
    """根据音频数据和编码方式计算音频时长（秒）"""
    if encoding == "raw":
        # PCM 16-bit 单声道
        return len(audio_data) / (sample_rate * 2)
    elif encoding in ("lame", "mp3"):
        return _estimate_mp3_duration(audio_data)
    return 0.0


# ===================== 鉴权工具函数 =====================

def create_auth_url(host_url: str, api_key: str, api_secret: str) -> str:
    """
    生成讯飞 WebSocket 鉴权 URL（ASR 和 TTS 通用）
    
    鉴权流程:
      1. 拼接签名原文: host: $host\ndate: $date\n$request-line
      2. 使用 api_secret 对签名原文做 hmac-sha256 签名
      3. 将签名结果 base64 编码得到 signature
      4. 拼接 authorization_origin 并 base64 编码得到 authorization
      5. 将 host / date / authorization 作为 URL 参数拼接到请求地址
    """
    parsed = urlparse(host_url)
    
    # RFC1123 格式的 GMT 时间戳
    date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
    
    # 拼接签名原文
    signature_origin = f"host: {parsed.hostname}\ndate: {date}\nGET {parsed.path} HTTP/1.1"
    
    # hmac-sha256 签名
    signature_sha = hmac.new(
        api_secret.encode("utf-8"),
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    
    signature = base64.b64encode(signature_sha).decode("utf-8")
    
    # 拼接 authorization_origin
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
    
    # 拼接最终 URL
    params = {"authorization": authorization, "date": date, "host": parsed.hostname}
    return host_url + "?" + urlencode(params)


# ===================== 在线语音听写 (ASR) =====================

class XunFeiASR:
    """
    讯飞在线语音听写（流式版）WebAPI
    
    接口要求:
      协议:    wss
      地址:    wss://iat-api.xfyun.cn/v2/iat (推荐)
              wss://ws-api.xfyun.cn/v2/iat
      音频:    采样率 16k/8k, 16bit, 单声道
      格式:    pcm / speex / speex-wb / mp3(仅中英文)
      时长:    最长 60s
    """

    # 推荐使用 iat-api 域名
    BASE_URL = "wss://iat-api.xfyun.cn/v2/iat"

    def __init__(self, app_id: str, api_key: str, api_secret: str):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.result_text = ""

        # 性能指标
        self.ttft = None        # 首字节延迟（秒）：从发送请求到收到第一个识别结果
        self.total_time = None  # 总处理时间（秒）：从开始到识别结束
        self.rtf = None         # 实时率 = 处理时间 / 音频时长
        # 内部计时变量
        self._asr_start = None
        self._first_result_time = None

    def _build_first_frame(self, audio_format: str = "audio/L16;rate=16000",
                           encoding: str = "raw",
                           language: str = "zh_cn",
                           domain: str = "iat",
                           accent: str = "mandarin") -> dict:
        """构建首帧请求（含 common + business + data）"""
        return {
            "common": {"app_id": self.app_id},
            "business": {
                "language": language,   # zh_cn: 中文, en_us: 英文
                "domain": domain,       # iat: 日常用语
                "accent": accent,       # mandarin: 普通话
                # "dwa": "wpgs",        # 开启动态修正（仅中文）
                # "eos": 3000,          # 后端点静音时间(ms), 默认2000
                # "ptt": 1,             # 标点符号 1:开启 0:关闭
            },
            "data": {
                "status": 0,            # 0: 首帧
                "format": audio_format, # 采样率
                "encoding": encoding,   # 音频编码
                "audio": "",            # 首帧可带音频, 也可为空
            },
        }

    def _build_audio_frame(self, audio_base64: str, status: int) -> dict:
        """
        构建中间帧 / 最后一帧
        status: 1=中间帧, 2=最后一帧
        """
        return {
            "data": {
                "status": status,
                "format": "audio/L16;rate=16000",
                "encoding": "raw",
                "audio": audio_base64,
            }
        }

    def recognize(self, audio_file_path: str,
                  audio_format: str = "audio/L16;rate=16000",
                  encoding: str = "raw") -> str:
        """
        同步识别音频文件
        
        Args:
            audio_file_path: 音频文件路径 (pcm/mp3)
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

        # 读取音频文件
        if isinstance(audio_file_path, str):
            with open(audio_file_path, "rb") as f:
                audio_data = f.read()
        elif isinstance(audio_file_path, bytes):
            audio_data = audio_file_path
        else:
            raise TypeError("audio_file_path 必须是文件路径(str)或音频数据(bytes)")

        # 从 audio_format 中提取采样率
        sr_match = re.search(r"rate=(\d+)", audio_format)
        sample_rate = int(sr_match.group(1)) if sr_match else 16000
        audio_duration = _get_audio_duration(audio_data, encoding, sample_rate)

        frame_size = 1280

        # 开始计时
        self._asr_start = time.perf_counter()

        ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: self._on_open(ws, audio_data, frame_size, audio_format, encoding),
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.run_forever()

        # 结束计时，计算指标
        self.total_time = time.perf_counter() - self._asr_start
        self.ttft = self._first_result_time
        if audio_duration > 0:
            self.rtf = self.total_time / audio_duration

        return self.result_text

    def _on_open(self, ws, audio_data: bytes, frame_size: int,
                 audio_format: str, encoding: str):
        """连接建立后发送音频数据"""
        import threading

        def send():
            # 发送首帧
            first = self._build_first_frame(audio_format, encoding)
            ws.send(json.dumps(first))

            # 分帧发送中间帧
            offset = 0
            while offset < len(audio_data):
                chunk = audio_data[offset: offset + frame_size]
                b64 = base64.b64encode(chunk).decode("utf-8")
                is_last = (offset + frame_size >= len(audio_data))
                frame = self._build_audio_frame(b64, status=2 if is_last else 1)
                ws.send(json.dumps(frame))
                offset += frame_size
                time.sleep(0.04)  # 建议 40ms 间隔
            time.sleep(1)  # 等待服务器处理完最后一帧

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
                    # 记录首次识别时间（TTFT）
                    if self._first_result_time is None:
                        self._first_result_time = time.perf_counter() - self._asr_start
                    self.result_text += word

        # status=2 表示识别结束
        if data.get("status") == 2:
            ws.close()

    def _on_error(self, ws, error):
        print(f"[ASR WebSocket Error] {error}")

    def _on_close(self, ws, close_status, close_msg):
        pass  # 连接关闭


# ===================== 在线语音合成 (TTS) =====================

class XunFeiTTS:
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
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.audio_data = b""

        # 性能指标
        self.ttft = None        # 首字节延迟（秒）：从发送请求到收到第一个音频数据
        self.total_time = None  # 总处理时间（秒）：从开始到合成结束
        self.rtf = None         # 实时率 = 处理时间 / 音频时长
        # 内部计时变量
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
                   output_file: str = "output.mp3") -> bytes:
        """
        同步合成语音
        
        Args:
            text:        要合成的文本
            vcn:         发音人 (x4_xiaoyan, x4_lingxiaoxuan_oral 等)
            speed:       语速 [0-100], 默认50
            volume:      音量 [0-100], 默认50
            pitch:       音高 [0-100], 默认50
            aue:         音频编码 raw=pcm, lame=mp3, opus, speex等
            auf:         采样率 audio/L16;rate=16000 或 audio/L16;rate=8000
            tte:         文本编码 UTF8 / GBK / GB2312 等
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

        # 文本 base64 编码
        text_b64 = base64.b64encode(text.encode("utf-8")).decode("utf-8")

        # 构建请求参数（一次性发送, status 固定为 2）
        request_body = {
            "common": {"app_id": self.app_id},
            "business": {
                "aue": aue,       # 音频编码
                "sfl": 1,         # aue=lame 时需传 sfl=1, 开启流式返回mp3
                "auf": auf,       # 采样率
                "vcn": vcn,       # 发音人
                "speed": speed,   # 语速
                "volume": volume, # 音量
                "pitch": pitch,   # 音高
                "tte": tte,       # 文本编码
                # "bgs": 0,       # 背景音 0:无 1:有
                # "reg": "2",     # 英文发音方式
                # "rdn": "0",     # 数字发音方式
            },
            "data": {
                "status": 2,      # TTS 固定为 2 (一次性传输)
                "text": text_b64,
            },
        }

        self._request_body = json.dumps(request_body)

        # 开始计时
        self._tts_start = time.perf_counter()

        ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.run_forever()

        # 结束计时，计算指标
        self.total_time = time.perf_counter() - self._tts_start
        self.ttft = self._first_audio_time

        # 计算合成音频时长用于 RTF
        if self.audio_data:
            if aue == "lame":
                audio_duration = _estimate_mp3_duration(self.audio_data)
            elif aue == "raw":
                sr_match = re.search(r"rate=(\d+)", auf)
                sr = int(sr_match.group(1)) if sr_match else 16000
                audio_duration = len(self.audio_data) / (sr * 2)
            else:
                audio_duration = 0.0
            if audio_duration > 0:
                self.rtf = self.total_time / audio_duration

        # 保存到文件
        if self.audio_data and output_file:
            with open(output_file, "wb") as f:
                f.write(self.audio_data)
            print(f"[TTS] 音频已保存: {output_file} ({len(self.audio_data)} bytes)")

        return self.audio_data

    def _on_open(self, ws):
        """连接建立后发送合成请求"""
        ws.send(self._request_body)

    def _on_message(self, ws, message):
        """处理返回的合成音频"""
        resp = json.loads(message)
        code = resp.get("code", -1)
        if code != 0:
            print(f"[TTS Error] code={code}, message={resp.get('message', '')}")
            ws.close()
            return

        data = resp.get("data")
        if data is None:
            return  # 服务端可能返回 data 为空的帧, 直接忽略

        audio_b64 = data.get("audio", "")
        if audio_b64:
            # 记录首次音频数据时间（TTFT）
            if self._first_audio_time is None:
                self._first_audio_time = time.perf_counter() - self._tts_start
            self.audio_data += base64.b64decode(audio_b64)

        # status=2 表示合成结束
        if data.get("status") == 2:
            ws.close()

    def _on_error(self, ws, error):
        print(f"[TTS WebSocket Error] {error}")

    def _on_close(self, ws, close_status, close_msg):
        pass


# ===================== 使用示例 =====================

if __name__ == "__main__":
    # ---------- ASR 示例 ----------
    print("=" * 50)
    print("语音听写 (ASR) 示例")
    print("=" * 50)
    
    asr = XunFeiASR(APP_ID, API_KEY, API_SECRET)
    test_audio = "cv-test/cv-corpus-25.0-2026-03-09/zh-CN/clips/common_voice_zh-CN_18524189.mp3"
    # 支持格式: pcm(16k/8k 16bit 单声道) / mp3(仅中英文)
    result = asr.recognize(test_audio,
                           audio_format="audio/L16;rate=16000",
                           encoding="raw")
    print(f"识别结果: {result}")
    if asr.ttft is not None:
        print(f"ASR TTFT (首字节延迟): {asr.ttft*1000:.1f} ms")
    if asr.total_time is not None:
        print(f"ASR 总耗时: {asr.total_time*1000:.1f} ms")
    if asr.rtf is not None:
        print(f"ASR RTF (实时率): {asr.rtf:.4f}")

    # ---------- TTS 示例 ----------
    print("=" * 50)
    print("语音合成 (TTS) 示例")
    print("=" * 50)
    
    tts = XunFeiTTS(APP_ID, API_KEY, API_SECRET)
    # 发音人列表: https://www.xfyun.cn/services/online_tts
    audio = tts.synthesize(
        text="你好，我是讯飞语音合成，很高兴为你服务！",
        vcn="x4_xiaoyan",   # 发音人
        aue="lame",          # mp3格式
        speed=50,
        volume=50,
        pitch=50,
        output_file="tts_output.mp3",
    )
    print(f"合成音频大小: {len(audio)} bytes")
    if tts.ttft is not None:
        print(f"TTS TTFT (首字节延迟): {tts.ttft*1000:.1f} ms")
    if tts.total_time is not None:
        print(f"TTS 总耗时: {tts.total_time*1000:.1f} ms")
    if tts.rtf is not None:
        print(f"TTS RTF (实时率): {tts.rtf:.4f}")
