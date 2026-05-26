from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import get_settings
from .glmocr_sdk import _is_url

mcp = FastMCP("paper-ingest-mcp")


@mcp.tool()
async def parse_with_glmocr(
    sources: list[str] | None = None,
    filenames: list[str] | None = None,
    output_dir: str | None = None,
    concurrency: int = 4,
    layout_device: str | None = None,
    config: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Parse local files or public URLs with GLM-OCR MaaS/API concurrently.

    Use sources for explicit local paths or URLs. filenames optionally provides
    output stems in the same order as sources.
    """
    settings = get_settings()
    resolved_sources = sources or []

    if not resolved_sources:
        return {
            "status": "error",
            "message": "Provide at least one source.",
        }

    filenames = filenames or []
    if filenames and len(filenames) != len(resolved_sources):
        return {
            "status": "error",
            "message": "filenames count must match the number of sources.",
            "source_count": len(resolved_sources),
            "filename_count": len(filenames),
        }

    resolved_output_dir = output_dir or str(settings.glmocr_output_path)
    command = [
        sys.executable,
        str(Path(__file__).with_name("glmocr-sdk.py")),
        "--output-dir",
        resolved_output_dir,
        "--concurrency",
        str(concurrency),
    ]
    for source in resolved_sources:
        command.extend(["--url" if _is_url(source) else "--file", source])
    for filename in filenames:
        command.extend(["--filename", filename])
    # API/MaaS mode does not use local layout devices. Keep the parameter for
    # backward-compatible tool schemas, but do not forward it to GLM-OCR.
    if config:
        command.extend(["--config", config])
    if api_key:
        command.extend(["--api-key", api_key])

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout_path = Path(temp_dir) / "glmocr_stdout.txt"
            stderr_path = Path(temp_dir) / "glmocr_stderr.txt"
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(Path(__file__).parents[2]),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                )
                await process.wait()
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        if process.returncode != 0:
            return {
                "status": "error",
                "message": f"GLM-OCR CLI exited with code {process.returncode}.",
                "stdout": stdout[-4000:],
                "stderr": stderr[-4000:],
                "sources": resolved_sources,
            }

        results = json.loads(stdout)
        return {
            "status": "success",
            "output_dir": str(Path(resolved_output_dir).expanduser().resolve()),
            "count": len(results),
            "results": results,
            "stderr": stderr[-4000:] if stderr else "",
        }

    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "sources": resolved_sources,
        }


@mcp.tool()
def get_server_sop() -> dict[str, Any]:
    """
    Return the recommended Agent SOP for this MCP server.
    """
    return {
        "status": "success",
        "sop": [
            "Call parse_with_glmocr(sources=[...]) for local PDF/image paths or public PDF/image URLs.",
            "This server forces GLM-OCR MaaS/API mode and does not require a local OCR service.",
            "Use conservative concurrency for public URLs and large PDFs because each item is sent to the remote API.",
            "Use parse_with_glmocr results and the output_dir/<filename>/ layout for downstream tools such as Obsidian.",
        ],
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
