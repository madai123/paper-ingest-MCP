from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import shutil
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv
from typing import Any, Iterable, Sequence

import httpx
from urllib.parse import unquote, urlparse


load_dotenv()

URL_SCHEMES = {"http", "https"}
DEFAULT_OUTPUT_DIR = Path("./glmocr_results")
STDOUT_REDIRECT_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class GlmOcrRequest:
    """
    One independent GLM-OCR parse request.

    source:
        Local file path, remote URL, data URI, bytes, or a list accepted by
        glmocr.parse in MaaS/API mode.
    filename:
        Optional output file/directory stem. When provided, this wrapper writes a
        stable manifest and best-effort JSON/Markdown files under that stem.
    output_dir:
        Optional per-request output directory. Falls back to the batch output_dir.
    """

    source: str | Path | bytes | Sequence[str | Path | bytes]
    filename: str | None = None
    output_dir: str | Path | None = None


@dataclass(frozen=True, slots=True)
class GlmOcrResponse:
    source: str
    output_dir: str
    filename: str
    manifest_path: str
    saved_files: list[str]
    json_path: str | None = None
    markdown_path: str | None = None
    image_paths: list[str] | None = None


def _is_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in URL_SCHEMES


def _sanitize_stem(value: str) -> str:
    stem = Path(value).stem if not _is_url(value) else Path(unquote(urlparse(value).path)).stem
    stem = stem or "glmocr_result"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "glmocr_result"


def _request_source_label(source: str | Path | bytes | Sequence[str | Path | bytes]) -> str:
    if isinstance(source, bytes):
        return "bytes"
    if isinstance(source, (str, Path)):
        return str(source)
    first = next(iter(source), "document")
    if isinstance(first, bytes):
        return "document"
    return str(first)


def _resolve_filename(source: str | Path | bytes | Sequence[str | Path | bytes], filename: str | None) -> str:
    if filename:
        return _sanitize_stem(filename)
    return _sanitize_stem(_request_source_label(source))


def _snapshot_files(output_dir: Path) -> set[Path]:
    if not output_dir.exists():
        return set()
    return {p.resolve() for p in output_dir.rglob("*") if p.is_file()}


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if hasattr(value, "model_dump"):
            return _json_safe(value.model_dump())
        if hasattr(value, "dict"):
            return _json_safe(value.dict())
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        return str(value)


def _extract_json_result(result: Any) -> Any | None:
    for attr in ("json_result", "json", "result", "data"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
            return _json_safe(value)
    return None


def _extract_markdown_result(result: Any) -> str | None:
    for attr in ("markdown", "markdown_result", "md_result", "md"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
            if value is not None:
                return str(value)
    return None


IMAGE_PLACEHOLDER_RE = re.compile(
    r"!\[\]\(page=(\d+),bbox=\[([0-9.]+),\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)\]\)"
)


def _materialize_pdf_source(source: str | Path | bytes | Sequence[str | Path | bytes]) -> tuple[Path | None, tempfile.TemporaryDirectory[str] | None]:
    if isinstance(source, bytes):
        tmp_dir = tempfile.TemporaryDirectory()
        pdf_path = Path(tmp_dir.name) / "source.pdf"
        pdf_path.write_bytes(source)
        return pdf_path, tmp_dir

    if isinstance(source, (str, Path)):
        source_text = str(source)
        if _is_url(source_text):
            tmp_dir = tempfile.TemporaryDirectory()
            parsed = urlparse(source_text)
            suffix = Path(unquote(parsed.path)).suffix.lower()
            if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
                suffix = ".pdf"
            local_path = Path(tmp_dir.name) / f"source{suffix}"
            with httpx.Client(follow_redirects=True, timeout=60) as client:
                response = client.get(source_text, headers={"User-Agent": "paper-ingest-mcp/0.1"})
                response.raise_for_status()
            local_path.write_bytes(response.content)
            return local_path, tmp_dir

        pdf_path = Path(source_text).expanduser().resolve()
        if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
            return pdf_path, None

    return None, None


def _crop_markdown_image_placeholders(
    *,
    markdown: str,
    source: str | Path | bytes | Sequence[str | Path | bytes],
    output_dir: Path,
    filename: str,
) -> tuple[str, list[str]]:
    matches = list(IMAGE_PLACEHOLDER_RE.finditer(markdown))
    if not matches:
        return markdown, []

    try:
        import fitz
    except ImportError:
        return markdown, []

    pdf_path, tmp_dir = _materialize_pdf_source(source)
    if pdf_path is None:
        return markdown, []

    image_dir = output_dir / "imgs"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []

    try:
        doc = fitz.open(pdf_path)
        replacements: dict[str, str] = {}
        for idx, match in enumerate(matches):
            original = match.group(0)
            if original in replacements:
                continue

            page_number = int(match.group(1))
            bbox = [float(match.group(i)) for i in range(2, 6)]
            page_index = page_number - 1
            if page_index < 0 or page_index >= len(doc):
                continue

            page = doc[page_index]
            rect = page.rect
            x0, y0, x1, y1 = bbox
            clip = fitz.Rect(
                x0 / 1000 * rect.width,
                y0 / 1000 * rect.height,
                x1 / 1000 * rect.width,
                y1 / 1000 * rect.height,
            ) & rect
            if clip.is_empty or clip.width <= 1 or clip.height <= 1:
                continue

            image_path = image_dir / f"page{page_number}_idx{idx}.jpg"
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
            pix.save(str(image_path))
            image_paths.append(str(image_path))
            relative_path = image_path.relative_to(output_dir).as_posix()
            replacements[original] = f"![]({relative_path})"

        for original, replacement in replacements.items():
            markdown = markdown.replace(original, replacement)

    finally:
        if 'doc' in locals():
            doc.close()
        if tmp_dir is not None:
            tmp_dir.cleanup()

    return markdown, image_paths


def _postprocess_paper_markdown(markdown: str) -> str:
    """
    Normalize GLM-OCR Markdown for paper reading tools such as Obsidian.

    GLM-OCR often emits inline math as `$ a $`; Obsidian expects `$a$`.
    Keep block math untouched except for trimming edge whitespace inside delimiters.
    """
    markdown = re.sub(r"(?<!\$)\$\s*([^$\n]*?\S)\s*\$(?!\$)", r"$\1$", markdown)
    markdown = re.sub(r"\$\$\s*\n?", r"$$\n", markdown)
    markdown = re.sub(r"\n?\s*\$\$", r"\n$$", markdown)
    return markdown


def _copy_first_temp_file(temp_root: Path, suffix: str, target_path: Path) -> Path | None:
    for source_path in sorted(temp_root.rglob(f"*{suffix}")):
        if source_path.is_file():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            return target_path
    return None


def _copy_temp_images(temp_root: Path, image_dir: Path) -> list[str]:
    image_paths: list[str] = []
    for suffix in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for source_path in sorted(temp_root.rglob(suffix)):
            if not source_path.is_file():
                continue
            image_dir.mkdir(parents=True, exist_ok=True)
            target_path = image_dir / source_path.name
            if target_path.exists():
                target_path = image_dir / f"{source_path.stem}_{len(image_paths)}{source_path.suffix}"
            shutil.copy2(source_path, target_path)
            image_paths.append(str(target_path))
    return image_paths


def _save_result(
    *,
    result: Any,
    source: str | Path | bytes | Sequence[str | Path | bytes],
    filename: str,
    output_dir: Path,
    crop_source: str | Path | bytes | Sequence[str | Path | bytes] | None = None,
) -> GlmOcrResponse:
    root_dir = output_dir
    # If the caller already passed a document-specific directory such as
    # raw/2505.11470 with filename=2505.11470, write directly there instead of
    # creating raw/2505.11470/2505.11470.
    document_dir = root_dir if root_dir.name == filename else root_dir / filename
    image_dir = document_dir / "imgs"
    document_dir.mkdir(parents=True, exist_ok=True)

    temp_saved_dir: tempfile.TemporaryDirectory[str] | None = None
    temp_root: Path | None = None
    try:
        if hasattr(result, "save"):
            temp_saved_dir = tempfile.TemporaryDirectory()
            temp_root = Path(temp_saved_dir.name)
            result.save(output_dir=str(temp_root))

        json_payload = _extract_json_result(result)
        json_path: Path | None = None
        if json_payload is not None:
            json_path = document_dir / f"{filename}.json"
            json_path.write_text(
                json.dumps(json_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        elif temp_root is not None:
            json_path = _copy_first_temp_file(temp_root, ".json", document_dir / f"{filename}.json")

        markdown_payload = _extract_markdown_result(result)
        markdown_path: Path | None = None
        image_paths: list[str] = []
        if markdown_payload:
            markdown_payload, image_paths = _crop_markdown_image_placeholders(
                markdown=markdown_payload,
                source=crop_source or source,
                output_dir=document_dir,
                filename=filename,
            )
            markdown_payload = _postprocess_paper_markdown(markdown_payload)
            markdown_path = document_dir / f"{filename}.md"
            markdown_path.write_text(markdown_payload, encoding="utf-8")
        elif temp_root is not None:
            markdown_path = _copy_first_temp_file(temp_root, ".md", document_dir / f"{filename}.md")
            if markdown_path is not None:
                markdown_path.write_text(
                    _postprocess_paper_markdown(markdown_path.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )

        if temp_root is not None:
            image_paths.extend(_copy_temp_images(temp_root, image_dir))

        saved_files = []
        if json_path:
            saved_files.append(str(json_path))
        if markdown_path:
            saved_files.append(str(markdown_path))
        saved_files.extend(image_paths)

        manifest_path = document_dir / "manifest.json"
        manifest = {
            "source": _request_source_label(source),
            "filename": filename,
            "output_dir": str(root_dir),
            "document_dir": str(document_dir),
            "json_path": str(json_path) if json_path else None,
            "markdown_path": str(markdown_path) if markdown_path else None,
            "images_dir": str(image_dir),
            "saved_files": sorted(set(saved_files)),
            "image_paths": sorted(set(image_paths)),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return GlmOcrResponse(
            source=manifest["source"],
            output_dir=str(document_dir),
            filename=filename,
            manifest_path=str(manifest_path),
            saved_files=manifest["saved_files"],
            json_path=str(json_path) if json_path else None,
            markdown_path=str(markdown_path) if markdown_path else None,
            image_paths=manifest["image_paths"],
        )
    finally:
        if temp_saved_dir is not None:
            temp_saved_dir.cleanup()


def parse_glmocr_sync(
    request: GlmOcrRequest,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    **glmocr_kwargs: Any,
) -> GlmOcrResponse:
    """
    Parse one local file, URL, or multi-page document synchronously via API.

    This wrapper always uses GLM-OCR MaaS/API mode. Local file paths are still
    valid inputs; they are uploaded by the upstream GLM-OCR client.
    """
    with STDOUT_REDIRECT_LOCK, contextlib.redirect_stdout(sys.stderr):
        return _parse_glmocr_sync_inner(
            request,
            output_dir=output_dir,
            **glmocr_kwargs,
        )


def _parse_glmocr_sync_inner(
    request: GlmOcrRequest,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    **glmocr_kwargs: Any,
) -> GlmOcrResponse:
    try:
        from glmocr import parse
    except ImportError as exc:
        raise RuntimeError(
            "glmocr is not installed. Install it first, for example: pip install glmocr"
        ) from exc

    target_dir = Path(request.output_dir or output_dir).expanduser().resolve()
    filename = _resolve_filename(request.source, request.filename)
    parse_source: Any = request.source
    glmocr_kwargs["mode"] = "maas"

    try:
        result = parse(parse_source, **glmocr_kwargs)
    except Exception as exc:
        if exc.__class__.__name__ == "MissingApiKeyError":
            raise RuntimeError(
                "GLM-OCR MaaS/API mode requires ZHIPU_API_KEY. "
                "Set ZHIPU_API_KEY in your shell or .env, or pass --api-key."
            ) from exc
        raise

    return _save_result(
        result=result,
        source=request.source,
        filename=filename,
        output_dir=target_dir,
        crop_source=parse_source,
    )


async def parse_glmocr(
    request: GlmOcrRequest,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    **glmocr_kwargs: Any,
) -> GlmOcrResponse:
    """Async wrapper for one independent GLM-OCR request."""
    return await asyncio.to_thread(
        parse_glmocr_sync,
        request,
        output_dir=output_dir,
        **glmocr_kwargs,
    )


async def parse_glmocr_batch(
    requests: Iterable[GlmOcrRequest],
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    concurrency: int = 4,
    **glmocr_kwargs: Any,
) -> list[GlmOcrResponse]:
    """
    Parse many independent documents concurrently.

    Note that an upstream list source is still treated as a single document. For
    multiple independent documents, pass multiple GlmOcrRequest objects.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")

    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(request: GlmOcrRequest) -> GlmOcrResponse:
        async with semaphore:
            return await parse_glmocr(request, output_dir=output_dir, **glmocr_kwargs)

    return await asyncio.gather(*(_run_one(request) for request in requests))


def _expand_directory(path: Path) -> list[Path]:
    suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)


def _build_cli_requests(args: argparse.Namespace) -> list[GlmOcrRequest]:
    sources: list[str | Path] = []
    for value in [*args.sources, *args.file, *args.url]:
        if not _is_url(str(value)):
            path = Path(value).expanduser()
            if path.is_dir():
                sources.extend(_expand_directory(path))
                continue
            sources.append(path)
        else:
            sources.append(str(value))

    if not sources:
        raise SystemExit("No input provided. Use positional sources, --file, or --url.")

    filenames = args.filename or []
    if len(filenames) > 1 and len(filenames) != len(sources):
        raise SystemExit("When multiple --filename values are provided, count must match inputs.")

    requests: list[GlmOcrRequest] = []
    for index, source in enumerate(sources):
        filename = None
        if len(filenames) == 1 and len(sources) == 1:
            filename = filenames[0]
        elif len(filenames) > 1:
            filename = filenames[index]
        requests.append(GlmOcrRequest(source=source, filename=filename))
    return requests


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GLM-OCR MaaS/API batch wrapper for local files and URLs.")
    parser.add_argument("sources", nargs="*", help="Local files, directories, or URLs.")
    parser.add_argument("--file", action="append", default=[], help="Local file or directory input. Repeatable.")
    parser.add_argument("--url", action="append", default=[], help="Remote file URL input. Repeatable.")
    parser.add_argument("--filename", action="append", help="Output stem. Repeat once per input, or once for one input.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent independent OCR calls.")
    parser.add_argument("--layout-device", help="Ignored in API mode; kept for backward compatibility.")
    parser.add_argument("--config", help="Pass-through GLM-OCR config path.")
    parser.add_argument("--api-key", help="Zhipu API key for GLM-OCR MaaS mode. Prefer ZHIPU_API_KEY env var.")
    return parser


async def _main_async(args: argparse.Namespace) -> list[GlmOcrResponse]:
    glmocr_kwargs: dict[str, Any] = {}
    if args.config:
        glmocr_kwargs["config_path"] = args.config
    if args.api_key:
        glmocr_kwargs["api_key"] = args.api_key

    return await parse_glmocr_batch(
        _build_cli_requests(args),
        output_dir=args.output_dir,
        concurrency=args.concurrency,
        **glmocr_kwargs,
    )


def main() -> None:
    args = build_arg_parser().parse_args()
    responses = asyncio.run(_main_async(args))
    print(json.dumps([asdict(response) for response in responses], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
