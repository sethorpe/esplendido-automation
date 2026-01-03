"""PDF Extractor Module

Extracts invoice amounts from utility PDFs using either:
- Local parsing (privacy-first, no external API calls)
- Claude API (LLM-based fallback for complex cases)

Handles City of Johannesburg (refuse) and Body Corporate (water, electricity) invoices.
"""

import base64
import io
import json
import os
from decimal import Decimal
from enum import Enum
import re
from typing import Optional

import anthropic
import pdfplumber
from pydantic import BaseModel, Field

from src.logger_config import setup_logger

log = setup_logger(__name__)


# -----------------------------------------------------------------------------
# Filename Patterns for Invoice PDFs
# -----------------------------------------------------------------------------
# Body Corporate: Invoice-INV followed by digits (e.g., "Invoice-INV12345.pdf")
BODY_CORPORATE_FILENAME_PATTERN = re.compile(r"^Invoice-INV\d+\.pdf$", re.IGNORECASE)

# City of Johannesburg: COJ_YYYY-MM-DD_accountnumber.pdf (e.g., "COJ_2024-01-15_123456789.pdf")
CITY_OF_JOBURG_FILENAME_PATTERN = re.compile(
    r"^COJ_\d{4}-\d{2}-\d{2}_\d+\.pdf$", re.IGNORECASE
)


# -----------------------------------------------------------------------------
# Regex Patterns for Local Extraction
# -----------------------------------------------------------------------------
# Match currency amounts like "R 245.50" or "R245.50" or "245.50"
AMOUNT_PATTERN = re.compile(r"R?\s*([\d,]+\.?\d*)")

# City of Joburg patterns
REFUSE_PATTERN = re.compile(r"refuse|waste", re.IGNORECASE)

# Body Corporate patterns
WATER_PATTERN = re.compile(r"water\s*(?:recovered)?", re.IGNORECASE)
ELECTRICITY_PATTERN = re.compile(r"electricity\s*(?:recovered)?", re.IGNORECASE)


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
    """Check if a filename matches the City of Johannesburg invoice pattern.

    Args:
        filename: The filename to check

    Returns:
        True if filename matches COJ_YYYY-MM-DD_accountnumber.pdf pattern
    """
    return bool(CITY_OF_JOBURG_FILENAME_PATTERN.match(filename))


class PDFExtractor:
    """Extracts structured data from utility invoice PDFs.

    Supports two extraction modes:
    - 'local': Privacy-first local parsing using pdfplumber (no external API)
    - 'claude': LLM-based extraction using Claude API (fallback for complex cases)
    """

    MODEL = "claude-sonnet-4-20250514"

    def __init__(self, mode: str = "local", api_key: Optional[str] = None):
        """Initialize the PDF extractor.

        Args:
            mode: Extraction mode ('local' or 'claude')
            api_key: Anthropic API key (only needed for 'claude' mode)
        """
        self.mode = mode
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

        if mode == "claude":
            if not self.api_key:
                log.error("Anthropic API key not found for Claude mode")
                raise ValueError(
                    "Anthropic API key required for 'claude' mode. "
                    "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
                )
            log.debug("Initializing Anthropic client")
            self.client = anthropic.Anthropic(api_key=self.api_key)
            log.info(f"PDF Extractor initialized in Claude mode with model: {self.MODEL}")
        elif mode == "local":
            log.info("PDF Extractor initialized in local mode (privacy-first)")
        else:
            raise ValueError(f"Invalid extraction mode: {mode}. Must be 'local' or 'claude'")

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
        if self.mode == "local":
            return self._extract_local(pdf_content, invoice_type)
        else:
            return self._extract_claude(pdf_content, invoice_type)

    # -------------------------------------------------------------------------
    # Local Extraction Methods (Privacy-First)
    # -------------------------------------------------------------------------

    def _extract_local(
        self, pdf_content: bytes, invoice_type: InvoiceType
    ) -> ExtractionResult:
        """Extract using local PDF parsing - no external API calls."""
        log.info(f"Extracting data from {invoice_type.value} invoice PDF using local parser")

        try:
            if invoice_type == InvoiceType.CITY_OF_JOBURG:
                return self._extract_city_local(pdf_content)
            else:
                return self._extract_body_corp_local(pdf_content)
        except Exception as e:
            log.exception(f"Local extraction failed: {e}")
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Local extraction failed: {e}",
            )

    def _extract_city_local(self, pdf_content: bytes) -> ExtractionResult:
        """Parse City of Johannesburg PDF locally using pdfplumber.

        City PDF structure (page 2):
        - Section header: "PIKITUP" followed by "Refuse" with VAT number
        - Line item: "Refuse Residential ( Billing Period YYYY/MM )" → base amount
        - Next line: "VAT: 15.00%" → VAT amount → Total amount (what we need)

        Example:
        Refuse Residential ( Billing Period 2025/12 )                246.00
        VAT: 15.00%                                                    36.90      282.90
        """
        try:
            with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or ""

            log.debug(f"Extracted {len(text)} characters from City PDF")

            # Search for the specific pattern in City invoices
            refuse_total = None
            lines = text.split("\n")

            # Pattern: Find "Refuse Residential" line, then find "Total Amount" on VAT line
            # The structure is:
            # Line 1: "Refuse Residential ( Billing Period ... )" → base amount
            # Line 2: "VAT: 15.00%" → VAT amount → TOTAL AMOUNT (at end of line)

            for i, line in enumerate(lines):
                # Look for "Refuse Residential" line
                if re.search(r"Refuse\s+Residential", line, re.IGNORECASE):
                    log.debug(f"Found refuse residential line: {line}")

                    # Check the next line for VAT and total amount
                    if i + 1 < len(lines):
                        vat_line = lines[i + 1]
                        if "VAT" in vat_line:
                            log.debug(f"Found VAT line: {vat_line}")

                            # Extract all numbers from the VAT line
                            # Format: "VAT: 15.00%     36.90     282.90"
                            # We want the LAST number (total amount after VAT)
                            numbers = re.findall(r"(\d+\.\d{2})", vat_line)

                            if len(numbers) >= 2:
                                # Last number is the total (base + VAT)
                                total_str = numbers[-1]
                                try:
                                    refuse_total = Decimal(total_str)
                                    log.debug(f"Extracted refuse total from VAT line: R{refuse_total}")
                                    break
                                except (ValueError, TypeError):
                                    log.warning(f"Could not parse amount: {total_str}")
                                    continue

            if refuse_total is None:
                log.warning("Could not find refuse amount in City PDF")
                return ExtractionResult(
                    success=False,
                    invoice_type=InvoiceType.CITY_OF_JOBURG,
                    error_message="Could not locate refuse amount in PDF",
                    raw_response=text[:500],  # First 500 chars for debugging
                )

            extraction = CityOfJoburgExtraction(
                refuse_total=refuse_total,
                invoice_date=None,  # TODO: Extract date if needed
                account_number=None,  # TODO: Extract account number if needed
            )

            log.info(f"Successfully extracted City invoice - Refuse: R{refuse_total}")
            return ExtractionResult(
                success=True,
                invoice_type=InvoiceType.CITY_OF_JOBURG,
                data=extraction,
                raw_response=None,  # Don't store full text (privacy)
            )

        except Exception as e:
            log.error(f"Error parsing City PDF: {e}")
            return ExtractionResult(
                success=False,
                invoice_type=InvoiceType.CITY_OF_JOBURG,
                error_message=f"PDF parsing error: {e}",
            )

    def _extract_body_corp_local(self, pdf_content: bytes) -> ExtractionResult:
        """Parse Body Corporate PDF locally using pdfplumber.

        Body Corporate PDF structure (table format):
        Account | Description | Qty | Unit Price | Disc | Tax | Total
        --------|-------------|-----|------------|------|-----|------
        Water Recovered | Water (2025-11-08 to 2025-12-08)... | 1.00 | 137.26 | 0.00 | 0.00 | 137.26
        Electricity Recovered | Electricity (2025-11-08 to 2025-12-08) | 1.00 | 454.38 | 0.00 | 0.00 | 454.38

        We need to extract the "Total" column value (last number on the line).
        """
        try:
            with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or ""

            log.debug(f"Extracted {len(text)} characters from Body Corporate PDF")

            # Search for water and electricity line items
            water_total = None
            electricity_total = None
            lines = text.split("\n")

            for line in lines:
                # Look for "Water Recovered" or "Water" line
                # Format: "Water Recovered  Water (2025-11-08 to 2025-12-08) - Previous: 159, Current: 169 - Usage: 10  1.00  137.26  0.00  0.00  137.26"
                # OR: "Water Recovered Water (2025-11-08 to 2025-12-08) - Previous: 159, 1.00 137.26 0.00 0.00 137.26"
                # We want the LAST number on the line (Total column)
                if re.search(r"Water\s+(?:Recovered)?", line, re.IGNORECASE) and "water" in line.lower() and water_total is None:
                    log.debug(f"Found water line: {line}")

                    # Extract all decimal numbers from the line
                    numbers = re.findall(r"\d+\.\d{2}", line)

                    if numbers:
                        # The last number is the Total column
                        total_str = numbers[-1]
                        try:
                            water_total = Decimal(total_str)
                            log.debug(f"Extracted water total: R{water_total}")
                        except (ValueError, TypeError):
                            log.warning(f"Could not parse water amount: {total_str}")

                # Look for "Electricity Recovered" or just "Electricity" line
                # Format: "Electricity Recovered  Electricity (2025-11-08 to 2025-12-08)  1.00  454.38  0.00  0.00  454.38"
                # OR: "Electricity Electricity (2025-11-08 to 2025-12-08) 1.00 454.38 0.00 0.00 454.38"
                if re.search(r"Electricity", line, re.IGNORECASE) and electricity_total is None:
                    log.debug(f"Found electricity line: {line}")

                    # Extract all decimal numbers from the line
                    numbers = re.findall(r"\d+\.\d{2}", line)

                    if numbers:
                        # The last number is the Total column
                        total_str = numbers[-1]
                        try:
                            electricity_total = Decimal(total_str)
                            log.debug(f"Extracted electricity total: R{electricity_total}")
                        except (ValueError, TypeError):
                            log.warning(f"Could not parse electricity amount: {total_str}")

            # Validate we found both amounts
            missing = []
            if water_total is None:
                missing.append("water")
            if electricity_total is None:
                missing.append("electricity")

            if missing:
                log.warning(f"Could not find {', '.join(missing)} amount(s) in Body Corporate PDF")
                return ExtractionResult(
                    success=False,
                    invoice_type=InvoiceType.BODY_CORPORATE,
                    error_message=f"Could not locate {', '.join(missing)} amount(s) in PDF",
                    raw_response=text[:500],
                )

            extraction = BodyCorporateExtraction(
                water_total=water_total,
                electricity_total=electricity_total,
                invoice_date=None,  # TODO: Extract date if needed
                unit_number=None,  # TODO: Extract unit number if needed
            )

            log.info(
                f"Successfully extracted Body Corporate invoice - "
                f"Water: R{water_total}, Electricity: R{electricity_total}"
            )
            return ExtractionResult(
                success=True,
                invoice_type=InvoiceType.BODY_CORPORATE,
                data=extraction,
                raw_response=None,  # Don't store full text (privacy)
            )

        except Exception as e:
            log.error(f"Error parsing Body Corporate PDF: {e}")
            return ExtractionResult(
                success=False,
                invoice_type=InvoiceType.BODY_CORPORATE,
                error_message=f"PDF parsing error: {e}",
            )

    # -------------------------------------------------------------------------
    # Claude API Extraction Methods (Fallback Mode)
    # -------------------------------------------------------------------------

    def _extract_claude(
        self, pdf_content: bytes, invoice_type: InvoiceType
    ) -> ExtractionResult:
        """Extract using Claude API - sends PDF to external service."""
        try:
            log.info(f"Extracting data from {invoice_type.value} invoice PDF using Claude API")
            log.warning("Claude mode sends PDF content to Anthropic API (contains PII)")

            pdf_base64 = self._encode_pdf(pdf_content)
            prompt = self._build_extraction_prompt(invoice_type)

            log.debug(f"Calling Claude API with model {self.MODEL}")
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
            log.debug("Received response from Claude API")
            result = self._parse_claude_response(raw_response, invoice_type)

            if result.success:
                log.info(f"Successfully extracted data from {invoice_type.value} invoice")
            else:
                log.warning(f"Extraction failed for {invoice_type.value}: {result.error_message}")

            return result

        except anthropic.APIError as e:
            log.error(f"Anthropic API error during extraction: {e}")
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Anthropic API error: {e}",
            )
        except Exception as e:
            log.exception(f"Unexpected error during Claude extraction: {e}")
            return ExtractionResult(
                success=False,
                invoice_type=invoice_type,
                error_message=f"Claude extraction failed: {e}",
            )

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

    def _parse_claude_response(
        self, raw_response: str, invoice_type: InvoiceType
    ) -> ExtractionResult:
        """Parse Claude's JSON response into structured data."""
        try:
            cleaned = raw_response.strip()
            if cleaned.startswith("```"):
                # Remove markdown code block wrapper
                lines = cleaned.split("\n")
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


# -------------------------------------------------------------------------
# Convenience Functions
# -------------------------------------------------------------------------


def extract_city_of_joburg(
    pdf_content: bytes, mode: str = "local", api_key: Optional[str] = None
) -> ExtractionResult:
    """Convenience function to extract data from a City of Johannesburg invoice.

    Args:
        pdf_content: Raw PDF file content as bytes
        mode: Extraction mode ('local' or 'claude')
        api_key: Optional Anthropic API key (only for 'claude' mode)

    Returns:
        ExtractionResult with CityOfJoburgExtraction data
    """
    extractor = PDFExtractor(mode=mode, api_key=api_key)
    return extractor.extract_from_pdf(pdf_content, InvoiceType.CITY_OF_JOBURG)


def extract_body_corporate(
    pdf_content: bytes, mode: str = "local", api_key: Optional[str] = None
) -> ExtractionResult:
    """Convenience function to extract data from a Body Corporate invoice.

    Args:
        pdf_content: Raw PDF file content as bytes
        mode: Extraction mode ('local' or 'claude')
        api_key: Optional Anthropic API key (only for 'claude' mode)

    Returns:
        ExtractionResult with BodyCorporateExtraction data
    """
    extractor = PDFExtractor(mode=mode, api_key=api_key)
    return extractor.extract_from_pdf(pdf_content, InvoiceType.BODY_CORPORATE)
