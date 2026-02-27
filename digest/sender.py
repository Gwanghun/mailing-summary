"""
DigestSender – send a DigestEmail via Gmail SMTP with App Password auth.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from digest.digest_builder import DigestEmail

logger = logging.getLogger(__name__)


class DigestSender:
    """Send a :class:`DigestEmail` via Gmail SMTP + App Password (STARTTLS).

    Parameters
    ----------
    gmail_user:
        The Gmail address used as the SMTP login and From address.
    app_password:
        A Gmail App Password (16-character string) generated in Google
        Account settings.  Do **not** use the regular Gmail password here.
    smtp_host:
        SMTP server hostname (default: ``smtp.gmail.com``).
    smtp_port:
        SMTP server port (default: ``587`` – STARTTLS).
    """

    def __init__(
        self,
        gmail_user: str,
        app_password: str,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
    ) -> None:
        self._gmail_user = gmail_user
        self._app_password = app_password
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, digest: DigestEmail, recipient: str) -> bool:
        """Deliver *digest* to *recipient* using Gmail SMTP.

        The email is sent as a ``multipart/alternative`` message with both
        a plain-text and an HTML part so that all email clients can render
        it correctly.

        Returns
        -------
        bool
            ``True`` if the message was accepted by the SMTP server,
            ``False`` on any error (exception is caught and logged, never
            re-raised).
        """
        msg = self._build_mime(digest, recipient)
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._gmail_user, self._app_password)
                server.sendmail(
                    from_addr=self._gmail_user,
                    to_addrs=[recipient],
                    msg=msg.as_string(),
                )
            logger.info(
                "Digest sent successfully | to=%s | subject=%r | total=%d",
                recipient,
                digest.subject,
                digest.total_count,
            )
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error(
                "SMTP authentication failed for %s – check App Password",
                self._gmail_user,
            )
        except smtplib.SMTPRecipientsRefused as exc:
            logger.error("Recipient refused by SMTP server: %s", exc.recipients)
        except smtplib.SMTPException as exc:
            logger.error("SMTP error while sending digest: %s", exc)
        except OSError as exc:
            logger.error(
                "Network error connecting to %s:%d – %s",
                self._smtp_host,
                self._smtp_port,
                exc,
            )
        return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_mime(self, digest: DigestEmail, recipient: str) -> MIMEMultipart:
        """Construct a ``multipart/alternative`` MIME message."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = digest.subject
        msg["From"] = self._gmail_user
        msg["To"] = recipient

        # Plain-text part first (lower priority fallback)
        part_text = MIMEText(digest.plain_text, "plain", "utf-8")
        # HTML part last (higher priority, preferred by most clients)
        part_html = MIMEText(digest.html_body, "html", "utf-8")

        msg.attach(part_text)
        msg.attach(part_html)
        return msg
