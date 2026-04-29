"""
字符错误率（CER）计算工具

符合 NIST / HuggingFace 标准 ASR 评测规范：
    CER = (S + D + I) / N = Levenshtein 编辑距离 / 参考文本字符数

标准处理流程：
    1. Unicode NFC 归一化
    2. 去除标注标记（如 <UNK>, [NOISE], (LAUGHTER) 等）
    3. 大小写归一化（转小写）
    4. 去除标点（基于 Unicode Category，只保留 Letter/Number/Mark/Space）
    5. CJK 数字转换：阿拉伯数字 → 中文字符（统一数字表示形式）
    6. CJK 语种去空格，非 CJK 保留空格（空格也是字符）

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
_ANNOTATION_RE = re.compile(r'[<\[\(][^\>\]\)]*[\>\]\)]')


# ==================== CJK 数字转换 ====================

# CJK 语种 (中日韩) - 评测时去除空格
CJK_LANGUAGES = {"zh", "zh_paraformer", "zh_qwen3asr", "ja", "ko"}

# 阿拉伯数字 → 中文字符映射 (逐位转换)
_ZH_DIGIT_MAP = str.maketrans("0123456789", "零一二三四五六七八九")
_JA_DIGIT_MAP = str.maketrans("0123456789", "〇一二三四五六七八九")


def _normalize_digits_cjk(text: str, lang: str = "zh") -> str:
    """CJK 语种: 将阿拉伯数字逐位转为对应语言的字符，统一数字表示形式

    例如: "2014年" → "二零一四年", "100元" → "一零零元"
    这样 ASR 输出 "二零一四年" 和参考文本 "2014年" 就能正确匹配
    """
    if lang in ("zh", "zh_paraformer", "zh_qwen3asr"):
        return text.translate(_ZH_DIGIT_MAP)
    elif lang == "ja":
        return text.translate(_JA_DIGIT_MAP)
    # 韩语: 保留阿拉伯数字 (韩文 ASR 通常输出阿拉伯数字)
    return text


# ==================== 文本归一化 ====================

def normalize_text(text: str, lang: str = "zh") -> str:
    """
    NIST/HuggingFace 标准文本正则化

    1. Unicode NFC 归一化
    2. 去除标注标记 (<UNK>, [NOISE], (LAUGHTER) 等)
    3. 大小写归一化 (转小写)
    4. 去除标点 (基于 Unicode Category，只保留 Letter/Number/Mark/Space)
    5. CJK 语种: 阿拉伯数字 → 中文/日文字符，统一数字表示形式
    6. CJK 语种去空格，非 CJK 保留空格
    7. 去除首尾空白

    Args:
        text: 原始文本
        lang: 语种 (默认 "zh", 用于 CJK 数字转换和空格处理)

    Returns:
        正则化后的文本
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

    # 5. CJK 语种: 阿拉伯数字 → 中文/日文字符
    is_cjk = lang in CJK_LANGUAGES
    if is_cjk:
        text = _normalize_digits_cjk(text, lang)

    # 6. CJK 去空格，非 CJK 保留空格
    if is_cjk:
        text = re.sub(r'\s+', '', text)
    else:
        text = re.sub(r'\s+', ' ', text).strip()

    return text


# ==================== Levenshtein 距离 ====================

def levenshtein_distance(s1, s2):
    """计算两个字符序列的编辑距离（使用一维滚动数组，节省内存）"""
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


# ==================== CER 计算 ====================

def compute_cer_detail(reference: str, hypothesis: str, lang: str = "zh"):
    """
    计算 CER 的详细结果，返回编辑距离和参考字符数

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


def calculate_cer(reference: str, hypothesis: str, lang: str = "zh") -> float:
    """
    计算字符错误率 (Character Error Rate, CER)

    CER = Levenshtein 编辑距离 / 参考文本字符数

    编辑距离 = S（替换）+ D（删除）+ I（插入）
    N = 参考文本字符数

    标准处理流程:
    1. Unicode NFC 归一化
    2. 去除标注标记
    3. 大小写归一化
    4. 去除标点
    5. CJK 数字转换 (阿拉伯数字 → 中文字符)
    6. CJK 语种去空格，非 CJK 保留空格

    注意：CER 可以超过 1.0（当插入数很多时），这是正常的。

    Args:
        reference: 参考文本 (ground truth)
        hypothesis: 预测文本 (ASR 识别结果)
        lang: 语种 (默认 "zh")

    Returns:
        CER 值 (>= 0.0，无上限)
    """
    distance, ref_len = compute_cer_detail(reference, hypothesis, lang)
    if ref_len == 0:
        return 0.0 if distance == 0 else float("inf")
    return distance / ref_len
