"""Main Orchestrator Module

Coordinates the full rental invoice automation workflow:
1. Fetch utility PDFs from Gmail
2. Extract invoice amounts using Claude API
3. Prepare data for RentBook invoice creation
"""

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.gmail_client import GmailClient, EmailAttachment
from src.pdf_extractor import (
    PDFExtractor,
    InvoiceType,
    ExtractionResult,
    CityOfJoburgExtraction,
    BodyCorporateExtraction,
    filter_attachments_by_invoice_type,
)
from src.rentbook_bot import RentBookBot, create_authenticated_bot
from src.logger_config import setup_logger

log = setup_logger(__name__)


class InvoiceData(BaseModel):
    """Compiled invoice data ready for RentBook submission."""

    base_rent: Decimal = Field(description="Monthly base rent amount")
    refuse: Decimal = Field(description="Municipal refuse charges")
    water: Decimal = Field(description="Water charges from Body Corporate")
    electricity: Decimal = Field(description="Electricity charges from Body Corporate")
    total: Decimal = Field(description="Total invoice amount")

    # Metadata
    city_invoice_date: Optional[str] = None
    body_corporate_invoice_date: Optional[str] = None
    extraction_timestamp: datetime = Field(default_factory=datetime.now)


class Config:
    """Application configuration loaded from config.yaml and .env"""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        load_dotenv()

        self.gmail_address = os.getenv("GMAIL_ADDRESS")
        self.gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")

        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

        self.rentbook_email = os.getenv("RENTBOOK_EMAIL")
        self.rentbook_password = os.getenv("RENTBOOK_PASSWORD")

        self._validate()

    def _validate(self):
        """Validate that all required credentials are present."""
        missing = []

        if not self.gmail_address:
            missing.append("GMAIL_ADDRESS")
        if not self.gmail_app_password:
            missing.append("GMAIL_APP_PASSWORD")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.rentbook_email:
            missing.append("RENTBOOK_EMAIL")
        if not self.rentbook_password:
            missing.append("RENTBOOK_PASSWORD")

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Please check your .env file"
            )

    @property
    def gmail_config(self):
        return self.config["gmail"]

    @property
    def rental_config(self):
        return self.config["rental"]

    @property
    def search_days_back(self) -> int:
        return self.gmail_config.get("search_days_back", 30)

    @property
    def base_rent(self) -> Decimal:
        return Decimal(str(self.rental_config["base_rent"]))

    @property
    def rentbook_config(self):
        return self.config["rentbook"]

    @property
    def headless(self) -> bool:
        return self.rentbook_config.get("headless", True)  # Default to True


def fetch_utility_pdfs(config: Config) -> dict[str, list[EmailAttachment]]:
    """Fetch utility PDFs from Gmail

    Returns:
        Dictionary with 'city' and 'body_corporate' keys containing attachments
    """
    log.info("=" * 60)
    log.info("STEP 1: Fetching utility PDFs from Gmail")
    log.info("=" * 60)

    after_date = datetime.now() - timedelta(days=config.search_days_back)

    city_query = config.gmail_config["search_queries"]["city_of_joburg"]
    body_corp_query = config.gmail_config["search_queries"]["body_corporate"]

    city_sender = city_query.split("from:")[1].split()[0]
    body_corp_sender = body_corp_query.split("from:")[1].split()[0]

    max_results = config.gmail_config.get("max_results", 5)

    log.info(f"Searching for emails from last {config.search_days_back} days")
    log.debug(f"City sender: {city_sender}")
    log.debug(f"Body Corporate sender: {body_corp_sender}")

    with GmailClient(config.gmail_address, config.gmail_app_password) as client:
        city_attachments = client.get_pdf_attachments(
            sender=city_sender,
            after_date=after_date,
            max_results=max_results,
        )

        body_corp_attachments = client.get_pdf_attachments(
            sender=body_corp_sender,
            after_date=after_date,
            max_results=max_results,
        )

        log.info(
            f"Summary: {len(city_attachments)} City PDF(s), {len(body_corp_attachments)} Body Corporate PDF(s)"
        )

        return {
            "city": city_attachments,
            "body_corporate": body_corp_attachments,
        }


def extract_invoice_data(
    attachments: dict[str, list[EmailAttachment]],
    config: Config,
) -> InvoiceData:
    """Extract data from PDFs and compile into InvoiceData.

    Args:
        attachments: Dictionary with 'city' and 'body_corporate' attachment lists
        config: Application configuration

    Returns:
        Compiled InvoiceData ready for RentBook

    Raises:
        ValueError: If required PDFs are missing or extraction fails
    """
    log.info("=" * 60)
    log.info("STEP 2: Extracting data from PDFs")
    log.info("=" * 60)

    # Filter attachments by invoice type patterns FIRST
    log.debug("Filtering PDFs by expected filename patterns")
    city_pdfs = filter_attachments_by_invoice_type(
        attachments["city"], InvoiceType.CITY_OF_JOBURG
    )
    body_corp_pdfs = filter_attachments_by_invoice_type(
        attachments["body_corporate"], InvoiceType.BODY_CORPORATE
    )

    # Validate we have the required PDFs BEFORE calling Claude API
    if not city_pdfs:
        log.error(
            "No City of Johannesburg invoice PDFs found matching expected pattern"
        )
        if attachments["city"]:
            log.info(
                f"Found {len(attachments['city'])} City PDFs, but none matched pattern:"
            )
            for att in attachments["city"]:
                log.info(f"  - {att.filename}")
        raise ValueError(
            "No City of Johannesburg invoice PDFs found matching expected pattern"
        )

    if not body_corp_pdfs:
        log.error("No Body Corporate invoice PDFs found matching expected pattern")
        if attachments["body_corporate"]:
            log.info(
                f"Found {len(attachments['body_corporate'])} Body Corporate PDFs, but none matched pattern:"
            )
            for att in attachments["body_corporate"]:
                log.info(f"  - {att.filename}")
        raise ValueError(
            "No Body Corporate invoice PDFs found matching expected pattern"
        )

    # Now create extractor only after validation
    log.info("Initializing PDF extractor with Claude API")
    extractor = PDFExtractor(api_key=config.anthropic_api_key)

    # Use the most recent PDF (first in list)
    city_pdf = city_pdfs[0]
    body_corp_pdf = body_corp_pdfs[0]

    log.info(f"Processing City PDF: {city_pdf.filename}")
    city_result = extractor.extract_from_pdf(
        city_pdf.content, InvoiceType.CITY_OF_JOBURG
    )

    if not city_result.success:
        log.error(f"City PDF extraction failed: {city_result.error_message}")
        raise ValueError(f"City PDF extraction failed: {city_result.error_message}")

    city_data: CityOfJoburgExtraction = city_result.data
    log.info(f"Extracted - Refuse: R{city_data.refuse_total}")

    log.info(f"Processing Body Corporate PDF: {body_corp_pdf.filename}")
    body_corp_result = extractor.extract_from_pdf(
        body_corp_pdf.content, InvoiceType.BODY_CORPORATE
    )

    if not body_corp_result.success:
        log.error(
            f"Body Corporate PDF extraction failed: {body_corp_result.error_message}"
        )
        raise ValueError(
            f"Body Corporate PDF extraction failed: {body_corp_result.error_message}"
        )

    body_corp_data: BodyCorporateExtraction = body_corp_result.data
    log.info(f"Extracted - Water: R{body_corp_data.water_total}")
    log.info(f"Extracted - Electricity: R{body_corp_data.electricity_total}")

    # Compile invoice data
    base_rent = config.base_rent
    total = (
        base_rent
        + city_data.refuse_total
        + body_corp_data.water_total
        + body_corp_data.electricity_total
    )

    invoice_data = InvoiceData(
        base_rent=base_rent,
        refuse=city_data.refuse_total,
        water=body_corp_data.water_total,
        electricity=body_corp_data.electricity_total,
        total=total,
        city_invoice_date=city_data.invoice_date,
        body_corporate_invoice_date=body_corp_data.invoice_date,
    )

    log.info("Invoice data compiled successfully")
    return invoice_data


def display_invoice_summary(invoice: InvoiceData):
    """Display a formatted invoice summary."""
    log.info("=" * 60)
    log.info("INVOICE SUMMARY")
    log.info("=" * 60)
    log.info(f"Base Rent:              R{invoice.base_rent:>10.2f}")
    log.info(f"Municipal Refuse:       R{invoice.refuse:>10.2f}")
    log.info(f"Water:                  R{invoice.water:>10.2f}")
    log.info(f"Electricity:            R{invoice.electricity:>10.2f}")
    log.info("-" * 60)
    log.info(f"TOTAL:                  R{invoice.total:>10.2f}")
    log.info("=" * 60)

    if invoice.city_invoice_date:
        log.info(f"City Invoice Date: {invoice.city_invoice_date}")
    if invoice.body_corporate_invoice_date:
        log.info(f"Body Corporate Invoice Date: {invoice.body_corporate_invoice_date}")


async def test_rentbook_authentication(config: Config) -> bool:
    """Test RentBook authentication.

    Args:
        config: Application configuration

    Returns:
        True if authentication succeeded
    """
    log.info("=" * 60)
    log.info("STEP 3: Testing RentBook Authentication")
    log.info("=" * 60)

    try:
        bot = await create_authenticated_bot(
            email=config.rentbook_email,
            password=config.rentbook_password,
            headless=config.headless,
        )

        log.info("Authentication test successful!")
        current_url = await bot.get_current_url()
        log.info(f"Current URL: {current_url}")

        # clean up
        await bot.stop()
        return True

    except Exception as e:
        log.error(f"RentBook authentication failed: {e}")
        raise


async def async_main():
    """Main orchestrator workflow."""
    try:
        log.info("=" * 60)
        log.info("Esplendido Rental Invoice Automation")
        log.info("=" * 60)

        # Load configuration
        log.info("Loading configuration from config.yaml and .env")
        config = Config()

        # Step 1: Fetch PDFs from Gmail
        attachments = fetch_utility_pdfs(config)

        # Step 2: Extract data from PDFs
        invoice_data = extract_invoice_data(attachments, config)

        # Step 3: Display Summary
        display_invoice_summary(invoice_data)

        # Step 4: Test RentBook Authentication
        await test_rentbook_authentication(config)

        # TODO: Step 5: Submit to RentBook (US-6)
        log.warning("RentBook submission not yet implemented (US-6)")
        log.info("Invoice data is ready for manual entry")

        log.info("=" * 60)
        log.info("Automation complete!")
        log.info("=" * 60)

        return 0

    except ValueError as e:
        log.error(f"Configuration/Validation Error: {e}")
        return 1
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        return 2


def main():
    """Synchronous entry point."""
    import asyncio

    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
