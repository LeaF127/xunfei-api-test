#!/usr/bin/env python3
"""
统一批量评测脚本 — 支持 xunfei / aliyun 多服务方
用法:
    python batch.py --provider xunfei --limit 400 --workers 2
    python batch.py --provider aliyun  --limit 100 --workers 1
"""

import os
import sys
import json
import time
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from providers import get_asr, get_tts
from utils.audio import resample_streaming
from utils.metrics import calculate_cer

# ---------- 线程锁打印 ----------

_print_lock = threading.Lock()

def _sync_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)

# ---------- 增量：尝试加载已有结果 ----------

def try_load_existing(seq, output_asr_dir):
    path = os.path.join(output_asr_dir, f"result_{seq:04d}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec = json.load(f)
            return rec, True
        except (json.JSONDecodeError, IOError):
            return None, False
    return None, False

# ---------- 单条处理 ----------

def process_one(provider, seq, total, fname, sentence, audio, tts_out,
                output_asr_dir):
    asr_api = get_asr(provider)
    tts_api = get_tts(provider)

    _sync_print(f"\n[{seq}/{total}] {fname}")
    _sync_print(f"  原文: {sentence}")

    rec = {
        "index": seq, "filename": fname, "ground_truth": sentence,
        "provider": provider,
        "asr_result": "", "cer": 1.0,
        "asr_ttft_ms": None, "asr_total_time_ms": None, "asr_rtf": None,
        "tts_ttft_ms": None, "tts_total_time_ms": None, "tts_rtf": None,
        "timestamp": datetime.now().isoformat(),
    }
    ok = True

    # ---- ASR ----
    if not os.path.exists(audio):
        _sync_print(f"  ⚠ 音频不存在: {audio}")
        ok = False
    else:
        try:
            resampled = resample_streaming(audio)
            if provider == "xunfei":
                asr_text = asr_api.recognize(resampled)
            else:
                asr_text = asr_api.recognize(resampled, audio_format="wav")

            cer = calculate_cer(sentence, asr_text)
            rec["asr_result"] = asr_text
            rec["cer"] = round(cer, 4)
            rec["asr_ttft_ms"]      = round(asr_api.ttft * 1000, 1)  if asr_api.ttft      is not None else None
            rec["asr_total_time_ms"] = round(asr_api.total_time * 1000, 1) if asr_api.total_time is not None else None
            rec["asr_rtf"]          = round(asr_api.rtf, 4)           if asr_api.rtf       is not None else None
        except Exception as e:
            _sync_print(f"  ❌ ASR 异常: {e}")
            ok = False

    _sync_print(f"  识别: {rec['asr_result']}")
    _sync_print(f"  CER:  {rec['cer']:.4f}")
    if rec["asr_ttft_ms"] is not None:
        _sync_print(f"  ASR TTFT: {rec['asr_ttft_ms']:.1f} ms | 总耗时: {rec['asr_total_time_ms']:.1f} ms | RTF: {rec['asr_rtf']:.4f}")

    # ---- TTS ----
    try:
        r = tts_api.synthesize(sentence, output_file=tts_out)
        rec["tts_ttft_ms"]      = round(tts_api.ttft * 1000, 1)  if tts_api.ttft      is not None else None
        rec["tts_total_time_ms"] = round(tts_api.total_time * 1000, 1) if tts_api.total_time is not None else None
        rec["tts_rtf"]          = round(tts_api.rtf, 4)           if tts_api.rtf       is not None else None
        if r:
            _sync_print(f"  ✅ TTS → {tts_out}")
            if rec["tts_ttft_ms"] is not None:
                _sync_print(f"  TTS TTFT: {rec['tts_ttft_ms']:.1f} ms | 总耗时: {rec['tts_total_time_ms']:.1f} ms | RTF: {rec['tts_rtf']:.4f}")
        else:
            _sync_print(f"  ❌ TTS 合成失败")
            ok = False
    except Exception as e:
        _sync_print(f"  ❌ TTS 异常: {e}")
        ok = False

    time.sleep(0.1)

    with open(os.path.join(output_asr_dir, f"result_{seq:04d}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    return rec, ok

# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser(description="ASR + TTS 批量评测")
    ap.add_argument("--provider",  default="xunfei", choices=["xunfei", "aliyun"],
                    help="服务方 (xunfei / aliyun)")
    ap.add_argument("--data_root",  default="cv-test/cv-corpus-25.0-2026-03-09/zh-CN_subset_5000",
                    help="数据集目录路径")
    ap.add_argument("--limit",      type=int, default=400, help="处理条数上限")
    ap.add_argument("--output_asr", default=None, help="ASR 结果输出目录 (默认 outputs/<provider>/asr)")
    ap.add_argument("--output_tts", default=None, help="TTS 音频输出目录 (默认 outputs/<provider>/tts)")
    ap.add_argument("--voice",      default=None, help="TTS 发音人")
    ap.add_argument("--workers",    type=int, default=2, help="并发路数")
    args = ap.parse_args()

    provider = args.provider
    out_asr = args.output_asr or f"outputs/{provider}/asr"
    out_tts = args.output_tts or f"outputs/{provider}/tts"

    tsv_path  = os.path.join(args.data_root, "test_subset.tsv")
    clips_dir = os.path.join(args.data_root, "clips")

    if not os.path.exists(tsv_path):
        sys.exit(f"❌ TSV 不存在: {tsv_path}")

    df = pd.read_csv(tsv_path, sep="\t").head(args.limit)
    print(f"✅ 加载 {len(df)} 条数据  (TSV: {tsv_path})")
    print(f"⚡ 服务方: {provider} | 并发路数: {args.workers}")

    os.makedirs(out_asr, exist_ok=True)
    os.makedirs(out_tts, exist_ok=True)

    total = len(df)

    # 增量模式
    cached_results = []
    pending_tasks  = []
    skipped = 0

    for i, row in df.iterrows():
        fname    = row["path"]
        sentence = row["sentence"]
        audio    = os.path.join(clips_dir, fname)
        seq      = i + 1
        tts_out  = os.path.join(out_tts, fname)

        rec, hit = try_load_existing(seq, out_asr)
        if hit:
            cached_results.append(rec)
            skipped += 1
        else:
            pending_tasks.append((provider, seq, total, fname, sentence, audio, tts_out, out_asr))

    new_count = len(pending_tasks)
    print(f"📦 增量模式: 跳过已缓存 {skipped} 条 | 待请求 {new_count} 条")

    new_results = []
    ok_count, fail_count = 0, 0

    if new_count > 0:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(process_one, *task): task[1]
                for task in pending_tasks
            }
            for future in as_completed(futures):
                seq = futures[future]
                try:
                    rec, ok = future.result()
                    new_results.append(rec)
                    if ok:
                        ok_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    _sync_print(f"  ❌ [{seq}] 执行异常: {e}")
                    fail_count += 1

    results = cached_results + new_results
    results.sort(key=lambda r: r["index"])

    avg_cer = sum(r["cer"] for r in results) / len(results) if results else 0

    def _avg(key):
        vals = [r[key] for r in results if r[key] is not None]
        return sum(vals) / len(vals) if vals else None

    summary = {
        "total": len(results), "success": ok_count + skipped, "fail": fail_count,
        "cached": skipped, "new_processed": new_count,
        "provider": provider, "workers": args.workers,
        "average_cer": round(avg_cer, 4),
        "average_asr_ttft_ms":      round(_avg("asr_ttft_ms"), 1)       if _avg("asr_ttft_ms")      is not None else None,
        "average_asr_total_time_ms": round(_avg("asr_total_time_ms"), 1) if _avg("asr_total_time_ms") is not None else None,
        "average_asr_rtf":          round(_avg("asr_rtf"), 4)           if _avg("asr_rtf")          is not None else None,
        "average_tts_ttft_ms":      round(_avg("tts_ttft_ms"), 1)       if _avg("tts_ttft_ms")      is not None else None,
        "average_tts_total_time_ms": round(_avg("tts_total_time_ms"), 1) if _avg("tts_total_time_ms") is not None else None,
        "average_tts_rtf":          round(_avg("tts_rtf"), 4)           if _avg("tts_rtf")          is not None else None,
        "timestamp": datetime.now().isoformat(),
        "results": results,
    }
    with open(os.path.join(out_asr, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    pd.DataFrame(results).to_csv(os.path.join(out_asr, "summary.csv"),
                                  index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"🎉 完成！  总计 {len(results)} | 缓存 {skipped} | 新请求 {new_count} | 失败 {fail_count} | 平均CER {avg_cer:.4f}")
    print(f"   服务方: {provider} | 并发: {args.workers}")
    if _avg("asr_ttft_ms") is not None:
        print(f"   ASR 平均 TTFT: {_avg('asr_ttft_ms'):.1f} ms | 平均 RTF: {_avg('asr_rtf'):.4f}")
    if _avg("tts_ttft_ms") is not None:
        print(f"   TTS 平均 TTFT: {_avg('tts_ttft_ms'):.1f} ms | 平均 RTF: {_avg('tts_rtf'):.4f}")
    print(f"   ASR → {out_asr}/")
    print(f"   TTS → {out_tts}/")


if __name__ == "__main__":
    main()
