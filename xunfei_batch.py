#!/usr/bin/env python3
"""
讯飞 ASR / TTS 批量处理脚本（多路并发版）
"""

import os, sys, json, time, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import APP_ID, API_KEY, API_SECRET
from xunfei_asr_tts_demo import XunFeiASR, XunFeiTTS
from util import resample_streaming

# ---------- CER 计算 ----------

def calculate_cer(reference, hypothesis):
    """编辑距离 / 参考长度 → 字错误率"""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    if not hypothesis:
        return 1.0
    ref, hyp = list(reference), list(hypothesis)
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]) + 1
    return dp[n][m] / n

# ---------- 线程锁打印（避免输出交错） ----------

_print_lock = threading.Lock()

def _sync_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)

# ---------- 单条处理 ----------

def process_one(seq, total, fname, sentence, audio, tts_out,
                output_asr_dir):
    """
    处理单条数据（每个线程独立执行）
    每个线程创建独立的 ASR/TTS 实例，避免状态冲突
    """
    # 每个线程独立创建 API 实例
    asr_api = XunFeiASR(APP_ID, API_KEY, API_SECRET)
    tts_api = XunFeiTTS(APP_ID, API_KEY, API_SECRET)

    _sync_print(f"\n[{seq}/{total}] {fname}")
    _sync_print(f"  原文: {sentence}")

    rec = {
        "index": seq, "filename": fname, "ground_truth": sentence,
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
            asr_text  = asr_api.recognize(resampled)
            cer       = calculate_cer(sentence, asr_text)
            rec["asr_result"] = asr_text
            rec["cer"] = round(cer, 4)
            rec["asr_ttft_ms"]      = round(asr_api.ttft * 1000, 1)  if asr_api.ttft      is not None else None
            rec["asr_total_time_ms"] = round(asr_api.total_time * 1000, 1) if asr_api.total_time is not None else None
            rec["asr_rtf"]          = round(asr_api.rtf, 4)           if asr_api.rtf       is not None else None
            # 清理临时重采样文件
            if resampled != audio and os.path.exists(resampled):
                os.remove(resampled)
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

    # 保存单条结果 JSON
    with open(os.path.join(output_asr_dir, f"result_{seq:04d}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    return rec, ok

# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_root",  default="cv-test/cv-corpus-25.0-2026-03-09/zh-CN_subset_5000",
                    help="zh-CN_subset_5000 目录路径")
    ap.add_argument("--limit",      type=int, default=400, help="处理条数上限")
    ap.add_argument("--output_asr", default="outputs/asr", help="ASR 结果输出目录")
    ap.add_argument("--output_tts", default="outputs/tts", help="TTS 音频输出目录")
    ap.add_argument("--voice",      default="xiaoyan", help="TTS 发音人")
    ap.add_argument("--workers",    type=int, default=2, help="并发路数（默认 2）")
    args = ap.parse_args()

    tsv_path   = os.path.join(args.data_root, "test_subset.tsv")
    clips_dir  = os.path.join(args.data_root, "clips")

    if not os.path.exists(tsv_path):
        sys.exit(f"❌ TSV 不存在: {tsv_path}")

    df = pd.read_csv(tsv_path, sep="\t").head(args.limit)
    print(f"✅ 加载 {len(df)} 条数据  (TSV: {tsv_path})")
    print(f"⚡ 并发路数: {args.workers}")

    os.makedirs(args.output_asr, exist_ok=True)
    os.makedirs(args.output_tts, exist_ok=True)

    total = len(df)
    results = []
    ok_count, fail_count = 0, 0

    # 构建任务列表
    tasks = []
    for i, row in df.iterrows():
        fname    = row["path"]
        sentence = row["sentence"]
        audio    = os.path.join(clips_dir, fname)
        seq      = i + 1
        tts_out  = os.path.join(args.output_tts, fname)
        tasks.append((seq, total, fname, sentence, audio, tts_out, args.output_asr))

    # 多路并发执行
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_one, *task): task[0]
            for task in tasks
        }
        for future in as_completed(futures):
            seq = futures[future]
            try:
                rec, ok = future.result()
                results.append(rec)
                if ok:
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                _sync_print(f"  ❌ [{seq}] 执行异常: {e}")
                fail_count += 1

    # 按 index 排序，保证输出顺序
    results.sort(key=lambda r: r["index"])

    # ---- 汇总 ----
    avg_cer = sum(r["cer"] for r in results) / len(results) if results else 0

    def _avg(key):
        vals = [r[key] for r in results if r[key] is not None]
        return sum(vals) / len(vals) if vals else None

    summary = {
        "total": len(results), "success": ok_count, "fail": fail_count,
        "workers": args.workers,
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
    with open(os.path.join(args.output_asr, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    pd.DataFrame(results).to_csv(os.path.join(args.output_asr, "summary.csv"),
                                  index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"🎉 完成！  总计 {len(results)} | 成功 {ok_count} | 失败 {fail_count} | 平均CER {avg_cer:.4f}")
    print(f"   并发路数: {args.workers}")
    if _avg("asr_ttft_ms") is not None:
        print(f"   ASR 平均 TTFT: {_avg('asr_ttft_ms'):.1f} ms | 平均 RTF: {_avg('asr_rtf'):.4f}")
    if _avg("tts_ttft_ms") is not None:
        print(f"   TTS 平均 TTFT: {_avg('tts_ttft_ms'):.1f} ms | 平均 RTF: {_avg('tts_rtf'):.4f}")
    print(f"   ASR → {args.output_asr}/")
    print(f"   TTS → {args.output_tts}/")


if __name__ == "__main__":
    main()
