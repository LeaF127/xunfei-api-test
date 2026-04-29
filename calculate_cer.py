#!/usr/bin/env python3
"""
批量计算 CER（字错误率）脚本

用法：
    python calculate_cer.py --dir outputs/xunfei/asr
    python calculate_cer.py --dir outputs/aliyun/asr --output cer_results.json
    python calculate_cer.py --dir outputs/aliyun/asr --format csv

输出格式：
  JSON: cer_results.json
  CSV: cer_results.csv
"""

import argparse
import json
import os
import re
import unicodedata
from pathlib import Path
from datetime import datetime

import pandas as pd

from utils.metrics import calculate_cer

def levenshtein_distance(s1, s2):
    """计算两个字符串的编辑距离"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


# CJK 语种 (中日韩) - 评测时去除空格
# 韩语(ko)使用空格作为词分隔符, 保留空格为NIST标准做法
# 此处按需求去除韩语空格, 以排除ASR模型띄어쓰기缺陷对CER的影响
CJK_LANGUAGES = {"zh", "zh_paraformer", "zh_qwen3asr", "ja", "ko"}


# 阿拉伯数字 → 中文/日文字符映射 (逐位转换)
_ZH_DIGIT_MAP = str.maketrans("0123456789", "零一二三四五六七八九")
_JA_DIGIT_MAP = str.maketrans("0123456789", "〇一二三四五六七八九")


def _normalize_digits_cjk(text, lang):
    """CJK 语种: 将阿拉伯数字逐位转为对应语言的字符, 统一数字表示形式
    例如: "2014年" → "二零一四年", "100元" → "一零零元"
    这样 ASR 输出 "二零一四年" 和参考文本 "2014年" 就能正确匹配
    """
    if lang in ("zh", "zh_paraformer", "zh_qwen3asr"):
        return text.translate(_ZH_DIGIT_MAP)
    elif lang == "ja":
        return text.translate(_JA_DIGIT_MAP)
    # 韩语: 保留阿拉伯数字 (韩文 ASR 通常输出阿拉伯数字)
    return text


def normalize_text(text, lang):
    """
    文本归一化 (标准 ASR 评测流程, 符合 NIST/HuggingFace 标准)
    1. Unicode NFC 归一化 (组合字符、全角/半角等)
    2. 去除 SPS 标注标记 (<disfluency>, <noise>, <unclear>, [um], [noise] 等)
    3. 大小写归一化: 全部转小写
    4. 去除标点符号
    5. CJK 语种: 阿拉伯数字转为中文/日文字符, 统一数字表示
    6. CJK 语种去除空格, 非 CJK 语种保留空格
    7. 去除首尾空白
    """
    # Unicode NFC 归一化 (NIST 标准推荐)
    text = unicodedata.normalize("NFC", text)

    # 去除 SPS 标注标记: <...> 和 [...]
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[[^\]]+\]", "", text)

    # 统一转小写
    text = text.lower()

    # 去除标点 (保留字母、数字、空格和 CJK 字符)
    text = "".join(
        ch for ch in text
        if unicodedata.category(ch).startswith(("L", "N", "Zs"))
        or ch == " "  # 确保普通空格保留
    )

    # CJK 语种: 阿拉伯数字 → 中文/日文字符 (统一数字表示形式)
    if lang in CJK_LANGUAGES:
        text = _normalize_digits_cjk(text, lang)

    # CJK 语种去除空格
    if lang in CJK_LANGUAGES:
        text = text.replace(" ", "")

    # 合并连续空格为单个空格 (非 CJK)
    if lang not in CJK_LANGUAGES:
        text = re.sub(r" +", " ", text)

    return text.strip()


def compute_cer_detail(reference, hypothesis, lang="zh"):
    """
    计算 CER 的详细结果, 返回编辑距离和参考字符数
    用于微平均聚合 (NIST/HuggingFace 标准聚合方式)
    
    Returns: (distance, ref_len) 或 (inf, 0) 表示空参考
    """
    ref_norm = normalize_text(reference, lang)
    hyp_norm = normalize_text(hypothesis, lang)
    ref_chars = list(ref_norm)
    hyp_chars = list(hyp_norm)
    distance = levenshtein_distance(ref_chars, hyp_chars)
    ref_len = len(ref_chars)
    return distance, ref_len


def compute_cer(reference, hypothesis, lang="zh"):
    """
    计算 CER (Character Error Rate) - 符合 NIST/HuggingFace 标准 ASR 评测规范
    
    CER = (S + D + I) / N = Levenshtein编辑距离 / 参考文本字符数
    
    标准处理流程:
    1. Unicode NFC 归一化
    2. 去除标注标记
    3. 大小写归一化
    4. 去除标点
    5. CJK 语种去空格, 非 CJK 保留空格(空格也是字符)
    
    注意: CER 可以超过 1.0 (当插入数很多时), 这是正常的
    """
    distance, ref_len = compute_cer_detail(reference, hypothesis, lang)
    if ref_len == 0:
        return 0.0 if distance == 0 else float("inf")
    return distance / ref_len


def load_json_files(directory: str):
    """
    遍历目录，加载所有 JSON 文件
    
    Args:
        directory: 目录路径
    
    Yields:
        (filename, data) 元组
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")

    for json_file in sorted(dir_path.glob("*.json")):
        # 跳过 summary.json
        if json_file.name == "summary.json":
            continue
        
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            yield json_file.name, data
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠ 无法读取 {json_file.name}: {e}")
            continue


def calculate_directory_cer(directory: str):
    """
    计算指定目录下所有 JSON 文件的 CER
    
    Args:
        directory: 结果目录路径
    
    Returns:
        results: 结果列表，每个元素包含文件名、原文、识别结果、CER
    """
    results = []
    
    for filename, data in load_json_files(directory):
        ground_truth = data.get("ground_truth", "")
        asr_result = data.get("asr_result", "")
        
        # 跳过空结果
        if not ground_truth or not asr_result:
            continue
        
        # 计算 CER
        cer = compute_cer(ground_truth, asr_result)
        
        results.append({
            "filename": filename,
            "ground_truth": ground_truth,
            "asr_result": asr_result,
            "cer": round(cer, 4),
        })
    
    return results


def main():
    ap = argparse.ArgumentParser(description="批量计算 CER（字错误率）")
    ap.add_argument("--dir", default=None, help="结果目录路径（默认：outputs/xunfei/asr）")
    ap.add_argument("--provider", default="xunfei", choices=["xunfei", "aliyun", "doubao"], help="服务方（用于设置默认目录）")
    ap.add_argument("--output", default="cer_results.json", help="输出文件路径（默认 cer_results.json）")
    ap.add_argument("--format", choices=["json", "csv"], default="json", help="输出格式（默认 json）")
    ap.add_argument("--fuzzy", action="store_true", help="如果启用，忽略CER大于等于1.0的结果")
    args = ap.parse_args()
    
    # 设置默认目录
    if args.dir is None:
        args.dir = f"outputs/{args.provider}/asr"
        print(f"ℹ 未指定目录，使用默认目录: {args.dir}")
    
    print(f"📂 开始处理目录: {args.dir}")
    
    # 计算所有文件的 CER
    results = calculate_directory_cer(args.dir)
    
    if not results:
        print("⚠ 没有找到有效的结果文件")
        return
    
    # 计算统计信息
    cer_values = [r["cer"] for r in results]
    if args.fuzzy:
        cer_values = [cer for cer in cer_values if cer < 1.0]
    avg_cer = sum(cer_values) / len(cer_values)
    max_cer = max(cer_values)
    min_cer = min(cer_values)
    
    # 准备输出数据
    summary = {
        "directory": args.dir,
        "provider": args.provider,
        "total_files": len(results),
        "average_cer": round(avg_cer, 4),
        "max_cer": round(max_cer, 4),
        "min_cer": round(min_cer, 4),
        "timestamp": datetime.now().isoformat(),
        "results": results,
    }
    
    # 保存 JSON 格式
    if args.format == "json":
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"✅ JSON 结果已保存: {args.output}")
    
    # 保存 CSV 格式
    if args.format == "csv":
        csv_file = args.output.replace(".json", ".csv")
        pd.DataFrame(results).to_csv(csv_file, index=False, encoding="utf-8-sig")
        print(f"✅ CSV 结果已保存: {csv_file}")
    
    # 打印统计信息
    print(f"\n{'='*50}")
    print(f"📊 统计信息")
    print(f"   总文件数: {len(results)}")
    print(f"   平均 CER: {avg_cer:.4f}")
    print(f"   最高 CER: {max_cer:.4f}")
    print(f"   最低 CER: {min_cer:.4f}")
    
    # 显示前 10 个cer最高的结果
    print(f"\n CER 最高前 10 个结果:")
    sorted_results = sorted(results, key=lambda x: x["cer"], reverse=True)
    for r in sorted_results[:10]:
        print(f"   {r['filename']}: CER={r['cer']:.4f}")
    
    print(f"\n{'='*50}")


if __name__ == "__main__":
    main()
