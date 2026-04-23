#!/usr/bin/env python3
"""
讯飞 ASR / TTS 批量处理脚本
"""

import os, sys, json, time, base64, hashlib, hmac, argparse, threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

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

# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_root",  default="cv-test/cv-corpus-25.0-2026-03-09/zh-CN_subset_5000",
                    help="zh-CN_subset_5000 目录路径")
    ap.add_argument("--limit",      type=int, default=400, help="处理条数上限")
    ap.add_argument("--output_asr", default="outputs/asr", help="ASR 结果输出目录")
    ap.add_argument("--output_tts", default="outputs/tts", help="TTS 音频输出目录")
    ap.add_argument("--voice",      default="xiaoyan", help="TTS 发音人")
    args = ap.parse_args()

    tsv_path   = os.path.join(args.data_root, "test_subset.tsv")
    clips_dir  = os.path.join(args.data_root, "clips")

    if not os.path.exists(tsv_path):
        sys.exit(f"❌ TSV 不存在: {tsv_path}")

    df = pd.read_csv(tsv_path, sep="\t").head(args.limit)
    print(f"✅ 加载 {len(df)} 条数据  (TSV: {tsv_path})")

    os.makedirs(args.output_asr, exist_ok=True)
    os.makedirs(args.output_tts, exist_ok=True)

    asr_api = XunFeiASR(APP_ID, API_KEY, API_SECRET)
    tts_api = XunFeiTTS(APP_ID, API_KEY, API_SECRET)

    results = []
    ok, fail = 0, 0

    for i, row in df.iterrows():
        fname    = row["path"]
        sentence = row["sentence"]
        audio    = os.path.join(clips_dir, fname)
        seq      = i + 1

        print(f"\n[{seq}/{len(df)}] {fname}")
        print(f"  原文: {sentence}")

        # ---- ASR ----
        asr_text, cer = "", 1.0
        if not os.path.exists(audio):
            print(f"  ⚠ 音频不存在: {audio}")
            fail += 1
        else:
            try:
                resampled = resample_streaming(audio)
                asr_text  = asr_api.recognize(resampled)
                cer       = calculate_cer(sentence, asr_text)
                ok += 1
                # 清理临时重采样文件
                if resampled != audio and os.path.exists(resampled):
                    os.remove(resampled)
            except Exception as e:
                print(f"  ❌ ASR 异常: {e}")
                fail += 1

        print(f"  识别: {asr_text}")
        print(f"  CER:  {cer:.4f}")

        # 保存单条 ASR 结果
        rec = {"index": seq, "filename": fname, "ground_truth": sentence,
               "asr_result": asr_text, "cer": round(cer, 4),
               "timestamp": datetime.now().isoformat()}
        results.append(rec)
        with open(os.path.join(args.output_asr, f"result_{seq:04d}.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)

        # ---- TTS ----
        tts_out = os.path.join(args.output_tts, fname)
        try:
            r = tts_api.synthesize(sentence, output_file=tts_out)
            if r:
                print(f"  ✅ TTS → {tts_out}")
            else:
                print(f"  ❌ TTS 合成失败")
        except Exception as e:
            print(f"  ❌ TTS 异常: {e}")

        time.sleep(0.1)

    # ---- 汇总 ----
    avg_cer = sum(r["cer"] for r in results) / len(results) if results else 0
    summary = {"total": len(results), "success": ok, "fail": fail,
               "average_cer": round(avg_cer, 4), "timestamp": datetime.now().isoformat(),
               "results": results}
    with open(os.path.join(args.output_asr, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    pd.DataFrame(results).to_csv(os.path.join(args.output_asr, "summary.csv"),
                                  index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"🎉 完成！  总计 {len(results)} | 成功 {ok} | 失败 {fail} | 平均CER {avg_cer:.4f}")
    print(f"   ASR → {args.output_asr}/")
    print(f"   TTS → {args.output_tts}/")


if __name__ == "__main__":
    main()

