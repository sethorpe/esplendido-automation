"""
Gmail Client Module

Fetches utility emails and PDF attachments from Gmail using the Gmail API.
Handles OAuth 2.0 authentication with offline access for token persistence.
"""

import base64
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from pydantic import BaseModel, ConfigDict

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


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
    Client for fetching utility emaild and PDF attachments from Gmail.

    Uses OAuth 2.0 with offline access for persistent authentication.
    Tokens are cached locally to avoid repeated authorization flows.
    """

    def __init__(self, credentials_path: Path, token_path: Path):
        """Initialize the Gmail client.

        Args:
            credentials_path: Path to the OAuth 2.0 client credentials JSON file
                (downloaded from Google Cloud Console)
            token_path: Path where the access/refresh token will be cached.
        """
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self._service: Optional[Resource] = None

    def authenticate(self) -> None:
        """Authenticate with Gmail API using OAuth 2.0.

        If a valid token exists, it will be loaded from cache.
        If the token is expired, it will be refreshed.
        If no token exists, the OAuth flow will be initiated (opens browser)
        """
        creds: Optional[Credentials] = None

        # Load existing token if available
        if self.token_path.exists():
            with open(self.token_path, "rb") as token_file:
                creds = pickle.load(token_file)

        # Refresh or obtain new credentials if needed
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Credentials file not found: {self.credentials_path}\n"
                        "Download OAuth 2.0 credentials from Google Cloud Console"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Cache the token for future use
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, "wb") as token_file:
                pickle.dump(creds, token_file)

        self._service = build("gmail", "v1", credentials=creds)

    @property
    def service(self) -> Resource:
        """Get the authenticated Gmail API service."""
        if self._service is None:
            raise RuntimeError(
                "Gmail client not authenticated. Call authenticate() first."
            )
        return self._service

    def search_emails(
        self,
        sender: str,
        subject_contains: Optional[str] = None,
        after_date: Optional[datetime] = None,
        max_results: int = 10,
    ) -> list[dict]:
        """Search for emails matching the given criteria.

        Args:
            sender: Email address of the sender to search for.
            subject_contains: Optional string that must appear in the subject
            after_date: Optional date to filter emails after
            max_results: Maximum number of emails to return

        Returns:
            List of email message metadata dictionaries
        """
        # Build Gmail search query
        query_parts = [f"from:{sender}"]

        if subject_contains:
            query_parts.append(f"subject:{subject_contains}")

        if after_date:
            date_str = after_date.strftime("%Y/%m/%d")
            query_parts.append(f"after:{date_str}")

        query = " ".join(query_parts)

        # Execute search
        results = (
            self.service.users()  # type: ignore[attr-defined]
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )

        return results.get("messages", [])

    def get_email_details(self, message_id: str) -> dict:
        """Get full details of an email message.

        Args:
            message_id: The Gmail message ID

        Returns:
            Full message data including headers and payload
        """
        return (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    def _parse_email_date(self, headers: list[dict]) -> datetime:
        """Extract and parse the date from email headers."""
        for header in headers:
            if header["name"].lower() == "date":
                # Parse various email date formats
                date_str = header["value"]
                # Remove timezone name if present (e.g., "(PST)")
                if "(" in date_str:
                    date_str = date_str[: date_str.index("(")].strip()
                # Try common formats
                for fmt in [
                    "%a, %d %b %Y %H:%M:%S %z",
                    "%d %b %Y %H:%M:%S %z",
                    "%a, %d %b %Y %H:%M:%S",
                ]:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
                # Fallback to current time if parsing fails
                return datetime.now()
        return datetime.now()

    def _get_header_value(self, headers: list[dict], name: str) -> str:
        """Get a specific header value from email headers."""
        for header in headers:
            if header["name"].lower() == name.lower():
                return header["value"]
        return ""

    def _extract_attachments_from_parts(
        self,
        parts: list[dict],
        message_id: str,
        email_subject: str,
        email_date: datetime,
        sender: str,
    ) -> list[EmailAttachment]:
        """Recursively extract PDF attachments from message parts."""
        attachments = []

        for part in parts:
            filename = part.get("filename", "")
            mime_type = part.get("mimeType", "")

            # Check for nested parts (multipart messages)
            if "parts" in part:
                attachments.extend(
                    self._extract_attachments_from_parts(
                        part["parts"], message_id, email_subject, email_date, sender
                    )
                )

            # Check if this part is a PDF attachment
            elif filename and mime_type == "application/pdf":
                attachment_id = part["body"].get("attachmentId")

                if attachment_id:
                    # Fetch the attachment data
                    attachment_data = (
                        self.service.users()
                        .messages()
                        .attachments()
                        .get(userId="me", messageId=message_id, id=attachment_id)
                        .execute()
                    )

                    # Decode the base64 content
                    content = base64.urlsafe_b64decode(attachment_data["data"])

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

    def get_pdf_attachments(
        self,
        sender: str,
        subject_contains: Optional[str] = None,
        after_date: Optional[datetime] = None,
        max_results: int = 5,
    ) -> list[EmailAttachment]:
        """
        Search for emails and extract PDF attachments.

        Args:
            sender: Email address of the sender to search for
            subject_contains: Optional string that must appear in the subject
            after_date: Optional date to filter emails after
            max_results: Maximum number of emails to search

        Returns:
            List of EmailAttachment objects containing PDF data
        """
        # Search for matching emails
        messages = self.search_emails(
            sender=sender,
            subject_contains=subject_contains,
            after_date=after_date,
            max_results=max_results,
        )

        attachments = []

        for msg in messages:
            # Get full messsage details
            message = self.get_email_details(msg["id"])
            payload = message.get("payload", {})
            headers = payload.get("headers", [])

            # Extract email metadata
            email_subject = self._get_header_value(headers, "subject")
            email_date = self._parse_email_date(headers)
            email_sender = self._get_header_value(headers, "from")

            # Extract attachments from message parts
            parts = payload.get("parts", [])
            if parts:
                attachments.extend(
                    self._extract_attachments_from_parts(
                        parts, msg["id"], email_subject, email_date, email_sender
                    )
                )
        return attachments

    def fetch_utility_pdfs(
        credentials_path: Path,
        token_path: Path,
        city_sender: str,
        body_corporate_sender: str,
        after_date: Optional[datetime] = None,
    ) -> dict[str, list[EmailAttachment]]:
        """Convenience function to fetch utility PDFs from configured senders.

        Args:
            credentials_path: Path to OAuth credentials JSON
            token_path: Path to token cache file
            city_sender: Email address for City of Johannesburg
            body_corporate_sender: Email address for Body Corporate
            after_date: Optional date to filter emails after

        Returns:
            Dictionary with 'city' and 'body_corporate' keys containing attachments
        """
        client = GmailClient(credentials_path, token_path)
        client.authenticate()

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
