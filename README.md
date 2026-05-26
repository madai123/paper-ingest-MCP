# paper-ingest-mcp

`paper-ingest-mcp` 是一个本地 MCP Server，用来把论文 PDF、图片或公开 PDF URL 交给 GLM-OCR API 解析，并把结果保存成稳定的 Markdown、JSON、图片和 manifest 路径，方便 Agent 后续写入 Obsidian、知识库或其他论文处理流程。

## 功能

- 支持本地 PDF、图片、目录和公开 URL 作为输入。
- 只使用 GLM-OCR MaaS/API 模式，不需要配置本地 OCR 服务或本地模型。
- 支持批量解析，并通过 `concurrency` 控制并发 OCR 数量。
- 输出目录固定为 `output_dir/<filename>/`，便于后续工具引用。
- 自动保存 `manifest.json`、`<filename>.json`、`<filename>.md` 和图片目录。
- 对 GLM-OCR Markdown 做轻量后处理，例如把 `$ a $` 规范为 `$a$`，更适合 Obsidian 阅读。

## 环境要求

- Python 3.10+
- `uv`
- 智谱 API Key：`ZHIPU_API_KEY`

## 安装

```powershell
git clone <your-repo-url>
cd paper-ingest-mcp
uv sync
```

配置环境变量：

```powershell
$env:ZHIPU_API_KEY="你的智谱 API Key"
$env:GLMOCR_MODE="maas"
$env:GLMOCR_OUTPUT_PATH="./glmocr_results"
```

也可以在项目根目录创建 `.env`：

```env
ZHIPU_API_KEY=你的智谱 API Key
GLMOCR_MODE=maas
GLMOCR_OUTPUT_PATH=./glmocr_results
```


## MCP 配置

把下面配置加入你的 MCP Client。命令路径可以按实际项目位置调整。

```json
{
  "mcpServers": {
    "paper-ingest-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "E:/mcp/paper-ingest-mcp",
        "run",
        "paper-ingest-mcp"
      ],
      "env": {
        "ZHIPU_API_KEY": "你的智谱 API Key",
        "GLMOCR_OUTPUT_PATH": "E:/mcp/paper-ingest-mcp/glmocr_results"
      }
    }
  }
}
```

Server 使用 stdio transport，启动入口是：

```powershell
uv run paper-ingest-mcp
```

## MCP 工具

### `parse_with_glmocr`

解析本地 PDF、图片、目录或公开 URL。所有输入都会走 GLM-OCR MaaS/API，不会调用本地 OCR 服务。

参数：

- `sources`：必填，文件路径或 URL 列表。
- `filenames`：可选，输出文件名 stem。填写时数量必须和 `sources` 一致。
- `output_dir`：可选，输出根目录。默认使用 `GLMOCR_OUTPUT_PATH`，否则是 `./glmocr_results`。
- `concurrency`：可选，并发解析数量，默认 `4`。
- `layout_device`：兼容旧参数，API 模式下会被忽略。
- `config`：可选，GLM-OCR 配置文件路径。本项目会强制使用 MaaS/API 模式。
- `api_key`：可选，智谱 API Key。推荐使用 `ZHIPU_API_KEY` 环境变量。

示例：通过 API 解析一个本地 PDF。

```json
{
  "sources": ["E:/papers/attention.pdf"],
  "filenames": ["attention"],
  "output_dir": "E:/mcp/paper-ingest-mcp/glmocr_results",
  "concurrency": 1
}
```

示例：通过 API 批量解析本地文件。

```json
{
  "sources": [
    "E:/papers/paper1.pdf",
    "E:/papers/paper2.pdf"
  ],
  "filenames": ["paper1", "paper2"],
  "concurrency": 2
}
```

示例：通过 API 解析公开 PDF URL。

```json
{
  "sources": ["https://example.com/paper.pdf"],
  "filenames": ["remote_paper"],
  "concurrency": 1
}
```

返回值里最常用的字段：

- `status`：`success` 或 `error`。
- `output_dir`：本次输出根目录。
- `results`：每篇文档的结果列表。
- `markdown_path`：Markdown 文件路径。
- `json_path`：JSON 文件路径。
- `image_paths`：裁剪或保存出的图片路径。
- `manifest_path`：manifest 文件路径。

### `get_server_sop`

返回给 Agent 使用的简短 SOP。通常不需要手动调用，主要用于让 Agent 了解推荐工作流。

## 命令行用法

除了 MCP 工具，也可以直接用 CLI 测试。

通过 API 解析本地 PDF：

```powershell
uv run python src/paper_ingest_mcp/glmocr-sdk.py `
  --file E:/papers/attention.pdf `
  --filename attention `
  --output-dir ./glmocr_results `
  --concurrency 1
```

通过 API 解析公开 URL：

```powershell
uv run python src/paper_ingest_mcp/glmocr-sdk.py `
  --url https://example.com/paper.pdf `
  --filename remote_paper `
  --output-dir ./glmocr_results `
  --concurrency 1
```

通过 API 解析目录内所有支持的文件：

```powershell
uv run python src/paper_ingest_mcp/glmocr-sdk.py `
  --file E:/papers `
  --output-dir ./glmocr_results `
  --concurrency 2
```

## 输出结构

默认输出类似：

```text
glmocr_results/
  attention/
    manifest.json
    attention.json
    attention.md
    imgs/
      page2_idx0.jpg
```

如果 `output_dir` 已经是文档目录，例如 `glmocr_results/attention` 且 `filename` 也是 `attention`，工具会直接写入该目录，不会再创建一层重复的 `attention/attention/`。

## 并发建议

- 本地 PDF 或图片：通常可以从 `2` 到 `4` 开始，具体取决于 API 配额、文件大小和网络。
- 公开 URL：建议用 `1` 或较小并发，避免远程下载和 API 请求同时造成超时。
- 大批量任务：可以传本地路径或公网 URL；本地路径会由 GLM-OCR API 客户端编码上传。
- 如果遇到限流、超时或显存压力，降低 `concurrency`。

## 常见问题

### 提示缺少 API Key

如果 GLM-OCR 运行在 MaaS 模式，需要设置：

```powershell
$env:ZHIPU_API_KEY="你的智谱 API Key"
```

或写入 `.env`。

### 需要本地 OCR 服务吗

不需要。本项目强制使用 GLM-OCR MaaS/API 模式，只需要 `ZHIPU_API_KEY`。

### URL 解析很慢

URL 输入需要由 API 访问或处理，速度取决于远程文件下载、网络和 API 响应时间。批量处理时可以降低 `concurrency`。

### Markdown 图片还是 bbox 占位符

工具会尽量保存 GLM-OCR 返回的图片结果。如果 API 返回的是 bbox 占位符，且输入无法在本地用于裁剪，可能会保留原始占位符。

### 不想输出到项目目录

设置 `GLMOCR_OUTPUT_PATH`，或调用时传 `output_dir`：

```json
{
  "sources": ["E:/papers/paper.pdf"],
  "output_dir": "D:/paper-ocr-results"
}
```

## 开发

```powershell
uv sync
uv run python -m compileall src
uv run python src/paper_ingest_mcp/glmocr-sdk.py --help
```

本地启动 MCP Server：

```powershell
uv run paper-ingest-mcp
```

## 提交前检查

提交前建议确认：

- `.env` 没有被提交。
- `glmocr_results/` 没有被提交。
- 临时 PDF 或私有论文没有被提交。
- `uv run python -m compileall src` 可以通过。
- `uv run python src/paper_ingest_mcp/glmocr-sdk.py --help` 可以正常输出帮助信息。

## 引用

- [zai-org/GLM-OCR](https://github.com/zai-org/GLM-OCR)
