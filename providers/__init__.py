"""服务方工厂函数 — 按名称获取 ASR / TTS 实例"""
from providers.base import BaseASR, BaseTTS


def get_asr(provider: str) -> BaseASR:
    """
    获取 ASR 实例

    Args:
        provider: "xunfei" 或 "aliyun"
    """
    if provider == "xunfei":
        from providers.xunfei import XunFeiASR
        from config import XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET
        return XunFeiASR(XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET)
    elif provider == "aliyun":
        from providers.aliyun import AliASR
        from config import ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY
        return AliASR(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY)
    else:
        raise ValueError(f"未知的 provider: {provider}")


def get_tts(provider: str) -> BaseTTS:
    """
    获取 TTS 实例

    Args:
        provider: "xunfei" 或 "aliyun"
    """
    if provider == "xunfei":
        from providers.xunfei import XunFeiTTS
        from config import XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET
        return XunFeiTTS(XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET)
    elif provider == "aliyun":
        from providers.aliyun import AliTTS
        from config import ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY
        return AliTTS(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY)
    else:
        raise ValueError(f"未知的 provider: {provider}")
