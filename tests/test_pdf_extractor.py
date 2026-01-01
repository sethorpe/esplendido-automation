"""Tests for the PDF Extractor module."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.pdf_extractor import (
    BODY_CORPORATE_FILENAME_PATTERN,
    CITY_OF_JOBURG_FILENAME_PATTERN,
    BodyCorporateExtraction,
    CityOfJoburgExtraction,
    ExtractionResult,
    InvoiceType,
    PDFExtractor,
    extract_body_corporate,
    extract_city_of_joburg,
    filter_attachments_by_invoice_type,
    matches_body_corporate_pattern,
    matches_city_of_joburg_pattern,
)


class TestFilenamePatterns:
    """Tests for filename pattern matching."""

    # Body Corporate pattern tests
    def test_body_corporate_pattern_matches_valid_filename(self):
        """Test pattern matches Invoice-INVxxxxx.pdf format."""
        assert matches_body_corporate_pattern("Invoice-INV12345.pdf")
        assert matches_body_corporate_pattern("Invoice-INV00001.pdf")
        assert matches_body_corporate_pattern("Invoice-INV999999.pdf")

    def test_body_corporate_pattern_case_insensitive(self):
        """Test pattern matching is case insensitive."""
        assert matches_body_corporate_pattern("invoice-inv12345.pdf")
        assert matches_body_corporate_pattern("INVOICE-INV12345.PDF")
        assert matches_body_corporate_pattern("Invoice-INV12345.PDF")

    def test_body_corporate_pattern_rejects_invalid_filenames(self):
        """Test pattern rejects non-matching filenames."""
        assert not matches_body_corporate_pattern("Statement.pdf")
        assert not matches_body_corporate_pattern("Terms-and-Conditions.pdf")
        assert not matches_body_corporate_pattern("Invoice-12345.pdf")  # Missing INV
        assert not matches_body_corporate_pattern("INV12345.pdf")  # Missing Invoice-
        assert not matches_body_corporate_pattern("Invoice-INV.pdf")  # Missing digits
        assert not matches_body_corporate_pattern(
            "Invoice-INVabc.pdf"
        )  # Letters not digits

    # City of Johannesburg pattern tests
    def test_city_of_joburg_pattern_matches_valid_filename(self):
        """Test pattern matches COJ_YYYY-MM-DD_accountnumber.pdf format."""
        assert matches_city_of_joburg_pattern("COJ_2024-01-15_555745535.pdf")
        assert matches_city_of_joburg_pattern("COJ_2025-12-31_123456789.pdf")
        assert matches_city_of_joburg_pattern(
            "COJ_2024-06-01_1.pdf"
        )  # Single digit account

    def test_city_of_joburg_pattern_case_insensitive(self):
        """Test pattern matching is case insensitive."""
        assert matches_city_of_joburg_pattern("coj_2024-01-15_555745535.pdf")
        assert matches_city_of_joburg_pattern("COJ_2024-01-15_555745535.PDF")

    def test_city_of_joburg_pattern_rejects_invalid_filenames(self):
        """Test pattern rejects non-matching filenames."""
        assert not matches_city_of_joburg_pattern("Statement.pdf")
        assert not matches_city_of_joburg_pattern(
            "COJ_2024-01-15.pdf"
        )  # Missing account
        assert not matches_city_of_joburg_pattern(
            "COJ_20240115_555745535.pdf"
        )  # Wrong date format
        assert not matches_city_of_joburg_pattern(
            "COJ_2024-1-15_555745535.pdf"
        )  # Single digit month
        assert not matches_city_of_joburg_pattern(
            "2024-01-15_555745535.pdf"
        )  # Missing COJ_
        assert not matches_city_of_joburg_pattern(
            "COJ_2024-01-15_abc.pdf"
        )  # Letters in account


class TestFilterAttachments:
    """Tests for attachment filtering by invoice type."""

    def test_filter_body_corporate_returns_matching_only(self):
        """Test filtering returns only matching Body Corporate attachments."""
        # Create mock attachments
        att1 = MagicMock()
        att1.filename = "Invoice-INV12345.pdf"
        att2 = MagicMock()
        att2.filename = "Terms-and-Conditions.pdf"
        att3 = MagicMock()
        att3.filename = "Invoice-INV67890.pdf"

        result = filter_attachments_by_invoice_type(
            [att1, att2, att3], InvoiceType.BODY_CORPORATE
        )

        assert len(result) == 2
        assert att1 in result
        assert att3 in result
        assert att2 not in result

    def test_filter_body_corporate_empty_when_no_matches(self):
        """Test filtering returns empty list when no matches."""
        att1 = MagicMock()
        att1.filename = "Statement.pdf"
        att2 = MagicMock()
        att2.filename = "Terms.pdf"

        result = filter_attachments_by_invoice_type(
            [att1, att2], InvoiceType.BODY_CORPORATE
        )

        assert len(result) == 0

    def test_filter_city_of_joburg_returns_matching_only(self):
        """Test filtering returns only matching City of Joburg attachments."""
        att1 = MagicMock()
        att1.filename = "COJ_2024-01-15_555745535.pdf"
        att2 = MagicMock()
        att2.filename = "other-document.pdf"

        result = filter_attachments_by_invoice_type(
            [att1, att2], InvoiceType.CITY_OF_JOBURG
        )

        assert len(result) == 1
        assert att1 in result
        assert att2 not in result

    def test_filter_city_of_joburg_empty_when_no_matches(self):
        """Test City of Joburg filtering returns empty when no matches."""
        att1 = MagicMock()
        att1.filename = "random.pdf"

        result = filter_attachments_by_invoice_type([att1], InvoiceType.CITY_OF_JOBURG)

        assert len(result) == 0

    def test_filter_empty_list(self):
        """Test filtering handles empty attachment list."""
        result = filter_attachments_by_invoice_type([], InvoiceType.BODY_CORPORATE)
        assert result == []

        result = filter_attachments_by_invoice_type([], InvoiceType.CITY_OF_JOBURG)
        assert result == []


class TestPDFExtractorInit:
    """Tests for PDFExtractor initialization."""

    def test_init_with_api_key(self):
        """Test initialization with explicit API key."""
        extractor = PDFExtractor(api_key="test-api-key")
        assert extractor.api_key == "test-api-key"

    def test_init_with_env_var(self, monkeypatch):
        """Test initialization reads from environment variable."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-api-key")
        extractor = PDFExtractor()
        assert extractor.api_key == "env-api-key"

    def test_init_without_api_key_raises(self, monkeypatch):
        """Test initialization fails without API key."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="Anthropic API key required"):
            PDFExtractor()


class TestPDFEncoding:
    """Tests for PDF encoding functionality."""

    def test_encode_pdf(self):
        """Test PDF content is properly base64 encoded."""
        extractor = PDFExtractor(api_key="test-key")
        pdf_bytes = b"%PDF-1.4 test content"
        encoded = extractor._encode_pdf(pdf_bytes)

        # Verify it's a valid base64 string
        import base64

        decoded = base64.standard_b64decode(encoded)
        assert decoded == pdf_bytes


class TestExtractionPrompts:
    """Tests for extraction prompt generation."""

    def test_city_of_joburg_prompt(self):
        """Test City of Joburg prompt contains required fields."""
        extractor = PDFExtractor(api_key="test-key")
        prompt = extractor._build_extraction_prompt(InvoiceType.CITY_OF_JOBURG)

        assert "refuse_total" in prompt
        assert "invoice_date" in prompt
        assert "account_number" in prompt
        assert "City of Johannesburg" in prompt

    def test_body_corporate_prompt(self):
        """Test Body Corporate prompt contains required fields."""
        extractor = PDFExtractor(api_key="test-key")
        prompt = extractor._build_extraction_prompt(InvoiceType.BODY_CORPORATE)

        assert "water_total" in prompt
        assert "electricity_total" in prompt
        assert "invoice_date" in prompt
        assert "unit_number" in prompt
        assert "Body Corporate" in prompt


class TestResponseParsing:
    """Tests for parsing Claude API responses."""

    def test_parse_city_of_joburg_response(self):
        """Test parsing a valid City of Joburg response."""
        extractor = PDFExtractor(api_key="test-key")
        raw_response = """{
            "refuse_total": 245.50,
            "invoice_date": "2024-01-15",
            "account_number": "ACC123456"
        }"""

        result = extractor._parse_response(raw_response, InvoiceType.CITY_OF_JOBURG)

        assert result.success is True
        assert result.invoice_type == InvoiceType.CITY_OF_JOBURG
        assert isinstance(result.data, CityOfJoburgExtraction)
        assert result.data.refuse_total == Decimal("245.50")
        assert result.data.invoice_date == "2024-01-15"
        assert result.data.account_number == "ACC123456"

    def test_parse_body_corporate_response(self):
        """Test parsing a valid Body Corporate response."""
        extractor = PDFExtractor(api_key="test-key")
        raw_response = """{
            "water_total": 150.00,
            "electricity_total": 850.75,
            "invoice_date": "2024-01-20",
            "unit_number": "Unit 42"
        }"""

        result = extractor._parse_response(raw_response, InvoiceType.BODY_CORPORATE)

        assert result.success is True
        assert result.invoice_type == InvoiceType.BODY_CORPORATE
        assert isinstance(result.data, BodyCorporateExtraction)
        assert result.data.water_total == Decimal("150.00")
        assert result.data.electricity_total == Decimal("850.75")
        assert result.data.invoice_date == "2024-01-20"
        assert result.data.unit_number == "Unit 42"

    def test_parse_response_with_markdown_wrapper(self):
        """Test parsing response that has markdown code blocks."""
        extractor = PDFExtractor(api_key="test-key")
        raw_response = """```json
{
    "refuse_total": 300.00,
    "invoice_date": null,
    "account_number": null
}
```"""

        result = extractor._parse_response(raw_response, InvoiceType.CITY_OF_JOBURG)

        assert result.success is True
        assert result.data.refuse_total == Decimal("300.00")

    def test_parse_response_with_null_optional_fields(self):
        """Test parsing response with null optional fields."""
        extractor = PDFExtractor(api_key="test-key")
        raw_response = """{
            "refuse_total": 200.00,
            "invoice_date": null,
            "account_number": null
        }"""

        result = extractor._parse_response(raw_response, InvoiceType.CITY_OF_JOBURG)

        assert result.success is True
        assert result.data.refuse_total == Decimal("200.00")
        assert result.data.invoice_date is None
        assert result.data.account_number is None

    def test_parse_invalid_json_response(self):
        """Test parsing invalid JSON returns error result."""
        extractor = PDFExtractor(api_key="test-key")
        raw_response = "This is not valid JSON"

        result = extractor._parse_response(raw_response, InvoiceType.CITY_OF_JOBURG)

        assert result.success is False
        assert "Failed to parse JSON" in result.error_message
        assert result.raw_response == raw_response

    def test_parse_missing_required_field(self):
        """Test parsing response missing required field."""
        extractor = PDFExtractor(api_key="test-key")
        raw_response = """{
            "invoice_date": "2024-01-15"
        }"""

        result = extractor._parse_response(raw_response, InvoiceType.CITY_OF_JOBURG)

        assert result.success is False
        assert "Missing required field" in result.error_message


class TestExtractFromPDF:
    """Tests for the main extraction method."""

    @patch("src.pdf_extractor.anthropic.Anthropic")
    def test_extract_city_of_joburg_success(self, mock_anthropic_class):
        """Test successful extraction from City of Joburg PDF."""
        # Setup mock
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(
                text='{"refuse_total": 245.50, "invoice_date": "2024-01-15", "account_number": "ACC123"}'
            )
        ]
        mock_client.messages.create.return_value = mock_message

        extractor = PDFExtractor(api_key="test-key")
        result = extractor.extract_from_pdf(b"%PDF-test", InvoiceType.CITY_OF_JOBURG)

        assert result.success is True
        assert result.data.refuse_total == Decimal("245.50")

        # Verify API was called with correct parameters
        call_args = mock_client.messages.create.call_args
        assert call_args.kwargs["model"] == PDFExtractor.MODEL
        assert call_args.kwargs["messages"][0]["content"][0]["type"] == "document"

    @patch("src.pdf_extractor.anthropic.Anthropic")
    def test_extract_body_corporate_success(self, mock_anthropic_class):
        """Test successful extraction from Body Corporate PDF."""
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(
                text='{"water_total": 150.00, "electricity_total": 800.00, "invoice_date": null, "unit_number": "42"}'
            )
        ]
        mock_client.messages.create.return_value = mock_message

        extractor = PDFExtractor(api_key="test-key")
        result = extractor.extract_from_pdf(b"%PDF-test", InvoiceType.BODY_CORPORATE)

        assert result.success is True
        assert result.data.water_total == Decimal("150.00")
        assert result.data.electricity_total == Decimal("800.00")

    @patch("src.pdf_extractor.anthropic.Anthropic")
    def test_extract_handles_api_error(self, mock_anthropic_class):
        """Test extraction handles API errors gracefully."""
        import anthropic

        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic.APIError(
            message="API Error",
            request=MagicMock(),
            body=None,
        )

        extractor = PDFExtractor(api_key="test-key")
        result = extractor.extract_from_pdf(b"%PDF-test", InvoiceType.CITY_OF_JOBURG)

        assert result.success is False
        assert "Anthropic API error" in result.error_message


class TestConvenienceFunctions:
    """Tests for convenience extraction functions."""

    @patch("src.pdf_extractor.PDFExtractor")
    def test_extract_city_of_joburg_function(self, mock_extractor_class):
        """Test the convenience function for City of Joburg extraction."""
        mock_instance = MagicMock()
        mock_extractor_class.return_value = mock_instance
        mock_instance.extract_from_pdf.return_value = ExtractionResult(
            success=True,
            invoice_type=InvoiceType.CITY_OF_JOBURG,
            data=CityOfJoburgExtraction(refuse_total=Decimal("100.00")),
        )

        result = extract_city_of_joburg(b"%PDF-test", api_key="test-key")

        assert result.success is True
        mock_instance.extract_from_pdf.assert_called_once_with(
            b"%PDF-test", InvoiceType.CITY_OF_JOBURG
        )

    @patch("src.pdf_extractor.PDFExtractor")
    def test_extract_body_corporate_function(self, mock_extractor_class):
        """Test the convenience function for Body Corporate extraction."""
        mock_instance = MagicMock()
        mock_extractor_class.return_value = mock_instance
        mock_instance.extract_from_pdf.return_value = ExtractionResult(
            success=True,
            invoice_type=InvoiceType.BODY_CORPORATE,
            data=BodyCorporateExtraction(
                water_total=Decimal("100.00"),
                electricity_total=Decimal("200.00"),
            ),
        )

        result = extract_body_corporate(b"%PDF-test", api_key="test-key")

        assert result.success is True
        mock_instance.extract_from_pdf.assert_called_once_with(
            b"%PDF-test", InvoiceType.BODY_CORPORATE
        )


class TestPydanticModels:
    """Tests for Pydantic data models."""

    def test_city_of_joburg_extraction_model(self):
        """Test CityOfJoburgExtraction model validation."""
        extraction = CityOfJoburgExtraction(
            refuse_total=Decimal("245.50"),
            invoice_date="2024-01-15",
            account_number="ACC123",
        )

        assert extraction.refuse_total == Decimal("245.50")
        assert extraction.invoice_date == "2024-01-15"

    def test_body_corporate_extraction_model(self):
        """Test BodyCorporateExtraction model validation."""
        extraction = BodyCorporateExtraction(
            water_total=Decimal("150.00"),
            electricity_total=Decimal("800.00"),
            invoice_date="2024-01-20",
            unit_number="Unit 42",
        )

        assert extraction.water_total == Decimal("150.00")
        assert extraction.electricity_total == Decimal("800.00")

    def test_extraction_result_model(self):
        """Test ExtractionResult model."""
        result = ExtractionResult(
            success=True,
            invoice_type=InvoiceType.CITY_OF_JOBURG,
            data=CityOfJoburgExtraction(refuse_total=Decimal("100.00")),
        )

        assert result.success is True
        assert result.invoice_type == InvoiceType.CITY_OF_JOBURG
        assert result.error_message is None

    def test_extraction_result_error_model(self):
        """Test ExtractionResult model for error case."""
        result = ExtractionResult(
            success=False,
            invoice_type=InvoiceType.BODY_CORPORATE,
            error_message="Something went wrong",
            raw_response="invalid response",
        )

        assert result.success is False
        assert result.data is None
        assert result.error_message == "Something went wrong"
