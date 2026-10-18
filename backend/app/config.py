"""应用配置。所有密钥只从环境变量读取，不落库、不出现在日志中。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
import sys

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dashscope_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.8-flash"

    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_port: int = 6379

    db_path: Path = Path("data/app.db")
    data_dir: Path = Path("data")

    project_roots: str = "D:\\"
    dev_origin: str = "http://localhost:5173"

    # 对话助手
    api_base: str = "http://127.0.0.1:8000"
    chat_context_window: int = 32_768
    chat_budget_ratio: float = 0.7
    chat_compact_ratio: float = 0.6
    chat_output_reserve: int = 4_000
    chat_keep_recent_turns: int = 2
    chat_tool_result_limit: int = 6_000
    chat_max_rounds: int = 12
    chat_investigate_rounds: int = 6

    @field_validator("db_path", "data_dir", mode="after")
    @classmethod
    def _absolutize(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @property
    def project_root_list(self) -> list[Path]:
        # Windows 盘符含冒号，只能按分号切；POSIX 按 os.pathsep
        sep = ";" if sys.platform == "win32" else os.pathsep
        roots: list[Path] = []
        for part in self.project_roots.split(sep):
            part = part.strip()
            if not part:
                continue
            path = Path(part)
            if not path.is_absolute():
                path = (REPO_ROOT / path)
            roots.append(path.resolve())
        return roots

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        for sub in ("jobs", "resumes/generated", "resumes/imported", "projects", "artifacts"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
