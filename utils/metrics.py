"""评测指标工具"""


def calculate_cer(reference: str, hypothesis: str) -> float:
    """
    计算字错误率 (CER) = 编辑距离 / 参考长度
    """
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
