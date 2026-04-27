# asr-tts-benchmark

> 多服务方 ASR（语音识别）+ TTS（语音合成）性能评测工具，使用 [CommonVoice](https://commonvoice.mozilla.org/) 中文数据集。

## 支持的服务方

| 服务方 | ASR | TTS | 鉴权方式 |
|--------|-----|-----|---------|
| 讯飞 (xunfei) | ✅ 在线语音听写 | ✅ 在线语音合成 | WebSocket HMAC |
| 阿里云 (aliyun) | ✅ 一句话识别 | ✅ 语音合成 | AccessKey Token |

## 项目结构

```
├── config.py                     # 统一配置（按服务方前缀读取 .env）
├── .env.example                  # 环境变量模板
├── providers/
│   ├── base.py                   # BaseASR / BaseTTS 抽象基类
│   ├── __init__.py               # 工厂函数 get_asr() / get_tts()
│   ├── xunfei/
│   │   ├── auth.py               # 讯飞 WebSocket 鉴权
│   │   ├── asr.py                # 讯飞 ASR
│   │   └── tts.py                # 讯飞 TTS
│   └── aliyun/
│       ├── auth.py               # 阿里云 AccessKey 签名 → Token
│       ├── asr.py                # 阿里云 ASR
│       └── tts.py                # 阿里云 TTS
├── utils/
│   ├── audio.py                  # 音频时长估算 + 重采样
│   └── metrics.py                # CER 编辑距离
├── batch.py                      # 统一批量评测入口
├── asr_test.py                   # ASR 单条测试
└── requirements.txt
```

## 快速开始

### 1. 环境准备

- **Python** >= 3.10
- **FFmpeg**（音频重采样依赖）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API 密钥

复制 `.env.example` 为 `.env`，填入对应服务方的密钥：

```env
# ===== 讯飞 =====
XUNFEI_APP_ID=你的APP_ID
XUNFEI_API_KEY=你的API_KEY
XUNFEI_API_SECRET=你的API_SECRET

# ===== 阿里云 =====
ALIYUN_ACCESS_KEY_ID=你的ACCESS_KEY_ID
ALIYUN_ACCESS_KEY_SECRET=你的ACCESS_KEY_SECRET
ALIYUN_APP_KEY=你的APP_KEY
```

> - 讯飞：前往 [讯飞开放平台](https://www.xfyun.cn/) 获取
> - 阿里云：前往 [阿里云智能语音交互](https://nls-portal.console.aliyun.com/) 获取

### 4. 准备测试数据（可选，批量评测需要）

从 [Mozilla CommonVoice](https://commonvoice.mozilla.org/zh-CN/datasets) 下载中文数据集，放置于 `cv-test/` 目录。

## 使用方法

### ASR 单条测试

```bash
# 讯飞
python asr_test.py --provider xunfei --audio test.mp3

# 阿里云
python asr_test.py --provider aliyun --audio test.mp3
```

### 批量评测（ASR + TTS）

```bash
# 讯飞
python batch.py --provider xunfei --limit 400 --workers 2

# 阿里云
python batch.py --provider aliyun --limit 400 --workers 2
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--provider` | 服务方 xunfei / aliyun | `xunfei` |
| `--data_root` | CommonVoice 数据目录 | `cv-test/...` |
| `--limit` | 处理条数上限 | `400` |
| `--output_asr` | ASR 结果目录 | `outputs/<provider>/asr` |
| `--output_tts` | TTS 音频目录 | `outputs/<provider>/tts` |
| `--voice` | TTS 发音人 | 服务方默认值 |
| `--workers` | 并发路数 | `2` |

### 在代码中使用

```python
from providers import get_asr, get_tts

# ASR
asr = get_asr("aliyun")
text = asr.recognize("test.wav", audio_format="wav")
print(text)

# TTS
tts = get_tts("xunfei")
audio = tts.synthesize("你好世界", output_file="output.mp3")
```

## 扩展新的服务方

1. 在 `providers/` 下创建新目录（如 `providers/tencent/`）
2. 实现 `auth.py`、`asr.py`、`tts.py`，继承 `BaseASR` / `BaseTTS`
3. 在 `providers/__init__.py` 的工厂函数中注册
4. 在 `config.py` 和 `.env.example` 中添加对应的环境变量

## 参考文档

- [讯飞语音听写 API](https://www.xfyun.cn/doc/asr/voicedictation/API.html)
- [讯飞在线语音合成 API](https://www.xfyun.cn/doc/tts/online_tts/API.html)
- [阿里云一句话识别](https://help.aliyun.com/document_detail/84428.html)
- [阿里云语音合成](https://help.aliyun.com/document_detail/84435.html)

## License

MIT
