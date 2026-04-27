"""
字符错误率（CER）计算工具

符合 NIST / HuggingFace 标准 ASR 评测规范：
    CER = (S + D + I) / N = Levenshtein 编辑距离 / 参考文本字符数

标准处理流程：
    1. Unicode NFC 归一化
    2. 去除标注标记（如 <UNK>, [NOISE], (LAUGHTER) 等）
    3. 大小写归一化（转小写）
    4. 去除标点（基于 Unicode Category，只保留 Letter/Number/Mark/Space）
    5. CJK 语种去空格，非 CJK 保留空格（空格也是字符）

注意：CER 可以超过 1.0（当插入数很多时），这是正常的。
"""

import re
import unicodedata


# 判断是否包含 CJK 字符的 Unicode 码点范围
_CJK_RANGES_RE = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff'   # CJK Ideographs
    r'\u3040-\u309f\u30a0-\u30ff'                   # Hiragana + Katakana
    r'\uac00-\ud7af]'                                # Hangul
)

# 标注标记正则：<...>, [...], (...) 通常为 ASR 标注噪声标记
_ANNOTATION_RE = re.compile(r'[<\[\(][^>\]\)]*[>\]\)]')


def _normalize_text(text: str) -> str:
    """
    NIST/HuggingFace 标准文本正规化

    1. Unicode NFC 归一化
    2. 去除标注标记（<UNK>, [NOISE], (LAUGHTER) 等）
    3. 大小写归一化（转小写）
    4. 去除标点（基于 Unicode Category，只保留 Letter/Number/Mark/Space）
    5. CJK 语种去空格，非 CJK 保留空格

    Args:
        text: 原始文本

    Returns:
        正规化后的文本
    """
    if not text:
        return ""

    # 1. Unicode NFC 归一化
    text = unicodedata.normalize("NFC", text)

    # 2. 去除标注标记
    text = _ANNOTATION_RE.sub("", text)

    # 3. 大小写归一化
    text = text.lower()

    # 4. 基于 Unicode Category 过滤：
    #    保留 Letter (L*), Number (N*), Mark (M*)
    #    空格分隔符 Zs → 转为普通空格
    #    去掉 Punctuation (P*), Symbol (S*), Control (C*), 其他
    chars = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith(('L', 'N', 'M')):
            chars.append(ch)
        elif cat == 'Zs':
            chars.append(' ')
    text = ''.join(chars)

    # 5. CJK 语种去空格，非 CJK 保留空格
    if _CJK_RANGES_RE.search(text):
        text = re.sub(r'\s+', '', text)
    else:
        text = re.sub(r'\s+', ' ', text).strip()

    return text


def calculate_cer(reference: str, hypothesis: str) -> float:
    """
    计算字错误率 (Character Error Rate, CER)

    CER = Levenshtein 编辑距离 / 参考文本字符数

    编辑距离 = S（替换）+ D（删除）+ I（插入）
    N = 参考文本字符数

    注意：CER 可以超过 1.0（当插入数很多时），这是正常的。

    Args:
        reference: 参考文本（ground truth）
        hypothesis: 预测文本（ASR 识别结果）

    Returns:
        CER 值（>= 0.0，无上限）
    """
    # 1. 正规化文本
    ref = _normalize_text(reference)
    hyp = _normalize_text(hypothesis)

    # 2. 处理边界情况
    if not ref:
        return 0.0 if not hyp else float("inf")
    if not hyp:
        return 1.0  # 全部删除

    # 3. 转换为字符序列
    ref_chars = list(ref)
    hyp_chars = list(hyp)
    n = len(ref_chars)
    m = len(hyp_chars)

    # 4. 计算编辑距离（Levenshtein Distance）
    dp = [[0] * (m + 1) for _ in range(n + 1)]

    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_chars[i - 1] == hyp_chars[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(
                    dp[i - 1][j],      # 删除
                    dp[i][j - 1],      # 插入
                    dp[i - 1][j - 1],  # 替换
                ) + 1

    # 5. CER = 编辑距离 / 参考文本字符数（无上限截断）
    return dp[n][m] / n
