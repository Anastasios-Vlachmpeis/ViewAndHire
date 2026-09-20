from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    whisper_model: str = "base"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    frontend_dir: Path = Path(__file__).resolve().parent.parent / "frontend"
    weights_dir: Path = Path(__file__).resolve().parent / "weights"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "viewandhire.db"

    @property
    def interviews_dir(self) -> Path:
        return self.data_dir / "interviews"


settings = Settings()
