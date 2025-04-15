"""
Main entry point for the BookMyShow Ticket Bot.

This module initializes the application, sets up components,
and provides the main execution flow.
"""

import os
import sys
import asyncio
import argparse
import signal
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from .config import config
from .utils.logger import logger, get_logger
from .utils.browser_manager import browser_manager
from .utils.captcha_solver import captcha_solver
from .utils.proxy_manager import proxy_manager
from .monitoring.event_tracker import event_tracker, Event
from .auth.login import auth_manager
from .ticket.selector import ticket_selector
from .payment.gift_card import payment_processor
from .notification.alerts import notification_manager
from .tasks.purchase_flow import purchase_flow
from .tasks.scheduler import scheduler_manager


# Set up logger
log = get_logger(__name__)


class BookMyShowBot:
    """
    Main application class for the BookMyShow Ticket Bot.
    
    Coordinates the different components and provides the main
    execution flow for monitoring and purchasing tickets.
    """
    
    def __init__(self):
        """Initialize the bot."""
        self.initialized = False
        self.running = False
        self.shutdown_requested = False
        self.purchase_in_progress = False
    
    async def initialize(self) -> None:
        """Initialize the bot and its components."""
        if self.initialized:
            return
        
        log.info("Initializing BookMyShow Bot")
        
        # Initialize logger
        logger.initialize()
        
        # Initialize browser manager
        await browser_manager.initialize()
        
        # Initialize proxy manager if enabled
        if config.get("proxy.enabled", False):
            log.info("Proxy support enabled")
            # Proxy manager is initialized when imported
        
        # Initialize scheduler if enabled
        if config.get("scheduler.enabled", False):
            log.info("Scheduler support enabled")
            scheduler_manager.initialize()
        
        # Event tracker is initialized when imported
        
        self.initialized = True
        log.info("BookMyShow Bot initialized")
    
    async def add_event(self, 
                       url: str,
                       name: Optional[str] = None,
                       event_date: Optional[str] = None,
                       expected_on_sale_date: Optional[str] = None,
                       quantity: int = 2) -> Event:
        """
        Add an event to track.
        
        Args:
            url: BookMyShow URL for the event
            name: Event name (optional, will be extracted from page if not provided)
            event_date: Date of the event (YYYY-MM-DD format)
            expected_on_sale_date: Expected date tickets go on sale (YYYY-MM-DD format)
            quantity: Number of tickets to purchase
            
        Returns:
            The created Event
        """
        if not self.initialized:
            await self.initialize()
        
        # Extract event ID from URL
        event_id = self._extract_event_id(url)
        
        # If name not provided, try to extract it from the page
        if not name:
            name = await self._extract_event_name(url)
        
        # Create the event
        event = Event(
            event_id=event_id,
            name=name,
            url=url,
            event_date=event_date,
            expected_on_sale_date=expected_on_sale_date,
            quantity=quantity
        )
        
        # Add it to the tracker
        event_tracker.add_event(event)
        
        return event
    
    def _extract_event_id(self, url: str) -> str:
        """
        Extract event ID from BookMyShow URL.
        
        Args:
            url: BookMyShow URL
            
        Returns:
            Event ID
        """
        # Try to extract from URL
        import re
        
        # Pattern examples:
        # - https://in.bookmyshow.com/events/event-name/ET00123456
        # - https://in.bookmyshow.com/buytickets/event-name/ET00123456
        # - https://in.bookmyshow.com/NCR/event-name/ET00123456
        
        patterns = [
            r'/([A-Z]{2}\d{8})(?:/|$)',  # Standard event ID format
            r'eventCode=([A-Z]{2}\d{8})' # Query parameter format
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # If no ID found, generate one based on URL hash
        import hashlib
        return f"GEN{hashlib.md5(url.encode()).hexdigest()[:8].upper()}"
    
    async def _extract_event_name(self, url: str) -> str:
        """
        Extract event name from BookMyShow page.
        
        Args:
            url: BookMyShow URL
            
        Returns:
            Event name or placeholder if not found
        """
        try:
            context = await browser_manager.create_context()
            page = await browser_manager.new_page(context)
            
            await browser_manager.navigate(page, url)
            
            # Try various selectors to find the event name
            selectors = [
                "h1", 
                ".event-title", 
                ".movie-name",
                ".event-name",
                ".heading-name",
                "title"
            ]
            
            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        name = await element.text_content()
                        name = name.strip()
                        if name and "bookmyshow" not in name.lower():
                            await page.close()
                            await context.close()
                            return name
                except Exception:
                    continue
            
            # If event name couldn't be extracted, use the URL
            await page.close()
            await context.close()
            
            # Extract name from URL path
            import re
            match = re.search(r'/([^/]+)/[A-Z]{2}\d{8}', url)
            if match:
                name = match.group(1).replace("-", " ").title()
                return name
            
            return "Unnamed Event"
        except Exception as e:
            log.error(f"Error extracting event name: {e}")
            return "Unnamed Event"
    
    async def start_monitoring(self, 
                             event_ids: Optional[List[str]] = None,
                             single_run: bool = False,
                             interval: int = 60,
                             use_scheduler: bool = False) -> List[str]:
        """
        Start monitoring events for ticket availability.
        
        Args:
            event_ids: List of event IDs to monitor, or None for all
            single_run: Whether to run only once instead of continuously
            interval: Monitoring interval in seconds (for scheduled monitoring)
            use_scheduler: Whether to use the scheduler for monitoring
            
        Returns:
            List of event IDs that became available (only for single_run=True)
        """
        if not self.initialized:
            await self.initialize()
        
        async def on_ticket_available(event: Event) -> None:
            """Callback when tickets become available."""
            log.info(f"🎉 TICKETS AVAILABLE for {event.name}! 🎉")
            
            # Send notification
            await notification_manager.notify_ticket_available(event)
            
            # Start purchase process if enabled and not already in progress
            auto_purchase = config.get("purchase.auto_purchase", True)
            if auto_purchase and not self.purchase_in_progress and not event.sold_out:
                log.info(f"Auto-purchase enabled, initiating purchase for {event.name}")
                asyncio.create_task(self.purchase_tickets(event))
            elif not auto_purchase:
                log.info(f"Auto-purchase disabled, not purchasing tickets for {event.name}")
        
        # If using scheduler
        if use_scheduler and not single_run:
            log.info(f"Setting up scheduled monitoring (interval: {interval}s)")
            if event_ids:
                log.info(f"Monitoring events: {', '.join(event_ids)}")
            else:
                log.info("Monitoring all events")
                
            # Initialize and start scheduler
            scheduler_manager.initialize()
            job_id = scheduler_manager.schedule_regular_monitoring(
                interval=interval,
                event_ids=event_ids,
                job_id="monitor_command"
            )
            
            # Keep the scheduler running until interrupted
            self.running = True
            self.shutdown_requested = False
            
            # Set up signal handlers for graceful shutdown
            self._setup_signal_handlers()
            
            try:
                log.info(f"Scheduled monitoring active with job ID: {job_id}")
                log.info("Press Ctrl+C to stop monitoring")
                
                # Wait until shutdown is requested
                while not self.shutdown_requested:
                    await asyncio.sleep(1)
                
                self.running = False
                return []
                
            except asyncio.CancelledError:
                log.info("Monitoring cancelled")
                scheduler_manager.remove_job(job_id)
                self.running = False
                return []
        
        # If not using scheduler, run directly
        self.running = True
        self.shutdown_requested = False
        
        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        try:
            log.info("Starting event monitoring")
            newly_available = await event_tracker.monitor_events(
                event_ids=event_ids,
                single_run=single_run,
                notification_callback=on_ticket_available
            )
            
            self.running = False
            return newly_available
        
        except asyncio.CancelledError:
            log.info("Event monitoring cancelled")
            self.running = False
            return []
        
        except Exception as e:
            log.error(f"Error during event monitoring: {e}")
            self.running = False
            return []
        

    async def purchase_tickets(self, event: Event, quantity: Optional[int] = None) -> bool:
        """
        Purchase tickets for an event.
        
        Args:
            event: Event to purchase tickets for
            quantity: Number of tickets to purchase, or None to use event default
            
        Returns:
            True if purchase was successful, False otherwise
        """
        if self.purchase_in_progress:
            log.warning("Another purchase is already in progress, skipping")
            return False
        
        self.purchase_in_progress = True
        
        try:
            log.info(f"Starting ticket purchase for {event.name}")
            
            # Use event quantity if not specified
            if quantity is None:
                quantity = event.quantity
            
            # Execute the purchase flow
            success = await purchase_flow.execute_purchase(event, quantity)
            
            if success:
                log.info(f"Purchase successful for {event.name}!")
            else:
                log.error(f"Purchase failed for {event.name}")
            
            return success
            
        except Exception as e:
            log.error(f"Error during ticket purchase: {e}")
            await notification_manager.notify_purchase_failed(event, f"Unexpected error: {str(e)}")
            return False
        
        finally:
            self.purchase_in_progress = False
            
            # Notify that purchase process is starting
            await notification_manager.notify_purchase_started(event, event.quantity)
            
            # Initialize browser
            context = await browser_manager.create_context(load_session=True, session_id="bookmyshow")
            page = await browser_manager.new_page(context)
            
            # Navigate to event page
            await browser_manager.navigate(page, event.url)
            
            # Step 1: Login if needed
            is_logged_in = await auth_manager.check_auth_status(page)
            if not is_logged_in:
                log.info("Not logged in, authenticating")
                try:
                    logged_in = await auth_manager.login(page, credentials, session_id="bookmyshow")
                    if not logged_in:
                        log.error("Failed to log in")
                        await notification_manager.notify_purchase_failed(event, "Authentication failed")
                        return False
                except Exception as e:
                    log.error(f"Login error: {e}")
                    await notification_manager.notify_purchase_failed(event, f"Authentication error: {str(e)}")
                    return False
            
            # Step 2: Navigate to ticket selection and select tickets
            log.info("Selecting tickets")
            try:
                tickets_selected = await ticket_selector.select_tickets(page, event.url)
                if not tickets_selected:
                    log.error("Failed to select tickets")
                    await notification_manager.notify_purchase_failed(event, "Ticket selection failed")
                    return False
            except Exception as e:
                log.error(f"Ticket selection error: {e}")
                await notification_manager.notify_purchase_failed(event, f"Ticket selection error: {str(e)}")
                return False
            
            # Step 3: Process payment
            log.info("Processing payment")
            try:
                payment_successful = await payment_processor.process_payment(page)
                if not payment_successful:
                    log.error("Payment failed")
                    await notification_manager.notify_purchase_failed(event, "Payment processing failed")
                    return False
            except Exception as e:
                log.error(f"Payment error: {e}")
                await notification_manager.notify_purchase_failed(event, f"Payment error: {str(e)}")
                return False
            
            # Step 4: Verify success and save tickets
            log.info("Verifying purchase")
            try:
                # Look for confirmation indicators
                confirmation = await page.query_selector("text=Booking Confirmed, text=Your transaction is successful")
                if confirmation:
                    # Try to get booking ID
                    booking_id = "Unknown"
                    booking_id_element = await page.query_selector(".booking-id, [data-id='booking-id']")
                    if booking_id_element:
                        booking_id = await booking_id_element.text_content()
                        booking_id = booking_id.strip()
                    
                    # Get total amount if available
                    total_amount = 0.0
                    amount_element = await page.query_selector(".total-amount, .amount-paid")
                    if amount_element:
                        amount_text = await amount_element.text_content()
                        import re
                        amount_match = re.search(r'₹\s*([\d,]+(\.\d+)?)', amount_text)
                        if amount_match:
                            amount_str = amount_match.group(1).replace(',', '')
                            total_amount = float(amount_str)
                    
                    # Take screenshot of confirmation
                    screenshot_dir = Path("data/screenshots")
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = screenshot_dir / f"confirmation_{event.event_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=str(screenshot_path))
                    log.info(f"Saved confirmation screenshot to {screenshot_path}")
                    
                    # Send success notification
                    await notification_manager.notify_purchase_success(
                        event, 
                        event.quantity, 
                        total_amount
                    )
                    
                    log.info(f"Purchase successful! Booking ID: {booking_id}")
                    return True
                else:
                    log.warning("Could not verify purchase success")
                    await notification_manager.notify_purchase_failed(event, "Could not verify purchase success")
                    return False
            except Exception as e:
                log.error(f"Verification error: {e}")
                await notification_manager.notify_purchase_failed(event, f"Verification error: {str(e)}")
                return False
    
    
    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()
        
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self.shutdown())
            )
    
    async def shutdown(self) -> None:
        """Gracefully shut down the bot."""
        if self.shutdown_requested:
            return
        
        self.shutdown_requested = True
        log.info("Shutting down BookMyShow Bot")
        
        # Close browser
        await browser_manager.close()
        
        self.running = False
        log.info("BookMyShow Bot shutdown complete")

    async def add_gift_card(self, card_number: str, pin: str, balance: float = 0.0) -> None:
        """
        Add a gift card for payment.
        
        Args:
            card_number: Gift card number
            pin: Gift card PIN
            balance: Current balance (if known)
        """
        payment_processor.add_gift_card(card_number, pin, balance)
        log.info(f"Added gift card ending in ...{card_number[-4:]}")


async def main() -> None:
    """Main entry point for the application."""
    parser = argparse.ArgumentParser(description="BookMyShow Ticket Bot")
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Add event command
    add_parser = subparsers.add_parser("add", help="Add an event to track")
    add_parser.add_argument("url", help="BookMyShow event URL")
    add_parser.add_argument("--name", help="Event name")
    add_parser.add_argument("--date", help="Event date (YYYY-MM-DD)")
    add_parser.add_argument("--sale-date", help="Expected on-sale date (YYYY-MM-DD)")
    add_parser.add_argument("--quantity", type=int, default=2, help="Number of tickets to purchase")
    
    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Monitor events for ticket availability")
    monitor_parser.add_argument("--event", dest="event_ids", action="append", help="Event ID to monitor (can be used multiple times)")
    monitor_parser.add_argument("--once", action="store_true", help="Run monitoring only once instead of continuously")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List tracked events")
    
    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove an event from tracking")
    remove_parser.add_argument("event_id", help="Event ID to remove")
    
    # Gift card commands
    gift_card_parser = subparsers.add_parser("gift-card", help="Manage gift cards")
    gift_card_subparsers = gift_card_parser.add_subparsers(dest="gift_card_command", help="Gift card command")
    
    # Add gift card
    add_card_parser = gift_card_subparsers.add_parser("add", help="Add a gift card")
    add_card_parser.add_argument("number", help="Gift card number")
    add_card_parser.add_argument("pin", help="Gift card PIN")
    add_card_parser.add_argument("--balance", type=float, default=0.0, help="Current balance")
    
    # List gift cards
    list_cards_parser = gift_card_subparsers.add_parser("list", help="List available gift cards")
    
    # Purchase command
    purchase_parser = subparsers.add_parser("purchase", help="Purchase tickets for an event")
    purchase_parser.add_argument("event_id", help="Event ID to purchase")
    
    # Parse arguments
    args = parser.parse_args()
    
    # Create bot
    bot = BookMyShowBot()
    await bot.initialize()
    
    try:
        if args.command == "add":
            event = await bot.add_event(
                url=args.url,
                name=args.name,
                event_date=args.date,
                expected_on_sale_date=args.sale_date,
                quantity=args.quantity
            )
            print(f"Added event: {event.name} ({event.event_id})")
            
        elif args.command == "monitor":
            print("Starting event monitoring...")
            if args.once:
                newly_available = await bot.start_monitoring(
                    event_ids=args.event_ids,
                    single_run=True
                )
                if newly_available:
                    print(f"Found {len(newly_available)} newly available events!")
                else:
                    print("No new available events found")
            else:
                # Continuous monitoring until interrupted
                await bot.start_monitoring(event_ids=args.event_ids)
            
        elif args.command == "list":
            events = event_tracker.get_all_events()
            if not events:
                print("No events are being tracked")
            else:
                print(f"Tracking {len(events)} events:")
                for event in events:
                    status = "🟢 AVAILABLE" if event.tickets_available else "🔴 NOT AVAILABLE"
                    status = "⚫️ SOLD OUT" if event.sold_out else status
                    status = "⚪️ NOT ENABLED" if not event.tracking_enabled else status
                    
                    print(f"- {event.name} ({event.event_id}): {status}")
                    print(f"  URL: {event.url}")
                    print(f"  Last checked: {event.last_checked or 'Never'}")
                    if event.last_available:
                        print(f"  Last available: {event.last_available}")
                    print()
            
        elif args.command == "remove":
            success = event_tracker.remove_event(args.event_id)
            if success:
                print(f"Removed event {args.event_id}")
            else:
                print(f"Event {args.event_id} not found")
        
        elif args.command == "gift-card":
            if args.gift_card_command == "add":
                await bot.add_gift_card(args.number, args.pin, args.balance)
                print(f"Added gift card ending in ...{args.number[-4:]}")
            
            elif args.gift_card_command == "list":
                cards = payment_processor.gift_cards
                if not cards:
                    print("No gift cards available")
                else:
                    print(f"Available gift cards ({len(cards)}):")
                    for i, card in enumerate(cards, 1):
                        masked_number = f"{'*' * (len(card.card_number) - 4)}{card.card_number[-4:]}"
                        print(f"{i}. {masked_number} - Balance: ₹{card.balance:.2f}")
                        if card.last_used:
                            print(f"   Last used: {card.last_used}")
            else:
                gift_card_parser.print_help()
        
        elif args.command == "purchase":
            event = event_tracker.get_event(args.event_id)
            if not event:
                print(f"Event {args.event_id} not found")
            else:
                print(f"Attempting to purchase tickets for: {event.name}")
                success = await bot.purchase_tickets(event)
                if success:
                    print("Purchase successful! 🎉")
                else:
                    print("Purchase failed. Check logs for details.")
                
        else:
            # If no command is provided, show help
            parser.print_help()
    
    finally:
        # Always ensure proper shutdown
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())