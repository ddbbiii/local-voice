# Local Artifacts Not Stored In Git

This repository intentionally does not include model weights, virtual
environments, packaged backend runtimes, installer outputs, or user memory
data. They are too large or machine-specific for source control.

## Required Model Layout

Place local models under:

```text
E:\program\models
```

Expected first-run paths:

```text
E:\program\models\llm\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf
E:\program\models\llm\qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf
E:\program\models\asr\faster-whisper-medium.en
```

The app defaults in `src-tauri/resources/default-settings.json` and
`backend/models.py` point to these locations.

## Download Sources

- LLM: Qwen2.5 7B Instruct GGUF, `Q4_K_M`, split GGUF format.
- ASR: `Systran/faster-whisper-medium.en`.

Use Hugging Face or a mirror you trust. Keep the downloaded files outside this
repository.

## Generated Artifacts

These are rebuilt locally and are not tracked:

```text
.venv/
node_modules/
dist/
build/
src-tauri/target/
src-tauri/resources/backend-runtime/assistant-backend/
*.msi
```

To rebuild the packaged backend runtime:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\Build-Desktop.cmd
```

For development, the Tauri shell starts the backend from `.venv` directly, so
the packaged backend runtime is not required.

## Local User Data

Runtime settings, logs, conversations, Chroma indexes, and Markdown memory are
stored under the app data directory, not in Git. In development mode, local data
may also appear under `.assistant_data/`.
