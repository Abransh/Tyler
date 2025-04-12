"""
Browser management module for the BookMyShow Bot.

This module handles Playwright browser setup, configuration, and provides
methods for browser interaction with anti-bot protection.
"""

import os
import random
import time
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union, Callable

import playwright
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Response

from ..config import config
from ..utils.logger import get_logger


logger = get_logger(__name__)


class BrowserManager:
    """
    Manages Playwright browser instances with anti-bot protections.
    
    Provides methods for creating and configuring browser contexts,
    handling common browser operations, and implementing anti-detection
    measures to avoid bot detection.
    """
    
    def __init__(self):
        """Initialize the browser manager."""
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False
        
        # Load browser config
        self.browser_type = config.get("browser.type", "chromium")
        self.headless = config.get("browser.headless", True)
        self.user_agent = config.get("browser.user_agent")
        self.viewport = config.get("browser.viewport", {"width":
1920, "height": 1080})
        self.timeout = config.get("browser.timeout", 30000)
        self.browser_args = config.get("browser.args", [])
        
        # Human emulation settings
        self.human_emulation = config.get("browser.human_emulation.enabled", True)
        self.min_delay = config.get("browser.human_emulation.min_delay", 100)
        self.max_delay = config.get("browser.human_emulation.max_delay", 1500)
        self.mouse_movement = config.get("browser.human_emulation.mouse_movement", True)
        
        # Session storage path
        self.session_path = Path(config.get("auth.session_path", "data/sessions"))
        self.session_path.mkdir(parents=True, exist_ok=True)
        
        # Proxy settings
        self.proxy_enabled = config.get("proxy.enabled", False)
        self.proxy_config = self._get_proxy_config() if self.proxy_enabled else None
    
    def _get_proxy_config(self) -> Dict[str, str]:
        """
        Get proxy configuration from settings.
        
        Returns:
            Dictionary with proxy configuration
        """
        proxy_type = config.get("proxy.type", "http")
        
        # Check if we should use a provider
        for provider in ["brightdata", "oxylabs"]:
            if config.get(f"proxy.providers.{provider}.enabled", False):
                # Here we would implement provider-specific logic
                # For now, return a placeholder
                logger.info(f"Using {provider} proxy service")
                return {"server": f"{proxy_type}://proxy.example.com:8080"}
        
        # Default proxy settings if no provider is enabled
        return {
            "server": config.get("proxy.server", f"{proxy_type}://proxy.example.com:8080"),
            "username": config.get("proxy.username", ""),
            "password": config.get("proxy.password", ""),
        }

    async def initialize(self) -> None:
        """
        Initialize the browser manager.
        
        Launches Playwright and creates a browser instance.
        """
        if self._initialized:
            return
            
        logger.info(f"Initializing browser manager with {self.browser_type} browser")
        self._playwright = await async_playwright().start()
        
        # Select browser based on configuration
        if self.browser_type == "chromium":
            browser_instance = self._playwright.chromium
        elif self.browser_type == "firefox":
            browser_instance = self._playwright.firefox
        elif self.browser_type == "webkit":
            browser_instance = self._playwright.webkit
        else:
            logger.error(f"Invalid browser type: {self.browser_type}")
            raise ValueError(f"Invalid browser type: {self.browser_type}")
        
        # Launch browser with configured options
        self._browser = await browser_instance.launch(
            headless=self.headless,
            args=self.browser_args
        )
        
        self._initialized = True
        logger.info(f"Browser manager initialized with {self.browser_type}")
    
    async def create_context(self, 
                            load_session: bool = False, 
                            session_id: Optional[str] = None) -> BrowserContext:
        """
        Create a new browser context with anti-detection measures.
        
        Args:
            load_session: Whether to load a saved session
            session_id: ID of the session to load
            
        Returns:
            Browser context
        """
        if not self._initialized:
            await self.initialize()
        
        # Prepare context options with anti-fingerprinting measures
        context_options = self._get_stealth_context_options()
        
        # Add proxy if enabled
        if self.proxy_enabled and self.proxy_config:
            context_options["proxy"] = self.proxy_config
        
        # Load session state if requested
        if load_session and session_id:
            session_file = self.session_path / f"{session_id}.json"
            if session_file.exists():
                try:
                    with open(session_file, "r") as f:
                        storage_state = json.load(f)
                    context_options["storage_state"] = storage_state
                    logger.info(f"Loaded session from {session_file}")
                except Exception as e:
                    logger.error(f"Failed to load session: {e}")
        
        # Create the context
        context = await self._browser.new_context(**context_options)
        
        # Apply additional anti-bot measures
        await self._apply_stealth_scripts(context)
        
        self._context = context
        return context
    
    async def new_page(self, context: Optional[BrowserContext] = None) -> Page:
        """
        Create a new page in the specified or current context.
        
        Args:
            context: Browser context to create page in, or None to use current
            
        Returns:
            Page object
        """
        if context is None:
            if self._context is None:
                self._context = await self.create_context()
            context = self._context
        
        page = await context.new_page()
        
        # Set default timeout
        page.set_default_timeout(self.timeout)
        
        # Setup page event handlers
        await self._setup_page_handlers(page)
        
        self._page = page
        return page
    
    async def save_session(self, context: Optional[BrowserContext] = None, 
                          session_id: str = "default") -> None:
        """
        Save the current browser session to disk.
        
        Args:
            context: Browser context to save, or None to use current
            session_id: ID to save the session under
        """
        if context is None:
            context = self._context
        
        if context is None:
            logger.warning("Cannot save session: No browser context available")
            return
        
        storage_state = await context.storage_state()
        session_file = self.session_path / f"{session_id}.json"
        
        # Ensure the directory exists
        session_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(session_file, "w") as f:
            json.dump(storage_state, f, indent=2)
        
        logger.info(f"Saved session to {session_file}")
    
    def _get_stealth_context_options(self) -> Dict[str, Any]:
        """
        Get browser context options with anti-fingerprinting measures.
        
        Returns:
            Dictionary of context options
        """
        # Base options
        options = {
            "viewport": self.viewport,
            "user_agent": self.user_agent,
            "is_mobile": False,
            "has_touch": False,
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
            "color_scheme": "light",
            "reduced_motion": "no-preference",
            "forced_colors": "none",
            "accept_downloads": True,
        }
        
        # Add device scale factor for more natural appearance
        options["device_scale_factor"] = 1.0
        
        # Randomize viewport slightly to avoid exact matches
        if self.viewport:
            width_variance = random.randint(-5, 5)
            height_variance = random.randint(-5, 5)
            options["viewport"] = {
                "width": self.viewport["width"] + width_variance,
                "height": self.viewport["height"] + height_variance
            }
        
        # Permissions to grant
        options["permissions"] = ["geolocation"]
        
        return options
    
    async def _apply_stealth_scripts(self, context: BrowserContext) -> None:
        """
        Apply stealth scripts to the context to avoid bot detection.
        
        Args:
            context: Browser context to apply scripts to
        """
        # Common bot detection evasion scripts
        scripts = [
            # Mask WebDriver properties
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false
            });
            """,
            
            # Hide automation-related properties
            """
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' || 
                parameters.name === 'clipboard-read' || 
                parameters.name === 'clipboard-write' ?
                Promise.resolve({state: 'prompt', onchange: null}) :
                originalQuery(parameters)
            );
            """,
            
            # Mask Chrome properties in non-Chrome browsers
            """
            if (!window.chrome) {
                window.chrome = {
                    runtime: {}
                };
            }
            """,
            
            # Mask Playwright-specific properties
            """
            delete window.__playwright;
            delete window.playwright;
            """
        ]
        
        # Add scripts to the context
        for script in scripts:
            await context.add_init_script(script)
        
        logger.debug("Applied stealth scripts to browser context")
    
    async def _setup_page_handlers(self, page: Page) -> None:
        """
        Setup event handlers for the page.
        
        Args:
            page: Page to setup handlers for
        """
        # Log console messages from the page
        page.on("console", lambda msg: 
                logger.debug(f"Console {msg.type}: {msg.text}") 
                if msg.type != "error" else 
                logger.error(f"Console error: {msg.text}"))
        
        # Log page errors
        page.on("pageerror", lambda err: 
                logger.error(f"Page error: {err}"))
        
        # Log request failures
        page.on("requestfailed", lambda request: 
                logger.warning(f"Request failed: {request.url} - {request.failure}"))
    
    async def navigate(self, 
                     page: Page, 
                     url: str, 
                     wait_until: str = "networkidle") -> Response:
        """
        Navigate to a URL with human-like delay and behavior.
        
        Args:
            page: Page to navigate
            url: URL to navigate to
            wait_until: Navigation wait condition
            
        Returns:
            Response object
        """
        logger.info(f"Navigating to {url}")
        
        # Add a slight random delay before navigation
        if self.human_emulation:
            await self.random_delay()
        
        response = await page.goto(url, wait_until=wait_until)
        
        # Add post-navigation delay to simulate page reading
        if self.human_emulation:
            await self.random_delay(factor=3)  # Longer delay after page load
        
        return response
    
    async def click(self, 
                   page: Page, 
                   selector: str, 
                   delay: Optional[int] = None, 
                   human_like: Optional[bool] = None) -> None:
        """
        Click an element with human-like behavior.
        
        Args:
            page: Page to interact with
            selector: Element selector
            delay: Optional specific delay after click
            human_like: Whether to use human-like behavior, overrides global setting
        """
        human_like = self.human_emulation if human_like is None else human_like
        
        # Wait for element to be visible
        await page.wait_for_selector(selector, state="visible")
        
        if human_like:
            # Move mouse to element with slight randomization
            element = await page.query_selector(selector)
            if element:
                bounding_box = await element.bounding_box()
                if bounding_box:
                    x = bounding_box["x"] + bounding_box["width"] * random.uniform(0.2, 0.8)
                    y = bounding_box["y"] + bounding_box["height"] * random.uniform(0.2, 0.8)
                    
                    # Random mouse movement path
                    if self.mouse_movement:
                        await self._human_mouse_movement(page, x, y)
                    else:
                        await page.mouse.move(x, y)
                    
                    # Slight pause before clicking
                    await self.random_delay(50, 200)
        
        # Click the element
        await page.click(selector)
        
        # Wait after click
        await self.random_delay(delay or self.min_delay, self.max_delay)
    
    async def type(self, 
                  page: Page, 
                  selector: str, 
                  text: str, 
                  delay: Optional[Tuple[int, int]] = None) -> None:
        """
        Type text with human-like characteristics.
        
        Args:
            page: Page to interact with
            selector: Input element selector
            text: Text to type
            delay: Optional (min, max) typing delay in ms
        """
        # Wait for element to be visible
        await page.wait_for_selector(selector, state="visible")
        
        # Focus the element
        await page.focus(selector)
        await self.random_delay(50, 150)
        
        # Use default delay if not specified
        if delay is None:
            delay = (50, 150)  # Typical human typing speed variance
        
        # Type with variable speed
        for char in text:
            # Simulate human typing variation
            await page.keyboard.type(char, delay=random.randint(delay[0], delay[1]))
            
            # Occasional longer pause (as if thinking)
            if random.random() < 0.05:
                await self.random_delay(200, 500)
        
        # Slight pause after typing
        await self.random_delay()
    
    async def _human_mouse_movement(self, page: Page, target_x: float, target_y: float) -> None:
        """
        Simulate human-like mouse movement.
        
        Args:
            page: Page to interact with
            target_x: Target X coordinate
            target_y: Target Y coordinate
        """
        # Get current mouse position
        current_position = await page.evaluate("""
            () => {
                return {x: 100, y: 100};  // Default starting position
            }
        """)
        
        current_x = current_position.get("x", 0)
        current_y = current_position.get("y", 0)
        
        # Calculate distance
        distance_x = target_x - current_x
        distance_y = target_y - current_y
        distance = (distance_x ** 2 + distance_y ** 2) ** 0.5
        
        # Number of steps based on distance
        steps = min(max(int(distance / 10), 5), 25)
        
        # Generate slightly curved path using bezier curve approximation
        control_x = current_x + distance_x * 0.5 + random.uniform(-100, 100)
        control_y = current_y + distance_y * 0.5 + random.uniform(-100, 100)
        
        # Move mouse along path
        for i in range(1, steps + 1):
            t = i / steps
            # Quadratic bezier curve
            x = (1 - t) ** 2 * current_x + 2 * (1 - t) * t * control_x + t ** 2 * target_x
            y = (1 - t) ** 2 * current_y + 2 * (1 - t) * t * control_y + t ** 2 * target_y
            
            await page.mouse.move(x, y)
            await self.random_delay(5, 15)  # Small delay between movements
    
    async def random_delay(self, min_ms: Optional[int] = None, max_ms: Optional[int] = None,
                         factor: float = 1.0) -> None:
        """
        Wait for a random amount of time to simulate human behavior.
        
        Args:
            min_ms: Minimum delay in milliseconds
            max_ms: Maximum delay in milliseconds
            factor: Multiplier for the delay range
        """
        min_delay = (min_ms or self.min_delay) * factor
        max_delay = (max_ms or self.max_delay) * factor
        
        delay_ms = random.uniform(min_delay, max_delay)
        delay_s = delay_ms / 1000.0
        
        await playwright.async_api.Playwright.create_future(self._playwright, delay_s)
    
    async def wait_for_navigation(self, 
                                page: Page, 
                                url_includes: Optional[str] = None,
                                timeout: Optional[int] = None) -> Response:
        """
        Wait for navigation to complete, optionally to a URL containing a string.
        
        Args:
            page: Page to wait on
            url_includes: String that should be in the URL after navigation
            timeout: Maximum time to wait in milliseconds
            
        Returns:
            Response object
        """
        wait_options = {"timeout": timeout or self.timeout}
        
        if url_includes:
            wait_options["url"] = lambda url: url_includes in url
            
        return await page.wait_for_navigation(**wait_options)
    
    async def wait_and_click(self, 
                           page: Page, 
                           selector: str, 
                           timeout: Optional[int] = None) -> None:
        """
        Wait for an element to appear and then click it.
        
        Args:
            page: Page to interact with
            selector: Element selector
            timeout: Maximum time to wait in milliseconds
        """
        await page.wait_for_selector(selector, state="visible", 
                                  timeout=timeout or self.timeout)
        await self.click(page, selector)
    
    async def close(self) -> None:
        """Close all browser resources."""
        logger.info("Closing browser resources")
        
        if self._page:
            await self._page.close()
            self._page = None
        
        if self._context:
            await self._context.close()
            self._context = None
        
        if self._browser:
            await self._browser.close()
            self._browser = None
        
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        
        self._initialized = False
        logger.info("Browser resources closed")


# Singleton instance
browser_manager = BrowserManager()