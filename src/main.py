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
from pathlib import Path
from typing import Optional, List, Dict, Any

from .config import config
from .utils.logger import logger, get_logger
from .utils.browser_manager import browser_manager
from .monitoring.event_tracker import event_tracker, Event

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
    
    async def initialize(self) -> None:
        """Initialize the bot and its components."""
        if self.initialized:
            return
        
        log.info("Initializing BookMyShow Bot")
        
        # Initialize logger
        logger.initialize()
        
        # Initialize browser manager
        await browser_manager.initialize()
        
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
                             single_run: bool = False) -> List[str]:
        """
        Start monitoring events for ticket availability.
        
        Args:
            event_ids: List of event IDs to monitor, or None for all
            single_run: Whether to run only once instead of continuously
            
        Returns:
            List of event IDs that became available (only for single_run=True)
        """
        if not self.initialized:
            await self.initialize()
        
        def on_ticket_available(event: Event) -> None:
            """Callback when tickets become available."""
            log.info(f"🎉 TICKETS AVAILABLE for {event.name}! 🎉")
            # Here we would normally trigger the ticket purchase process
            # and send notifications
        
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
                
        else:
            # If no command is provided, show help
            parser.print_help()
    
    finally:
        # Always ensure proper shutdown
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())