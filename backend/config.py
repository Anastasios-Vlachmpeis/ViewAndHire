from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    whisper_model: str = "base"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    frontend_dir: Path = Path(__file__).resolve().parent.parent / "frontend"
    weights_dir: Path = Path(__file__).resolve().parent / "weights"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("openai_api_key", "openai_base_url", "openai_model", mode="before")
    @classmethod
    def strip_env_values(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Prefer .env over a stale shell variable so local edits actually take effect.
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    @property
    def db_path(self) -> Path:
        return self.data_dir / "viewandhire.db"

    @property
    def interviews_dir(self) -> Path:
        return self.data_dir / "interviews"


settings = Settings()
