#!/usr/bin/env python3
"""
豆包大模型流式语音识别 — 单文件长音频转写

功能:
  - 传入单个音频文件进行识别
  - 识别文本带时间戳 (逐句, 精确到毫秒)
  - 支持超过 30 分钟的长音频
  - 结果保存为 JSON 和 TXT

用法:
    python bigmodel_transcribe.py --input audio.wav
    python bigmodel_transcribe.py --input audio.mp3 --audio-format mp3
    python bigmodel_transcribe.py --input audio.wav --output result.json
    DOUBAO_ASR_DEBUG=1 python bigmodel_transcribe.py --input audio.wav

输出格式:
  JSON: {utterances: [{text, start_ms, end_ms}, ...], full_text, duration_ms}
  TXT:  [00:00.742 --> 00:02.082] 碗叫他给打了
"""

import argparse
import json
import os
import struct
import sys
import time
import uuid
import gzip
import threading

import websocket

_DEBUG = os.getenv("DOUBAO_ASR_DEBUG", "").strip() in ("1", "true", "True")


def _debug(msg: str):
    if _DEBUG:
        print(f"[DEBUG] {msg}")


# ==================== V3 二进制协议 ====================

def _build_header(message_type, message_type_specific=0,
                  serialization=1, compression=0):
    return bytes([
        0x11,
        (message_type << 4) | (message_type_specific & 0x0F),
        (serialization << 4) | (compression & 0x0F),
        0x00,
    ])


def _parse_response(data: bytes) -> dict:
    if len(data) < 4:
        return {"msg_type": -1, "msg_specific": 0, "sequence": 0, "payload": {}}
    msg_type = (data[1] >> 4) & 0x0F
    msg_specific = data[1] & 0x0F
    serialization = (data[2] >> 4) & 0x0F
    compression = (data[2]) & 0x0F

    if msg_type == 0x0F:
        if len(data) < 12:
            return {"msg_type": msg_type, "msg_specific": msg_specific,
                    "sequence": 0, "payload": {"code": -1, "message": data.hex()}}
        error_code = struct.unpack(">I", data[4:8])[0]
        error_size = struct.unpack(">I", data[8:12])[0]
        error_msg = data[12:12 + error_size].decode("utf-8", errors="replace")
        return {"msg_type": msg_type, "msg_specific": msg_specific,
                "sequence": 0, "payload": {"code": error_code, "message": error_msg}}

    if len(data) < 12:
        return {"msg_type": msg_type, "msg_specific": msg_specific,
                "sequence": 0, "payload": {}}

    sequence = struct.unpack(">i", data[4:8])[0]
    payload_size = struct.unpack(">I", data[8:12])[0]
    payload_raw = data[12:12 + payload_size]

    if compression == 1 and payload_raw:
        try:
            payload_raw = gzip.decompress(payload_raw)
        except Exception:
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


def _extract_utterances(payload: dict) -> list[dict]:
    utterances = []
    result_obj = payload.get("result", {})
    if isinstance(result_obj, dict):
        for u in result_obj.get("utterances", []):
            if isinstance(u, dict) and "text" in u:
                utterances.append({
                    "text": u["text"],
                    "start_ms": u.get("start_time", 0),
                    "end_ms": u.get("end_time", 0),
                    "definite": u.get("definite", False),
                })
    return utterances


def _extract_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    result_obj = payload.get("result", {})
    if isinstance(result_obj, dict) and "text" in result_obj:
        return result_obj["text"]
    return ""


# ==================== 核心转写 ====================

def transcribe(audio_path: str, api_key: str, resource_id: str,
               audio_format: str = "wav", sample_rate: int = 16000,
               ws_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
               ) -> dict:
    """
    转写单个音频文件，返回带时间戳的结果。
    使用后台线程持续发送音频包，主线程持续接收响应，避免服务端超时。
    """
    with open(audio_path, "rb") as f:
        audio = f.read()

    audio_duration_s = len(audio) / (sample_rate * 2)
    audio_duration_ms = int(audio_duration_s * 1000)
    _debug(f"音频: {len(audio)}B = {audio_duration_s:.1f}s ({audio_duration_s/60:.1f}min)")

    request_id = str(uuid.uuid4())
    ws_headers = [
        f"X-Api-Key: {api_key}",
        f"X-Api-Resource-Id: {resource_id}",
        f"X-Api-Request-Id: {request_id}",
        "X-Api-Sequence: -1",
    ]

    config = {
        "user": {"uid": "transcribe"},
        "audio": {
            "format": audio_format,
            "rate": sample_rate,
            "bits": 16,
            "channel": 1,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "result_type": "full",
            "show_utterances": True,
        },
    }
    config_payload = json.dumps(config, ensure_ascii=False).encode("utf-8")
    config_frame = _build_header(1, 0b0000, 1, 0) + struct.pack(">I", len(config_payload)) + config_payload

    all_utterances = []
    full_text = ""
    start_time = time.perf_counter()

    # 发送线程状态
    send_done = threading.Event()
    send_error = [None]  # 用列表以便线程内赋值

    ws = websocket.WebSocket()
    try:
        _debug(f"连接 {ws_url} ...")
        ws.connect(ws_url, header=ws_headers, max_size=50 * 1024 * 1024, timeout=120)
        _debug("连接成功!")

        # Step 1: 发送 config
        ws.send_binary(config_frame)

        # Step 2: 接收 config ack
        ws.settimeout(30)
        try:
            ack_data = ws.recv()
            if isinstance(ack_data, bytes) and len(ack_data) >= 4:
                ack = _parse_response(ack_data)
                p = ack.get("payload", {})
                _debug(f"Config ack: {json.dumps(p, ensure_ascii=False)[:300]}")
                if isinstance(p, dict) and p.get("code") is not None:
                    if p["code"] not in (0, 20000000):
                        raise RuntimeError(f"配置错误: code={p['code']}, msg={p.get('message','')}")
        except websocket.WebSocketTimeoutException:
            _debug("Config ack 超时, 继续")

        # Step 3: 后台线程持续发送音频包
        bytes_per_chunk = int(sample_rate * 2 * 0.2)  # 200ms
        total_chunks = max(1, (len(audio) + bytes_per_chunk - 1) // bytes_per_chunk)
        _debug(f"共 {total_chunks} 个音频包, 后台线程持续发送...")

        def _sender():
            """后台线程: 持续发送音频包, 不等待响应"""
            try:
                for i in range(total_chunks):
                    chunk = audio[i * bytes_per_chunk : (i + 1) * bytes_per_chunk]
                    is_last = (i == total_chunks - 1)
                    if is_last:
                        header = _build_header(2, 0b0010, 0, 0)
                    else:
                        header = _build_header(2, 0b0000, 0, 0)
                    frame = header + struct.pack(">I", len(chunk)) + chunk
                    ws.send_binary(frame)

                    # 进度打印 (每 100 包, 约 20s)
                    if (i + 1) % 100 == 0 or is_last:
                        progress = (i + 1) / total_chunks * 100
                        elapsed = time.perf_counter() - start_time
                        print(f"\r  发送: {i+1}/{total_chunks} ({progress:.0f}%) "
                              f"{elapsed:.1f}s", end="", flush=True)

                    # 发包间隔: 长音频适当减速避免积压, 短音频快速发完
                    if not is_last:
                        time.sleep(0.02)  # 20ms 间隔, 每秒发 50 包 = 10s 音频/s

                print()  # 换行
                _debug(f"发送完成: {total_chunks} 包")

            except Exception as e:
                send_error[0] = e
            finally:
                send_done.set()

        sender_thread = threading.Thread(target=_sender, daemon=True)
        sender_thread.start()

        # Step 4: 主线程持续接收响应 (与发送并行)
        ws.settimeout(5)
        recv_count = 0

        while not send_done.is_set() or True:
            # 检查发送线程是否出错
            if send_error[0]:
                raise send_error[0]

            try:
                resp_data = ws.recv()
            except websocket.WebSocketTimeoutException:
                # 发送还在进行中, 继续等
                if send_done.is_set():
                    # 发送已结束但没收到数据, 再等一下
                    ws.settimeout(300)
                    try:
                        resp_data = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        _debug("发送完成后等待最终响应超时")
                        break
                    except (websocket.WebSocketConnectionClosedException,
                            ConnectionResetError, OSError):
                        _debug("连接关闭")
                        break
                    ws.settimeout(5)
                else:
                    continue
            except (websocket.WebSocketConnectionClosedException,
                    ConnectionResetError, OSError):
                _debug("连接关闭")
                break

            if not isinstance(resp_data, bytes) or len(resp_data) < 4:
                continue

            resp = _parse_response(resp_data)
            msg_specific = resp.get("msg_specific", 0)
            payload = resp.get("payload", {})

            recv_count += 1

            if resp["msg_type"] == 0x0F:
                raise RuntimeError(f"ASR 错误: {payload}")

            text = _extract_text(payload)
            if text:
                full_text = text

            uts = _extract_utterances(payload)
            all_utterances.extend(uts)

            # 最终响应
            if msg_specific in (0b0010, 0b0011) or resp.get("sequence", 0) < 0:
                _debug(f"收到最终响应 (已发完={send_done.is_set()}, recv={recv_count})")
                break

        # 确保发送线程结束
        sender_thread.join(timeout=10)

        if send_error[0]:
            raise send_error[0]

    finally:
        try:
            ws.close()
        except Exception:
            pass

    elapsed = time.perf_counter() - start_time
    _debug(f"总耗时: {elapsed:.1f}s, 分句数: {len(all_utterances)}")

    # 去重: 按 (start_ms, end_ms) 去重, 同一时间窗口只保留最后一次结果
    # 流式识别会反复返回同一句话, 后来的结果更准确
    seen = {}  # (start_ms, end_ms) -> utterance dict
    for u in all_utterances:
        key = (u["start_ms"], u["end_ms"])
        existing = seen.get(key)
        # definite 结果优先; 同优先级下后者覆盖前者
        if existing is None or u.get("definite") or not existing.get("definite"):
            seen[key] = u

    final_uts = sorted(seen.values(), key=lambda u: u["start_ms"])

    clean_uts = [{"text": u["text"], "start_ms": u["start_ms"], "end_ms": u["end_ms"]}
                 for u in final_uts]

    if not clean_uts and full_text:
        clean_uts = [{"text": full_text, "start_ms": 0, "end_ms": audio_duration_ms}]

    return {
        "utterances": clean_uts,
        "full_text": full_text,
        "duration_ms": audio_duration_ms,
    }


# ==================== 格式化输出 ====================

def format_timestamp(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def save_results(result: dict, json_path: str, txt_path: str | None = None):
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON: {json_path}")

    if txt_path is None:
        txt_path = json_path.rsplit(".", 1)[0] + ".txt"

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"时长: {format_timestamp(result['duration_ms'])}\n")
        f.write(f"全文: {result['full_text']}\n")
        f.write(f"{'='*60}\n\n")
        for u in result["utterances"]:
            start = format_timestamp(u["start_ms"])
            end = format_timestamp(u["end_ms"])
            f.write(f"[{start} --> {end}] {u['text']}\n")
    print(f"✅ TXT:  {txt_path}")


# ==================== 主流程 ====================

def main():
    ap = argparse.ArgumentParser(description="豆包大模型 ASR — 单文件长音频转写 (带时间戳)")
    ap.add_argument("--input", required=True, help="输入音频文件路径")
    ap.add_argument("--output", default=None, help="输出 JSON 路径 (默认 <input>.transcript.json)")
    ap.add_argument("--audio-format", default=None, help="音频格式 wav/mp3/ogg/flac (默认自动判断)")
    ap.add_argument("--sample-rate", type=int, default=16000, help="音频采样率 (默认 16000)")
    ap.add_argument("--api-key", default=None, help="API Key (或设置 DOUBAO_API_KEY)")
    ap.add_argument("--resource-id", default=None, help="Resource ID (或设置 DOUBAO_RESOURCE_ID)")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    api_key = args.api_key or os.getenv("DOUBAO_API_KEY", "")
    resource_id = args.resource_id or os.getenv("DOUBAO_RESOURCE_ID", "volc.bigasr.sauc.duration")

    if not api_key:
        sys.exit("❌ 缺少 API Key, 请通过 --api-key 或 .env 中 DOUBAO_API_KEY 提供")
    if not os.path.exists(args.input):
        sys.exit(f"❌ 文件不存在: {args.input}")

    audio_format = args.audio_format
    if not audio_format:
        ext = os.path.splitext(args.input)[1].lstrip(".").lower()
        audio_format = ext if ext in ("wav", "mp3", "ogg", "flac", "pcm") else "wav"

    output_path = args.output or args.input.rsplit(".", 1)[0] + ".transcript.json"

    print(f"🎤 豆包大模型 ASR 转写")
    print(f"   输入: {args.input}")
    print(f"   格式: {audio_format}, 采样率: {args.sample_rate}")
    print(f"   输出: {output_path}")
    print()

    result = transcribe(
        audio_path=args.input,
        api_key=api_key,
        resource_id=resource_id,
        audio_format=audio_format,
        sample_rate=args.sample_rate,
    )

    save_results(result, output_path)

    uts = result["utterances"]
    print(f"\n{'='*60}")
    print(f"🎉 转写完成！")
    print(f"   时长: {format_timestamp(result['duration_ms'])}")
    print(f"   分句: {len(uts)} 句")
    if uts:
        print(f"   首句: [{format_timestamp(uts[0]['start_ms'])}] {uts[0]['text']}")
        print(f"   末句: [{format_timestamp(uts[-1]['start_ms'])}] {uts[-1]['text']}")
    print(f"   全文: {result['full_text'][:80]}...")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
