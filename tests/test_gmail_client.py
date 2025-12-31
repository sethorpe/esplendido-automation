"""Tests for the Gmail client module"""

import base64
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.gmail_client import EmailAttachment, GmailClient


@pytest.fixture
def mock_credentials():
    """Create mock credentials"""
    creds = MagicMock()
    creds.valid = True
    creds.expired = False
    return creds


@pytest.fixture
def gmail_client(tmp_path):
    """Create a GmailClient instance with temporary paths."""
    credentials_path = tmp_path / "credentials.json"
    token_path = tmp_path / "token.pickle"
    return GmailClient(credentials_path, token_path)


class TestGmailClientInit:
    """Tests for GmailClient initialization."""

    def test_init_sets_paths(self, tmp_path):
        """Test that init properly sets credentials and token paths."""
        creds = tmp_path / "creds.json"
        token = tmp_path / "token.pickle"

        client = GmailClient(creds, token)
        assert client.credentials_path == creds
        assert client.token_path == token
        assert client._service is None

    def test_service_raises_without_auth(self, gmail_client):
        """Test that accessing service without auth raises error."""
        with pytest.raises(RuntimeError, match="not authenticated"):
            _ = gmail_client.service


class TestGmailClientAuthentication:
    """Test for OAuth authentication."""

    @patch("src.gmail_client.build")
    @patch("src.gmail_client.pickle")
    def test_authenticate_loads_existing_valid_token(
        self, mock_pickle, mock_build, gmail_client, mock_credentials, tmp_path
    ):
        """Test that valid cached tokens are loaded."""

        token_path = tmp_path / "token.pickle"
        token_path.write_bytes(b"dummy")
        gmail_client.token_path = token_path

        mock_pickle.load.return_value = mock_credentials
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        gmail_client.authenticate()

        assert gmail_client._service == mock_service
        mock_build.assert_called_once_with("gmail", "v1", credentials=mock_credentials)

    @patch("src.gmail_client.build")
    @patch("src.gmail_client.pickle")
    @patch("src.gmail_client.Request")
    def test_authenticate_refreshes_expired_token(
        self, mock_request, mock_pickle, mock_build, gmail_client, tmp_path
    ):
        """Test that expired tokens are refreshed."""
        token_path = tmp_path / "token.pickle"
        token_path.write_bytes(b"dummy")
        gmail_client.token_path = token_path

        expired_creds = MagicMock()
        expired_creds.valid = False
        expired_creds.expired = True
        expired_creds.refresh_token = "refresh_token"
        mock_pickle.load.return_value = expired_creds

        gmail_client.authenticate()

        expired_creds.refresh.assert_called_once()

    class TestEmailSearch:
        """Tests for email search functionality."""

    @patch("src.gmail_client.build")
    def test_search_emails_builds_correct_query(self, mock_build, gmail_client):
        """Test that search query is built correctly."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_list = mock_service.users().messages().list
        mock_list.return_value.execute.return_value = {"messages": []}

        gmail_client._service = mock_service

        gmail_client.search_emails(
            sender="test@example.com",
            subject_contains="Invoice",
            after_date=datetime(2024, 1, 15),
        )

        mock_list.assert_called_once()
        call_kwargs = mock_list.call_args[1]
        assert "from:test@example.com" in call_kwargs["q"]
        assert "subject:Invoice" in call_kwargs["q"]
        assert "after:2024/01/15" in call_kwargs["q"]

    @patch("src.gmail_client.build")
    def test_search_emails_returns_messages(self, mock_build, gmail_client):
        """Test that search returns message list."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        expected_messages = [{"id": "123"}, {"id": "456"}]
        mock_service.users().messages().list.return_value.execute.return_value = {
            "messages": expected_messages
        }

        gmail_client._service = mock_service
        result = gmail_client.search_emails(sender="test@example.com")

        assert result == expected_messages

    @patch("src.gmail_client.build")
    def test_search_emails_handles_no_results(self, mock_build, gmail_client):
        """Test that search handles empty results gracefully."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.users().messages().list.return_value.execute.return_value = {}

        gmail_client._service = mock_service
        result = gmail_client.search_emails(sender="test@example.com")

        assert result == []


class TestPdfAttachmentExtraction:
    """Tests for PDF attachment extraction."""

    @patch("src.gmail_client.build")
    def test_get_pdf_attachments_extracts_pdf(self, mock_build, gmail_client):
        """Test that PDF attachments are correctly extracted."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        gmail_client._service = mock_service

        # Mock search results
        mock_service.users().messages().list.return_value.execute.return_value = {
            "messages": [{"id": "msg123"}]
        }

        # Mock message details with PDF attachment
        pdf_content = b"PDF content here"
        encoded_pdf = base64.urlsafe_b64encode(pdf_content).decode()

        mock_service.users().messages().get.return_value.execute.return_value = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Your Invoice"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Date", "value": "Mon, 15 Jan 2024 10:00:00 +0000"},
                ],
                "parts": [
                    {
                        "filename": "invoice.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att123"},
                    }
                ],
            }
        }

        # Mock attachment download
        mock_service.users().messages().attachments().get.return_value.execute.return_value = {
            "data": encoded_pdf
        }

        attachments = gmail_client.get_pdf_attachments(sender="sender@example.com")

        assert len(attachments) == 1
        assert attachments[0].filename == "invoice.pdf"
        assert attachments[0].content == pdf_content
        assert attachments[0].email_subject == "Your Invoice"

    @patch("src.gmail_client.build")
    def test_get_pdf_attachments_ignores_non_pdf(self, mock_build, gmail_client):
        """Test that non-PDF attachments are ignored."""
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        gmail_client._service = mock_service

        mock_service.users().messages().list.return_value.execute.return_value = {
            "messages": [{"id": "msg123"}]
        }

        mock_service.users().messages().get.return_value.execute.return_value = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Date", "value": "Mon, 15 Jan 2024 10:00:00 +0000"},
                ],
                "parts": [
                    {
                        "filename": "image.png",
                        "mimeType": "image/png",
                        "body": {"attachmentId": "att123"},
                    }
                ],
            }
        }

        attachments = gmail_client.get_pdf_attachments(sender="sender@example.com")

        assert len(attachments) == 0


class TestEmailAttachmentModel:
    """Tests for the EmailAttachment Pydantic model."""

    def test_email_attachment_creation(self):
        """Test creating an EmailAttachment instance."""
        attachment = EmailAttachment(
            filename="test.pdf",
            content=b"PDF content",
            email_subject="Test Subject",
            email_date=datetime(2024, 1, 15),
            sender="test@example.com",
        )

        assert attachment.filename == "test.pdf"
        assert attachment.content == b"PDF content"
        assert attachment.email_subject == "Test Subject"
        assert attachment.sender == "test@example.com"
