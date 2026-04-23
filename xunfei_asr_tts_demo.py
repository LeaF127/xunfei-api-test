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
import time
import websocket  # pip install websocket-client
from urllib.parse import urlencode, urlparse

from config import APP_ID, API_KEY, API_SECRET

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
                # "eos": 3000,          # 后端点静默时间(ms), 默认2000
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
        url = create_auth_url(self.BASE_URL, self.api_key, self.api_secret)

        # 读取音频文件
        if isinstance(audio_file_path, str):
            # 文件路径就读取文件内容
            with open(audio_file_path, "rb") as f:
                audio_data = f.read()
        elif isinstance(audio_file_path, bytes):
            # 音频数据直接用
            audio_data = audio_file_path
        else:
            # 不知道是什么类型，报错
            raise TypeError("audio_file_path 必须是文件路径(str)或音频数据(bytes)")
            
        # 分帧发送
        frame_size = 1280

        ws = websocket.WebSocketApp(
            url,
            on_open=lambda ws: self._on_open(ws, audio_data, frame_size, audio_format, encoding),
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.run_forever()
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
                self.result_text += cw.get("w", "")

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

        ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.run_forever()

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
    test_audio = "cv-test/cv-corpus-25.0-2026-03-09/zh-CN/clips/common_voice_zh-CN_18524189.mp3"  # 替换为你的测试音频文件路径
    # 支持格式: pcm(16k/8k 16bit 单声道) / mp3(仅中英文)
    result = asr.recognize(test_audio,
                           audio_format="audio/L16;rate=16000",
                           encoding="raw")
    print(f"识别结果: {result}")

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
