"""PDF Extractor Module

Extracts invoice amounts from utility PDFs using the Anthropic Claude API.
Handles City of Johannesburg (refuse) and Body Corporate (water, electricity) invoices.
"""

import base64
import json
import os
from decimal import Decimal
from enum import Enum
import re
from typing import Optional

import anthropic
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Filename Patterns for Invoice PDFs
# -----------------------------------------------------------------------------
# Body Corporate: Invoice-INV followed by digitd (e.g., "Invoice-INV12345.pdf")
BODY_CORPORATE_FILENAME_PATTERN = re.compile(r"^Invoice-INV\d+\.pdf$", re.IGNORECASE)

# City of Johannesburg: COJ_YYYY-MM-DD_accountnumber.pdf (e.g., "COJ_2024-01-15_123456789.pdf")
CITY_OF_JOBURG_FILENAME_PATTERN = re.compile(
    r"^COJ_\d{4}-\d{2}-\d{2}_\d+\.pdf$", re.IGNORECASE
)


class InvoiceType(str, Enum):
    """Types of utility invoices processed by the system."""

    CITY_OF_JOBURG = "city_of_joburg"
    BODY_CORPORATE = "body_corporate"


class CityOfJoburgExtraction(BaseModel):
    """Extracted data from a City of Johannesburg invoice."""

    refuse_total: Decimal = Field(
        description="Total refuse/waste collection amount in ZAR"
    )
    invoice_date: Optional[str] = Field(
        default=None, description="Invoice date if found"
    )
    account_number: Optional[str] = Field(
        default=None, description="Account number if found"
    )


class BodyCorporateExtraction(BaseModel):
    """Extracted data from a Body Corporate invoice."""

    water_total: Decimal = Field(description="Total water charges in ZAR")
    electricity_total: Decimal = Field(description="Total electricity charges in ZAR")
    invoice_date: Optional[str] = Field(
        default=None, description="Invoice date if found"
    )
    unit_number: Optional[str] = Field(default=None, description="Unit number if found")


class ExtractionResult(BaseModel):
    """Result of a PDF extraction operation."""

    success: bool
    invoice_type: InvoiceType
    data: Optional[CityOfJoburgExtraction | BodyCorporateExtraction] = None
    error_message: Optional[str] = None
    raw_response: Optional[str] = None


def filter_attachments_by_invoice_type(
    attachments: list, invoice_type: InvoiceType
) -> list:
    """Filter attachments to only those matching the expected filename pattern.

    Args:
        attachments: List of attachment objects with a `filename` attribute
        invoice_type: Type of invoice to filter for

    Returns:
        List of attachments matching the filename pattern for the invoice type.
        For CITY_OF_JOBURG, return all attachments (no pattern defined yet).
        For BODY_CORPORATE, returns only Invoice-INVxxxxx.pdf files.
    """
    if invoice_type == InvoiceType.BODY_CORPORATE:
        return [
            att
            for att in attachments
            if BODY_CORPORATE_FILENAME_PATTERN.match(att.filename)
        ]
    else:
        return [
            att
            for att in attachments
            if CITY_OF_JOBURG_FILENAME_PATTERN.match(att.filename)
        ]


def matches_body_corporate_pattern(filename: str) -> bool:
    """Check if a filename matches the Body Corporate invoice pattern.

    Args:
        filename: The filename to check

    Returns:
        True if filename matches Invoice-INVxxxxx.pdf pattern
    """
    return bool(BODY_CORPORATE_FILENAME_PATTERN.match(filename))


def matches_city_of_joburg_pattern(filename: str) -> bool:
    """
    Check if a filename matches the City of Johannesburg invoice pattern.

    Args:
        filename: The filename to check

    Returns:
        True if filename matches COJ_YYYY-MM-DD_accountnumber.pdf pattern
    """
    return bool(CITY_OF_JOBURG_FILENAME_PATTERN.match(filename))


class PDFExtractor:
    """Extracts structured data from utility invoice PDFs using Claude API.

    Uses Claude's vision capabilities to read PDF content and extract
    specific monetary amounts based on the invoice type.
    """

    MODEL = "claude-sonnet-4-20250514"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the PDF extractor.

        Args:
            api_key: Anthropic API key. If not provided, reads from
                ANTHROPIC_API_KEY environment variable.
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY environment "
                "variable or pass api_key parameter."
            )
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def _encode_pdf(self, pdf_content: bytes) -> str:
        """Encode PDF bytes to base64 string."""
        return base64.standard_b64encode(pdf_content).decode("utf-8")

    def _build_extraction_prompt(self, invoice_type: InvoiceType) -> str:
        """Build the extraction prompt based on invoice type."""
        if invoice_type == InvoiceType.CITY_OF_JOBURG:
            return """Analyze this City of Johannesburg municipal invoice PDF.
            
            Extract the following information and respond with ONLY a JSON object (no markdown, no explanation):
            
            {
                "refuse_total": <number - the total refuse/waste collection amount in ZAR>,
                "invoice_date": <string or null - the invoice date if visible>,
                "account_number": <string or null - the account number if visible>
            }
            
            Important:
            - Look for "Refuse" or "Refuse Residential" line items
            - The refuse_total should be the total amount for refuse services only
            - Use numeric values without currency symbols (e.g., 245.50 not R245.50)
            - If you cannot find a value, use null"""
        else:  # BODY CORPORATE
            return """Analyze this Body Corporate/Homeowners Association invoice PDF.
            
            Extract the following information and respond with ONLY a JSON object (no markdown, no explanation):
            
            {
                "water_total": <number - the total water charges in ZAR>,
                "electricity_total": <number - the total electricity charges in ZAR>,
                "invoice_date": <string or null - the invoice date if visible>,
                "unit_number": <string or null - the unit/apartment number if visible>
            }
            
            Important:
            - Look for "Water Recovered" or "Water" line items for water_total
            - Look for "Electricity Recovered" or "Electricity" line items for electricity_total
            - Use numeric values without currency symbols (e.g., 850.00 not R850.00)
            - If you cannot find a value, use null."""

    def extract_from_pdf(
        self, pdf_content: bytes, invoice_type: InvoiceType
    ) -> ExtractionResult:
        """Extract invoice data from a PDF.

        Args:
            pdf_content: Raw PDF file contents as bytes
            invoice_type: Type of invoice to determine extraction fields

        Returns:
            ExtractionResult with extracted data or error information
        """
        try:
            pdf_base64 = self._encode_pdf(pdf_content)
            prompt = self._build_extraction_prompt(invoice_type)

            message = self.client.messages.create(
                model=self.MODEL,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": pdf_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )

            raw_response = message.content[0].text
            return self._parse_response(raw_response, invoice_type)
        except anthropic.APIError as e:
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Anthropic API error: {e}",
            )
        except Exception as e:
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Extraction failed: {e}",
            )

    def _parse_response(
        self, raw_response: str, invoice_type: InvoiceType
    ) -> ExtractionResult:
        """Parse Claude's JSON response into structured data."""
        try:
            cleaned = raw_response.strip()
            if cleaned.startswith("```"):
                # Remove markdown code block wrapper
                lines = cleaned.split("\n")
                # Remove first line (```json) and last line (````)
                cleaned = "\n".join(lines[1:-1])

            data = json.loads(cleaned)

            if invoice_type == InvoiceType.CITY_OF_JOBURG:
                extraction = CityOfJoburgExtraction(
                    refuse_total=Decimal(str(data["refuse_total"])),
                    invoice_date=data.get("invoice_date"),
                    account_number=data.get("account_number"),
                )
            else:
                extraction = BodyCorporateExtraction(
                    water_total=Decimal(str(data["water_total"])),
                    electricity_total=Decimal(str(data["electricity_total"])),
                    invoice_date=data.get("invoice_date"),
                    unit_number=data.get("unit_number"),
                )
            return ExtractionResult(
                success=True,
                invoice_type=invoice_type,
                data=extraction,
                raw_response=raw_response,
            )
        except json.JSONDecodeError as e:
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Failed to parse JSON response: {e}",
                raw_response=raw_response,
            )
        except KeyError as e:
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Missing required field in response: {e}",
                raw_response=raw_response,
            )
        except (ValueError, TypeError) as e:
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Invalid data format: {e}",
                raw_response=raw_response,
            )


def extract_city_of_joburg(
    pdf_content: bytes, api_key: Optional[str] = None
) -> ExtractionResult:
    """COnvenience function to extract data from a City of Johannesburg invoice.

    Args:
        pdf_content: RawPDF file content as bytes
        api_key: Optional Anthropic API key (defaults to env var)

    Returns:
        ExtractionResult with CityOfJoburgExtraction data
    """
    extractor = PDFExtractor(api_key=api_key)
    return extractor.extract_from_pdf(pdf_content, InvoiceType.CITY_OF_JOBURG)


def extract_body_corporate(
    pdf_content: bytes, api_key: Optional[str] = None
) -> ExtractionResult:
    """Convenience function to extract data from a Body Corporate invoice.

    Args:
        pdf_content: Raw PDF file content as bytes
        api_key: Optional Anthropic API key (defaults to env var)

    Returns:
        ExtractionResult with BodyCorporateExtraction data
    """
    extractor = PDFExtractor(api_key=api_key)
    return extractor.extract_from_pdf(pdf_content, InvoiceType.BODY_CORPORATE)
