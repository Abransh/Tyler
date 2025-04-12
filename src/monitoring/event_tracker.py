"""
Event tracking module for the BookMyShow Bot.

This module is responsible for monitoring BookMyShow for specific events,
tracking their availability, and triggering actions when tickets become available.
"""

import json
import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union, Set

from playwright.async_api import Page, Response

from ..config import config
from ..utils.logger import get_logger
from ..utils.browser_manager import browser_manager


logger = get_logger(__name__)


class Event:
    """
    Represents an event being tracked for ticket availability.
    
    Stores event details, availability status, and monitoring settings.
    """
    
    def __init__(self, 
                event_id: str, 
                name: str, 
                url: str,
                venue: str = "",
                city: str = "",
                event_date: Optional[str] = None,
                ticket_price_range: Optional[Tuple[float, float]] = None,
                preferred_seats: Optional[List[str]] = None,
                expected_on_sale_date: Optional[str] = None,
                quantity: int = 1,
                max_price: Optional[float] = None,
                tracking_enabled: bool = True):
        """
        Initialize an event to track.
        
        Args:
            event_id: Unique identifier for the event
            name: Event name
            url: BookMyShow URL for the event
            venue: Event venue
            city: City where the event is taking place
            event_date: Date of the event (YYYY-MM-DD format)
            ticket_price_range: Expected ticket price range (min, max)
            preferred_seats: List of preferred seating areas
            expected_on_sale_date: Expected date tickets go on sale (YYYY-MM-DD format)
            quantity: Number of tickets to purchase
            max_price: Maximum price willing to pay per ticket
            tracking_enabled: Whether tracking is enabled for this event
        """
        self.event_id = event_id
        self.name = name
        self.url = url
        self.venue = venue
        self.city = city
        self.event_date = event_date
        self.ticket_price_range = ticket_price_range
        self.preferred_seats = preferred_seats or []
        self.expected_on_sale_date = expected_on_sale_date
        self.quantity = quantity
        self.max_price = max_price
        self.tracking_enabled = tracking_enabled
        
        # Status tracking
        self.tickets_available = False
        self.last_checked = None
        self.last_available = None
        self.check_count = 0
        self.sold_out = False
        self.error_count = 0
        self.last_error = None
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Event':
        """
        Create an Event from a dictionary.
        
        Args:
            data: Dictionary containing event data
            
        Returns:
            Event instance
        """
        return cls(
            event_id=data.get("event_id", ""),
            name=data.get("name", ""),
            url=data.get("url", ""),
            venue=data.get("venue", ""),
            city=data.get("city", ""),
            event_date=data.get("event_date"),
            ticket_price_range=tuple(data.get("ticket_price_range", (0, 0))),
            preferred_seats=data.get("preferred_seats", []),
            expected_on_sale_date=data.get("expected_on_sale_date"),
            quantity=data.get("quantity", 1),
            max_price=data.get("max_price"),
            tracking_enabled=data.get("tracking_enabled", True)
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the event to a dictionary.
        
        Returns:
            Dictionary representation of the event
        """
        return {
            "event_id": self.event_id,
            "name": self.name,
            "url": self.url,
            "venue": self.venue,
            "city": self.city,
            "event_date": self.event_date,
            "ticket_price_range": self.ticket_price_range,
            "preferred_seats": self.preferred_seats,
            "expected_on_sale_date": self.expected_on_sale_date,
            "quantity": self.quantity,
            "max_price": self.max_price,
            "tracking_enabled": self.tracking_enabled,
            "tickets_available": self.tickets_available,
            "last_checked": self.last_checked,
            "last_available": self.last_available,
            "check_count": self.check_count,
            "sold_out": self.sold_out,
            "error_count": self.error_count,
            "last_error": self.last_error
        }
    
    def update_status(self, 
                     tickets_available: bool, 
                     error: Optional[str] = None) -> None:
        """
        Update the event's status.
        
        Args:
            tickets_available: Whether tickets are available
            error: Error message if an error occurred during checking
        """
        self.last_checked = datetime.now().isoformat()
        self.check_count += 1
        
        if error:
            self.error_count += 1
            self.last_error = error
            return
        
        # Update availability
        self.tickets_available = tickets_available
        
        if tickets_available:
            self.last_available = datetime.now().isoformat()


class EventTracker:
    """
    Tracks events and their ticket availability on BookMyShow.
    
    Manages a list of events to monitor, checks their status,
    and provides notifications when tickets become available.
    """
    
    def __init__(self):
        """Initialize the event tracker."""
        self.events: Dict[str, Event] = {}
        self.events_path = Path(config.get("events.events_path", "data/events/tracked_events.json"))
        self.base_url = config.get("bookmyshow.base_url", "https://in.bookmyshow.com")
        self.api_base_url = config.get("bookmyshow.api_base_url", "https://api.bookmyshow.com")
        self.regions = config.get("bookmyshow.regions", ["NCR", "Mumbai", "Bengaluru"])
        
        # Monitoring settings
        self.interval = config.get("monitoring.interval", 60)
        self.accelerated_interval = config.get("monitoring.accelerated_interval", 5)
        self.acceleration_threshold = config.get("monitoring.acceleration_threshold", 30)
        
        # Load tracked events
        self._load_events()
    
    def _load_events(self) -> None:
        """
        Load tracked events from disk.
        """
        # Ensure the directory exists
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        
        if not self.events_path.exists():
            logger.info("No tracked events file found, starting with empty list")
            return
        
        try:
            with open(self.events_path, "r") as f:
                event_data = json.load(f)
            
            for event_id, event_dict in event_data.items():
                self.events[event_id] = Event.from_dict(event_dict)
            
            logger.info(f"Loaded {len(self.events)} tracked events")
        except Exception as e:
            logger.error(f"Failed to load tracked events: {e}")
    
    def _save_events(self) -> None:
        """
        Save tracked events to disk.
        """
        # Ensure the directory exists
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            event_data = {event_id: event.to_dict() for event_id, event in self.events.items()}
            
            with open(self.events_path, "w") as f:
                json.dump(event_data, f, indent=2)
            
            logger.debug(f"Saved {len(self.events)} tracked events")
        except Exception as e:
            logger.error(f"Failed to save tracked events: {e}")
    
    def add_event(self, event: Event) -> None:
        """
        Add an event to track.
        
        Args:
            event: Event to track
        """
        self.events[event.event_id] = event
        logger.info(f"Added event to tracking: {event.name} ({event.event_id})")
        self._save_events()
    
    def remove_event(self, event_id: str) -> bool:
        """
        Remove an event from tracking.
        
        Args:
            event_id: ID of the event to remove
            
        Returns:
            True if the event was removed, False if not found
        """
        if event_id in self.events:
            event = self.events.pop(event_id)
            logger.info(f"Removed event from tracking: {event.name} ({event_id})")
            self._save_events()
            return True
        
        return False
    
    def get_event(self, event_id: str) -> Optional[Event]:
        """
        Get an event by ID.
        
        Args:
            event_id: ID of the event to get
            
        Returns:
            Event if found, None otherwise
        """
        return self.events.get(event_id)
    
    def get_all_events(self) -> List[Event]:
        """
        Get all tracked events.
        
        Returns:
            List of tracked events
        """
        return list(self.events.values())
    
    def get_available_events(self) -> List[Event]:
        """
        Get all events with available tickets.
        
        Returns:
            List of events with available tickets
        """
        return [event for event in self.events.values() 
                if event.tickets_available and event.tracking_enabled]
    
    async def check_event(self, event: Event) -> bool:
        """
        Check if tickets are available for an event.
        
        Args:
            event: Event to check
            
        Returns:
            True if tickets are available, False otherwise
        """
        if not event.tracking_enabled:
            logger.debug(f"Skipping check for disabled event: {event.name}")
            return False
        
        logger.info(f"Checking ticket availability for event: {event.name}")
        
        # Initialize the browser if needed
        await browser_manager.initialize()
        
        try:
            # Create a new browser context and page
            context = await browser_manager.create_context()
            page = await browser_manager.new_page(context)
            
            # Navigate to the event page
            await browser_manager.navigate(page, event.url)
            
            # Check if tickets are available
            available = await self._check_page_for_availability(page, event)
            
            # Update the event status
            event.update_status(available)
            
            # Close the page and context
            await page.close()
            await context.close()
            
            # Log result
            if available:
                logger.info(f"Tickets available for event: {event.name}")
            else:
                logger.info(f"No tickets available for event: {event.name}")
            
            return available
        except Exception as e:
            error_msg = f"Error checking event {event.name}: {str(e)}"
            logger.error(error_msg)
            event.update_status(False, error=error_msg)
            return False
        finally:
            # Save updated event data
            self._save_events()
    
    async def _check_page_for_availability(self, page: Page, event: Event) -> bool:
        """
        Check a page for ticket availability.
        
        Args:
            page: Page to check
            event: Event being checked
            
        Returns:
            True if tickets are available, False otherwise
        """
        # Check for common indicators of ticket availability
        
        # Method 1: Check for "Book tickets" button
        book_button_selector = "button:has-text('Book tickets'), button:has-text('Book now'), a:has-text('Book tickets'), a:has-text('Book now')"
        has_book_button = await page.is_visible(book_button_selector, timeout=5000)
        
        # Method 2: Check for "Sold out" indicators
        sold_out_selector = "text='Sold out', text='All full', text='No tickets available'"
        is_sold_out = await page.is_visible(sold_out_selector, timeout=5000)
        
        # Method 3: Check for ticket selection elements
        ticket_selection_selector = ".TicketCategories, .seating-layout, .ticket-types"
        has_ticket_selection = await page.is_visible(ticket_selection_selector, timeout=5000)
        
        # Update sold out status
        event.sold_out = is_sold_out
        
        # Log findings
        logger.debug(f"Availability check for {event.name}: Book button: {has_book_button}, Sold out: {is_sold_out}, Ticket selection: {has_ticket_selection}")
        
        # Determine availability based on findings
        # Tickets are available if we found a book button or ticket selection and no sold out message
        return (has_book_button or has_ticket_selection) and not is_sold_out
    
    async def monitor_events(self, 
                            event_ids: Optional[List[str]] = None, 
                            single_run: bool = False,
                            notification_callback: Optional[callable] = None) -> None:
        """
        Monitor events for ticket availability.
        
        Args:
            event_ids: IDs of events to monitor, or None to monitor all
            single_run: Run the monitoring loop only once
            notification_callback: Function to call when tickets become available
        """
        events_to_monitor = []
        
        if event_ids:
            events_to_monitor = [self.events[event_id] for event_id in event_ids 
                             if event_id in self.events]
        else:
            events_to_monitor = list(self.events.values())
        
        if not events_to_monitor:
            logger.warning("No events to monitor")
            return
        
        events_to_monitor = [e for e in events_to_monitor if e.tracking_enabled]
        
        if not events_to_monitor:
            logger.warning("No enabled events to monitor")
            return
        
        logger.info(f"Starting to monitor {len(events_to_monitor)} events")
        
        newly_available_events: Set[str] = set()
        
        while True:
            start_time = time.time()
            
            for event in events_to_monitor:
                # Determine if we should use accelerated polling
                use_accelerated = False
                
                if event.expected_on_sale_date:
                    try:
                        sale_date = datetime.fromisoformat(event.expected_on_sale_date)
                        time_until_sale = sale_date - datetime.now()
                        
                        if timedelta(minutes=0) <= time_until_sale <= timedelta(minutes=self.acceleration_threshold):
                            use_accelerated = True
                            logger.info(f"Using accelerated polling for event {event.name} - {time_until_sale.total_seconds()//60:.0f} minutes until sale")
                    except ValueError:
                        # Invalid date format
                        pass
                
                # Check the event
                was_available = event.tickets_available
                available = await self.check_event(event)
                
                # If tickets just became available
                if available and not was_available:
                    logger.info(f"Tickets just became available for event: {event.name}")
                    newly_available_events.add(event.event_id)
                    
                    # Call notification callback if provided
                    if notification_callback:
                        try:
                            notification_callback(event)
                        except Exception as e:
                            logger.error(f"Error in notification callback: {e}")
                
                # Brief pause between checks to avoid overloading the site
                await asyncio.sleep(2)
            
            if single_run:
                break
            
            # Calculate remaining time to sleep
            elapsed = time.time() - start_time
            sleep_time = self.accelerated_interval if use_accelerated else self.interval
            
            if elapsed < sleep_time:
                await asyncio.sleep(sleep_time - elapsed)
        
        # Return newly available events
        return list(newly_available_events)


# Singleton instance
event_tracker = EventTracker()