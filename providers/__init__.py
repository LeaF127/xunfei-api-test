"""
统一入口：按 provider 名称获取 ASR / TTS 实例
"""
from providers.base import BaseASR, BaseTTS


def get_asr(provider: str) -> BaseASR:
    """获取 ASR 实例"""
    if provider == "xunfei":
        from providers.xunfei import XunFeiASR
        from config import XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET
        return XunFeiASR(XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET)
    elif provider == "aliyun":
        from providers.aliyun import AliASR
        from config import ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY
        return AliASR(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY)
    elif provider == "doubao":
        from providers.doubao import DoubaoASR
        from config import DOUBAO_APP_ID, DOUBAO_ACCESS_TOKEN, DOUBAO_SECRET_KEY, DOUBAO_CLUSTER
        return DoubaoASR(DOUBAO_APP_ID, DOUBAO_ACCESS_TOKEN, DOUBAO_SECRET_KEY, DOUBAO_CLUSTER)
    else:
        raise ValueError(f"不支持的 provider: {provider}")


def get_tts(provider: str) -> BaseTTS:
    """获取 TTS 实例"""
    if provider == "xunfei":
        from providers.xunfei import XunFeiTTS
        from config import XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET
        return XunFeiTTS(XUNFEI_APP_ID, XUNFEI_API_KEY, XUNFEI_API_SECRET)
    elif provider == "aliyun":
        from providers.aliyun import AliTTS
        from config import ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY
        return AliTTS(ALIYUN_ACCESS_KEY_ID, ALIYUN_ACCESS_KEY_SECRET, ALIYUN_APP_KEY)
    elif provider == "doubao":
        from providers.doubao import DoubaoTTS
        from config import DOUBAO_APP_ID, DOUBAO_ACCESS_TOKEN, DOUBAO_CLUSTER
        return DoubaoTTS(DOUBAO_APP_ID, DOUBAO_ACCESS_TOKEN, DOUBAO_CLUSTER)
    else:
        raise ValueError(f"不支持的 provider: {provider}")


__all__ = ["get_asr", "get_tts"]
