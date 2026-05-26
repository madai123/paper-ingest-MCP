# paper-ingest-mcp

`paper-ingest-mcp` 是一个本地 MCP Server，用来把论文 PDF、图片或公开 PDF URL 交给 GLM-OCR 解析，并把结果保存成稳定的 Markdown、JSON、图片和 manifest 路径，方便 Agent 后续写入 Obsidian、知识库或其他论文处理流程。

## 功能

- 支持本地 PDF、图片、目录和公开 URL。
- 支持批量解析，并通过 `concurrency` 控制并发 OCR 数量。
- 输出目录固定为 `output_dir/<filename>/`，便于后续工具引用。
- 自动保存 `manifest.json`、`<filename>.json`、`<filename>.md` 和图片目录。
- 对 GLM-OCR Markdown 做轻量后处理，例如把 `$ a $` 规范为 `$a$`，更适合 Obsidian 阅读。

## 环境要求

- Python 3.10+
- `uv`
- GLM-OCR 所需的后端环境
- 如果使用 GLM-OCR MaaS 模式，需要智谱 API Key：`ZHIPU_API_KEY`

## 安装

```powershell
git clone <your-repo-url>
cd paper-ingest-mcp
uv sync
```

配置环境变量：

```powershell
$env:ZHIPU_API_KEY="你的智谱 API Key"
$env:GLMOCR_OUTPUT_PATH="./glmocr_results"
```

也可以在项目根目录创建 `.env`：

```env
ZHIPU_API_KEY=你的智谱 API Key
GLMOCR_OUTPUT_PATH=./glmocr_results
```

`.env` 已被 `.gitignore` 忽略，不要把真实密钥提交到仓库。

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

解析 PDF、图片或公开 URL。

参数：

- `sources`：必填，文件路径或 URL 列表。
- `filenames`：可选，输出文件名 stem。填写时数量必须和 `sources` 一致。
- `output_dir`：可选，输出根目录。默认使用 `GLMOCR_OUTPUT_PATH`，否则是 `./glmocr_results`。
- `concurrency`：可选，并发解析数量，默认 `4`。
- `layout_device`：可选，透传给 GLM-OCR，例如 `cpu` 或 `cuda:1`。
- `config`：可选，GLM-OCR 配置文件路径。
- `api_key`：可选，智谱 API Key。推荐使用 `ZHIPU_API_KEY` 环境变量。

示例：解析一个本地 PDF。

```json
{
  "sources": ["E:/papers/attention.pdf"],
  "filenames": ["attention"],
  "output_dir": "E:/mcp/paper-ingest-mcp/glmocr_results",
  "concurrency": 1
}
```

示例：批量解析本地文件。

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

示例：解析公开 PDF URL。

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

解析本地 PDF：

```powershell
uv run python src/paper_ingest_mcp/glmocr-sdk.py `
  --file E:/papers/attention.pdf `
  --filename attention `
  --output-dir ./glmocr_results `
  --concurrency 1
```

解析公开 URL：

```powershell
uv run python src/paper_ingest_mcp/glmocr-sdk.py `
  --url https://example.com/paper.pdf `
  --filename remote_paper `
  --output-dir ./glmocr_results `
  --concurrency 1
```

解析目录内所有支持的文件：

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

- 本地 PDF 或图片：通常可以从 `2` 到 `4` 开始。
- 公开 URL：建议用 `1` 或较小并发，因为工具会先下载远程文件再解析。
- 大批量任务：优先先把 PDF 下载到本地，再传本地路径。
- 如果遇到限流、超时或显存压力，降低 `concurrency`。

## 常见问题

### 提示缺少 API Key

如果 GLM-OCR 运行在 MaaS 模式，需要设置：

```powershell
$env:ZHIPU_API_KEY="你的智谱 API Key"
```

或写入 `.env`。

### URL 解析很慢

URL 输入会先下载到临时文件，再交给 GLM-OCR。批量处理时建议先下载到本地，然后传本地路径。

### Markdown 图片还是 bbox 占位符

工具会尽量把 GLM-OCR 的图片占位符裁剪成 `imgs/` 下的图片。如果运行环境缺少 PDF 裁剪依赖或输入不是可裁剪的 PDF，可能会保留原始占位符。

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
