#!/usr/bin/env python3
"""ASR 单条快速测试 — 支持 xunfei / aliyun"""

import argparse
from providers import get_asr
from utils.audio import resample_streaming


def main():
    ap = argparse.ArgumentParser(description="ASR 单条识别测试")
    ap.add_argument("--audio",   default="test.mp3", help="音频文件路径")
    ap.add_argument("--provider", default="xunfei", choices=["xunfei", "aliyun"],
                    help="服务方")
    args = ap.parse_args()

    print("=" * 50)
    print(f"ASR 单条测试 — {args.provider}")
    print("=" * 50)

    asr = get_asr(args.provider)
    audio_bytes = resample_streaming(args.audio)

    if args.provider == "xunfei":
        result = asr.recognize(audio_bytes)
    else:
        result = asr.recognize(audio_bytes, audio_format="wav")

    print(f"识别结果: {result}")
    if asr.ttft is not None:
        print(f"TTFT (首字节延迟): {asr.ttft*1000:.1f} ms")
    if asr.total_time is not None:
        print(f"总耗时: {asr.total_time*1000:.1f} ms")
    if asr.rtf is not None:
        print(f"RTF (实时率): {asr.rtf:.4f}")


if __name__ == "__main__":
    main()
