"""Application configuration loaded from environment variables."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class Settings:
    """Central settings object for the Ada backend."""

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-01-preview")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # Storage backend
    STORAGE_TYPE: str = os.getenv("STORAGE_TYPE", "file")  # "file" or "mongo"
    MONGO_CONNECTION_STRING: str = os.getenv("MONGO_CONNECTION_STRING", "")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "ada_platform")

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    TEMPLATES_DIR: Path = BASE_DIR / "templates"
    OUTPUT_DIR: Path = BASE_DIR / "output"
    DATA_DIR: Path = BASE_DIR / "app" / "data"
    UPLOAD_DIR: Path = BASE_DIR / "uploads"

    @property
    def azure_configured(self) -> bool:
        return bool(self.AZURE_OPENAI_ENDPOINT and self.AZURE_OPENAI_API_KEY)

    @property
    def azure_config(self) -> dict:
        return {
            "endpoint": self.AZURE_OPENAI_ENDPOINT,
            "deployment": self.AZURE_OPENAI_DEPLOYMENT,
            "api_key": self.AZURE_OPENAI_API_KEY,
            "api_version": self.AZURE_OPENAI_API_VERSION,
        }


settings = Settings()

# Ensure directories exist
for d in [settings.OUTPUT_DIR, settings.DATA_DIR, settings.UPLOAD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

