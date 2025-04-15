"""
Authentication module for the BookMyShow Bot.

This module handles user authentication including login, session management,
and verification of authentication status.
"""

import json
import time
import re
import base64
from pathlib import Path
from typing import Dict, Optional, Tuple, Any, Union

from playwright.async_api import Page, Response, TimeoutError as PlaywrightTimeoutError

from ..config import config
from ..utils.logger import get_logger
from ..utils.browser_manager import browser_manager


logger = get_logger(__name__)


class AuthenticationError(Exception):
    """Exception raised for authentication failures."""
    pass


class BookMyShowAuth:
    """
    Handles authentication with BookMyShow.
    
    Manages login, session storage, and authentication status verification.
    Supports both mobile number and email-based authentication.
    """
    
    def __init__(self):
        """Initialize the authentication manager."""
        self.base_url = config.get("bookmyshow.base_url", "https://in.bookmyshow.com")
        self.use_saved_session = config.get("auth.use_saved_session", True)
        self.session_path = Path(config.get("auth.session_path", "data/sessions"))
        self.session_validity = config.get("auth.session_validity", 86400)  # 24 hours
        
        # Ensure session directory exists
        self.session_path.mkdir(parents=True, exist_ok=True)
        
        self.default_region = config.get("bookmyshow.regions", ["NCR"])[0]
        self._authenticated = False
        self._last_auth_check = 0
        self._auth_check_interval = 300  # 5 minutes
    
    async def login(self, 
                  page: Page, 
                  credentials: Dict[str, str],
                  session_id: str = "default",
                  force_login: bool = False) -> bool:
        """
        Log in to BookMyShow.
        
        Args:
            page: Page object to use for authentication
            credentials: Dictionary with login credentials (mobile or email/password)
            session_id: ID to save the session under
            force_login: Whether to force login even if a saved session exists
            
        Returns:
            True if login successful, False otherwise
            
        Raises:
            AuthenticationError: If authentication fails
        """
        # Try to use saved session if allowed
        if not force_login and self.use_saved_session:
            if await self._try_load_session(page, session_id):
                logger.info("Successfully loaded saved session")
                return True
        
        logger.info("Performing fresh login to BookMyShow")
        
        # Navigate to homepage
        try:
            await browser_manager.navigate(page, self.base_url)
            
            # Wait for page to load and set up region if needed
            await self._handle_region_selection(page)
            
            # Check if already logged in
            if await self._check_if_logged_in(page):
                logger.info("Already logged in to BookMyShow")
                await self._save_session(page, session_id)
                return True
            
            # Click on sign-in button
            try:
                # Try different possible selectors for the sign-in button
                selectors = [
                    "text=Sign in",
                    "[data-id=signin]",
                    ".sign-in-icon",
                    ".signIn",
                    "a:has-text('Sign in')",
                    "button:has-text('Sign in')"
                ]
                
                for selector in selectors:
                    if await page.is_visible(selector, timeout=1000):
                        await browser_manager.click(page, selector)
                        logger.debug(f"Clicked sign-in button using selector: {selector}")
                        break
                else:
                    logger.warning("Could not find sign-in button with known selectors")
                    # Try to find a likely sign-in element
                    elements = await page.query_selector_all("a, button")
                    for element in elements:
                        text = await element.text_content()
                        if text and ("sign" in text.lower() or "log" in text.lower()):
                            await element.click()
                            await browser_manager.random_delay()
                            logger.debug(f"Clicked potential sign-in element with text: {text}")
                            break
                    else:
                        raise AuthenticationError("Could not find sign-in button")
                
                # Wait for login modal/page to appear
                await page.wait_for_selector("input[type='tel'], input[type='email'], input[type='text']", 
                                         timeout=10000)
            except PlaywrightTimeoutError:
                logger.error("Timed out waiting for login form")
                raise AuthenticationError("Login form did not appear")
            
            # Determine login method based on credentials provided
            if "mobile" in credentials:
                success = await self._login_with_mobile(page, credentials["mobile"])
            elif "email" in credentials and "password" in credentials:
                success = await self._login_with_email(page, credentials["email"], credentials["password"])
            else:
                raise AuthenticationError("Invalid credentials provided. Need mobile or email/password")
            
            if not success:
                raise AuthenticationError("Login failed")
            
            # Save session for future use
            await self._save_session(page, session_id)
            
            logger.info("Successfully logged in to BookMyShow")
            self._authenticated = True
            return True
            
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            raise AuthenticationError(f"Failed to log in: {str(e)}")
    
    async def _login_with_mobile(self, page: Page, mobile: str) -> bool:
        """
        Log in using mobile number.
        
        Args:
            page: Page object
            mobile: Mobile number
            
        Returns:
            True if login successful, False otherwise
        """
        try:
            # Look for mobile input field
            mobile_input_selector = "input[type='tel'], input[placeholder*='Mobile']"
            await page.wait_for_selector(mobile_input_selector, timeout=5000)
            
            # Clear and enter mobile number
            await browser_manager.type(page, mobile_input_selector, mobile)
            logger.debug(f"Entered mobile number: {mobile[:4]}XXXX{mobile[-2:]}")
            
            # Find and click continue/next button
            continue_selectors = [
                "button:has-text('Continue')",
                "button:has-text('Next')",
                "button:has-text('Proceed')",
                "button[type='submit']",
                "input[type='submit']"
            ]
            
            for selector in continue_selectors:
                if await page.is_visible(selector, timeout=1000):
                    await browser_manager.click(page, selector)
                    logger.debug(f"Clicked continue button using selector: {selector}")
                    break
            else:
                logger.warning("Could not find continue button")
                return False
            
            # Note: At this point, BookMyShow will send an OTP to the mobile number
            # OTP handling needs to be implemented
            logger.warning("OTP-based authentication requires manual intervention")
            logger.info("Waiting for 60 seconds for manual OTP entry...")
            
            # Wait for OTP input to appear
            otp_selectors = [
                "input[placeholder*='OTP']", 
                "input.otpInput",
                "input[maxlength='6']",
                "input[maxlength='4']"
            ]
            
            for selector in otp_selectors:
                if await page.is_visible(selector, timeout=5000):
                    logger.info(f"OTP input found. Please enter OTP manually using selector: {selector}")
                    break
            else:
                logger.warning("Could not find OTP input field")
            
            # Wait for potential manual OTP entry and login completion
            # In a production system, this would be replaced with automated OTP retrieval
            await page.wait_for_timeout(60000)
            
            # Check if login was successful
            return await self._check_if_logged_in(page)
            
        except Exception as e:
            logger.error(f"Mobile login failed: {str(e)}")
            return False
    
    async def _login_with_email(self, page: Page, email: str, password: str) -> bool:
        """
        Log in using email and password.
        
        Args:
            page: Page object
            email: Email address
            password: Password
            
        Returns:
            True if login successful, False otherwise
        """
        try:
            # Look for email/password option if there are multiple login methods
            email_option_selectors = [
                "text=Continue with Email",
                "a:has-text('Email')",
                "button:has-text('Email')",
                "text=Email/Password"
            ]
            
            for selector in email_option_selectors:
                if await page.is_visible(selector, timeout=1000):
                    await browser_manager.click(page, selector)
                    logger.debug(f"Clicked email login option using selector: {selector}")
                    break
            
            # Wait for email input
            email_selector = "input[type='email'], input[placeholder*='Email']"
            await page.wait_for_selector(email_selector, timeout=5000)
            
            # Enter email
            await browser_manager.type(page, email_selector, email)
            logger.debug(f"Entered email: {email}")
            
            # Find password field
            password_selector = "input[type='password'], input[placeholder*='Password']"
            await page.wait_for_selector(password_selector, timeout=5000)
            
            # Enter password
            await browser_manager.type(page, password_selector, password)
            logger.debug("Entered password")
            
            # Click login button
            login_selectors = [
                "button:has-text('Login')",
                "button:has-text('Sign in')",
                "button[type='submit']",
                "input[type='submit']"
            ]
            
            for selector in login_selectors:
                if await page.is_visible(selector, timeout=1000):
                    await browser_manager.click(page, selector)
                    logger.debug(f"Clicked login button using selector: {selector}")
                    break
            else:
                logger.warning("Could not find login button")
                return False
            
            # Wait for login to complete
            await page.wait_for_timeout(5000)
            
            # Check if login was successful
            return await self._check_if_logged_in(page)
            
        except Exception as e:
            logger.error(f"Email login failed: {str(e)}")
            return False
    
    async def _check_if_logged_in(self, page: Page) -> bool:
        """
        Check if user is logged in.
        
        Args:
            page: Page object
            
        Returns:
            True if logged in, False otherwise
        """
        try:
            # Check for profile indicators
            profile_selectors = [
                ".profile-icon",
                ".userProfile",
                "[data-id='userprofile']",
                "text=My Profile", 
                "text=My Account",
                ".signed-user-det"
            ]
            
            for selector in profile_selectors:
                if await page.is_visible(selector, timeout=1000):
                    logger.debug(f"Found logged-in indicator with selector: {selector}")
                    self._authenticated = True
                    self._last_auth_check = time.time()
                    return True
            
            # Check for sign-in button (which should not be present if logged in)
            sign_in_selectors = [
                "text=Sign in", 
                "[data-id=signin]", 
                "a:has-text('Sign in')",
                "button:has-text('Sign in')",
                ".sign-in-icon"
            ]
            
            for selector in sign_in_selectors:
                if await page.is_visible(selector, timeout=1000):
                    logger.debug(f"Found sign-in button with selector: {selector} (not logged in)")
                    self._authenticated = False
                    return False
            
            # If we can't determine status, assume not logged in
            logger.warning("Could not determine login status with certainty")
            self._authenticated = False
            return False
            
        except Exception as e:
            logger.error(f"Error checking login status: {str(e)}")
            self._authenticated = False
            return False
    
    async def check_auth_status(self, page: Page) -> bool:
        """
        Check authentication status and refresh if needed.
        
        Args:
            page: Page object to use for checking
            
        Returns:
            True if authenticated, False otherwise
        """
        # Only check periodically to avoid too many checks
        current_time = time.time()
        if self._authenticated and (current_time - self._last_auth_check) < self._auth_check_interval:
            return True
        
        # Update the last check time
        self._last_auth_check = current_time
        
        # Check actual status on the page
        return await self._check_if_logged_in(page)
    
    async def _try_load_session(self, page: Page, session_id: str = "default") -> bool:
        """
        Try to load a saved session.
        
        Args:
            page: Page to load session into
            session_id: ID of the session to load
            
        Returns:
            True if session loaded and valid, False otherwise
        """
        session_file = self.session_path / f"{session_id}.json"
        
        if not session_file.exists():
            logger.debug(f"No saved session found at {session_file}")
            return False
        
        # Check if session file is too old
        file_age = time.time() - session_file.stat().st_mtime
        if file_age > self.session_validity:
            logger.info(f"Saved session is too old ({file_age / 3600:.1f} hours), creating fresh login")
            return False
        
        try:
            # Load session data
            with open(session_file, "r") as f:
                storage_state = json.load(f)
            
            # Create a new context with the session data
            context = await browser_manager._browser.new_context(storage_state=storage_state)
            
            # Replace the page's context with the new one
            old_context = page.context
            page = await context.new_page()
            
            # Navigate to BookMyShow
            await browser_manager.navigate(page, f"{self.base_url}/explore/home")
            
            # Check if still logged in
            is_logged_in = await self._check_if_logged_in(page)
            
            if is_logged_in:
                logger.info(f"Successfully restored session from {session_file}")
                self._authenticated = True
                return True
            else:
                logger.info("Saved session is no longer valid")
                await context.close()
                return False
                
        except Exception as e:
            logger.error(f"Error loading saved session: {str(e)}")
            return False
    
    async def _save_session(self, page: Page, session_id: str = "default") -> None:
        """
        Save the current session.
        
        Args:
            page: Page with the session to save
            session_id: ID to save the session under
        """
        try:
            storage_state = await page.context.storage_state()
            session_file = self.session_path / f"{session_id}.json"
            
            with open(session_file, "w") as f:
                json.dump(storage_state, f, indent=2)
                
            logger.info(f"Saved session to {session_file}")
        except Exception as e:
            logger.error(f"Error saving session: {str(e)}")
    
    async def _handle_region_selection(self, page: Page) -> None:
        """
        Handle region selection if prompted.
        
        Args:
            page: Page object
        """
        try:
            # Check if region selection popup is present
            region_selectors = [
                "text=Select your region",
                "text=Choose your region",
                ".regionPopup", 
                "[data-id='region-popup']"
            ]
            
            for selector in region_selectors:
                if await page.is_visible(selector, timeout=3000):
                    logger.info("Region selection prompt detected")
                    
                    # Click on the preferred region
                    region_button_selector = f"text={self.default_region}, a:has-text('{self.default_region}')"
                    
                    if await page.is_visible(region_button_selector, timeout=2000):
                        await browser_manager.click(page, region_button_selector)
                        logger.info(f"Selected region: {self.default_region}")
                    else:
                        # If preferred region not found, click the first available
                        logger.warning(f"Preferred region {self.default_region} not found, selecting first available")
                        region_options = await page.query_selector_all(".region-list li a, .regionHolder a")
                        if region_options:
                            await region_options[0].click()
                    
                    # Wait for region selection to take effect
                    await page.wait_for_load_state("networkidle")
                    return
            
            logger.debug("No region selection prompt detected")
        except Exception as e:
            logger.warning(f"Error handling region selection: {str(e)}")


# Singleton instance
auth_manager = BookMyShowAuth()