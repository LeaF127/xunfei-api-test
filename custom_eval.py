#!/usr/bin/env python3
"""
自定义 ASR 评估脚本 — 从 JSONL 读取数据集，调用自定义 URL 的 ASR 服务，计算 CER

用法:
    # 基本用法 (默认适配 dotcasr 格式)
    python custom_eval.py --dataset test.jsonl --url http://125.77.202.194:58113/dotcasr

    # 自定义参数
    python custom_eval.py --dataset test.jsonl --url http://localhost:8080/asr \\
        --userid myuser --token mytoken --workers 4 --output results/

    # 指定音频格式 (默认 wav)
    python custom_eval.py --dataset test.jsonl --url http://localhost:8080/asr --audio-format mp3

JSONL 数据集格式 (每行一个 JSON):
    {"audio": "/path/to/audio1.wav", "ref": "参考文本一"}
    {"audio": "/path/to/audio2.mp3", "ref": "参考文本二"}

ASR 请求格式 (默认 dotcasr):
    POST multipart/form-data
    字段: userid=xxx, token=xxx, file=@audio.wav

ASR 响应格式 (默认):
    {"result": "识别文本", "errCode": "0"}
"""

import argparse
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

from utils.metrics import calculate_cer

# ---------- 线程锁打印 ----------

_print_lock = threading.Lock()

def _sync_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)

# ---------- JSONL 加载 ----------

def load_jsonl(path: str) -> list[dict]:
    """加载 JSONL 文件，每行一个 JSON 对象"""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"⚠ 第 {lineno} 行 JSON 解析失败: {e}")
                continue

            audio = obj.get("audio") or obj.get("path") or obj.get("file")
            ref = obj.get("ref") or obj.get("text") or obj.get("sentence")

            if not audio:
                print(f"⚠ 第 {lineno} 行缺少音频路径字段 (audio/path/file)")
                continue
            if ref is None:
                print(f"⚠ 第 {lineno} 行缺少参考文本字段 (ref/text/sentence)")
                continue

            items.append({"audio": audio, "ref": ref})

    return items

# ---------- ASR 请求 ----------

def call_asr(url: str, audio_path: str, userid: str, token: str,
             audio_format: str = "wav", timeout: int = 30) -> tuple[str, float]:
    """
    调用自定义 ASR 服务

    Args:
        url: ASR 服务地址
        audio_path: 音频文件路径
        userid: 用户 ID
        token: 认证 token
        audio_format: 音频格式 (用于 Content-Type)
        timeout: 请求超时秒数

    Returns:
        (识别文本, 请求耗时秒数)
    """
    start = time.perf_counter()

    mime_map = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "pcm": "audio/pcm",
    }
    mime = mime_map.get(audio_format.lower(), "application/octet-stream")

    filename = os.path.basename(audio_path)

    with open(audio_path, "rb") as f:
        files = {"file": (filename, f, mime)}
        data = {"userid": userid, "token": token}

        resp = requests.post(url, files=files, data=data, timeout=timeout)

    elapsed = time.perf_counter() - start

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    result = resp.json()

    # 检查错误码
    err_code = result.get("errCode") or result.get("err_code") or result.get("code")
    if err_code is not None and str(err_code) not in ("0", "0", "success", "ok"):
        err_msg = result.get("errMsg") or result.get("message") or ""
        raise RuntimeError(f"ASR 服务错误: errCode={err_code}, {err_msg}")

    # 提取识别文本
    text = ""
    # 优先取 result 字段
    r = result.get("result")
    if isinstance(r, str):
        text = r
    elif isinstance(r, dict):
        text = r.get("text") or r.get("transcription") or ""
    elif isinstance(r, list) and r:
        text = r[0] if isinstance(r[0], str) else r[0].get("text", "")

    # 回退: 直接取 text 字段
    if not text:
        text = result.get("text", "")

    return text.strip(), elapsed

# ---------- 单条处理 ----------

def process_one(url: str, userid: str, token: str,
                seq: int, total: int, item: dict,
                audio_format: str, timeout: int) -> dict:
    """处理单条数据"""
    lines = []
    audio_path = item["audio"]
    ref = item["ref"]

    lines.append(f"\n[{seq}/{total}] {os.path.basename(audio_path)}")
    lines.append(f"  参考: {ref}")

    rec = {
        "index": seq,
        "audio": audio_path,
        "ref": ref,
        "hyp": "",
        "cer": 1.0,
        "time_ms": None,
        "error": None,
    }

    try:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频不存在: {audio_path}")

        hyp_text, elapsed = call_asr(url, audio_path, userid, token,
                                     audio_format=audio_format, timeout=timeout)

        cer = calculate_cer(ref, hyp_text)
        rec["hyp"] = hyp_text
        rec["cer"] = round(cer, 4)
        rec["time_ms"] = round(elapsed * 1000, 1)

        lines.append(f"  识别: {hyp_text}")
        lines.append(f"  CER:  {cer:.4f}")
        lines.append(f"  耗时: {rec['time_ms']:.0f} ms")

    except Exception as e:
        rec["error"] = str(e)
        lines.append(f"  ❌ 错误: {e}")

    _sync_print("\n".join(lines))
    return rec

# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser(description="自定义 ASR 评估 — JSONL 数据集 + 自定义 URL")
    ap.add_argument("--dataset", required=True, help="JSONL 数据集路径")
    ap.add_argument("--url", required=True, help="ASR 服务 URL")
    ap.add_argument("--userid", default="benchmark", help="用户 ID (默认 benchmark)")
    ap.add_argument("--token", default="benchmark", help="认证 token (默认 benchmark)")
    ap.add_argument("--audio-format", default="wav",
                    help="音频格式 wav/mp3/ogg/flac (默认 wav)")
    ap.add_argument("--timeout", type=int, default=30, help="单次请求超时秒数 (默认 30)")
    ap.add_argument("--workers", type=int, default=1, help="并发路数 (默认 1)")
    ap.add_argument("--output", default=None,
                    help="输出目录 (默认 outputs/custom_eval_<timestamp>)")
    ap.add_argument("--limit", type=int, default=None, help="处理条数上限")
    args = ap.parse_args()

    # 加载数据集
    items = load_jsonl(args.dataset)
    if not items:
        sys.exit("❌ 未加载到有效数据")

    if args.limit:
        items = items[:args.limit]

    total = len(items)
    print(f"✅ 加载数据集: {args.dataset} ({total} 条)")
    print(f"⚡ ASR URL: {args.url} | 并发: {args.workers} | 音频格式: {args.audio_format}")

    # 输出目录
    out_dir = args.output or f"outputs/custom_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(out_dir, exist_ok=True)

    # 并发处理
    results = []
    ok_count, fail_count = 0, 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for i, item in enumerate(items):
            future = executor.submit(
                process_one, args.url, args.userid, args.token,
                i + 1, total, item, args.audio_format, args.timeout
            )
            futures[future] = i + 1

        for future in as_completed(futures):
            seq = futures[future]
            try:
                rec = future.result()
                results.append(rec)
                if rec["error"] is None:
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                _sync_print(f"  ❌ [{seq}] 执行异常: {e}")
                fail_count += 1

    # 排序
    results.sort(key=lambda r: r["index"])

    # 统计
    valid = [r for r in results if r["error"] is None]
    cer_values = [r["cer"] for r in valid]
    time_values = [r["time_ms"] for r in valid if r["time_ms"] is not None]
    avg_cer = sum(cer_values) / len(cer_values) if cer_values else 0
    avg_time = sum(time_values) / len(time_values) if time_values else 0

    # 保存结果
    summary = {
        "total": len(results), "success": ok_count, "fail": fail_count,
        "url": args.url, "dataset": args.dataset,
        "average_cer": round(avg_cer, 4),
        "average_time_ms": round(avg_time, 1),
        "timestamp": datetime.now().isoformat(),
        "results": results,
    }

    json_path = os.path.join(out_dir, "eval_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    csv_path = os.path.join(out_dir, "eval_results.csv")
    csv_data = [{"index": r["index"], "audio": r["audio"], "ref": r["ref"],
                 "hyp": r["hyp"], "cer": r["cer"], "time_ms": r["time_ms"],
                 "error": r["error"]} for r in results]
    try:
        import pandas as pd
        pd.DataFrame(csv_data).to_csv(csv_path, index=False, encoding="utf-8-sig")
    except ImportError:
        import csv
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            if csv_data:
                w = csv.DictWriter(f, fieldnames=csv_data[0].keys())
                w.writeheader()
                w.writerows(csv_data)

    # 打印汇总
    print(f"\n{'='*50}")
    print(f"🎉 完成！  成功 {ok_count} | 失败 {fail_count} | 平均 CER {avg_cer:.4f}")
    print(f"   平均耗时: {avg_time:.0f} ms")
    print(f"   结果 → {out_dir}/")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
