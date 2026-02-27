"""
Application settings managed via pydantic-settings.

All values are loaded from environment variables or a .env file.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Central configuration for the mailing_summary application.

    Attributes are populated from environment variables (case-insensitive).
    A .env file in the project root is loaded automatically.
    """

    # ------------------------------------------------------------------
    # Gmail / Google OAuth
    # ------------------------------------------------------------------
    gmail_user: str = Field(..., description="Gmail address used for reading emails")
    google_credentials_path: str = Field(
        "data/credentials.json",
        description="Path to the Google OAuth2 client-secrets JSON file",
    )
    google_token_path: str = Field(
        "data/token.json",
        description="Path where the OAuth2 access/refresh token is persisted",
    )

    # ------------------------------------------------------------------
    # SMTP (sending digest)
    # ------------------------------------------------------------------
    gmail_app_password: str = Field(
        "",
        description="Gmail App Password used for SMTP authentication",
    )
    smtp_host: str = Field("smtp.gmail.com", description="SMTP server hostname")
    smtp_port: int = Field(587, description="SMTP server port (587 = STARTTLS)")

    # ------------------------------------------------------------------
    # Digest delivery
    # ------------------------------------------------------------------
    digest_recipient: str = Field(
        "",
        description="Email address that receives the daily digest",
    )

    # ------------------------------------------------------------------
    # Anthropic / Claude
    # ------------------------------------------------------------------
    anthropic_api_key: str = Field(..., description="Anthropic API key for Claude")
    claude_model: str = Field(
        "claude-sonnet-4-6",
        description="Claude model identifier used for summarisation",
    )

    # ------------------------------------------------------------------
    # Processing behaviour
    # ------------------------------------------------------------------
    lookback_hours: int = Field(
        24,
        description="How many hours back to scan for new newsletters",
    )
    max_emails_per_run: int = Field(
        50,
        description="Maximum number of emails to process in a single run",
    )
    min_importance_score: int = Field(
        2,
        description="Emails with importance score below this threshold are skipped",
    )

    # ------------------------------------------------------------------
    # Runtime environment
    # ------------------------------------------------------------------
    environment: str = Field(
        "local",
        description="Deployment environment: local | staging | production",
    )
    log_level: str = Field(
        "INFO",
        description="Logging level: DEBUG | INFO | WARNING | ERROR | CRITICAL",
    )

    # ------------------------------------------------------------------
    # pydantic-settings configuration
    # ------------------------------------------------------------------
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------
    @property
    def db_path(self) -> str:
        """Absolute-or-relative path to the SQLite database file."""
        return "data/mailing_summary.db"
