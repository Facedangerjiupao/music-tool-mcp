# 工作流调用版：在 Audio Sonic MCP 上修改

这是 [Audio Sonic MCP](https://github.com/ripunjay-kashyap/audio-sonic-mcp) 的本地派生版本。
上游基线提交：`1cb0e3e36a5ed4cecec95987eb1e419f7b2b34f4`。保留原 MIT LICENSE、Git 来源和原有接口。
新增功能复用原项目的 yt-dlp 下载、FFmpeg 转码、Demucs 和特征分析模块；节拍检测调用 librosa，可选接入 Beat This! 和 All-In-One-Infer。

## 用途和默认行为

工作流传入网址或本地音视频，拿回文件路径和 JSON，继续交给其他 AI 或剪辑工具。
本工具不生成剧本或中文分析报告。默认不运行 Demucs、CLAP，不下载大模型。

| mode | 结果 |
|---|---|
| `extract` | 提取音频并返回文件路径，不运行节奏分析 |
| `rhythm`（默认） | 文件 + 全段 BPM、节拍、声音起始点、能量曲线、候选剪辑点 |
| `full` | rhythm + 上游的调性、瞬态、立体声等描述数据 |

默认保留媒体，供后续工作流读取；设置 `keep_media=false` 或 CLI `--discard-media` 时只留 JSON 和元数据。
原始本地输入永远保留。每个任务使用独立目录，禁止用同一 job_id 覆盖旧任务。

## 命令行

先按 [DOT_SETUP.md](DOT_SETUP.md) 创建 `.venv` 并安装依赖。FFmpeg/FFprobe 需位于 PATH，或用 `FFMPEG_BIN` 指定目录；克隆目录下的 `.runtime/ffmpeg` 也会自动查找。
默认使用 librosa；安装 `.[timeline]` 后指定 `beat-this` 可使用模型，首次推理会下载权重。

```powershell
# 音视频都可以；也可以把路径换成网站链接
.\run-media.cmd "D:\素材\参考视频.mp4" --mode rhythm --fps 30 --out outputs\features.json

# 需要小节第一拍时，使用已安装的 Beat This! 模型
.\run-media.cmd "D:\素材\音乐.mp3" --beat-engine beat-this --out outputs\features.json

# 只取音频；--media-type video 额外保留网址中的视频
.\run-media.cmd "https://example.com/video/123" --mode extract
.\run-media.cmd "https://example.com/video/123" --mode extract --media-type video
```

成功退出码 0，失败退出码 1；stdout 只有一份 JSON，日志在 stderr。
默认每个任务持久化到 `~/.cache/music-tool/jobs/<job_id>/result.json`，`artifacts` 内是绝对路径。
CLI 可用 `--jobs-root`，MCP/HTTP 可用 `JOBS_ROOT` 环境变量改变存储位置。

## MCP 调用

工具名 `prepare_media`，示例参数：

```json
{
  "source": "D:\\素材\\参考视频.mp4",
  "mode": "rhythm",
  "beat_engine": "librosa",
  "fps": 30,
  "keep_media": true
}
```

立即返回 `job_id`，随后调用 `get_job_status({"job_id":"返回的ID"})`。
任务状态为 `queued` → `running` → `success` / `error`。
成功结果保存在磁盘，服务重启后仍能读取；重启中断的未完成任务会明确报错。
`list_jobs` 列任务，`check_health` 查依赖和可选引擎。

客户端配置参考 [mcp.example.json](mcp.example.json)，安装步骤和 dot 调用约定见 [DOT_SETUP.md](DOT_SETUP.md)。模板中的路径需要替换为实际绝对路径。

## HTTP 调用

先安装 `.[cloud]`，再运行 `.\start-api.cmd`，默认只监听本机 `http://127.0.0.1:8000`。

```powershell
$body = @{source="D:\素材\参考视频.mp4"; mode="rhythm"; fps=30} | ConvertTo-Json
$job = Invoke-RestMethod http://127.0.0.1:8000/api/v1/media -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.job_id)"
```

接口说明在 `/docs`。本地路径指服务所在机器的路径。MCP SSE 入口为 `/mcp/sse`。

## 时间轴字段

`timeline.beats / downbeats / onsets / energy_peaks` 包含 `time_sec`、`frame`、`strength`。
`energy_curve` 包含实际 RMS、全段内归一化能量和低频能量；`cut_candidates` 包含时间和推荐依据。
`segments` 在使用结构模型时提供前奏、主歌、副歌等区间。

所有时间从解码后的音频起点 0 秒计算；帧号按给定 fps 四舍五入，秒时间保留。
分析覆盖整个音频，不使用上游默认的 60 秒代表片段。
`strength` 和候选分数只是同一音轨内的相对排序，不是正确率或概率。
声音起始点可能来自鼓、人声或其他乐器；不会把它冒充成已识别的底鼓/军鼓。
librosa 不提供小节第一拍，`downbeats` 保持空数组；不假设所有音乐都是 4/4。

## 可选现有模型

基础流程已经可以运行。需要模型能力时再安装：

```powershell
# Beat This!：节拍和小节第一拍；会安装 PyTorch，并在首次使用时下载权重
.\.venv\Scripts\python.exe -m pip install ".[timeline]"
.\run-media.cmd "D:\素材\音乐.mp3" --beat-engine beat-this

# All-In-One 的推理维护分支：前奏/主歌/副歌等结构；无需自行编译 NATTEN
.\.venv\Scripts\python.exe -m pip install "all-in-one-infer @ git+https://github.com/openmirlab/all-in-one-infer.git"
.\run-media.cmd "D:\素材\音乐.mp3" --beat-engine all-in-one

# 需要实际鼓/人声分轨时，复用上游的可选 Demucs 能力
.\.venv\Scripts\python.exe -m pip install ".[clap]"
.\run-media.cmd "D:\素材\音乐.mp3" --separate
```

可用 `AUDIO_DEVICE=cpu/cuda` 选择模型设备，默认 CPU。
指定的模型失败会明确报错，不悄悄换引擎。已实测 Beat This!；All-In-One-Infer 和 Demucs 的可选适配尚未完成真实模型验证，使用前需安装并自行检查。
`separate=true` 会处理整首歌，耗时和内存明显增加。

网址实际支持范围取决于 yt-dlp，未逐站验证；支持 `YTDLP_PROXY` 代理。
某些网站还要求登录或外部 JavaScript runtime，不能保证任意网址可提取。
默认工作流最大时长 600 秒，可用 `WORKFLOW_MAX_DURATION_SEC` 修改；本地源校验还受 `FILE_MAX_DURATION_SEC` 控制。
不支持直播和整张播放列表。实际音乐的节拍检测仍需抽查，尤其变速、弱节奏和自由拍音频。

## 重新安装基础环境

需要 Python 3.10+，推荐 3.12，以及 PATH 中的 FFmpeg/FFprobe（或 `FFMPEG_BIN` 指向其目录）。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[cloud,dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

## 开发验证记录

- 全量测试：155 通过，44 跳过（上游需要预存真实音乐样本的准确率测试），无失败。
- 已验证本地 WAV、含音轨的 MP4、中文路径、真实 yt-dlp HTTP 下载、CLI JSON、MCP stdio、MCP SSE、HTTP 任务和重启后读取结果。
- 90 秒已知节拍测试覆盖音频结尾；24 秒合成鼓声经过 Beat This! 实际推理返回 BPM 120.0、48 个节拍。
- 示例媒体、模型权重、登录信息和任务输出不随源码发布。
- 真实网站支持情况未逐站测试；小节和音乐结构预测不保证完全正确。

## 开源来源

- 底座：[Audio Sonic MCP](https://github.com/ripunjay-kashyap/audio-sonic-mcp)，MIT。
- 下载：[yt-dlp](https://github.com/yt-dlp/yt-dlp)；转码：[FFmpeg](https://ffmpeg.org/)。
- 基础节奏：[librosa](https://github.com/librosa/librosa)，ISC。
- 可选节拍：[Beat This!](https://github.com/CPJKU/beat_this)，MIT。
- 可选结构：[All-In-One-Infer](https://github.com/openmirlab/all-in-one-infer)，沿用 All-In-One 模型。

保留的 `README.upstream.md` 介绍原接口 `get_sonic_signature` / `analyze_file.py`；新增工作流请用本文接口。
