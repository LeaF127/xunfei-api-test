"""ASR / TTS 抽象基类，统一性能指标字段"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Metrics:
    """性能指标"""
    ttft: float | None = None          # 首字节延迟 (秒)
    total_time: float | None = None    # 总耗时 (秒)
    rtf: float | None = None           # 实时率 = 处理时间 / 音频时长


class BaseASR(ABC):
    """
    语音识别 (ASR) 抽象基类

    recognize() 返回 (识别文本, Metrics)，指标随返回值走，线程安全。
    同时保留 self.last_metrics 以便单条调试。
    """

    def __init__(self):
        self.last_metrics: Metrics = Metrics()

    @abstractmethod
    def recognize(self, audio_data, **kwargs) -> tuple[str, Metrics]:
        """
        识别音频，返回文本和性能指标。

        Args:
            audio_data: 文件路径 (str) 或音频二进制 (bytes)
        Returns:
            (识别出的文本, Metrics)
        """
        ...


class BaseTTS(ABC):
    """
    语音合成 (TTS) 抽象基类

    synthesize() 返回 (音频bytes, Metrics)，指标随返回值走，线程安全。
    同时保留 self.last_metrics 以便单条调试。
    """

    def __init__(self):
        self.last_metrics: Metrics = Metrics()

    @abstractmethod
    def synthesize(self, text: str, output_file: str | None = None, **kwargs) -> tuple[bytes, Metrics]:
        """
        合成语音。

        Args:
            text: 要合成的文本
            output_file: 输出文件路径（可选）
        Returns:
            (音频二进制数据, Metrics)
        """
        ...
