from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path

class Settings(BaseSettings):
    OPENAI_API_KEY: str | None = Field(default=None, env="OPENAI_API_KEY")
    ROOT: Path = Path(__file__).resolve().parents[2]  # repo root: proofs uite_qa/
    WORKSPACE_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "workspace")
    SQLITE_PATH: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "workspace" / "review.sqlite3")
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DEBUG: bool = True
    DEBUG_DEVTOOLS: bool = False

    class Config:
        env_file = ".env"

settings = Settings()

# Ensure workspace subdirs
for sub in ["store", "extractions"]:
    try:
        (settings.WORKSPACE_DIR / sub).mkdir(parents=True, exist_ok=True)
    except OSError:
        # In read-only deployments, ignore directory creation failure.
        pass
