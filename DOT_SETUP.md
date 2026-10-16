# 让 dot 安装和调用

## 直接发给 dot

复制下面这段话给你的 dot：

> 请安装 https://github.com/Facedangerjiupao/music-tool-mcp 。先阅读 README.md、DOT_SETUP.md 和 WORKFLOW.zh-CN.md，在你实际可用的电脑或执行环境中克隆仓库，创建 Python 3.12 虚拟环境并安装 FFmpeg / FFprobe 和 `.[timeline]`。按你的 MCP 客户端配置方式注册名为 `music-tool` 的 stdio 服务，使用真实绝对路径，并检查 `check_health`。如果当前环境无法注册自定义 MCP，请使用同仓库的 `audio-sonic-media` CLI，明确说明调用方式，不要声称已注册。用本地生成的短音频完成一次实际调用，确认成功并能读取返回的文件。以后我提供音乐链接或音视频文件、要求卡点或按音乐写分镜时，优先用 `prepare_media(mode="rhythm", beat_engine="beat-this", keep_media=true, fps=项目帧率)`，轮询 `get_job_status` 到完成，根据实测时间轴继续工作；只提取素材时用 `mode="extract"`。请记住实际安装路径和调用方式。网站校验失败时说明实际错误，需要时使用你已有的正常浏览器能力或本地文件输入。

安装在 dot 的执行环境与安装在你的电脑是两件事。若希望它使用你电脑上的音乐、浏览器和现有 MCP，需要先在 dot 的 Computers 中连接这台电脑并允许访问。官方说明：[Connect computers and apps to your dot](https://learn.chatgpt.com/docs/dots/computers-and-apps)。本仓库不要求公开本机端口。

## 1. 安装依赖

推荐 Python 3.12。需要 Git、FFmpeg 和 FFprobe：Linux 可从系统包管理器安装 `ffmpeg`；macOS 可使用 `brew install ffmpeg`；Windows 可使用 FFmpeg 官方下载页推荐的构建，把包含两个 exe 的 `bin` 目录放入 PATH，或设置 `FFMPEG_BIN` 指向该目录。

```bash
git clone https://github.com/Facedangerjiupao/music-tool-mcp.git
cd music-tool-mcp
python3.12 -m venv .venv
# 可选：CPU 环境先安装 CPU 版 PyTorch，避免安装 CUDA 依赖
.venv/bin/python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -e ".[timeline]"
```

Windows PowerShell：

```powershell
git clone https://github.com/Facedangerjiupao/music-tool-mcp.git
Set-Location music-tool-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -e ".[timeline]"
```

PyTorch 和模型需要可用磁盘及网络，Beat This! 默认使用 CPU，首次推理会下载权重。只做提取或 librosa 分析时，可省略 PyTorch 并把安装参数改成 `-e .`。

## 2. 注册 MCP

[mcp.example.json](mcp.example.json) 是通用配置模板，**不是可直接使用的真实路径**。把 command 改成已安装虚拟环境的 Python 绝对路径，把 JOBS_ROOT 改成可写的任务目录。Windows 的 Python 路径一般为 `C:\path\music-tool-mcp\.venv\Scripts\python.exe`；JSON 中反斜杠需要写成 `\\`。

模块已经安装，`args=["-X","utf8","-m","server"]` 无需依赖当前目录。客户端支持时建议启动超时 120 秒，工具调用超时 180 秒。实际任务异步执行，轮询接口即可。

若执行环境使用 Codex CLI，可运行以下命令，替换绝对路径：

```bash
codex mcp add music-tool --env TRANSPORT_MODE=stdio --env PYTHONUTF8=1 --env OMP_NUM_THREADS=4 --env JOBS_ROOT=/absolute/path/music-jobs -- /absolute/path/music-tool-mcp/.venv/bin/python -X utf8 -m server
codex mcp list
```

客户端配置位置由客户端决定。若 dot 没有注册自定义 MCP 的接口，直接使用下面的 CLI；不要修改无关应用配置或假定已连接的工具自动可见。

## 3. 实测后再使用

通过 MCP 调用 `check_health({})`，确认 FFmpeg、FFprobe 和所需引擎可用。调用 `prepare_media` 后保留其 `job_id`，用 `get_job_status` 检查到 `success`。安装检查可用自己生成的短 WAV；正式卡点分析应使用真实音乐。

CLI 与 MCP 调用同一处理流程：

```bash
.venv/bin/audio-sonic-media /absolute/path/music.mp3 --mode rhythm --beat-engine beat-this --fps 30 --out /absolute/path/timeline.json
# 只下载、提取，不分析
.venv/bin/audio-sonic-media 'https://example.com/video/123' --mode extract --media-type video
```

Windows 把 `.venv/bin/audio-sonic-media` 换成 `.\.venv\Scripts\audio-sonic-media.exe` 或克隆目录中的 `.\run-media.cmd`。成功退出码为 0，stdout 是 JSON，日志写入 stderr。默认任务目录为用户家目录下 `.cache/music-tool/jobs`；用 `JOBS_ROOT` 或 `--jobs-root` 指定位置。

## 4. 以后自动调用的约定

把以下约定加入 dot 自己支持的持久化说明或工作流：

- 有明确音频或含音轨的视频且需要卡点时，先运行工具，项目 fps 与请求一致；素材未选定时先选素材。
- 节奏分析用 `mode="rhythm"`、`beat_engine="beat-this"`、`keep_media=true`；仅提取用 `mode="extract"`。
- MCP 提交后轮询，不把 queued / running 当作完成。错误必须保留并说明，不编造结果。
- 镜头、动作、字幕和转场参考 `beats`、`downbeats`、`onsets`、`energy_curve`、`cut_candidates`。不要仅凭 BPM 推算。
- `onsets` 不是底鼓/军鼓分类；小节第一拍由模型预测，仍需抽查。
- 绝对文件路径属于运行工具的机器。后续剪辑程序必须能访问这些文件。

## 链接失败怎么办

本项目使用 yt-dlp，不内置验证码求解、浏览器接管或登录信息同步。抖音、B站等链接会受登录、地区与网站校验影响。返回错误时，可使用 dot 已有且能正常访问网站的浏览器能力取得素材，再输入本地文件。不要把 cookies、账号凭据或下载的视频提交到这个公开仓库。
