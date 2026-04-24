


# xunfei-api-test

> 测试讯飞在线语音转写（ASR）与语音合成（TTS）的各项指标，使用 [CommonVoice](https://commonvoice.mozilla.org/) 中文数据集进行评测。

## 快速开始

### 1. 环境准备

- **Python** >= 3.8
- **FFmpeg**（音频重采样依赖）

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API 密钥

在项目根目录创建 `.env` 文件，填入讯飞开放平台的密钥：

```env
APP_ID=你的APP_ID
API_KEY=你的API_KEY
API_SECRET=你的API_SECRET
```

> 前往 [讯飞开放平台](https://www.xfyun.cn/) 注册并创建应用获取密钥。

### 4. 准备测试数据（可选，仅批量评测需要）

从 [Mozilla CommonVoice](https://commonvoice.mozilla.org/zh-CN/datasets) 下载中文数据集，将子集放置于 `cv-test/` 目录下，确保目录结构包含 `test_subset.tsv` 和 `clips/` 文件夹。

## 使用方法

### ASR 单条识别

```bash
python asr_test.py
```

修改 `asr_test.py` 中的 `test_audio` 变量为你自己的音频文件路径。

### TTS 语音合成

```python
from xunfei_asr_tts_demo import XunFeiTTS
from config import APP_ID, API_KEY, API_SECRET

tts = XunFeiTTS(APP_ID, API_KEY, API_SECRET)
audio = tts.synthesize(
    text="你好，这是语音合成测试。",
    vcn="xiaoyan",      # 发音人
    speed=50,            # 语速 [0-100]
    volume=50,           # 音量 [0-100]
    pitch=50,            # 音高 [0-100]
    aue="lame",          # mp3 格式
    output_file="output.mp3"
)
```

### 批量评测（ASR + TTS）

```bash
python xunfei_batch.py \
    --data_root cv-test/cv-corpus-25.0-2026-03-09/zh-CN_subset_5000 \
    --limit 400 \
    --output_asr outputs/asr \
    --output_tts outputs/tts \
    --voice xiaoyan
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--data_root` | CommonVoice 数据集子集目录路径 | `cv-test/cv-corpus-25.0-2026-03-09/zh-CN_subset_5000` |
| `--limit` | 处理条数上限 | `400` |
| `--output_asr` | ASR 结果输出目录 | `outputs/asr` |
| `--output_tts` | TTS 音频输出目录 | `outputs/tts` |
| `--voice` | TTS 发音人 | `xiaoyan` |

**输出：**

- `outputs/asr/summary.json` — 评测汇总（含平均 CER）
- `outputs/asr/summary.csv` — 评测结果 CSV 表格
- `outputs/asr/result_0001.json` ... — 单条识别详情
- `outputs/tts/clips/` — TTS 合成音频


### `util.py`

音频重采样工具，基于 FFmpeg：

- `resample_mp3()` — 重采样并保存为文件
- `resample_streaming()` — 流式重采样，直接返回 bytes，不产生临时文件



## 参考文档

- [讯飞语音听写 API 文档](https://www.xfyun.cn/doc/asr/voicedictation/API.html)
- [讯飞在线语音合成 API 文档](https://www.xfyun.cn/doc/tts/online_tts/API.html)
- [Mozilla CommonVoice 数据集](https://commonvoice.mozilla.org/)

## License

MIT
