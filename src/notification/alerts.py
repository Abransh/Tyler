"""
Notification module for the BookMyShow Bot.

This module handles sending notifications about ticket availability,
purchase status, and errors through various channels.
"""

import os
import json
import smtplib
import time
import asyncio
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

from ..config import config
from ..utils.logger import get_logger
from ..monitoring.event_tracker import Event


logger = get_logger(__name__)


class NotificationError(Exception):
    """Exception raised for notification errors."""
    pass


class NotificationManager:
    """
    Manages notifications for the BookMyShow Bot.
    
    Supports multiple notification channels and priorities.
    """
    
    def __init__(self):
        """Initialize the notification manager."""
        # Load notification settings
        self.channels = config.get("notification.channels", {})
        self.events = config.get("notification.events", {})
    
    async def send_notification(self, 
                             event_type: str, 
                             message: str, 
                             details: Optional[Dict[str, Any]] = None) -> bool:
        """
        Send a notification for an event.
        
        Args:
            event_type: Type of event (e.g., ticket_available, purchase_success)
            message: Main notification message
            details: Additional details for the notification
            
        Returns:
            True if notification was sent successfully on any channel, False otherwise
        """
        if event_type not in self.events:
            logger.warning(f"Unknown event type: {event_type}")
            event_channels = list(self.channels.keys())
            priority = "medium"
        else:
            event_channels = self.events[event_type].get("channels", [])
            priority = self.events[event_type].get("priority", "medium")
        
        if not event_channels:
            logger.warning(f"No channels configured for event type: {event_type}")
            return False
        
        # Format the full message
        full_message = self._format_message(event_type, message, details, priority)
        
        # Send to each enabled channel
        success = False
        
        for channel in event_channels:
            if channel not in self.channels:
                logger.warning(f"Unknown channel: {channel}")
                continue
            
            channel_config = self.channels[channel]
            if not channel_config.get("enabled", False):
                logger.debug(f"Channel {channel} is disabled")
                continue
            
            try:
                if channel == "email":
                    result = await self._send_email(event_type, full_message, details, priority)
                elif channel == "telegram":
                    result = await self._send_telegram(event_type, full_message, details, priority)
                elif channel == "slack":
                    result = await self._send_slack(event_type, full_message, details, priority)
                elif channel == "sms":
                    result = await self._send_sms(event_type, full_message, details, priority)
                else:
                    logger.warning(f"Unsupported channel: {channel}")
                    result = False
                
                if result:
                    logger.info(f"Notification sent via {channel}")
                    success = True
                else:
                    logger.warning(f"Failed to send notification via {channel}")
                
            except Exception as e:
                logger.error(f"Error sending notification via {channel}: {str(e)}")
        
        return success
    
    def _format_message(self, 
                      event_type: str, 
                      message: str, 
                      details: Optional[Dict[str, Any]] = None, 
                      priority: str = "medium") -> str:
        """
        Format a notification message.
        
        Args:
            event_type: Type of event
            message: Main notification message
            details: Additional details for the notification
            priority: Priority level
            
        Returns:
            Formatted message
        """
        # Convert event type to friendly name
        event_names = {
            "ticket_available": "Tickets Available",
            "purchase_started": "Purchase Started",
            "purchase_success": "Purchase Successful",
            "purchase_failed": "Purchase Failed",
            "error": "Error"
        }
        
        event_name = event_names.get(event_type, event_type.replace("_", " ").title())
        
        # Priority indicator
        priority_indicators = {
            "high": "🔴 HIGH PRIORITY: ",
            "medium": "🟠 ",
            "low": ""
        }
        
        priority_prefix = priority_indicators.get(priority, "")
        
        # Timestamp
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # Basic message
        formatted = f"{priority_prefix}{event_name}: {message}\n\nTime: {timestamp}"
        
        # Add details if provided
        if details:
            formatted += "\n\nDetails:"
            for key, value in details.items():
                formatted += f"\n- {key}: {value}"
        
        return formatted
    
    async def _send_email(self, 
                        event_type: str, 
                        message: str, 
                        details: Optional[Dict[str, Any]] = None, 
                        priority: str = "medium") -> bool:
        """
        Send a notification via email.
        
        Args:
            event_type: Type of event
            message: Formatted notification message
            details: Additional details for the notification
            priority: Priority level
            
        Returns:
            True if sent successfully, False otherwise
        """
        email_config = self.channels.get("email", {})
        if not email_config.get("enabled", False):
            return False
        
        # Get email settings
        smtp_server = email_config.get("smtp_server", "")
        smtp_port = email_config.get("smtp_port", 587)
        use_tls = email_config.get("use_tls", True)
        username = email_config.get("smtp_user", "")
        password = email_config.get("smtp_password", "")
        sender = email_config.get("from_address", username)
        recipients = email_config.get("to_addresses", [])
        
        if not smtp_server or not username or not password or not recipients:
            logger.warning("Incomplete email configuration")
            return False
        
        try:
            # Create message
            msg = MIMEMultipart()
            msg["From"] = sender
            msg["To"] = ", ".join(recipients)
            
            # Subject with priority indicator
            priority_indicators = {"high": "🔴", "medium": "🟠", "low": ""}
            priority_prefix = priority_indicators.get(priority, "")
            
            event_names = {
                "ticket_available": "Tickets Available",
                "purchase_started": "Purchase Started",
                "purchase_success": "Purchase Successful",
                "purchase_failed": "Purchase Failed",
                "error": "Error"
            }
            event_name = event_names.get(event_type, event_type.replace("_", " ").title())
            
            msg["Subject"] = f"{priority_prefix} BookMyShow Bot: {event_name}"
            
            # Add message body
            msg.attach(MIMEText(message, "plain"))
            
            # Connect to server and send
            smtp = smtplib.SMTP(smtp_server, smtp_port)
            
            if use_tls:
                smtp.starttls()
            
            smtp.login(username, password)
            smtp.send_message(msg)
            smtp.quit()
            
            logger.info(f"Email notification sent to {len(recipients)} recipients")
            return True
            
        except Exception as e:
            logger.error(f"Error sending email notification: {str(e)}")
            return False
    
    async def _send_telegram(self, 
                           event_type: str, 
                           message: str, 
                           details: Optional[Dict[str, Any]] = None, 
                           priority: str = "medium") -> bool:
        """
        Send a notification via Telegram.
        
        Args:
            event_type: Type of event
            message: Formatted notification message
            details: Additional details for the notification
            priority: Priority level
            
        Returns:
            True if sent successfully, False otherwise
        """
        telegram_config = self.channels.get("telegram", {})
        if not telegram_config.get("enabled", False):
            return False
        
        bot_token = telegram_config.get("bot_token", "")
        chat_id = telegram_config.get("chat_id", "")
        
        if not bot_token or not chat_id:
            logger.warning("Incomplete Telegram configuration")
            return False
        
        try:
            # Send message
            api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            response = requests.post(api_url, json=payload)
            
            if response.status_code == 200:
                logger.info("Telegram notification sent")
                return True
            else:
                logger.warning(f"Telegram API returned: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending Telegram notification: {str(e)}")
            return False
    
    async def _send_slack(self, 
                        event_type: str, 
                        message: str, 
                        details: Optional[Dict[str, Any]] = None, 
                        priority: str = "medium") -> bool:
        """
        Send a notification via Slack.
        
        Args:
            event_type: Type of event
            message: Formatted notification message
            details: Additional details for the notification
            priority: Priority level
            
        Returns:
            True if sent successfully, False otherwise
        """
        slack_config = self.channels.get("slack", {})
        if not slack_config.get("enabled", False):
            return False
        
        webhook_url = slack_config.get("webhook_url", "")
        
        if not webhook_url:
            logger.warning("Incomplete Slack configuration")
            return False
        
        try:
            # Color coding by priority
            colors = {"high": "#FF0000", "medium": "#FFA500", "low": "#008000"}
            color = colors.get(priority, "#808080")
            
            # Create payload
            payload = {
                "attachments": [
                    {
                        "fallback": message,
                        "color": color,
                        "pretext": "BookMyShow Bot Notification",
                        "title": event_type.replace("_", " ").title(),
                        "text": message,
                        "ts": time.time()
                    }
                ]
            }
            
            response = requests.post(webhook_url, json=payload)
            
            if response.status_code == 200:
                logger.info("Slack notification sent")
                return True
            else:
                logger.warning(f"Slack API returned: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending Slack notification: {str(e)}")
            return False
    
    async def _send_sms(self, 
                      event_type: str, 
                      message: str, 
                      details: Optional[Dict[str, Any]] = None, 
                      priority: str = "medium") -> bool:
        """
        Send a notification via SMS.
        
        Args:
            event_type: Type of event
            message: Formatted notification message
            details: Additional details for the notification
            priority: Priority level
            
        Returns:
            True if sent successfully, False otherwise
            
        Note:
            This is a placeholder for SMS integration.
        """
        sms_config = self.channels.get("sms", {})
        if not sms_config.get("enabled", False):
            return False
        
        provider = sms_config.get("provider", "").lower()
        
        if provider == "twilio":
            return await self._send_twilio_sms(message, sms_config)
        else:
            logger.warning(f"Unsupported SMS provider: {provider}")
            return False
    
    async def _send_twilio_sms(self, message: str, sms_config: Dict[str, Any]) -> bool:
        """
        Send SMS via Twilio.
        
        Args:
            message: Message to send
            sms_config: SMS configuration
            
        Returns:
            True if sent successfully, False otherwise
            
        Note:
            This is a placeholder for Twilio integration.
        """
        account_sid = sms_config.get("account_sid", "")
        auth_token = sms_config.get("auth_token", "")
        from_number = sms_config.get("from_number", "")
        to_numbers = sms_config.get("to_numbers", [])
        
        if not account_sid or not auth_token or not from_number or not to_numbers:
            logger.warning("Incomplete Twilio configuration")
            return False
        
        try:
            # Shorten message for SMS
            if len(message) > 160:
                short_message = message[:157] + "..."
            else:
                short_message = message
            
            # Send to each number
            success = False
            
            for to_number in to_numbers:
                # Note: This is a placeholder for actual Twilio API integration
                # In a real implementation, this would use the Twilio SDK
                logger.info(f"Would send SMS to {to_number}: {short_message}")
                success = True
            
            return success
                
        except Exception as e:
            logger.error(f"Error sending Twilio SMS: {str(e)}")
            return False
    
    async def notify_ticket_available(self, event: Event) -> bool:
        """
        Send notification when tickets become available.
        
        Args:
            event: Event with available tickets
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        message = f"Tickets are now available for '{event.name}'!"
        
        details = {
            "Event": event.name,
            "URL": event.url,
            "Venue": event.venue or "Unknown venue",
            "Date": event.event_date or "Unknown date"
        }
        
        return await self.send_notification("ticket_available", message, details)
    
    async def notify_purchase_started(self, event: Event, quantity: int) -> bool:
        """
        Send notification when purchase process starts.
        
        Args:
            event: Event being purchased
            quantity: Number of tickets being purchased
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        message = f"Starting purchase of {quantity} tickets for '{event.name}'."
        
        details = {
            "Event": event.name,
            "URL": event.url,
            "Quantity": str(quantity),
            "Venue": event.venue or "Unknown venue",
            "Date": event.event_date or "Unknown date"
        }
        
        return await self.send_notification("purchase_started", message, details)
    
    async def notify_purchase_success(self, 
                                     event: Event, 
                                     quantity: int, 
                                     total_price: float) -> bool:
        """
        Send notification when purchase is successful.
        
        Args:
            event: Event purchased
            quantity: Number of tickets purchased
            total_price: Total price paid
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        message = f"Successfully purchased {quantity} tickets for '{event.name}'!"
        
        details = {
            "Event": event.name,
            "URL": event.url,
            "Quantity": str(quantity),
            "Total Price": f"₹{total_price:.2f}",
            "Venue": event.venue or "Unknown venue",
            "Date": event.event_date or "Unknown date"
        }
        
        return await self.send_notification("purchase_success", message, details)
    
    async def notify_purchase_failed(self, event: Event, error_message: str) -> bool:
        """
        Send notification when purchase fails.
        
        Args:
            event: Event that failed to purchase
            error_message: Error message explaining the failure
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        message = f"Failed to purchase tickets for '{event.name}'."
        
        details = {
            "Event": event.name,
            "URL": event.url,
            "Error": error_message,
            "Venue": event.venue or "Unknown venue",
            "Date": event.event_date or "Unknown date"
        }
        
        return await self.send_notification("purchase_failed", message, details)
    
    async def notify_error(self, error_message: str, context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Send notification for general errors.
        
        Args:
            error_message: Error message
            context: Additional context about the error
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        message = f"Error in BookMyShow Bot: {error_message}"
        
        return await self.send_notification("error", message, context)


# Singleton instance
notification_manager = NotificationManager()