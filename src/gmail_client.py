"""
Gmail Client Module

Fetches utility emails and PDF attachments from Gmail using IMAP.
Uses App Password authentication for hands-oiff automation.
"""

import imaplib
import email.message
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from src.logger_config import setup_logger

log = setup_logger(__name__)


class EmailAttachment(BaseModel):
    """Represents a PDF attachment from an email."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    filename: str
    content: bytes
    email_subject: str
    email_date: datetime
    sender: str


class GmailClient:
    """
    Client for fetching utility emails and PDF attachments from Gmail.

    Uses IMAP with App Password for authentication.
    """

    IMAP_SERVER = "imap.gmail.com"
    IMAP_PORT = 993

    def __init__(self, email_address: str, app_password: str):
        """Initialize the Gmail client.

        Args:
            credentials_path: Path to the OAuth 2.0 client credentials JSON file
                (downloaded from Google Cloud Console)
            token_path: Path where the access/refresh token will be cached.
        """
        self.email_address = email_address
        self.app_password = app_password
        self._connection: Optional[imaplib.IMAP4_SSL] = None

    @property
    def connection(self) -> imaplib.IMAP4_SSL:
        """Get the IMAP connection, raising if not connected."""
        if self._connection is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._connection

    def connect(self) -> None:
        """Connect and authenticate to Gmail IMAP server.

        Raises:
            imaplib.IMAP.error: If authentication fails.
        """
        log.info(f"Connecting to Gmail IMAP server: {self.IMAP_SERVER}:{self.IMAP_PORT}")
        self._connection = imaplib.IMAP4_SSL(self.IMAP_SERVER, self.IMAP_PORT)
        log.debug(f"Authenticating as {self.email_address}")
        self._connection.login(self.email_address, self.app_password)
        log.info("Successfully connected and authenticated to Gmail")

    def disconnect(self) -> None:
        """Close the IMAP connection."""
        if self._connection:
            try:
                log.debug("Disconnecting from Gmail IMAP server")
                self._connection.logout()
                log.info("Disconnected from Gmail")
            except Exception as e:
                log.warning(f"Error during disconnect: {e}")
            self._connection = None

    def __enter__(self) -> "GmailClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context mananger exit."""
        self.disconnect()

    def _build_search_criteria(
        self,
        sender: str,
        subject_contains: Optional[str] = None,
        after_date: Optional[datetime] = None,
    ) -> str:
        """Build IMAP search criteria string.

        Args:
            sender: Email address to search for
            subject_contains: Optional subject filter
            after_date: Optional date filter (emails after this date)

        Returns:
            IMAP search criteria string
        """
        criteria = [f'FROM "{sender}"']

        if subject_contains:
            criteria.append(f'SUBJECT "{subject_contains}"')

        if after_date:
            # IMAP date format: DD-MM-YYYY
            date_str = after_date.strftime("%d-%b-%Y")
            criteria.append(f"SINCE {date_str}")

        return " ".join(criteria)

    def _decode_header_value(self, value: Optional[str]) -> str:
        """Decode an email header value that may be encoded."""
        if not value:
            return ""

        decode_parts = decode_header(value)
        result = []
        for part, charset in decode_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(part)
        return "".join(result)

    def _parse_email_date(self, date_str: Optional[str]) -> datetime:
        """Parse email date header into datetime."""
        if date_str:
            try:
                return parsedate_to_datetime(date_str)
            except (ValueError, TypeError):
                pass
        return datetime.now()

    def _extract_attachments(
        self,
        msg: email.message.Message,
        email_subject: str,
        email_date: datetime,
        sender: str,
    ) -> list[EmailAttachment]:
        """Extract PDF attachments from an email message.

        Args:
            msg: Parsed email message
            email_subject: Subject line for metadata
            email_date: Date for metadata
            sender: Sender address for metadata

        Returns:
            List of EmailAttachment objects for PDF attachments
        """
        attachments = []

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # Check for PDF attachment
            if content_type == "application/pdf" or (
                "attachment" in content_disposition
                and part.get_filename()
                and part.get_filename().lower().endswith(".pdf")
            ):
                filename = part.get_filename()
                if filename:
                    # Decode filename if encoded
                    filename = self._decode_header_value(filename)

                    # Get attachment content
                    content = part.get_payload(decode=True)
                    if content:
                        attachments.append(
                            EmailAttachment(
                                filename=filename,
                                content=content,
                                email_subject=email_subject,
                                email_date=email_date,
                                sender=sender,
                            )
                        )

        return attachments

    def search_emails(
        self,
        sender: str,
        subject_contains: Optional[str] = None,
        after_date: Optional[datetime] = None,
        max_results: int = 10,
        mailbox: str = "INBOX",
    ) -> list[bytes]:
        """Search for emails matching the given criteria.

        Args:
            sender: Email address of the sender to search for
            subject_contains: Optional string that must appear in the subject
            after_date: Optional date to filter emails after
            max_results: Maximum number of emails to return
            mailbox: Mailbox to search (default: INBOX)

        Returns:
            List of message IDs (as bytes)
        """
        log.debug(f"Searching {mailbox} for emails from {sender}")
        self.connection.select(mailbox, readonly=True)

        criteria = self._build_search_criteria(sender, subject_contains, after_date)
        log.debug(f"Search criteria: {criteria}")
        _, message_numbers = self.connection.search(None, criteria)

        # message_numbers is a list with one element: space-separated IDs
        if not message_numbers[0]:
            log.info(f"No emails found matching criteria")
            return []

        message_ids = message_numbers[0].split()
        log.info(f"Found {len(message_ids)} email(s) matching criteria")

        # Return most recent first, limited to max_results
        return message_ids[-max_results:][::-1]

    def get_email(self, message_id: bytes) -> email.message.Message:
        """Fetch and parse a complete email message.

        Args:
            message_id: The IMAP message ID

        Returns:
            Parsed email.message.Message object
        """
        _, msg_data = self.connection.fetch(message_id, "(RFC822)")
        email_body = msg_data[0][1]
        return email.message_from_bytes(email_body)

    def get_pdf_attachments(
        self,
        sender: str,
        subject_contains: Optional[str] = None,
        after_date: Optional[datetime] = None,
        max_results: int = 10,
    ) -> list[EmailAttachment]:
        """
        Get PDF attachments from emails matching the search criteria.

        Args:
            sender: Email address of the sender to search for
            subject_contains: Optional string that must appear in the subject
            after_date: Optional date to filter emails after
            max_results: Maximum number of emails to search

        Returns:
            List of EmailAttachment objects containing PDF data
        """
        log.info(f"Fetching PDF attachments from {sender}")
        message_ids = self.search_emails(
            sender=sender,
            subject_contains=subject_contains,
            after_date=after_date,
            max_results=max_results,
        )

        attachments = []

        for msg_id in message_ids:
            msg = self.get_email(msg_id)

            # Extract metadata
            email_subject = self._decode_header_value(msg.get("Subject"))
            email_date = self._parse_email_date(msg.get("Date"))
            email_sender = self._decode_header_value(msg.get("From"))

            # Extract attachments from message parts
            msg_attachments = self._extract_attachments(msg, email_subject, email_date, email_sender)
            if msg_attachments:
                log.debug(f"Found {len(msg_attachments)} PDF(s) in email: {email_subject}")
            attachments.extend(msg_attachments)

        log.info(f"Retrieved {len(attachments)} total PDF attachment(s)")
        return attachments


def fetch_utility_pdfs(
    email_address: str,
    app_password: str,
    city_sender: str,
    body_corporate_sender: str,
    after_date: Optional[datetime] = None,
) -> dict[str, list[EmailAttachment]]:
    """Convenience function to fetch utility PDFs from configured senders.

    Args:
        email_address: Gmail address
        app_password: Google App Password
        city_sender: Email address for City of Johannesburg
        body_corporate_sender: Email address for Body Corporate
        after_date: Optional date to filter emails after

    Returns:
        Dictionary with 'city' and 'body_corporate' keys containing attachments
    """
    with GmailClient(email_address, app_password) as client:
        return {
            "city": client.get_pdf_attachments(
                sender=city_sender,
                after_date=after_date,
            ),
            "body_corporate": client.get_pdf_attachments(
                sender=body_corporate_sender,
                after_date=after_date,
            ),
        }
