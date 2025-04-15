"""
Ticket selection module for the BookMyShow Bot.

This module handles finding and selecting the best available tickets
based on user preferences and venue layout.
"""

import re
import json
import asyncio
import time
import random
from typing import Dict, List, Optional, Tuple, Any, Set, Union

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from ..config import config
from ..utils.logger import get_logger
from ..utils.browser_manager import browser_manager


logger = get_logger(__name__)


class TicketSelectionError(Exception):
    """Exception raised for ticket selection errors."""
    pass


class TicketCategory:
    """Represents a ticket category/section at a venue."""
    
    def __init__(self, 
                name: str, 
                price: float, 
                availability: int = 0, 
                element_id: Optional[str] = None,
                selector: Optional[str] = None):
        """
        Initialize a ticket category.
        
        Args:
            name: Name of the category (e.g., "GOLD", "SILVER")
            price: Price per ticket
            availability: Number of available tickets (0 if unknown)
            element_id: ID of the HTML element
            selector: CSS selector for the element
        """
        self.name = name
        self.price = price
        self.availability = availability
        self.element_id = element_id
        self.selector = selector
    
    def __str__(self) -> str:
        """
        String representation of the ticket category.
        
        Returns:
            String representation
        """
        return f"{self.name} (₹{self.price:.2f}) - {self.availability} available"
    
    def matches_preference(self, preferences: List[str]) -> bool:
        """
        Check if this category matches any of the preferred categories.
        
        Args:
            preferences: List of preferred category names
            
        Returns:
            True if matches, False otherwise
        """
        name_upper = self.name.upper()
        for pref in preferences:
            if pref.upper() in name_upper or name_upper in pref.upper():
                return True
        return False


class Seat:
    """Represents an individual seat at a venue."""
    
    def __init__(self, 
                row: str, 
                number: str, 
                price: float, 
                available: bool = True,
                category: Optional[str] = None,
                element_id: Optional[str] = None,
                selector: Optional[str] = None):
        """
        Initialize a seat.
        
        Args:
            row: Row identifier
            number: Seat number
            price: Price of the seat
            available: Whether the seat is available
            category: Ticket category this seat belongs to
            element_id: ID of the HTML element
            selector: CSS selector for the element
        """
        self.row = row
        self.number = number
        self.price = price
        self.available = available
        self.category = category
        self.element_id = element_id
        self.selector = selector
    
    def __str__(self) -> str:
        """
        String representation of the seat.
        
        Returns:
            String representation
        """
        status = "Available" if self.available else "Not Available"
        return f"{self.row}{self.number} - ₹{self.price:.2f} ({status})"


class TicketSelector:
    """
    Handles ticket selection on BookMyShow.
    
    Supports different venue types, including events with general admission,
    reserved seating, and multiple ticket categories.
    """
    
    def __init__(self):
        """Initialize the ticket selector."""
        # Load preferences from config
        self.max_price = config.get("ticket.max_price", 1500)
        self.min_price = config.get("ticket.min_price", 0)
        self.desired_quantity = config.get("ticket.quantity", 2)
        self.max_quantity = config.get("ticket.max_quantity", 4)
        self.preferred_areas = config.get("ticket.preferred_areas", ["GOLD", "SILVER", "PREMIUM"])
        self.adjacent_seats_only = config.get("ticket.adjacent_seats_only", True)
        self.auto_select_best = config.get("ticket.auto_select_best", True)
    
    async def navigate_to_ticket_selection(self, page: Page, event_url: str) -> bool:
        """
        Navigate to the ticket selection page.
        
        Args:
            page: Page object
            event_url: URL of the event
            
        Returns:
            True if navigation successful, False otherwise
        """
        try:
            logger.info(f"Navigating to event page: {event_url}")
            await browser_manager.navigate(page, event_url)
            
            # Look for book tickets button
            book_button_selectors = [
                "button:has-text('Book tickets')",
                "button:has-text('Book now')",
                "a:has-text('Book tickets')",
                "a:has-text('Book now')"
            ]
            
            for selector in book_button_selectors:
                if await page.is_visible(selector, timeout=3000):
                    await browser_manager.click(page, selector)
                    logger.info(f"Clicked booking button using selector: {selector}")
                    
                    # Wait for ticket selection page to load
                    await page.wait_for_load_state("networkidle")
                    return True
            
            logger.warning("Could not find booking button")
            return False
            
        except Exception as e:
            logger.error(f"Failed to navigate to ticket selection: {str(e)}")
            return False
    
    async def analyze_ticket_options(self, page: Page) -> Dict[str, Any]:
        """
        Analyze the ticket selection page to determine layout and options.
        
        Args:
            page: Page object
            
        Returns:
            Dictionary with ticket page information
        """
        logger.info("Analyzing ticket options")
        
        # Determine the type of ticket selection page
        page_info = {
            "has_categories": False,
            "is_reserved_seating": False,
            "categories": [],
            "available_count": 0,
            "max_price": 0,
            "min_price": float('inf')
        }
        
        # Check for category-based ticket selection
        category_selectors = [
            ".TicketCategories",
            ".ticket-types",
            ".ticket-categories",
            "[data-id='ticket-categories']"
        ]
        
        for selector in category_selectors:
            if await page.is_visible(selector, timeout=1000):
                page_info["has_categories"] = True
                categories = await self._extract_ticket_categories(page)
                page_info["categories"] = categories
                break
        
        # Check for seat selection interface
        seat_selection_selectors = [
            ".seating-layout",
            ".seat-layout",
            ".venue-map"
        ]
        
        for selector in seat_selection_selectors:
            if await page.is_visible(selector, timeout=1000):
                page_info["is_reserved_seating"] = True
                break
        
        # Calculate available ticket count and price range
        if page_info["has_categories"]:
            total_available = sum(cat.availability for cat in page_info["categories"] if cat.availability > 0)
            page_info["available_count"] = total_available
            
            prices = [cat.price for cat in page_info["categories"]]
            if prices:
                page_info["max_price"] = max(prices)
                page_info["min_price"] = min(prices)
        
        logger.info(f"Ticket page analysis: {json.dumps({k: v for k, v in page_info.items() if k != 'categories'})}")
        if page_info["categories"]:
            logger.info(f"Found {len(page_info['categories'])} ticket categories")
            for cat in page_info["categories"]:
                logger.info(f"  - {cat}")
        
        return page_info
    
    async def _extract_ticket_categories(self, page: Page) -> List[TicketCategory]:
        """
        Extract ticket categories from the page.
        
        Args:
            page: Page object
            
        Returns:
            List of ticket categories
        """
        categories = []
        
        try:
            # Try several possible selectors for category elements
            category_item_selectors = [
                ".TicketCategories__list li",
                ".ticket-types li",
                ".ticket-category-item",
                "[data-id^='category-']"
            ]
            
            for selector in category_item_selectors:
                category_elements = await page.query_selector_all(selector)
                if category_elements:
                    logger.debug(f"Found {len(category_elements)} ticket categories with selector: {selector}")
                    
                    for element in category_elements:
                        try:
                            # Extract category name
                            name_element = await element.query_selector("h2, .category-name, .name")
                            name = await name_element.text_content() if name_element else "Unknown"
                            name = name.strip()
                            
                            # Extract price
                            price_text = await element.text_content()
                            price_match = re.search(r'₹\s*([\d,]+)', price_text)
                            price = 0
                            if price_match:
                                price_str = price_match.group(1).replace(',', '')
                                price = float(price_str)
                            
                            # Extract availability if present
                            availability = 0
                            availability_element = await element.query_selector(".availability, .available-count")
                            if availability_element:
                                availability_text = await availability_element.text_content()
                                availability_match = re.search(r'(\d+)', availability_text)
                                if availability_match:
                                    availability = int(availability_match.group(1))
                            
                            # Get element ID for later selection
                            element_id = await element.get_attribute("id") or ""
                            
                            # Create category and add to list
                            category = TicketCategory(
                                name=name,
                                price=price,
                                availability=availability,
                                element_id=element_id,
                                selector=selector
                            )
                            categories.append(category)
                            
                        except Exception as e:
                            logger.warning(f"Error extracting category details: {str(e)}")
                    
                    # If we found categories, break the loop
                    if categories:
                        break
            
            if not categories:
                logger.warning("Could not extract ticket categories")
                
            return categories
                
        except Exception as e:
            logger.error(f"Error extracting ticket categories: {str(e)}")
            return []
    
    async def select_tickets(self, page: Page, event_url: str) -> bool:
        """
        Select tickets for an event.
        
        Args:
            page: Page object
            event_url: URL of the event
            
        Returns:
            True if tickets were successfully selected, False otherwise
        """
        try:
            logger.info("Starting ticket selection process")
            
            # Navigate to ticket selection if needed
            if "buytickets" not in page.url:
                success = await self.navigate_to_ticket_selection(page, event_url)
                if not success:
                    logger.error("Failed to navigate to ticket selection")
                    return False
            
            # Analyze the ticket page
            ticket_page_info = await self.analyze_ticket_options(page)
            
            # Start the actual ticket selection
            if ticket_page_info["has_categories"]:
                # For category-based selection
                success = await self._select_tickets_by_category(page, ticket_page_info["categories"])
            elif ticket_page_info["is_reserved_seating"]:
                # For reserved seating
                success = await self._select_reserved_seats(page)
            else:
                # For simple quantity selection
                success = await self._select_ticket_quantity(page)
            
            if not success:
                logger.error("Failed to select tickets")
                return False
            
            # Proceed to the next step (usually checkout)
            return await self._proceed_to_next_step(page)
            
        except Exception as e:
            logger.error(f"Error during ticket selection: {str(e)}")
            return False
    
    async def _select_tickets_by_category(self, page: Page, categories: List[TicketCategory]) -> bool:
        """
        Select tickets from available categories.
        
        Args:
            page: Page object
            categories: List of available ticket categories
            
        Returns:
            True if tickets were successfully selected, False otherwise
        """
        logger.info("Selecting tickets by category")
        
        # Filter categories by price and availability
        valid_categories = [
            cat for cat in categories 
            if self.min_price <= cat.price <= self.max_price and cat.availability > 0
        ]
        
        if not valid_categories:
            logger.warning("No valid ticket categories available")
            return False
        
        # Sort by preference
        preferred_categories = []
        other_categories = []
        
        for cat in valid_categories:
            if cat.matches_preference(self.preferred_areas):
                preferred_categories.append(cat)
            else:
                other_categories.append(cat)
        
        # Final list in order of preference
        sorted_categories = preferred_categories + other_categories
        
        logger.info(f"Categories after filtering and sorting: {[cat.name for cat in sorted_categories]}")
        
        # Try to select from each category
        for category in sorted_categories:
            logger.info(f"Attempting to select tickets from category: {category.name}")
            
            try:
                # Click on the category
                cat_selector = f"text='{category.name}'"
                if category.element_id:
                    cat_selector = f"#{category.element_id}"
                elif category.selector:
                    cat_selector = f"{category.selector}:has-text('{category.name}')"
                
                await browser_manager.click(page, cat_selector)
                logger.debug(f"Clicked category: {category.name}")
                
                # Wait for quantity selection to become visible
                await page.wait_for_selector("input[type='number'], select", timeout=5000)
                
                # Select quantity
                quantity_selected = await self._select_ticket_quantity(page)
                if quantity_selected:
                    logger.info(f"Successfully selected {self.desired_quantity} tickets from {category.name}")
                    return True
                
            except Exception as e:
                logger.warning(f"Failed to select tickets from category {category.name}: {str(e)}")
                continue
        
        logger.error("Failed to select tickets from any category")
        return False
    
    async def _select_reserved_seats(self, page: Page) -> bool:
        """
        Select seats from a seating map.
        
        Args:
            page: Page object
            
        Returns:
            True if seats were successfully selected, False otherwise
        """
        logger.info("Selecting reserved seats")
        
        try:
            # Look for seat selection instructions
            instructions = await page.query_selector(".seat-instructions, .seating-instructions")
            if instructions:
                instruction_text = await instructions.text_content()
                logger.debug(f"Seat selection instructions: {instruction_text}")
            
            # First, check if we need to select a section/area
            section_selectors = [
                ".venue-sections li",
                ".seat-areas button",
                ".seating-sections button"
            ]
            
            for selector in section_selectors:
                sections = await page.query_selector_all(selector)
                if sections:
                    logger.info(f"Found {len(sections)} seating sections")
                    
                    # Try preferred sections first
                    for pref in self.preferred_areas:
                        for section in sections:
                            section_text = await section.text_content()
                            if pref.upper() in section_text.upper():
                                await section.click()
                                await browser_manager.random_delay()
                                logger.info(f"Selected preferred section: {section_text}")
                                break
                        else:
                            continue
                        break
                    else:
                        # If no preferred section found, select the first one
                        await sections[0].click()
                        await browser_manager.random_delay()
                        section_text = await sections[0].text_content()
                        logger.info(f"Selected first available section: {section_text}")
                    
                    break
            
            # Wait for seat map to load
            await page.wait_for_load_state("networkidle")
            await browser_manager.random_delay()
            
            # Look for available seats
            seat_selectors = [
                ".available-seat",
                ".seat:not(.unavailable)",
                ".seat.available",
                "[data-status='available']"
            ]
            
            for selector in seat_selectors:
                available_seats = await page.query_selector_all(selector)
                if available_seats:
                    logger.info(f"Found {len(available_seats)} available seats with selector: {selector}")
                    
                    if self.adjacent_seats_only:
                        # Try to find adjacent seats
                        selected_seats = await self._find_adjacent_seats(page, available_seats, selector)
                    else:
                        # Just select the first N available seats
                        selected_seats = available_seats[:self.desired_quantity]
                    
                    # Click on selected seats
                    if selected_seats:
                        for seat in selected_seats:
                            await seat.click()
                            await browser_manager.random_delay(100, 300)
                        
                        logger.info(f"Selected {len(selected_seats)} seats")
                        return True
                    else:
                        logger.warning("Could not find suitable seats")
            
            logger.error("No available seats found")
            return False
            
        except Exception as e:
            logger.error(f"Error selecting reserved seats: {str(e)}")
            return False
    
    async def _find_adjacent_seats(self, page: Page, all_seats: List, seat_selector: str) -> List:
        """
        Find adjacent seats in the seating map.
        
        Args:
            page: Page object
            all_seats: List of all available seat elements
            seat_selector: CSS selector for available seats
            
        Returns:
            List of adjacent seat elements
        """
        logger.info(f"Looking for {self.desired_quantity} adjacent seats")
        
        try:
            # Get seat IDs and positions
            seat_positions = []
            for seat in all_seats:
                seat_id = await seat.get_attribute("id") or ""
                seat_class = await seat.get_attribute("class") or ""
                
                # Try to extract row and seat number
                row_match = re.search(r'row-([A-Z0-9]+)', seat_id) or re.search(r'row-([A-Z0-9]+)', seat_class)
                seat_match = re.search(r'seat-(\d+)', seat_id) or re.search(r'seat-(\d+)', seat_class)
                
                if row_match and seat_match:
                    row = row_match.group(1)
                    seat_num = int(seat_match.group(1))
                    
                    seat_positions.append({
                        "element": seat,
                        "row": row,
                        "seat": seat_num
                    })
            
            if not seat_positions:
                logger.warning("Could not extract seat positions")
                return all_seats[:self.desired_quantity]
            
            # Group seats by row
            rows = {}
            for pos in seat_positions:
                row = pos["row"]
                if row not in rows:
                    rows[row] = []
                rows[row].append(pos)
            
            # Sort seats within each row
            for row in rows:
                rows[row].sort(key=lambda x: x["seat"])
            
            # Find adjacent seats in each row
            for row, seats in rows.items():
                if len(seats) < self.desired_quantity:
                    continue
                
                for i in range(len(seats) - self.desired_quantity + 1):
                    # Check if this is a consecutive block
                    is_consecutive = True
                    for j in range(1, self.desired_quantity):
                        if seats[i + j]["seat"] != seats[i + j - 1]["seat"] + 1:
                            is_consecutive = False
                            break
                    
                    if is_consecutive:
                        logger.info(f"Found {self.desired_quantity} adjacent seats in row {row}")
                        return [seat["element"] for seat in seats[i:i + self.desired_quantity]]
            
            logger.warning(f"Could not find {self.desired_quantity} adjacent seats")
            
            # As a fallback, return any available seats
            return all_seats[:self.desired_quantity]
            
        except Exception as e:
            logger.error(f"Error finding adjacent seats: {str(e)}")
            return all_seats[:self.desired_quantity]
    
    async def _select_ticket_quantity(self, page: Page) -> bool:
        """
        Select ticket quantity.
        
        Args:
            page: Page object
            
        Returns:
            True if quantity was successfully selected, False otherwise
        """
        logger.info(f"Selecting ticket quantity: {self.desired_quantity}")
        
        try:
            # Look for quantity input
            quantity_selectors = [
                "input[type='number'], select.ticketCount, select.quantity",
                "input[name='qty'], input[name='quantity']",
                ".ticketQuantity input, .ticketQuantity select"
            ]
            
            for selector in quantity_selectors:
                quantity_inputs = await page.query_selector_all(selector)
                if quantity_inputs:
                    # Use the first quantity input found
                    quantity_input = quantity_inputs[0]
                    
                    # Check input type
                    tag_name = await quantity_input.get_property("tagName")
                    tag_name = await tag_name.json_value()
                    
                    if tag_name.lower() == "select":
                        # For dropdown selection
                        await quantity_input.select_option(value=str(self.desired_quantity))
                        logger.debug(f"Selected quantity {self.desired_quantity} from dropdown")
                    else:
                        # For number input
                        await quantity_input.fill("")  # Clear first
                        await browser_manager.type(page, selector, str(self.desired_quantity))
                        logger.debug(f"Entered quantity {self.desired_quantity} in input field")
                    
                    # Add a delay after selection
                    await browser_manager.random_delay()
                    return True
            
            # If standard inputs not found, try plus/minus buttons
            plus_button = await page.query_selector(".plus-icon, button:has-text('+')")
            if plus_button:
                # Start from 1 (usually default) and click the plus button
                for _ in range(self.desired_quantity - 1):
                    await plus_button.click()
                    await browser_manager.random_delay(100, 300)
                
                logger.debug(f"Selected quantity {self.desired_quantity} using plus button")
                return True
            
            logger.warning("Could not find ticket quantity input")
            return False
            
        except Exception as e:
            logger.error(f"Error selecting ticket quantity: {str(e)}")
            return False
    
    async def _proceed_to_next_step(self, page: Page) -> bool:
        """
        Proceed to the next step in the booking process.
        
        Args:
            page: Page object
            
        Returns:
            True if proceeded successfully, False otherwise
        """
        logger.info("Proceeding to next step")
        
        try:
            # Common next/continue button selectors
            proceed_selectors = [
                "button:has-text('Proceed')",
                "button:has-text('Continue')",
                "button:has-text('Next')",
                "button.proceed-btn",
                ".proceed-button",
                "input[type='submit']"
            ]
            
            for selector in proceed_selectors:
                proceed_button = await page.query_selector(selector)
                if proceed_button:
                    # Check if button is enabled
                    is_disabled = await proceed_button.get_attribute("disabled")
                    if is_disabled:
                        logger.warning(f"Proceed button is disabled: {selector}")
                        continue
                    
                    await browser_manager.click(page, selector)
                    logger.debug(f"Clicked proceed button: {selector}")
                    
                    # Wait for navigation to complete
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    return True
            
            logger.warning("Could not find proceed button")
            return False
            
        except Exception as e:
            logger.error(f"Error proceeding to next step: {str(e)}")
            return False


# Singleton instance
ticket_selector = TicketSelector()