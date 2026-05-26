from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    glmocr_output_path: Path


def get_settings() -> Settings:
    return Settings(
        glmocr_output_path=Path(
            os.getenv("GLMOCR_OUTPUT_PATH", "./glmocr_results")
        ).expanduser().resolve(),
    )
