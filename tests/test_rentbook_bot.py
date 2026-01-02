"""Tests for the RentBook Bot module (username/password authentication)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.rentbook_bot import (
    RentBookAuthError,
    RentBookBot,
    create_authenticated_bot,
    run_login,
)


class TestRentBookBotInit:
    """Tests for RentBookBot initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        bot = RentBookBot(
            email="test@example.com",
            password="testpass",
        )

        assert bot.email == "test@example.com"
        assert bot.password == "testpass"
        assert bot.headless is True
        assert bot.navigation_timeout == 30000
        assert bot.action_timeout == 10000
        assert bot._playwright is None
        assert bot._browser is None
        assert bot._context is None
        assert bot._page is None

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        bot = RentBookBot(
            email="user@example.com",
            password="secret123",
            headless=False,
            navigation_timeout=60000,
            action_timeout=20000,
        )

        assert bot.email == "user@example.com"
        assert bot.password == "secret123"
        assert bot.headless is False
        assert bot.navigation_timeout == 60000
        assert bot.action_timeout == 20000


class TestRentBookBotProperties:
    """Tests for RentBookBot property access."""

    def test_page_raises_without_start(self):
        """Test that accessing page without starting raises error."""
        bot = RentBookBot("test@example.com", "password")
        with pytest.raises(RuntimeError, match="Browser not started"):
            _ = bot.page

    def test_context_raises_without_start(self):
        """Test that accessing context without starting raises error."""
        bot = RentBookBot("test@example.com", "password")
        with pytest.raises(RuntimeError, match="Browser not started"):
            _ = bot.context


class TestRentBookBotLifecycle:
    """Tests for browser lifecycle management."""

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_start_creates_browser(self, mock_playwright):
        """Test that start() creates browser and context."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        bot = RentBookBot("test@example.com", "password", headless=True)
        await bot.start()

        mock_pw_instance.chromium.launch.assert_called_once_with(headless=True)
        mock_browser.new_context.assert_called_once()
        mock_context.new_page.assert_called_once()

        assert bot._page == mock_page
        assert bot._context == mock_context

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_stop_cleans_up_resources(self, mock_playwright):
        """Test that stop() properly cleans up all resources."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        bot = RentBookBot("test@example.com", "password")
        await bot.start()
        await bot.stop()

        mock_page.close.assert_called_once()
        mock_context.close.assert_called_once()
        mock_browser.close.assert_called_once()
        mock_pw_instance.stop.assert_called_once()

        assert bot._page is None
        assert bot._context is None
        assert bot._browser is None
        assert bot._playwright is None

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_context_manager(self, mock_playwright):
        """Test async context manager functionality."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        async with RentBookBot("test@example.com", "password") as bot:
            assert bot._page is not None

        mock_page.close.assert_called_once()


class TestRentBookBotLogin:
    """Tests for login functionality."""

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_login_fills_credentials(self, mock_playwright):
        """Test that login fills in email and password."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        # Mock form elements
        mock_email_input = AsyncMock()
        mock_password_input = AsyncMock()
        mock_login_button = AsyncMock()

        # Track calls to determine which selector is being requested
        selector_call_count = [0]

        async def wait_for_selector_side_effect(selector, **kwargs):
            selector_call_count[0] += 1
            # First 3 calls are for form elements
            if selector_call_count[0] == 1:  # email
                return mock_email_input
            elif selector_call_count[0] == 2:  # password
                return mock_password_input
            elif selector_call_count[0] == 3:  # login button
                return mock_login_button
            # Remaining calls are for error checking - raise timeout
            raise PlaywrightTimeoutError("no error found")

        mock_page.wait_for_selector = AsyncMock(
            side_effect=wait_for_selector_side_effect
        )
        mock_page.url = "https://app.rentbook.co.za/dashboard"

        bot = RentBookBot("user@example.com", "secret123")
        await bot.start()
        await bot.login()

        mock_email_input.fill.assert_called_once_with("user@example.com")
        mock_password_input.fill.assert_called_once_with("secret123")
        mock_login_button.click.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_login_raises_on_missing_form(self, mock_playwright):
        """Test that login raises error if form not found."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeoutError("timeout")
        )

        bot = RentBookBot("user@example.com", "password")
        await bot.start()

        with pytest.raises(RentBookAuthError, match="Could not find login form"):
            await bot.login()


class TestRentBookBotIsLoggedIn:
    """Tests for login state checking."""

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_is_logged_in_returns_false_on_login_page(self, mock_playwright):
        """Test is_logged_in returns False when on login page."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_page.url = "https://app.rentbook.co.za/login"

        bot = RentBookBot("test@example.com", "password")
        await bot.start()
        result = await bot.is_logged_in()

        assert result is False

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_is_logged_in_returns_true_on_dashboard(self, mock_playwright):
        """Test is_logged_in returns True when on dashboard."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_page.url = "https://app.rentbook.co.za/dashboard"
        mock_page.wait_for_selector = AsyncMock()

        bot = RentBookBot("test@example.com", "password")
        await bot.start()
        result = await bot.is_logged_in()

        assert result is True


class TestRentBookBotAuthenticate:
    """Tests for authenticate method."""

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_authenticate_skips_login_if_already_logged_in(self, mock_playwright):
        """Test that authenticate reuses valid session."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        # Simulate already logged in
        mock_page.url = "https://app.rentbook.co.za/dashboard"
        mock_page.wait_for_selector = AsyncMock()

        bot = RentBookBot("test@example.com", "password")
        await bot.start()
        result = await bot.authenticate()

        assert result is True
        # Should not navigate to login page when already logged in
        login_calls = [
            call for call in mock_page.goto.call_args_list if "login" in str(call)
        ]
        assert len(login_calls) == 0


class TestRentBookBotNavigation:
    """Tests for navigation methods."""

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_navigate_to_dashboard_when_logged_in(self, mock_playwright):
        """Test navigation to dashboard when logged in."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_page.url = "https://app.rentbook.co.za/dashboard"
        mock_page.wait_for_selector = AsyncMock()

        bot = RentBookBot("test@example.com", "password")
        await bot.start()
        await bot.navigate_to_dashboard()

        assert any("dashboard" in str(call) for call in mock_page.goto.call_args_list)

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_navigate_to_dashboard_raises_when_not_logged_in(
        self, mock_playwright
    ):
        """Test navigation raises error when not logged in."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_page.url = "https://app.rentbook.co.za/login"

        bot = RentBookBot("test@example.com", "password")
        await bot.start()

        with pytest.raises(RuntimeError, match="Not logged in"):
            await bot.navigate_to_dashboard()

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_get_current_url(self, mock_playwright):
        """Test getting current URL."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_page.url = "https://app.rentbook.co.za/test"

        bot = RentBookBot("test@example.com", "password")
        await bot.start()
        url = await bot.get_current_url()

        assert url == "https://app.rentbook.co.za/test"

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_take_screenshot(self, mock_playwright, tmp_path):
        """Test taking a screenshot."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        bot = RentBookBot("test@example.com", "password")
        await bot.start()

        screenshot_path = str(tmp_path / "screenshot.png")
        await bot.take_screenshot(screenshot_path)

        mock_page.screenshot.assert_called_once_with(path=screenshot_path)


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_create_authenticated_bot_success(self, mock_playwright):
        """Test creating an authenticated bot."""
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        # Simulate already logged in
        mock_page.url = "https://app.rentbook.co.za/dashboard"
        mock_page.wait_for_selector = AsyncMock()

        bot = await create_authenticated_bot(
            email="test@example.com",
            password="password",
        )

        assert bot._page is not None
        await bot.stop()

    @pytest.mark.asyncio
    @patch("src.rentbook_bot.async_playwright")
    async def test_create_authenticated_bot_failure_cleans_up(self, mock_playwright):
        """Test that failed authentication cleans up resources."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        # Simulate not logged in and login fails
        mock_page.url = "https://app.rentbook.co.za/login"
        mock_page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeoutError("timeout")
        )

        with pytest.raises(RentBookAuthError):
            await create_authenticated_bot(
                email="test@example.com",
                password="password",
            )

        mock_page.close.assert_called_once()


class TestURLConstants:
    """Tests for URL constants."""

    def test_base_url(self):
        """Test BASE_URL constant."""
        assert RentBookBot.BASE_URL == "https://app.rentbook.co.za"

    def test_login_url(self):
        """Test LOGIN_URL constant."""
        assert RentBookBot.LOGIN_URL == "https://app.rentbook.co.za/login"

    def test_dashboard_url(self):
        """Test DASHBOARD_URL constant."""
        assert RentBookBot.DASHBOARD_URL == "https://app.rentbook.co.za/dashboard"
