"""Tests for the Gmail client module (IMAP implementation)."""

import imaplib
from datetime import datetime
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from src.gmail_client import EmailAttachment, GmailClient, fetch_utility_pdfs


class TestGmailClientInit:
    """Tests for GmailClient initialization."""

    def test_init_sets_credentials(self):
        """Test that init properly sets email and app password."""
        client = GmailClient(
            email_address="test@gmail.com",
            app_password="abcdefghijklmnop",
        )

        assert client.email_address == "test@gmail.com"
        assert client.app_password == "abcdefghijklmnop"
        assert client._connection is None

    def test_connection_raises_without_connect(self):
        """Test that accessing connection without connecting raises error."""
        client = GmailClient("test@gmail.com", "password")

        with pytest.raises(RuntimeError, match="Not connected"):
            _ = client.connection


class TestGmailClientConnection:
    """Tests for IMAP connection management."""

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_connect_authenticates(self, mock_imap_class):
        """Test that connect establishes IMAP connection."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        client = GmailClient("test@gmail.com", "testpassword")
        client.connect()

        mock_imap_class.assert_called_once_with("imap.gmail.com", 993)
        mock_imap.login.assert_called_once_with("test@gmail.com", "testpassword")
        assert client._connection == mock_imap

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_disconnect_logs_out(self, mock_imap_class):
        """Test that disconnect properly logs out."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        client = GmailClient("test@gmail.com", "testpassword")
        client.connect()
        client.disconnect()

        mock_imap.logout.assert_called_once()
        assert client._connection is None

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_context_manager(self, mock_imap_class):
        """Test context manager connects and disconnects."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        with GmailClient("test@gmail.com", "testpassword") as client:
            assert client._connection is not None

        mock_imap.logout.assert_called_once()

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_connect_failure_raises(self, mock_imap_class):
        """Test that authentication failure raises error."""
        mock_imap = MagicMock()
        mock_imap.login.side_effect = imaplib.IMAP4.error("LOGIN failed")
        mock_imap_class.return_value = mock_imap

        client = GmailClient("test@gmail.com", "wrongpassword")

        with pytest.raises(imaplib.IMAP4.error):
            client.connect()


class TestSearchCriteria:
    """Tests for IMAP search criteria building."""

    def test_build_search_sender_only(self):
        """Test search criteria with sender only."""
        client = GmailClient("test@gmail.com", "password")
        criteria = client._build_search_criteria(sender="sender@example.com")

        assert criteria == 'FROM "sender@example.com"'

    def test_build_search_with_subject(self):
        """Test search criteria with subject filter."""
        client = GmailClient("test@gmail.com", "password")
        criteria = client._build_search_criteria(
            sender="sender@example.com",
            subject_contains="Invoice",
        )

        assert 'FROM "sender@example.com"' in criteria
        assert 'SUBJECT "Invoice"' in criteria

    def test_build_search_with_date(self):
        """Test search criteria with date filter."""
        client = GmailClient("test@gmail.com", "password")
        criteria = client._build_search_criteria(
            sender="sender@example.com",
            after_date=datetime(2024, 12, 15),
        )

        assert 'FROM "sender@example.com"' in criteria
        assert "SINCE 15-Dec-2024" in criteria

    def test_build_search_all_filters(self):
        """Test search criteria with all filters."""
        client = GmailClient("test@gmail.com", "password")
        criteria = client._build_search_criteria(
            sender="sender@example.com",
            subject_contains="Statement",
            after_date=datetime(2024, 1, 1),
        )

        assert 'FROM "sender@example.com"' in criteria
        assert 'SUBJECT "Statement"' in criteria
        assert "SINCE 01-Jan-2024" in criteria


class TestHeaderDecoding:
    """Tests for email header decoding."""

    def test_decode_plain_header(self):
        """Test decoding a plain ASCII header."""
        client = GmailClient("test@gmail.com", "password")
        result = client._decode_header_value("Simple Subject")

        assert result == "Simple Subject"

    def test_decode_none_header(self):
        """Test decoding None returns empty string."""
        client = GmailClient("test@gmail.com", "password")
        result = client._decode_header_value(None)

        assert result == ""

    def test_decode_empty_header(self):
        """Test decoding empty string."""
        client = GmailClient("test@gmail.com", "password")
        result = client._decode_header_value("")

        assert result == ""


class TestEmailSearch:
    """Tests for email search functionality."""

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_search_emails_returns_ids(self, mock_imap_class):
        """Test that search_emails returns message IDs."""
        mock_imap = MagicMock()
        mock_imap.search.return_value = ("OK", [b"1 2 3"])
        mock_imap_class.return_value = mock_imap

        client = GmailClient("test@gmail.com", "password")
        client.connect()
        result = client.search_emails(sender="sender@example.com")

        mock_imap.select.assert_called_with("INBOX", readonly=True)
        assert result == [b"3", b"2", b"1"]  # Most recent first

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_search_emails_empty_result(self, mock_imap_class):
        """Test search with no matching emails."""
        mock_imap = MagicMock()
        mock_imap.search.return_value = ("OK", [b""])
        mock_imap_class.return_value = mock_imap

        client = GmailClient("test@gmail.com", "password")
        client.connect()
        result = client.search_emails(sender="sender@example.com")

        assert result == []

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_search_emails_respects_max_results(self, mock_imap_class):
        """Test that max_results limits returned IDs."""
        mock_imap = MagicMock()
        mock_imap.search.return_value = ("OK", [b"1 2 3 4 5 6 7 8 9 10"])
        mock_imap_class.return_value = mock_imap

        client = GmailClient("test@gmail.com", "password")
        client.connect()
        result = client.search_emails(sender="sender@example.com", max_results=3)

        assert len(result) == 3
        assert result == [b"10", b"9", b"8"]  # Most recent 3


class TestPdfExtraction:
    """Tests for PDF attachment extraction."""

    def _create_email_with_pdf(self, filename: str, content: bytes) -> EmailMessage:
        """Helper to create an email with a PDF attachment."""
        msg = EmailMessage()
        msg["Subject"] = "Test Email"
        msg["From"] = "sender@example.com"
        msg["Date"] = "Mon, 15 Jan 2024 10:00:00 +0000"
        msg.set_content("Email body")
        msg.add_attachment(
            content,
            maintype="application",
            subtype="pdf",
            filename=filename,
        )
        return msg

    def test_extract_pdf_attachment(self):
        """Test extracting a PDF attachment from email."""
        client = GmailClient("test@gmail.com", "password")
        msg = self._create_email_with_pdf("invoice.pdf", b"%PDF-1.4 content")

        attachments = client._extract_attachments(
            msg,
            email_subject="Test Email",
            email_date=datetime(2024, 1, 15),
            sender="sender@example.com",
        )

        assert len(attachments) == 1
        assert attachments[0].filename == "invoice.pdf"
        assert attachments[0].content == b"%PDF-1.4 content"
        assert attachments[0].email_subject == "Test Email"

    def test_extract_multiple_pdfs(self):
        """Test extracting multiple PDF attachments."""
        msg = EmailMessage()
        msg["Subject"] = "Multiple PDFs"
        msg["From"] = "sender@example.com"
        msg["Date"] = "Mon, 15 Jan 2024 10:00:00 +0000"
        msg.set_content("Email body")
        msg.add_attachment(
            b"PDF1", maintype="application", subtype="pdf", filename="first.pdf"
        )
        msg.add_attachment(
            b"PDF2", maintype="application", subtype="pdf", filename="second.pdf"
        )

        client = GmailClient("test@gmail.com", "password")
        attachments = client._extract_attachments(
            msg,
            email_subject="Multiple PDFs",
            email_date=datetime(2024, 1, 15),
            sender="sender@example.com",
        )

        assert len(attachments) == 2
        filenames = [a.filename for a in attachments]
        assert "first.pdf" in filenames
        assert "second.pdf" in filenames

    def test_extract_ignores_non_pdf(self):
        """Test that non-PDF attachments are ignored."""
        msg = EmailMessage()
        msg["Subject"] = "Mixed Attachments"
        msg["From"] = "sender@example.com"
        msg["Date"] = "Mon, 15 Jan 2024 10:00:00 +0000"
        msg.set_content("Email body")
        msg.add_attachment(
            b"PDF", maintype="application", subtype="pdf", filename="doc.pdf"
        )
        msg.add_attachment(
            b"IMAGE", maintype="image", subtype="png", filename="image.png"
        )

        client = GmailClient("test@gmail.com", "password")
        attachments = client._extract_attachments(
            msg,
            email_subject="Mixed Attachments",
            email_date=datetime(2024, 1, 15),
            sender="sender@example.com",
        )

        assert len(attachments) == 1
        assert attachments[0].filename == "doc.pdf"


class TestGetPdfAttachments:
    """Tests for the main get_pdf_attachments method."""

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_get_pdf_attachments_integration(self, mock_imap_class):
        """Test full flow of getting PDF attachments."""
        # Create a test email with PDF
        msg = EmailMessage()
        msg["Subject"] = "Your Invoice"
        msg["From"] = "billing@example.com"
        msg["Date"] = "Mon, 15 Jan 2024 10:00:00 +0000"
        msg.set_content("Please find attached")
        msg.add_attachment(
            b"%PDF-1.4 invoice data",
            maintype="application",
            subtype="pdf",
            filename="Invoice-INV12345.pdf",
        )

        mock_imap = MagicMock()
        mock_imap.search.return_value = ("OK", [b"1"])
        mock_imap.fetch.return_value = ("OK", [(b"1", msg.as_bytes())])
        mock_imap_class.return_value = mock_imap

        client = GmailClient("test@gmail.com", "password")
        client.connect()
        attachments = client.get_pdf_attachments(sender="billing@example.com")

        assert len(attachments) == 1
        assert attachments[0].filename == "Invoice-INV12345.pdf"
        assert attachments[0].content == b"%PDF-1.4 invoice data"
        assert attachments[0].email_subject == "Your Invoice"


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


class TestFetchUtilityPdfs:
    """Tests for the fetch_utility_pdfs convenience function."""

    @patch("src.gmail_client.imaplib.IMAP4_SSL")
    def test_fetch_utility_pdfs(self, mock_imap_class):
        """Test fetching PDFs from multiple senders."""
        mock_imap = MagicMock()
        mock_imap.search.return_value = ("OK", [b""])
        mock_imap_class.return_value = mock_imap

        result = fetch_utility_pdfs(
            email_address="test@gmail.com",
            app_password="password",
            city_sender="city@joburg.org.za",
            body_corporate_sender="bc@example.com",
        )

        assert "city" in result
        assert "body_corporate" in result
        assert isinstance(result["city"], list)
        assert isinstance(result["body_corporate"], list)
