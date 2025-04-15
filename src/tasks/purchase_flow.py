"""
Purchase flow module for the BookMyShow Bot.

This module orchestrates the complete ticket purchase flow from monitoring
to payment, handling all the steps in the process.
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from ..config import config
from ..utils.logger import get_logger
from ..utils.browser_manager import browser_manager
from ..utils.captcha_solver import captcha_solver
from ..utils.proxy_manager import proxy_manager
from ..monitoring.event_tracker import Event
from ..auth.login import auth_manager
from ..ticket.selector import ticket_selector
from ..payment.gift_card import payment_processor
from ..notification.alerts import notification_manager


logger = get_logger(__name__)


class PurchaseFlowError(Exception):
    """Exception raised for purchase flow errors."""
    pass


class PurchaseFlow:
    """
    Orchestrates the complete ticket purchase flow.
    
    Handles all steps from monitoring to payment, with error handling
    and recovery strategies.
    """
    
    def __init__(self):
        """Initialize the purchase flow."""
        self.max_retries = config.get("purchase.max_retries", 3)
        self.retry_delay = config.get("purchase.retry_delay", 5)
        self.screenshot_dir = Path(config.get("purchase.screenshot_dir", "data/screenshots"))
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        
        # Track ongoing purchases
        self.active_purchases = {}
    
    async def execute_purchase(self, event: Event, quantity: Optional[int] = None) -> bool:
        """
        Execute the complete ticket purchase flow.
        
        Args:
            event: Event to purchase tickets for
            quantity: Number of tickets to purchase, or None to use event's quantity
            
        Returns:
            True if purchase was successful, False otherwise
        """
        # Check if there's already a purchase in progress for this event
        if event.event_id in self.active_purchases:
            logger.warning(f"Purchase already in progress for event: {event.name}")
            return False
        
        # Mark purchase as active
        self.active_purchases[event.event_id] = {
            "start_time": time.time(),
            "status": "starting"
        }
        
        # Use event's quantity if not specified
        if quantity is None:
            quantity = event.quantity
        
        logger.info(f"Starting purchase flow for '{event.name}', {quantity} tickets")
        
        # Notify about purchase start
        await notification_manager.notify_purchase_started(event, quantity)
        
        # Start with fresh purchase attempt
        attempt = 1
        error = None
        
        try:
            while attempt <= self.max_retries:
                logger.info(f"Purchase attempt {attempt}/{self.max_retries}")
                self.active_purchases[event.event_id]["status"] = f"attempt_{attempt}"
                
                try:
                    success = await self._execute_purchase_attempt(event, quantity)
                    
                    if success:
                        logger.info(f"Purchase successful for '{event.name}'!")
                        self.active_purchases[event.event_id]["status"] = "completed"
                        return True
                    
                    logger.warning(f"Purchase attempt {attempt} failed for '{event.name}'")
                    
                except Exception as e:
                    logger.error(f"Error during purchase attempt {attempt}: {str(e)}")
                    error = str(e)
                
                # Increment attempt and wait before retry
                attempt += 1
                if attempt <= self.max_retries:
                    delay = self.retry_delay * attempt
                    logger.info(f"Waiting {delay} seconds before next attempt")
                    await asyncio.sleep(delay)
            
            # All attempts failed
            logger.error(f"All purchase attempts failed for '{event.name}'")
            await notification_manager.notify_purchase_failed(
                event, 
                f"Failed after {self.max_retries} attempts: {error or 'Unknown error'}"
            )
            
            self.active_purchases[event.event_id]["status"] = "failed"
            return False
            
        finally:
            # Clean up
            if event.event_id in self.active_purchases:
                self.active_purchases[event.event_id]["end_time"] = time.time()
    
    async def _execute_purchase_attempt(self, event: Event, quantity: int) -> bool:
        """
        Execute a single purchase attempt.
        
        Args:
            event: Event to purchase tickets for
            quantity: Number of tickets to purchase
            
        Returns:
            True if purchase was successful, False otherwise
        """
        # Create a new browser context with proxy if enabled
        proxy = await proxy_manager.get_proxy() if proxy_manager.enabled else None
        
        context_options = {
            "load_session": True, 
            "session_id": "bookmyshow"
        }
        
        if proxy:
            logger.info(f"Using proxy: {proxy.host}:{proxy.port}")
            # The browser_manager will apply proxy settings when creating context
        
        context = await browser_manager.create_context(**context_options)
        page = await browser_manager.new_page(context)
        
        try:
            # Set up screenshot directory for this attempt
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            attempt_screenshot_dir = self.screenshot_dir / f"{event.event_id}_{timestamp}"
            attempt_screenshot_dir.mkdir(parents=True, exist_ok=True)
            
            # Step 1: Navigate to event page
            logger.info(f"Navigating to event: {event.url}")
            await browser_manager.navigate(page, event.url)
            
            # Take screenshot of event page
            await self._take_screenshot(page, attempt_screenshot_dir, "01_event_page")
            
            # Step 2: Authentication if needed
            await self._handle_authentication(page, attempt_screenshot_dir)
            
            # Step 3: Navigate to ticket selection
            logger.info("Navigating to ticket selection")
            await ticket_selector.navigate_to_ticket_selection(page, event.url)
            
            # Take screenshot after reaching ticket selection
            await self._take_screenshot(page, attempt_screenshot_dir, "03_ticket_selection")
            
            # Step 4: Check for CAPTCHA
            logger.info("Checking for CAPTCHA")
            has_captcha, _ = await captcha_solver.detect_captcha(page)
            if has_captcha:
                logger.info("CAPTCHA detected, attempting to solve")
                captcha_solved = await captcha_solver.solve_captcha(page)
                if not captcha_solved:
                    logger.error("Failed to solve CAPTCHA")
                    await self._take_screenshot(page, attempt_screenshot_dir, "04_captcha_failed")
                    return False
                await self._take_screenshot(page, attempt_screenshot_dir, "04_captcha_solved")
            
            # Step 5: Select tickets
            logger.info(f"Selecting {quantity} tickets")
            # Update quantity in the selector
            ticket_selector.desired_quantity = quantity
            
            tickets_selected = await ticket_selector.select_tickets(page, event.url)
            if not tickets_selected:
                logger.error("Failed to select tickets")
                await self._take_screenshot(page, attempt_screenshot_dir, "05_ticket_selection_failed")
                return False
            
            await self._take_screenshot(page, attempt_screenshot_dir, "05_tickets_selected")
            
            # Step 6: Check for CAPTCHA again after ticket selection
            logger.info("Checking for CAPTCHA after ticket selection")
            has_captcha, _ = await captcha_solver.detect_captcha(page)
            if has_captcha:
                logger.info("CAPTCHA detected after ticket selection, attempting to solve")
                captcha_solved = await captcha_solver.solve_captcha(page)
                if not captcha_solved:
                    logger.error("Failed to solve CAPTCHA after ticket selection")
                    await self._take_screenshot(page, attempt_screenshot_dir, "06_captcha_failed")
                    return False
                await self._take_screenshot(page, attempt_screenshot_dir, "06_captcha_solved")
            
            # Step 7: Process payment
            logger.info("Processing payment")
            payment_successful = await payment_processor.process_payment(page)
            if not payment_successful:
                logger.error("Payment failed")
                await self._take_screenshot(page, attempt_screenshot_dir, "07_payment_failed")
                return False
            
            await self._take_screenshot(page, attempt_screenshot_dir, "07_payment_completed")
            
            # Step 8: Verify and save booking details
            logger.info("Verifying booking")
            booking_details = await self._extract_booking_details(page)
            
            if booking_details and booking_details.get("confirmation_id"):
                logger.info(f"Booking confirmed! ID: {booking_details['confirmation_id']}")
                await self._take_screenshot(page, attempt_screenshot_dir, "08_booking_confirmed")
                
                # Save booking details
                await self._save_booking_details(event, booking_details, attempt_screenshot_dir)
                
                # Send success notification
                await notification_manager.notify_purchase_success(
                    event,
                    quantity,
                    booking_details.get("total_amount", 0)
                )
                
                return True
            else:
                logger.error("Could not verify booking success")
                await self._take_screenshot(page, attempt_screenshot_dir, "08_verification_failed")
                return False
                
        except Exception as e:
            logger.error(f"Error during purchase flow: {str(e)}")
            await self._take_screenshot(page, attempt_screenshot_dir, "error")
            raise
            
        finally:
            # Close browser context
            await context.close()
    
    async def _handle_authentication(self, page: Page, screenshot_dir: Path) -> bool:
        """
        Handle authentication if needed.
        
        Args:
            page: Page to authenticate on
            screenshot_dir: Directory for screenshots
            
        Returns:
            True if authenticated, False otherwise
        """
        logger.info("Checking authentication status")
        
        # Check if already logged in
        is_logged_in = await auth_manager.check_auth_status(page)
        
        if is_logged_in:
            logger.info("Already logged in")
            return True
        
        logger.info("Not logged in, attempting to authenticate")
        await self._take_screenshot(page, screenshot_dir, "02_before_login")
        
        # Get credentials from config
        credentials = {}
        if config.get("auth.mobile"):
            credentials["mobile"] = config.get("auth.mobile")
        elif config.get("auth.email") and config.get("auth.password"):
            credentials["email"] = config.get("auth.email")
            credentials["password"] = config.get("auth.password")
        else:
            logger.error("No login credentials configured")
            return False
        
        # Log in
        try:
            success = await auth_manager.login(page, credentials, session_id="bookmyshow")
            
            if success:
                logger.info("Login successful")
                await self._take_screenshot(page, screenshot_dir, "02_after_login")
                return True
            else:
                logger.error("Login failed")
                await self._take_screenshot(page, screenshot_dir, "02_login_failed")
                return False
                
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            await self._take_screenshot(page, screenshot_dir, "02_login_error")
            return False
    
    async def _take_screenshot(self, page: Page, directory: Path, name: str) -> None:
        """
        Take a screenshot of the current page state.
        
        Args:
            page: Page to screenshot
            directory: Directory to save screenshot in
            name: Base name for the screenshot
        """
        try:
            screenshot_path = directory / f"{name}.png"
            await page.screenshot(path=str(screenshot_path))
            logger.debug(f"Screenshot saved: {screenshot_path}")
        except Exception as e:
            logger.warning(f"Failed to take screenshot: {str(e)}")
    
    async def _extract_booking_details(self, page: Page) -> Dict[str, Any]:
        """
        Extract booking details from confirmation page.
        
        Args:
            page: Page with booking confirmation
            
        Returns:
            Dictionary with booking details
        """
        details = {
            "confirmation_id": None,
            "event_name": None,
            "total_amount": 0,
            "tickets": [],
            "venue": None,
            "date": None,
            "time": None
        }
        
        try:
            # Check for confirmation indicators
            confirmation_selectors = [
                "text=Booking Confirmed",
                "text=Your transaction is successful",
                ".success-message",
                ".confirmation-message"
            ]
            
            confirmation_found = False
            for selector in confirmation_selectors:
                if await page.is_visible(selector, timeout=1000):
                    confirmation_found = True
                    logger.info(f"Confirmation found with selector: {selector}")
                    break
            
            if not confirmation_found:
                logger.warning("No confirmation indicator found on page")
                return details
            
            # Extract confirmation ID
            id_selectors = [
                ".booking-id",
                "[data-id='booking-id']",
                ".confirmation-id",
                "text=/Booking ID: ([A-Z0-9]+)/"
            ]
            
            for selector in id_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        text = text.strip()
                        
                        # Try to extract just the ID if there's extra text
                        import re
                        id_match = re.search(r'([A-Z0-9]{5,})', text)
                        if id_match:
                            details["confirmation_id"] = id_match.group(1)
                        else:
                            details["confirmation_id"] = text
                        
                        logger.info(f"Extracted confirmation ID: {details['confirmation_id']}")
                        break
                except Exception as e:
                    logger.debug(f"Error extracting confirmation ID with selector {selector}: {str(e)}")
            
            # Extract event name
            name_selectors = [
                ".event-name",
                ".movie-name",
                "h1",
                ".heading-name"
            ]
            
            for selector in name_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        details["event_name"] = await element.text_content()
                        details["event_name"] = details["event_name"].strip()
                        logger.debug(f"Extracted event name: {details['event_name']}")
                        break
                except Exception:
                    pass
            
            # Extract total amount
            amount_selectors = [
                ".total-amount",
                ".amount-paid",
                "text=/Total: ₹([\\d,\\.]+)/",
                "text=/Amount Paid: ₹([\\d,\\.]+)/"
            ]
            
            for selector in amount_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        text = text.strip()
                        
                        # Extract amount
                        import re
                        amount_match = re.search(r'₹\s*([\d,]+(\.\d+)?)', text)
                        if amount_match:
                            amount_str = amount_match.group(1).replace(',', '')
                            details["total_amount"] = float(amount_str)
                            logger.debug(f"Extracted total amount: ₹{details['total_amount']}")
                            break
                except Exception:
                    pass
            
            # Extract venue
            venue_selectors = [
                ".venue-name",
                ".theatre-name",
                "[data-id='venue']"
            ]
            
            for selector in venue_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        details["venue"] = await element.text_content()
                        details["venue"] = details["venue"].strip()
                        logger.debug(f"Extracted venue: {details['venue']}")
                        break
                except Exception:
                    pass
            
            # Extract date and time
            date_selectors = [
                ".date-time",
                ".show-date",
                ".show-time",
                "[data-id='show-date']",
                "[data-id='show-time']"
            ]
            
            for selector in date_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        text = text.strip()
                        
                        # Try to extract date and time
                        import re
                        date_match = re.search(r'(\d{1,2}\s+[A-Za-z]{3}|\d{1,2}/\d{1,2}/\d{2,4})', text)
                        time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))', text)
                        
                        if date_match:
                            details["date"] = date_match.group(1)
                        if time_match:
                            details["time"] = time_match.group(1)
                        
                        logger.debug(f"Extracted date/time: {details['date']} {details['time']}")
                        break
                except Exception:
                    pass
            
            return details
            
        except Exception as e:
            logger.error(f"Error extracting booking details: {str(e)}")
            return details
    
    async def _save_booking_details(self, 
                                  event: Event, 
                                  booking_details: Dict[str, Any],
                                  screenshot_dir: Path) -> None:
        """
        Save booking details to file.
        
        Args:
            event: Event that was booked
            booking_details: Details of the booking
            screenshot_dir: Directory with screenshots
        """
        try:
            # Create bookings directory
            bookings_dir = Path("data/bookings")
            bookings_dir.mkdir(parents=True, exist_ok=True)
            
            # Add event details to booking
            booking_data = {
                "event_id": event.event_id,
                "event_url": event.url,
                "event_name": event.name,
                "booking_time": datetime.now().isoformat(),
                "screenshots_dir": str(screenshot_dir),
                **booking_details
            }
            
            # Save to file
            booking_file = bookings_dir / f"{event.event_id}_{booking_details.get('confirmation_id', 'unknown')}.json"
            
            import json
            with open(booking_file, "w") as f:
                json.dump(booking_data, f, indent=2)
            
            logger.info(f"Booking details saved to {booking_file}")
        except Exception as e:
            logger.error(f"Error saving booking details: {str(e)}")


# Singleton instance
purchase_flow = PurchaseFlow()