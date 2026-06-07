# Local Voice Memory Assistant

Windows 本地英语学习语音助手，包含：

- `Tauri + React + TypeScript` 桌面前端
- `FastAPI` 本地后端
- `Markdown` 作为持久化记忆主存储
- 可替换的 `ASR / LLM / Vector / TTS` 适配层
- 流式事件协议和按住说话交互

## 当前实现状态

当前仓库已经实现：

- 极简桌面 UI
- WebSocket 流式事件显示
- 前端按住说话录音并编码为 WAV
- Python 后端接收音频并驱动整条对话管线
- `memory.md` 持久化读写
- `ChromaAdapter`，不可用时自动回退到本地 JSON 索引
- `WhisperAdapter / LlamaCppAdapter / TTSAdapter` 适配边界

当前默认带有降级行为：

- 如果 `faster-whisper` 不可用，ASR 返回模拟文本
- 如果 `llama.cpp` 不可用，LLM 返回本地骨架回复
- TTS 当前为轻量 mock 适配器，前端使用本地 `speechSynthesis` 朗读句子

## 目录结构

- `src/`: React 前端
- `src-tauri/`: Tauri 桌面壳
- `backend/`: Python 后端
- `E:\program\1project\forCodex\english\codex-notes`: Codex 对话生成的资料、导出文档和工作笔记
- `E:\program\1project\forCodex\english\.codex-memory`: Codex 项目记忆草稿

## 本地开发

1. 安装前端依赖

```powershell
npm install
```

2. 创建并安装项目 Python 环境

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

3. 启动后端

```powershell
.\.venv\Scripts\python.exe -m backend.app
```

4. 启动前端

```powershell
npm run dev
```

5. 如需 Tauri 桌面壳

先安装 Rust 工具链，再执行：

```powershell
npm run tauri:dev
```

## 一键启动

- `Start-Desktop.cmd`
  - 直接启动桌面版开发环境
  - 会使用项目 `.venv`，不依赖系统 Python
- `Start-Web.cmd`
  - 同时拉起后端和前端网页调试环境
- `Stop-Assistant.cmd`
  - 关闭 `8765` 后端端口和 `5173` 前端端口上的进程
- `Check-Runtime.cmd`
  - 检查 `.venv`、模型文件、关键 Python 包、端口占用
  - 可加 `-SkipHealth` 只做静态检查，不启动/探测后端
- `Smoke-Memory.cmd`
  - 独立验证长期学习记忆写入、Markdown 重载、常错点提取和索引重建
  - 不加载本地大模型，适合改动记忆逻辑后快速回归

## 当前默认本地模型

- `LLM`: `E:\program\models\llm\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf`
- `ASR`: `E:\program\models\asr\faster-whisper-medium.en`
- `llama-cpp-python`: 固定使用 `0.3.19`

## 当前运行验证

本机当前检查结果：

- `.venv` 使用 Python 3.11
- `llama_cpp / faster_whisper / chromadb / pyttsx3` 均可导入
- `LLM / ASR` 模型文件均存在
- `/health` 可进入 `backend_mode=real`
- `vector_index_provider=chroma`

如果启动异常，先运行：

```powershell
.\Check-Runtime.cmd -SkipHealth
```

如需连同后端 health 一起检查：

```powershell
.\Check-Runtime.cmd
```

## 云端 LLM 切换

当前后端已经支持 `OpenAI-compatible` 的云端 LLM 接口。

推荐通过环境变量提供云端配置：

```powershell
$env:ASSISTANT_LLM_API_BASE="https://your-provider.example/v1"
$env:ASSISTANT_LLM_API_MODEL="your-chat-model"
$env:ASSISTANT_LLM_API_KEY="your-api-key"
```

然后把 `.assistant_data/config/settings.json` 里的 `llm_provider` 改成：

```json
"llm_provider": "api"
```

如果你想保留自动选择，也可以用：

```json
"llm_provider": "auto"
```

这时只要上面的环境变量存在，后端会优先走云端 API；否则继续走本地 `llama.cpp`。

注意：API key 不应写入 `src-tauri/resources/default-settings.json` 或提交到项目文件中。环境变量可用于运行时读取，但默认配置不会再自动把环境变量密钥持久化到 settings。

## 后续接入真实模型/能力

- `faster-whisper`: 已默认指向本地英语模型目录
- `llama.cpp`: 已默认指向本地 `Qwen2.5-7B-Instruct GGUF`
- `ChromaDB`: 当前已经通过 `ChromaAdapter` 接好，未安装时会回退到 JSON 索引
- `TTS`: 用真实本地 TTS 替换 `MockTTSAdapter`

## 当前学习记忆机制

- 后端启动时会压缩 Markdown 记忆并重建向量索引
- `GET /api/memory` 可读取长期学习记忆快照
- `POST /api/memory/rebuild` 可从 Markdown 重新构建检索索引
- 记忆会优先保留学习画像、纠错偏好、常练主题和高频错误类型
- 前端 Memory 面板只展示摘要和分类条目，不直接编辑 Markdown
