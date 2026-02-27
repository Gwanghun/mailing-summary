"""Gmail OAuth2 authentication module.

Handles the full credential lifecycle:
- Local development: interactive browser flow via InstalledAppFlow
- GitHub Actions / CI: restores token from the GOOGLE_TOKEN_JSON env variable
- Automatic token refresh when an existing token has expired
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

# Scopes required for reading, modifying, and sending Gmail messages.
SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_ENV_VAR_TOKEN = "GOOGLE_TOKEN_JSON"
_ENV_VAR_ENVIRONMENT = "ENVIRONMENT"


def authenticate(
    credentials_path: str = "data/credentials.json",
    token_path: str = "data/token.json",
    environment: str | None = None,
) -> Credentials:
    """Return valid Google OAuth2 credentials.

    The function resolves credentials in the following priority order:

    1. **GitHub Actions / CI** (``environment == "github_actions"`` or the
       ``ENVIRONMENT`` env var is set to ``"github_actions"``): the raw JSON
       stored in ``GOOGLE_TOKEN_JSON`` is written to *token_path* so it can
       be loaded normally on the next step.
    2. **Existing token file**: if a ``token.json`` already exists it is
       loaded; expired tokens are refreshed automatically via the stored
       ``refresh_token``.
    3. **Interactive browser flow**: when no valid token is available the
       function launches a local HTTP server and opens the browser for the
       user to authorise the application.  The resulting credentials are
       persisted to *token_path*.

    Args:
        credentials_path: Path to the ``credentials.json`` file downloaded
            from the Google Cloud Console.
        token_path: Path where the ``token.json`` access/refresh token file
            is read from and written to.
        environment: Runtime environment string.  Pass ``"github_actions"``
            (or leave *None* to auto-detect via the ``ENVIRONMENT`` env var)
            to enable the CI credential-restoration path.

    Returns:
        A valid :class:`google.oauth2.credentials.Credentials` object.

    Raises:
        FileNotFoundError: If *credentials_path* does not exist and no valid
            token can be loaded or restored.
        ValueError: If the ``GOOGLE_TOKEN_JSON`` env variable is required but
            empty or missing.
    """
    resolved_env = environment or os.environ.get(_ENV_VAR_ENVIRONMENT, "local")

    if resolved_env == "github_actions":
        _restore_token_from_env(token_path)

    creds: Credentials | None = _load_existing_token(token_path)

    if creds and creds.expired and creds.refresh_token:
        logger.info("Access token expired – refreshing via refresh_token.")
        creds.refresh(Request())
        _save_token(creds, token_path)
        logger.info("Token refreshed and saved to %s.", token_path)
        return creds

    if creds and creds.valid:
        logger.debug("Loaded valid credentials from %s.", token_path)
        return creds

    # No usable token – run the interactive OAuth flow.
    logger.info("No valid token found.  Starting interactive OAuth2 flow.")
    creds = _run_interactive_flow(credentials_path)
    _save_token(creds, token_path)
    logger.info("New credentials saved to %s.", token_path)
    return creds


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _restore_token_from_env(token_path: str) -> None:
    """Write the token JSON stored in ``GOOGLE_TOKEN_JSON`` to *token_path*.

    This is used in GitHub Actions where secrets cannot be checked in as
    files but can be exposed as environment variables.

    Args:
        token_path: Destination file path for the restored token.

    Raises:
        ValueError: If the environment variable is not set or empty.
    """
    raw = os.environ.get(_ENV_VAR_TOKEN, "").strip()
    if not raw:
        raise ValueError(
            f"Environment variable '{_ENV_VAR_TOKEN}' is required in the "
            "'github_actions' environment but was not set or is empty."
        )

    # Validate that the value is parseable JSON before writing.
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"'{_ENV_VAR_TOKEN}' does not contain valid JSON: {exc}"
        ) from exc

    dest = Path(token_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(raw, encoding="utf-8")
    logger.info("Token restored from env var to %s.", token_path)


def _load_existing_token(token_path: str) -> Credentials | None:
    """Load credentials from *token_path* if the file exists.

    Args:
        token_path: Path to the ``token.json`` file.

    Returns:
        :class:`~google.oauth2.credentials.Credentials` if the file exists,
        otherwise *None*.
    """
    path = Path(token_path)
    if not path.exists():
        logger.debug("Token file not found at %s.", token_path)
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        logger.debug("Loaded credentials from %s.", token_path)
        return creds
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load token from %s: %s", token_path, exc)
        return None


def _run_interactive_flow(credentials_path: str) -> Credentials:
    """Execute the installed-app OAuth2 flow and return fresh credentials.

    Args:
        credentials_path: Path to the ``credentials.json`` client-secrets file.

    Returns:
        Newly obtained :class:`~google.oauth2.credentials.Credentials`.

    Raises:
        FileNotFoundError: If *credentials_path* does not exist.
    """
    cred_path = Path(credentials_path)
    if not cred_path.exists():
        raise FileNotFoundError(
            f"OAuth2 client-secrets file not found: {credentials_path!r}.  "
            "Download it from the Google Cloud Console and place it at the "
            "configured path."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)
    creds: Credentials = flow.run_local_server(port=0)
    return creds


def _save_token(creds: Credentials, token_path: str) -> None:
    """Persist *creds* as JSON to *token_path*.

    Creates parent directories if they do not exist.

    Args:
        creds: The credentials to serialise.
        token_path: Destination file path.
    """
    dest = Path(token_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(creds.to_json(), encoding="utf-8")
