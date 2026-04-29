#!/usr/bin/env python3
"""
批量计算 CER（字错误率）脚本

用法：
    python calculate_cer.py --dir outputs/xunfei/asr
    python calculate_cer.py --dir outputs/aliyun/asr --output cer_results.json
    python calculate_cer.py --dir outputs/aliyun/asr --format csv
    python calculate_cer.py --dir outputs/aliyun/asr --fuzzy  # 忽略 CER>=1.0 的结果

输出格式：
  JSON: cer_results.json
  CSV: cer_results.csv
"""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime

import pandas as pd

from utils.metrics import calculate_cer


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
        cer = calculate_cer(ground_truth, asr_result)

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
    ap.add_argument("--provider", default="xunfei", choices=["xunfei", "aliyun", "doubao", "doubao_bigmodel"], help="服务方（用于设置默认目录）")
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

    # 显示前 10 个CER最高的结果
    print(f"\n CER 最高前 10 个结果:")
    sorted_results = sorted(results, key=lambda x: x["cer"], reverse=True)
    for r in sorted_results[:10]:
        print(f"   {r['filename']}: CER={r['cer']:.4f}")

    print(f"\n{'='*50}")


if __name__ == "__main__":
    main()
