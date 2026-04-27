"""ASR / TTS 抽象基类，统一性能指标字段"""
from abc import ABC, abstractmethod


class BaseASR(ABC):
    """语音识别 (ASR) 抽象基类"""

    def __init__(self):
        self.ttft: float | None = None          # 首字节延迟 (秒)
        self.total_time: float | None = None    # 总耗时 (秒)
        self.rtf: float | None = None           # 实时率 = 处理时间 / 音频时长

    @abstractmethod
    def recognize(self, audio_data, **kwargs) -> str:
        """
        识别音频，返回文本。

        Args:
            audio_data: 文件路径 (str) 或音频二进制 (bytes)
        Returns:
            识别出的文本
        """
        ...


class BaseTTS(ABC):
    """语音合成 (TTS) 抽象基类"""

    def __init__(self):
        self.ttft: float | None = None
        self.total_time: float | None = None
        self.rtf: float | None = None

    @abstractmethod
    def synthesize(self, text: str, output_file: str | None = None, **kwargs) -> bytes:
        """
        合成语音。

        Args:
            text: 要合成的文本
            output_file: 输出文件路径（可选）
        Returns:
            音频二进制数据
        """
        ...
