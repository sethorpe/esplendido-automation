"""Rentbook Bot Module

Browser automation for RentBook (rentbook.co.za) using Playwright.
Handles Google OAuth authentication with session persistence.
"""

import asyncio
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from src.logger_config import setup_logger

log = setup_logger(__name__)


class RentBookAuthError(Exception):
    """Raised when RentBook authentication fails."""


class RentBookBot:
    """Automated interaction with RentBook using Playwright.

    Handles Google OAuth login with session persistence to avoid
    repeated authentication flows.
    """

    BASE_URL = "https://rentbook.co.za"
    LOGIN_URL = "https://rentbook.cloud.mrisoftware.com/Account/Login"
    DASHBOARD_URL = "https://rentbook.cloud.mrisoftware.com"

    EMAIL_INPUT = 'input[name="email"], input[type="email"], #email'
    PASSWORD_INPUT = 'input[name="password"], input[type="password"], #password'
    LOGIN_BUTTON = (
        'button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")'
    )

    LOGGED_IN_INDICATOR = '[data-testid="user-menu"], .user-menu, .dashboard, nav'

    def __init__(
        self,
        email: str,
        password: str,
        headless: bool = True,
        navigation_timeout: int = 30000,
        action_timeout: int = 10000,
    ):
        """Initialize the RentBook bot.

        Args:
            email: RentBook account email
            password: RentBook account password
            headless: Run browser in headless mode (no visible window).
            navigation_timeout: Timeout for page navigation in milliseconds.
            action_timeout: Timeout for actions (clicks, etc.) in milliseconds.
        """
        self.email = email
        self.password = password
        self.headless = headless
        self.navigation_timeout = navigation_timeout
        self.action_timeout = action_timeout

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Page:
        """Get the current page, raising if not initialized."""
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """Get the current browser context, raising if not initialized."""
        if self._context is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._context

    async def start(self) -> None:
        """Start the browser and create a new context."""
        log.info(f"Starting browser (headless={self.headless})")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)

        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
        )
        self._context.set_default_navigation_timeout(self.navigation_timeout)
        self._context.set_default_timeout(self.action_timeout)

        self._page = await self._context.new_page()
        log.debug("Browser context and page created")

    async def stop(self) -> None:
        """Stop the browser and clean up resources."""
        log.debug("Stopping browser and cleaning up resources")
        if self._page:
            await self._page.close()
            self._page = None
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        log.info("Browser stopped and resources cleaned up")

    async def __aenter__(self) -> "RentBookBot":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.stop()

    async def is_logged_in(self) -> bool:
        """Check if the current session is authenticated with RentBook.

        Returns:
            True if logged in, False otherwise.
        """
        try:
            await self.page.goto(self.DASHBOARD_URL, wait_until="networkidle")

            # Check if we're on the dashboard (logged in) or redirected to login
            current_url = self.page.url

            # If redirected to login page, we're not logged in
            if "login" in current_url.lower():
                return False

            # Check for logged-in indicators on the page
            try:
                await self.page.wait_for_selector(
                    self.LOGGED_IN_INDICATOR,
                    timeout=5000,
                )
                return True
            except PlaywrightTimeoutError:
                # No logged-in indicator found
                return False

        except Exception:
            return False

    async def login(self) -> bool:
        """Log in to RentBook using username and password.

        Returns:
            True if login succeeded, False otherwise.

        Raises:
            RentBookAuthError: If login fails.
        """
        log.info("Attempting to log in to RentBook")
        await self.page.goto(self.LOGIN_URL, wait_until="networkidle")

        try:
            log.debug("Locating login form fields")
            email_input = await self.page.wait_for_selector(
                self.EMAIL_INPUT, timeout=10000
            )
            if email_input:
                log.debug("Filling email field")
                await email_input.fill(self.email)

            password_input = await self.page.wait_for_selector(
                self.PASSWORD_INPUT, timeout=5000
            )
            if password_input:
                log.debug("Filling password field")
                await password_input.fill(self.password)
        except PlaywrightTimeoutError:
            log.error("Could not find login form fields on RentBook login page")
            raise RentBookAuthError(
                "Could not find login form fields on RentBook login page"
            )

        try:
            login_button = await self.page.wait_for_selector(
                self.LOGIN_BUTTON, timeout=5000
            )
            if login_button:
                log.debug("Clicking login button")
                await login_button.click()
        except PlaywrightTimeoutError:
            log.error("Could not find login button")
            raise RentBookAuthError("Could not find login button")

        try:
            await self.page.wait_for_url(f"{self.DASHBOARD_URL}", timeout=15000)
        except PlaywrightTimeoutError:
            raise RentBookAuthError(
                "Login failed - did not redirect after submitting credentials"
            )

        error_selectors = [
            ".error-message",
            ".alert-danger",
            '[role="alert"]',
            ':has-text("Invalid credentials")',
            ':has-text("incorrect password")',
        ]
        for selector in error_selectors:
            try:
                error = await self.page.wait_for_selector(selector, timeout=10000)
                if error:
                    error_text = await error.text_content()
                    raise RentBookAuthError(f"Login failed: {error_text}")
            except PlaywrightTimeoutError:
                continue

        if await self.is_logged_in():
            log.info("Successfully logged in to RentBook")
            return True

        if "login" in self.page.url.lower():
            log.error("Login failed - still on login page after submission")
            raise RentBookAuthError(
                "Login failed = still on login page after submission"
            )

        log.info("Login successful")
        return True

    async def authenticate(self) -> bool:
        """Authenticate with RentBook.

        Checks if already logged in, otherwise performs login.

        Returns:
            True if authentication succeeded.

        Raises:
            RentBookAuthError: If authentication fails.
        """
        log.info("Authenticating with RentBook")
        await self.page.goto(self.DASHBOARD_URL, wait_until="networkidle")

        if await self.is_logged_in():
            log.info("Already authenticated - session is valid")
            return True

        log.info("Not logged in - initiating login flow")
        return await self.login()

    async def navigate_to_dashboard(self) -> None:
        """Navigate to the RentBook dashboard.

        Raises:
            RuntimeError: If not logged in.
        """
        await self.page.goto(self.DASHBOARD_URL, wait_until="networkidle")

        if not await self.is_logged_in():
            raise RuntimeError("Not logged in. Call authenticate() first.")

    async def get_current_url(self) -> str:
        """Get the current page URL."""
        return self.page.url

    async def take_screenshot(self, path: str) -> None:
        """Take a screenshot of the current page.

        Args:
            path: Path to save the screenshot.
        """
        await self.page.screenshot(path=path)


# -------------------------------------------------------------------------
# Convenience Functions
# -------------------------------------------------------------------------


async def create_authenticated_bot(
    email: str,
    password: str,
    headless: bool = True,
) -> RentBookBot:
    """Create and authenticate a RentBook bot.

    Args:
        email: RentBook account email
        password: RentBook account password
        headless: Run browser in headless mode.

    Returns:
        Authenticated RentBookBot instance.

    Raises:
        RentBookAuthError: If authentication fails.
    """

    bot = RentBookBot(
        email=email,
        password=password,
        headless=headless,
    )
    await bot.start()

    try:
        await bot.authenticate()
        return bot
    except Exception:
        await bot.stop()
        raise


def run_login(
    email: str,
    password: str,
    headless: bool = True,
) -> bool:
    """Synchronous wrapper to run the login flow.

    Useful for testing credentials or scripts that don't use async.

    Args:
        email: RentBook account email
        password: RentBook account password
        headless: Run browser in headless mode.

    Returns:
        True if login succeeded.
    """

    async def _login():
        async with RentBookBot(
            email=email,
            password=password,
            headless=headless,
        ) as bot:
            return await bot.login()

    return asyncio.run(_login())
