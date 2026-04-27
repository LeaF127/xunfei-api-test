"""
字符错误率（CER）计算工具
"""

import re


def _normalize_text(text: str) -> str:
    """
    正规化文本：
    1. 去除标点（保留中文、字母、数字）
    2. 转换为小写
    3. 去除空格
    
    Args:
        text: 原始文本
    
    Returns:
        正规化后的文本
    """
    if not text:
        return ""
    # 去除标点：保留中文字符、字母、数字
    # 正则匹配非中文、非字母数字的字符（包括标点和空格）
    text = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
    # 转换为小写
    text = text.lower()
    return text


def calculate_cer(reference: str, hypothesis: str) -> float:
    """
    计算字错误率 (Character Error Rate, CER)
    
    公式: CER = 编辑距离 / 参考文本长度
    
    Args:
        reference: 参考文本
        hypothesis: 预测文本（识别结果）
    
    Returns:
        CER 值（0.0 - 1.0）
    """
    # 1. 正规化文本
    ref = _normalize_text(reference)
    hyp = _normalize_text(hypothesis)
    
    # 2. 处理空文本情况
    if not ref:
        return 0.0 if not hyp else 1.0
    if not hyp:
        return 1.0
    
    # 3. 转换为字符序列
    ref_chars = list(ref)
    hyp_chars = list(hyp)
    
    # 4. 计算编辑距离
    n = len(ref_chars)
    m = len(hyp_chars)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    
    # 初始化第一行和第一列
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    
    # 5. 动态规划填充 DP 矩阵
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_chars[i - 1] == hyp_chars[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]) + 1
    
    # 6. 计算 CER
    cer = dp[n][m] / n
    return cer
