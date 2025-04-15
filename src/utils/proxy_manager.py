"""
Proxy management module for the BookMyShow Bot.

This module handles proxy rotation, testing, and management to avoid IP blocking
and distribute requests across multiple IPs.
"""

import json
import time
import random
import asyncio
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple

import aiohttp
from playwright.async_api import BrowserContext

from ..config import config
from ..utils.logger import get_logger


logger = get_logger(__name__)


class ProxyError(Exception):
    """Exception raised for proxy-related errors."""
    pass


class Proxy:
    """Represents a proxy server."""
    
    def __init__(self, 
                host: str, 
                port: int, 
                username: Optional[str] = None, 
                password: Optional[str] = None,
                protocol: str = "http",
                country: Optional[str] = None,
                last_used: float = 0,
                success_count: int = 0,
                failure_count: int = 0):
        """
        Initialize a proxy.
        
        Args:
            host: Proxy host address
            port: Proxy port
            username: Username for authentication
            password: Password for authentication
            protocol: Proxy protocol (http, https, socks5)
            country: Country of the proxy
            last_used: Timestamp of last usage
            success_count: Number of successful uses
            failure_count: Number of failed uses
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.protocol = protocol.lower()
        self.country = country
        self.last_used = last_used
        self.success_count = success_count
        self.failure_count = failure_count
        self.is_banned = False
        self.ban_time = 0
    
    @property
    def url(self) -> str:
        """
        Get the proxy URL.
        
        Returns:
            Full proxy URL with authentication if provided
        """
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"
    
    @property
    def server(self) -> str:
        """
        Get the proxy server address.
        
        Returns:
            Proxy server in host:port format
        """
        return f"{self.host}:{self.port}"
    
    @property
    def playwright_config(self) -> Dict[str, str]:
        """
        Get proxy configuration for Playwright.
        
        Returns:
            Dictionary with proxy configuration for Playwright
        """
        config = {"server": f"{self.protocol}://{self.host}:{self.port}"}
        if self.username and self.password:
            config["username"] = self.username
            config["password"] = self.password
        return config
    
    def mark_success(self) -> None:
        """Mark the proxy as successfully used."""
        self.last_used = time.time()
        self.success_count += 1
        if self.is_banned and time.time() - self.ban_time > 3600:  # Unban after 1 hour
            self.is_banned = False
    
    def mark_failure(self) -> None:
        """Mark the proxy as failed."""
        self.failure_count += 1
    
    def mark_banned(self) -> None:
        """Mark the proxy as banned."""
        self.is_banned = True
        self.ban_time = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert proxy to dictionary.
        
        Returns:
            Dictionary representation of proxy
        """
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "protocol": self.protocol,
            "country": self.country,
            "last_used": self.last_used,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "is_banned": self.is_banned,
            "ban_time": self.ban_time
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Proxy':
        """
        Create proxy from dictionary.
        
        Args:
            data: Dictionary with proxy data
            
        Returns:
            Proxy instance
        """
        return cls(
            host=data["host"],
            port=data["port"],
            username=data.get("username"),
            password=data.get("password"),
            protocol=data.get("protocol", "http"),
            country=data.get("country"),
            last_used=data.get("last_used", 0),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0)
        )


class ProxyManager:
    """
    Manages a pool of proxies for the BookMyShow Bot.
    
    Handles loading, testing, and rotating proxies to avoid detection
    and IP blocking.
    """
    
    def __init__(self):
        """Initialize the proxy manager."""
        self.enabled = config.get("proxy.enabled", False)
        self.rotation_enabled = config.get("proxy.rotation.enabled", False)
        self.rotation_interval = config.get("proxy.rotation.interval", 300)  # 5 minutes
        self.max_failures = config.get("proxy.max_failures", 5)
        self.test_url = config.get("proxy.test_url", "https://in.bookmyshow.com")
        self.proxies_path = Path(config.get("proxy.proxies_path", "data/proxies.json"))
        
        # List of proxies
        self.proxies: List[Proxy] = []
        self.current_proxy: Optional[Proxy] = None
        self.last_rotation_time = 0
        
        # Load proxies if available
        if self.enabled:
            self._load_proxies()
    
    def _load_proxies(self) -> None:
        """Load proxies from config and file."""
        # Check if proxies file exists
        if self.proxies_path.exists():
            try:
                with open(self.proxies_path, "r") as f:
                    proxy_data = json.load(f)
                
                for data in proxy_data:
                    self.proxies.append(Proxy.from_dict(data))
                
                logger.info(f"Loaded {len(self.proxies)} proxies from file")
            except Exception as e:
                logger.error(f"Error loading proxies from file: {str(e)}")
        
        # Add proxy from config if specified
        server = config.get("proxy.server", "")
        if server:
            parts = server.split("://")
            protocol = parts[0] if len(parts) > 1 else "http"
            server_part = parts[-1]
            
            # Parse auth if present
            if "@" in server_part:
                auth, server_part = server_part.split("@", 1)
                username, password = auth.split(":", 1) if ":" in auth else (auth, "")
            else:
                username, password = None, None
            
            # Parse host and port
            if ":" in server_part:
                host, port_str = server_part.split(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 80 if protocol == "http" else 443
            else:
                host = server_part
                port = 80 if protocol == "http" else 443
            
            # Create and add proxy
            proxy = Proxy(
                host=host,
                port=port,
                username=config.get("proxy.username") or username,
                password=config.get("proxy.password") or password,
                protocol=protocol
            )
            
            # Check if proxy already exists
            if not any(p.host == proxy.host and p.port == proxy.port for p in self.proxies):
                self.proxies.append(proxy)
                logger.info(f"Added proxy from config: {proxy.host}:{proxy.port}")
        
        # If no proxies loaded, log warning
        if not self.proxies:
            logger.warning("No proxies available. Proxy functionality will be disabled.")
            self.enabled = False
    
    def _save_proxies(self) -> None:
        """Save proxies to file."""
        if not self.proxies:
            return
        
        try:
            # Create directory if needed
            self.proxies_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save to file
            proxy_data = [proxy.to_dict() for proxy in self.proxies]
            with open(self.proxies_path, "w") as f:
                json.dump(proxy_data, f, indent=2)
            
            logger.debug(f"Saved {len(self.proxies)} proxies to file")
        except Exception as e:
            logger.error(f"Error saving proxies to file: {str(e)}")
    
    def add_proxy(self, 
                host: str, 
                port: int, 
                username: Optional[str] = None, 
                password: Optional[str] = None,
                protocol: str = "http") -> None:
        """
        Add a proxy to the pool.
        
        Args:
            host: Proxy host address
            port: Proxy port
            username: Username for authentication
            password: Password for authentication
            protocol: Proxy protocol (http, https, socks5)
        """
        proxy = Proxy(
            host=host,
            port=port,
            username=username,
            password=password,
            protocol=protocol
        )
        
        # Check if proxy already exists
        for existing_proxy in self.proxies:
            if existing_proxy.host == proxy.host and existing_proxy.port == proxy.port:
                # Update existing proxy
                existing_proxy.username = proxy.username
                existing_proxy.password = proxy.password
                existing_proxy.protocol = proxy.protocol
                logger.info(f"Updated existing proxy: {proxy.host}:{proxy.port}")
                self._save_proxies()
                return
        
        # Add new proxy
        self.proxies.append(proxy)
        logger.info(f"Added new proxy: {proxy.host}:{proxy.port}")
        self._save_proxies()
        
        # Enable proxy feature if not already enabled
        self.enabled = True
    
    def remove_proxy(self, host: str, port: int) -> bool:
        """
        Remove a proxy from the pool.
        
        Args:
            host: Proxy host address
            port: Proxy port
            
        Returns:
            True if proxy was removed, False if not found
        """
        for i, proxy in enumerate(self.proxies):
            if proxy.host == host and proxy.port == port:
                removed = self.proxies.pop(i)
                logger.info(f"Removed proxy: {removed.host}:{removed.port}")
                self._save_proxies()
                
                # If current proxy was removed, set to None
                if self.current_proxy and self.current_proxy.host == host and self.current_proxy.port == port:
                    self.current_proxy = None
                
                # Disable proxy feature if no proxies left
                if not self.proxies:
                    self.enabled = False
                
                return True
        
        logger.warning(f"Proxy not found: {host}:{port}")
        return False
    
    async def get_proxy(self, force_rotation: bool = False) -> Optional[Proxy]:
        """
        Get a proxy from the pool.
        
        Args:
            force_rotation: Whether to force proxy rotation
            
        Returns:
            Proxy or None if no proxies available
        """
        if not self.enabled or not self.proxies:
            return None
        
        current_time = time.time()
        
        # Check if we need to rotate
        need_rotation = (
            force_rotation or 
            self.current_proxy is None or 
            (self.rotation_enabled and current_time - self.last_rotation_time > self.rotation_interval)
        )
        
        if need_rotation:
            # Get available (non-banned) proxies
            available_proxies = [p for p in self.proxies if not p.is_banned]
            
            if not available_proxies:
                logger.warning("No available proxies. All proxies are banned.")
                if self.proxies:
                    # Unban the least recently banned proxy
                    unbanned = min(self.proxies, key=lambda p: p.ban_time)
                    unbanned.is_banned = False
                    logger.info(f"Unbanned proxy: {unbanned.host}:{unbanned.port}")
                    available_proxies = [unbanned]
                else:
                    return None
            
            # Sort by when they were last used (oldest first)
            available_proxies.sort(key=lambda p: p.last_used)
            
            # Select the least recently used proxy
            self.current_proxy = available_proxies[0]
            self.last_rotation_time = current_time
            
            logger.info(f"Rotated to proxy: {self.current_proxy.host}:{self.current_proxy.port}")
        
        return self.current_proxy
    
    async def test_proxies(self) -> Dict[str, int]:
        """
        Test all proxies in the pool for connectivity.
        
        Returns:
            Dictionary with counts of working and failing proxies
        """
        if not self.proxies:
            logger.warning("No proxies to test")
            return {"working": 0, "failing": 0}
        
        logger.info(f"Testing {len(self.proxies)} proxies")
        
        working = 0
        failing = 0
        
        for proxy in self.proxies:
            if await self.test_proxy(proxy):
                working += 1
            else:
                failing += 1
        
        logger.info(f"Proxy test results: {working} working, {failing} failing")
        self._save_proxies()
        
        return {"working": working, "failing": failing}
    
    async def test_proxy(self, proxy: Proxy) -> bool:
        """
        Test a single proxy for connectivity.
        
        Args:
            proxy: Proxy to test
            
        Returns:
            True if proxy is working, False otherwise
        """
        logger.debug(f"Testing proxy: {proxy.host}:{proxy.port}")
        
        try:
            # Try to connect to test URL using the proxy
            async with aiohttp.ClientSession() as session:
                timeout = aiohttp.ClientTimeout(total=10)
                proxy_url = proxy.url
                
                async with session.get(
                    self.test_url, 
                    proxy=proxy_url, 
                    timeout=timeout,
                    ssl=False  # Disable SSL verification for testing
                ) as response:
                    if response.status == 200:
                        proxy.mark_success()
                        logger.debug(f"Proxy test successful: {proxy.host}:{proxy.port}")
                        return True
                    else:
                        proxy.mark_failure()
                        logger.warning(f"Proxy test failed with status {response.status}: {proxy.host}:{proxy.port}")
                        return False
        
        except Exception as e:
            proxy.mark_failure()
            logger.warning(f"Proxy test failed with error: {proxy.host}:{proxy.port} - {str(e)}")
            
            # Check if too many failures
            if proxy.failure_count >= self.max_failures:
                proxy.mark_banned()
                logger.warning(f"Banned proxy due to too many failures: {proxy.host}:{proxy.port}")
            
            return False
    
    async def apply_proxy_to_context(self, context: BrowserContext) -> None:
        """
        Apply proxy to an existing browser context.
        
        Args:
            context: Browser context to apply proxy to
            
        Note:
            This is a placeholder, as Playwright doesn't support changing
            proxy settings for an existing context.
        """
        logger.warning("Cannot apply proxy to existing context. Create a new context instead.")
    
    async def handle_proxy_authentication(self, page) -> None:
        """
        Handle proxy authentication dialog.
        
        Args:
            page: Page object that might encounter proxy auth
        """
        if not self.current_proxy or not (self.current_proxy.username and self.current_proxy.password):
            return
        
        # Set up authentication handler
        await page.context.authenticate({
            "username": self.current_proxy.username,
            "password": self.current_proxy.password
        })
    
    def get_all_proxies(self) -> List[Dict[str, Any]]:
        """
        Get information about all proxies.
        
        Returns:
            List of dictionaries with proxy information
        """
        return [
            {
                "host": p.host,
                "port": p.port,
                "protocol": p.protocol,
                "country": p.country,
                "success_count": p.success_count,
                "failure_count": p.failure_count,
                "is_banned": p.is_banned,
                "last_used": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.last_used)) if p.last_used else "Never"
            }
            for p in self.proxies
        ]
    
    def load_brightdata_proxies(self, api_key: str, zone: str, count: int = 5) -> None:
        """
        Load proxies from Bright Data.
        
        Args:
            api_key: Bright Data API key
            zone: Proxy zone (residential, datacenter)
            count: Number of proxies to load
        """
        if not api_key:
            logger.error("No Bright Data API key provided")
            return
        
        try:
            # This is a placeholder - in a real implementation, you would
            # use the Bright Data API to get proxies
            logger.info(f"Loading {count} proxies from Bright Data ({zone})")
            
            # Simulated proxy loading
            for i in range(count):
                self.add_proxy(
                    host=f"brd-customer-{api_key[:8]}-zone-{zone}-{i}",
                    port=22225,
                    username=api_key,
                    password="",
                    protocol="http"
                )
                
        except Exception as e:
            logger.error(f"Error loading Bright Data proxies: {str(e)}")
    
    def load_oxylabs_proxies(self, username: str, password: str, country: str = "in", count: int = 5) -> None:
        """
        Load proxies from Oxylabs.
        
        Args:
            username: Oxylabs username
            password: Oxylabs password
            country: Country code
            count: Number of proxies to load
        """
        if not username or not password:
            logger.error("No Oxylabs credentials provided")
            return
        
        try:
            # This is a placeholder - in a real implementation, you would
            # use the Oxylabs API or documentation to get proper endpoints
            logger.info(f"Loading {count} proxies from Oxylabs ({country})")
            
            # Simulated proxy loading
            for i in range(count):
                self.add_proxy(
                    host=f"pr.oxylabs.io",
                    port=7777,
                    username=username,
                    password=password,
                    protocol="http"
                )
                
        except Exception as e:
            logger.error(f"Error loading Oxylabs proxies: {str(e)}")


# Singleton instance
proxy_manager = ProxyManager()