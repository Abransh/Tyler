"""
Payment processing module for the BookMyShow Bot.

This module handles all aspects of payment processing, with a focus on
gift card payments for faster checkout without additional authentication.
"""

import re
import json
import time
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from ..config import config
from ..utils.logger import get_logger
from ..utils.browser_manager import browser_manager


logger = get_logger(__name__)


class PaymentError(Exception):
    """Exception raised for payment errors."""
    pass


class GiftCard:
    """Represents a BookMyShow gift card."""
    
    def __init__(self, 
                card_number: str, 
                pin: str, 
                balance: float = 0.0, 
                last_used: Optional[str] = None):
        """
        Initialize a gift card.
        
        Args:
            card_number: Gift card number
            pin: Gift card PIN
            balance: Current balance (if known)
            last_used: ISO format timestamp when last used
        """
        self.card_number = card_number
        self.pin = pin
        self.balance = balance
        self.last_used = last_used
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert gift card to dictionary for serialization.
        
        Returns:
            Dictionary representation
        """
        return {
            "card_number": self.card_number,
            "pin": self.pin,
            "balance": self.balance,
            "last_used": self.last_used
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GiftCard':
        """
        Create a gift card from dictionary.
        
        Args:
            data: Dictionary with gift card data
            
        Returns:
            GiftCard instance
        """
        return cls(
            card_number=data.get("card_number", ""),
            pin=data.get("pin", ""),
            balance=data.get("balance", 0.0),
            last_used=data.get("last_used")
        )


class PaymentProcessor:
    """
    Handles payment processing for BookMyShow.
    
    Supports different payment methods with a focus on gift cards,
    and manages the checkout process.
    """
    
    def __init__(self):
        """Initialize the payment processor."""
        self.payment_method = config.get("payment.method", "gift_card")
        self.gift_cards_path = Path(config.get("payment.gift_cards_path", "data/payment/gift_cards.json"))
        self.apply_offers = config.get("payment.apply_offers", True)
        self.payment_timeout = config.get("payment.timeout", 120)
        
        # Ensure gift cards directory exists
        self.gift_cards_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load gift cards if available
        self.gift_cards: List[GiftCard] = []
        self._load_gift_cards()
    
    def _load_gift_cards(self) -> None:
        """Load gift cards from disk."""
        if not self.gift_cards_path.exists():
            logger.info("No gift cards file found")
            return
        
        try:
            with open(self.gift_cards_path, "r") as f:
                gift_card_data = json.load(f)
            
            self.gift_cards = [GiftCard.from_dict(data) for data in gift_card_data]
            logger.info(f"Loaded {len(self.gift_cards)} gift cards")
        except Exception as e:
            logger.error(f"Error loading gift cards: {str(e)}")
    
    def _save_gift_cards(self) -> None:
        """Save gift cards to disk."""
        try:
            gift_card_data = [card.to_dict() for card in self.gift_cards]
            
            with open(self.gift_cards_path, "w") as f:
                json.dump(gift_card_data, f, indent=2)
            
            logger.debug(f"Saved {len(self.gift_cards)} gift cards")
        except Exception as e:
            logger.error(f"Error saving gift cards: {str(e)}")
    
    def add_gift_card(self, card_number: str, pin: str, balance: float = 0.0) -> None:
        """
        Add a gift card to the system.
        
        Args:
            card_number: Gift card number
            pin: Gift card PIN
            balance: Current balance (if known)
        """
        # Check if card already exists
        for card in self.gift_cards:
            if card.card_number == card_number:
                card.pin = pin
                card.balance = balance
                logger.info(f"Updated existing gift card ending in ...{card_number[-4:]}")
                self._save_gift_cards()
                return
        
        # Add new card
        card = GiftCard(card_number=card_number, pin=pin, balance=balance)
        self.gift_cards.append(card)
        logger.info(f"Added new gift card ending in ...{card_number[-4:]}")
        self._save_gift_cards()
    
    def get_gift_cards_with_balance(self) -> List[GiftCard]:
        """
        Get gift cards with known positive balance.
        
        Returns:
            List of gift cards with positive balance
        """
        return [card for card in self.gift_cards if card.balance > 0]
    
    def update_gift_card_balance(self, card_number: str, balance: float) -> None:
        """
        Update the balance of a gift card.
        
        Args:
            card_number: Gift card number
            balance: New balance
        """
        for card in self.gift_cards:
            if card.card_number == card_number:
                card.balance = balance
                card.last_used = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                logger.info(f"Updated balance for gift card ...{card_number[-4:]}: ₹{balance:.2f}")
                self._save_gift_cards()
                return
        
        logger.warning(f"Gift card ...{card_number[-4:]} not found")
    
    async def process_payment(self, page: Page) -> bool:
        """
        Process payment for a booking.
        
        Args:
            page: Page object
            
        Returns:
            True if payment was successful, False otherwise
        """
        logger.info("Starting payment process")
        
        try:
            # Apply offers if configured
            if self.apply_offers:
                await self._apply_offers(page)
            
            # Choose payment method
            if self.payment_method == "gift_card":
                success = await self._pay_with_gift_card(page)
            else:
                logger.warning(f"Payment method {self.payment_method} not fully implemented")
                success = await self._pay_with_default_method(page)
            
            if not success:
                logger.error("Payment failed")
                return False
            
            # Wait for confirmation
            confirmed = await self._wait_for_confirmation(page)
            return confirmed
            
        except Exception as e:
            logger.error(f"Error during payment: {str(e)}")
            return False
    
    async def _apply_offers(self, page: Page) -> bool:
        """
        Apply available offers and discounts.
        
        Args:
            page: Page object
            
        Returns:
            True if offers were applied, False otherwise
        """
        logger.info("Checking for available offers")
        
        try:
            # Check for offers section
            offer_selectors = [
                ".offers-section",
                ".available-offers",
                ".promocode-section",
                "text=Apply Promocode"
            ]
            
            for selector in offer_selectors:
                if await page.is_visible(selector, timeout=3000):
                    logger.info("Found offers section")
                    
                    # Check for available offers
                    offer_items = await page.query_selector_all(".offer-item, .promocode-item")
                    if offer_items:
                        logger.info(f"Found {len(offer_items)} available offers")
                        
                        # Apply first offer
                        await offer_items[0].click()
                        await browser_manager.random_delay()
                        
                        # Look for apply button
                        apply_button = await page.query_selector("button:has-text('Apply'), button.apply-btn")
                        if apply_button:
                            await apply_button.click()
                            await browser_manager.random_delay()
                            logger.info("Applied offer")
                            return True
                    else:
                        logger.info("No offers available")
            
            logger.info("No offers section found")
            return False
            
        except Exception as e:
            logger.warning(f"Error applying offers: {str(e)}")
            return False
    
    async def _pay_with_gift_card(self, page: Page) -> bool:
        """
        Pay using gift card.
        
        Args:
            page: Page object
            
        Returns:
            True if payment was initiated successfully, False otherwise
        """
        logger.info("Attempting payment with gift card")
        
        if not self.gift_cards:
            logger.error("No gift cards available")
            return False
        
        try:
            # Select gift card payment option
            gift_card_selectors = [
                "text=Gift Card",
                "text=BookMyShow Gift Card",
                ".payment-gift-card",
                "label:has-text('Gift Card')"
            ]
            
            for selector in gift_card_selectors:
                if await page.is_visible(selector, timeout=3000):
                    await browser_manager.click(page, selector)
                    logger.info("Selected gift card payment option")
                    await browser_manager.random_delay()
                    break
            else:
                logger.warning("Gift card payment option not found")
                return False
            
            # Get the required payment amount
            amount = await self._get_payment_amount(page)
            if amount <= 0:
                logger.error("Could not determine payment amount")
                return False
            
            # Find a gift card with sufficient balance
            card = self._find_card_for_amount(amount)
            if not card:
                logger.error(f"No gift card with sufficient balance for ₹{amount:.2f}")
                return False
            
            # Enter gift card details
            card_number_selector = "input[placeholder*='Card Number'], input[name='card_number']"
            pin_selector = "input[placeholder*='PIN'], input[name='pin']"
            
            if await page.is_visible(card_number_selector, timeout=3000):
                await browser_manager.type(page, card_number_selector, card.card_number)
                logger.debug(f"Entered gift card number ending in ...{card.card_number[-4:]}")
            else:
                logger.warning("Gift card number field not found")
                return False
            
            if await page.is_visible(pin_selector, timeout=3000):
                await browser_manager.type(page, pin_selector, card.pin)
                logger.debug("Entered gift card PIN")
            else:
                logger.warning("Gift card PIN field not found")
                return False
            
            # Submit payment
            submit_selectors = [
                "button:has-text('Pay')",
                "button:has-text('Submit')",
                "button[type='submit']",
                "input[type='submit']"
            ]
            
            for selector in submit_selectors:
                if await page.is_visible(selector, timeout=3000):
                    await browser_manager.click(page, selector)
                    logger.info(f"Submitted payment with gift card ...{card.card_number[-4:]}")
                    
                    # Update card usage timestamp
                    card.last_used = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self._save_gift_cards()
                    
                    return True
            
            logger.warning("Payment submission button not found")
            return False
            
        except Exception as e:
            logger.error(f"Error processing gift card payment: {str(e)}")
            return False
    
    async def _pay_with_default_method(self, page: Page) -> bool:
        """
        Pay using default configured method.
        
        Args:
            page: Page object
            
        Returns:
            True if payment was initiated successfully, False otherwise
            
        Note:
            This is a placeholder for other payment methods.
        """
        logger.warning(f"Using default payment method: {self.payment_method}")
        
        try:
            # This is a placeholder - in a real implementation, different payment
            # methods would be handled here
            
            # Just click the first payment option for demonstration
            payment_options = await page.query_selector_all(".payment-option, .payment-method, input[name='payment']")
            if payment_options:
                await payment_options[0].click()
                await browser_manager.random_delay()
                
                # Find and click submit/pay button
                submit_button = await page.query_selector("button:has-text('Pay'), button:has-text('Continue')")
                if submit_button:
                    await submit_button.click()
                    logger.info("Selected default payment method and submitted")
                    return True
            
            logger.warning("Could not complete payment with default method")
            return False
            
        except Exception as e:
            logger.error(f"Error with default payment method: {str(e)}")
            return False
    
    async def _get_payment_amount(self, page: Page) -> float:
        """
        Get the payment amount from the page.
        
        Args:
            page: Page object
            
        Returns:
            Payment amount as float
        """
        try:
            # Try different selectors for payment amount
            amount_selectors = [
                ".total-amount",
                ".grand-total",
                ".payment-amount",
                "text=/Total Amount: ₹[\\d,.]+/"
            ]
            
            for selector in amount_selectors:
                amount_element = await page.query_selector(selector)
                if amount_element:
                    amount_text = await amount_element.text_content()
                    amount_match = re.search(r'₹\s*([\d,]+(\.\d+)?)', amount_text)
                    
                    if amount_match:
                        amount_str = amount_match.group(1).replace(',', '')
                        amount = float(amount_str)
                        logger.info(f"Payment amount: ₹{amount:.2f}")
                        return amount
            
            # If no amount found with specific selectors, try to find any currency amount
            page_text = await page.text_content()
            amount_matches = re.findall(r'₹\s*([\d,]+(\.\d+)?)', page_text)
            
            if amount_matches:
                # Use the largest amount found
                amounts = [float(match[0].replace(',', '')) for match in amount_matches]
                max_amount = max(amounts)
                logger.info(f"Found payment amount from text: ₹{max_amount:.2f}")
                return max_amount
            
            logger.warning("Could not find payment amount")
            return 0
            
        except Exception as e:
            logger.error(f"Error getting payment amount: {str(e)}")
            return 0
    
    def _find_card_for_amount(self, amount: float) -> Optional[GiftCard]:
        """
        Find a gift card with sufficient balance.
        
        Args:
            amount: Required payment amount
            
        Returns:
            GiftCard with sufficient balance, or None if not found
        """
        # If we don't know balances, just return the first card
        unknown_balance_cards = [card for card in self.gift_cards if card.balance == 0]
        if unknown_balance_cards:
            logger.info("Using gift card with unknown balance")
            return unknown_balance_cards[0]
        
        # Find cards with sufficient balance
        sufficient_cards = [card for card in self.gift_cards if card.balance >= amount]
        if sufficient_cards:
            # Use the card with the smallest sufficient balance
            card = min(sufficient_cards, key=lambda c: c.balance)
            logger.info(f"Using gift card with balance ₹{card.balance:.2f} for payment of ₹{amount:.2f}")
            return card
        
        return None
    
    async def _wait_for_confirmation(self, page: Page) -> bool:
        """
        Wait for payment confirmation.
        
        Args:
            page: Page object
            
        Returns:
            True if payment was confirmed, False otherwise
        """
        logger.info(f"Waiting for payment confirmation (timeout: {self.payment_timeout}s)")
        
        start_time = time.time()
        
        try:
            # Check for success indicators
            success_selectors = [
                "text=Payment Successful",
                "text=Booking Confirmed",
                "text=Your transaction is successful",
                ".success-message",
                ".confirmation-message"
            ]
            
            # Check for failure indicators
            failure_selectors = [
                "text=Payment Failed",
                "text=Transaction Failed",
                ".failure-message",
                ".error-message"
            ]
            
            while time.time() - start_time < self.payment_timeout:
                # Check for success
                for selector in success_selectors:
                    if await page.is_visible(selector, timeout=1000):
                        logger.info(f"Payment confirmed: {selector}")
                        return True
                
                # Check for failure
                for selector in failure_selectors:
                    if await page.is_visible(selector, timeout=1000):
                        logger.error(f"Payment failed: {selector}")
                        return False
                
                # Wait before checking again
                await page.wait_for_timeout(2000)
            
            logger.warning(f"Payment confirmation timed out after {self.payment_timeout}s")
            return False
            
        except Exception as e:
            logger.error(f"Error waiting for payment confirmation: {str(e)}")
            return False


# Singleton instance
payment_processor = PaymentProcessor()