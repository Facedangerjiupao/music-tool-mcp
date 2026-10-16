# Music Tool MCP · 音乐素材与视频卡点

基于 [Audio Sonic MCP](https://github.com/ripunjay-kashyap/audio-sonic-mcp) 改造的开源工作流工具。输入网站链接、本地音频或含音轨的视频，返回可继续使用的音频文件、真实节拍时间和 JSON；供 dot、Codex 和其他 AI 工作流调用。

[![Tests](https://github.com/Facedangerjiupao/music-tool-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/Facedangerjiupao/music-tool-mcp/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 从这里开始

- **让 dot 安装并使用：** [DOT_SETUP.md](DOT_SETUP.md)，包含可直接发给 dot 的安装指令。
- **完整参数和结果字段：** [WORKFLOW.zh-CN.md](WORKFLOW.zh-CN.md)。
- **通用 MCP 配置模板：** [mcp.example.json](mcp.example.json)。

## 能做什么

| 输入 / 模式 | 输出 |
|---|---|
| 网站链接或本地音视频，`extract` | 解码后的 WAV、保留的下载素材、绝对文件路径 |
| `rhythm` + librosa | 全段 BPM、节拍、声音起始点、能量曲线、候选剪辑点 |
| `rhythm` + Beat This! | 额外提供模型预测的小节第一拍 `downbeats` |
| `full` | 节奏时间轴 + 上游调性、瞬态、立体声等特征 |

复用 yt-dlp、FFmpeg、librosa 和可选的 Beat This!，不需要付费 API。工具提供数据，剧本、分镜和剪辑由调用它的 AI 继续完成。默认保留媒体，每个任务独立存储。

声音起始点 `onsets` 不等于底鼓或军鼓分类；本工具目前不识别每次鼓声的乐器类别。候选剪辑点属于相对推荐，真实音乐仍需抽查。

## 安装

需要 Python 3.10+（推荐 3.12）、Git，以及 PATH 中的 `ffmpeg` 和 `ffprobe`。也可以用 `FFMPEG_BIN` 指定二者所在目录。

```bash
git clone https://github.com/Facedangerjiupao/music-tool-mcp.git
cd music-tool-mcp
python -m venv .venv
# Linux / macOS
.venv/bin/python -m pip install -e ".[timeline]"
# Windows 使用：.\.venv\Scripts\python.exe -m pip install -e ".[timeline]"
```

`timeline` 会安装 PyTorch 和 Beat This!；首次用该引擎会下载权重。只需要提取或 librosa 时，用 `pip install -e .`。CPU 环境、FFmpeg 安装、MCP 注册详见 [dot 安装说明](DOT_SETUP.md)。

安装后通过虚拟环境中的 `audio-sonic-mcp` 启动 stdio MCP，通过 `audio-sonic-media` 调用 CLI。默认任务目录是 `~/.cache/music-tool/jobs`，可通过 `JOBS_ROOT` 或 CLI `--jobs-root` 修改。路径属于实际运行工具的机器。

```bash
.venv/bin/audio-sonic-media /path/to/music.mp3 --beat-engine beat-this --fps 30
.venv/bin/audio-sonic-media 'https://example.com/video/123' --mode extract --media-type video
```

Windows 克隆安装后也可用 `run-media.cmd` 和 `start-mcp.cmd`。

## MCP 工作流

1. `check_health` 检查 FFmpeg 和依赖。
2. `prepare_media` 提交来源和参数，立即获得 `job_id`。
3. 适当间隔调用 `get_job_status`，直到 `success` 或 `error`。
4. 成功后使用返回的绝对文件路径和 `timeline` 继续制作。

```json
{
  "source": "/absolute/path/music.mp3",
  "mode": "rhythm",
  "beat_engine": "beat-this",
  "fps": 30,
  "keep_media": true
}
```

结果包含 `beats`、`downbeats`、`onsets`、`energy_curve` 和 `cut_candidates`，以秒和给定 fps 的帧号表示。librosa 不预测小节第一拍，`downbeats` 留空；不会按 BPM 编造拍点。还保留 `list_jobs` 和上游 `get_sonic_signature` 接口。

## 网站链接与 dot 的运行位置

链接下载由 yt-dlp 处理。抖音、B站等站点是否成功取决于链接、地区、登录状态和网站校验，无法保证所有视频都能直接下载。本项目没有自动通过验证码或接管浏览器的功能。

遇到网站校验时，可在能正常访问的浏览器中完成登录并取得允许下载的素材，再把本地文件交给工具。浏览器操作属于调用方的能力；不会随着安装此 MCP 自动获得。

GitHub 发布源码后，dot 仍需在自己的执行环境或已连接电脑上安装。你电脑上的 MCP 配置不会自动同步到 dot 的云端。见 [官方 dot 电脑连接说明](https://learn.chatgpt.com/docs/dots/computers-and-apps)。

## 可选能力与验证

`.[cloud]` 提供 REST API 和 MCP SSE；`.[clap]` 提供上游 Demucs / CLAP。All-In-One-Infer 适配为可选功能，未完成真实模型验证。保留的 Dockerfile 来自上游、包含较重模型依赖，本分支推荐上述原生 Python 安装；未验证 Docker 构建。

```bash
.venv/bin/python -m pip install -e ".[dev,cloud]"
.venv/bin/python -m pytest -q
```

测试涵盖本地音视频、中文路径、完整 90 秒已知节拍、下载、CLI、MCP stdio / SSE、REST 与任务恢复。部分上游准确率测试需要另备音乐样本，会跳过。

## 来源与许可证

派生基线：Audio Sonic MCP `1cb0e3e36a5ed4cecec95987eb1e419f7b2b34f4`。保留原作者版权和 [MIT LICENSE](LICENSE)。上游文档存于 [README.upstream.md](README.upstream.md)。

主要组件：[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[FFmpeg](https://ffmpeg.org/)、[librosa](https://github.com/librosa/librosa)、[Beat This!](https://github.com/CPJKU/beat_this)。各组件及模型权重遵循各自许可证；下载的素材和运行环境不包含在仓库中。
