"""
CAPTCHA solving module for the BookMyShow Bot.

This module handles detection and solving of CAPTCHAs that may appear
during the booking process, using external services and OCR techniques.
"""

import os
import time
import base64
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional, Any, Tuple, Union

import requests
from playwright.async_api import Page, ElementHandle

from ..config import config
from ..utils.logger import get_logger


logger = get_logger(__name__)


class CaptchaError(Exception):
    """Exception raised for CAPTCHA-related errors."""
    pass


class CaptchaSolver:
    """
    Handles detection and solving of CAPTCHAs on BookMyShow.
    
    Supports multiple solving methods including external services (2Captcha, Anti-Captcha)
    and local OCR-based solutions for simple CAPTCHAs.
    """
    
    def __init__(self):
        """Initialize the CAPTCHA solver."""
        self.service = config.get("captcha.service", "2captcha")
        self.max_retries = config.get("captcha.max_retries", 3)
        self.timeout = config.get("captcha.timeout", 60)
        
        # API keys for external services
        self.twocaptcha_key = config.get("captcha.2captcha.api_key", "")
        self.anticaptcha_key = config.get("captcha.anticaptcha.api_key", "")
        
        # Check if OCR is available
        self.ocr_available = self._check_ocr_availability()
    
    def _check_ocr_availability(self) -> bool:
        """
        Check if OCR capabilities are available.
        
        Returns:
            True if OCR is available, False otherwise
        """
        try:
            import pytesseract
            return True
        except ImportError:
            return False
    
    async def detect_captcha(self, page: Page) -> Tuple[bool, Optional[ElementHandle]]:
        """
        Detect if a CAPTCHA is present on the page.
        
        Args:
            page: Page to check for CAPTCHA
            
        Returns:
            Tuple of (is_captcha_present, captcha_element)
        """
        logger.info("Checking for CAPTCHA presence")
        
        # Common CAPTCHA indicators
        captcha_selectors = [
            "img[alt*='captcha' i]",
            "img[src*='captcha' i]",
            "div.captcha",
            ".captcha-container",
            "label:has-text('CAPTCHA')",
            "text=Enter the characters shown",
            "text=I'm not a robot",
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "iframe[title*='recaptcha' i]",
            "iframe[title*='hcaptcha' i]"
        ]
        
        for selector in captcha_selectors:
            captcha_element = await page.query_selector(selector)
            if captcha_element:
                logger.info(f"CAPTCHA detected with selector: {selector}")
                return True, captcha_element
        
        # Check for ReCAPTCHA iframe
        frames = page.frames
        for frame in frames:
            url = frame.url
            if "recaptcha" in url or "hcaptcha" in url:
                logger.info(f"CAPTCHA iframe detected: {url}")
                return True, None
        
        logger.debug("No CAPTCHA detected")
        return False, None
    
    async def solve_captcha(self, page: Page) -> bool:
        """
        Detect and solve a CAPTCHA on the page.
        
        Args:
            page: Page with possible CAPTCHA
            
        Returns:
            True if CAPTCHA was solved successfully, False otherwise
        """
        # Detect CAPTCHA
        has_captcha, captcha_element = await self.detect_captcha(page)
        
        if not has_captcha:
            logger.debug("No CAPTCHA to solve")
            return True
        
        # Determine CAPTCHA type
        captcha_type = await self._determine_captcha_type(page, captcha_element)
        logger.info(f"Detected CAPTCHA type: {captcha_type}")
        
        # Solve based on type
        for attempt in range(1, self.max_retries + 1):
            logger.info(f"CAPTCHA solving attempt {attempt}/{self.max_retries}")
            
            try:
                if captcha_type == "image":
                    success = await self._solve_image_captcha(page, captcha_element)
                elif captcha_type == "recaptcha":
                    success = await self._solve_recaptcha(page)
                elif captcha_type == "hcaptcha":
                    success = await self._solve_hcaptcha(page)
                else:
                    logger.warning(f"Unsupported CAPTCHA type: {captcha_type}")
                    return False
                
                if success:
                    logger.info("CAPTCHA solved successfully")
                    return True
                    
            except Exception as e:
                logger.error(f"Error solving CAPTCHA: {str(e)}")
            
            # Wait before retry
            if attempt < self.max_retries:
                await page.wait_for_timeout(2000)
        
        logger.error("Failed to solve CAPTCHA after multiple attempts")
        return False
    
    async def _determine_captcha_type(self, 
                                    page: Page, 
                                    captcha_element: Optional[ElementHandle]) -> str:
        """
        Determine the type of CAPTCHA present.
        
        Args:
            page: Page with CAPTCHA
            captcha_element: CAPTCHA element if found
            
        Returns:
            CAPTCHA type (image, recaptcha, hcaptcha)
        """
        # Check for ReCAPTCHA
        recaptcha_present = await page.query_selector("iframe[src*='recaptcha'], iframe[title*='recaptcha' i]")
        if recaptcha_present:
            return "recaptcha"
        
        # Check for hCaptcha
        hcaptcha_present = await page.query_selector("iframe[src*='hcaptcha'], iframe[title*='hcaptcha' i]")
        if hcaptcha_present:
            return "hcaptcha"
        
        # If it's an image captcha
        if captcha_element:
            tag_name = await captcha_element.get_property("tagName")
            tag_name = await tag_name.json_value()
            
            if tag_name.lower() == "img":
                return "image"
        
        # Default to image if we can't determine
        return "image"
    
    async def _solve_image_captcha(self, page: Page, captcha_element: ElementHandle) -> bool:
        """
        Solve an image-based CAPTCHA.
        
        Args:
            page: Page with CAPTCHA
            captcha_element: CAPTCHA image element
            
        Returns:
            True if solved successfully, False otherwise
        """
        try:
            # First, try local OCR if available for simple CAPTCHAs
            if self.ocr_available:
                ocr_solution = await self._solve_with_ocr(page, captcha_element)
                if ocr_solution:
                    success = await self._enter_captcha_solution(page, ocr_solution)
                    if success:
                        return True
            
            # If OCR fails or not available, use external service
            if self.service == "2captcha" and self.twocaptcha_key:
                solution = await self._solve_with_2captcha(page, captcha_element)
            elif self.service == "anticaptcha" and self.anticaptcha_key:
                solution = await self._solve_with_anticaptcha(page, captcha_element)
            elif self.service == "manual":
                solution = await self._solve_manually(page, captcha_element)
            else:
                logger.error(f"No CAPTCHA service configured or invalid service: {self.service}")
                return False
            
            if not solution:
                logger.warning("Failed to get CAPTCHA solution")
                return False
            
            # Enter the solution
            return await self._enter_captcha_solution(page, solution)
            
        except Exception as e:
            logger.error(f"Error solving image CAPTCHA: {str(e)}")
            return False
    
    async def _solve_with_ocr(self, page: Page, captcha_element: ElementHandle) -> Optional[str]:
        """
        Attempt to solve a CAPTCHA using local OCR.
        
        Args:
            page: Page with CAPTCHA
            captcha_element: CAPTCHA image element
            
        Returns:
            CAPTCHA solution or None if failed
        """
        try:
            # Import pytesseract
            import pytesseract
            from PIL import Image, ImageEnhance, ImageFilter
            
            # Take screenshot of the CAPTCHA element
            screenshot_buffer = await captcha_element.screenshot()
            
            # Create a temporary file for the image
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(screenshot_buffer)
                tmp_path = tmp.name
            
            try:
                # Open and preprocess the image
                img = Image.open(tmp_path)
                
                # Apply some preprocessing to make OCR more effective
                img = img.convert("L")  # Convert to grayscale
                img = img.point(lambda x: 0 if x < 128 else 255)  # Convert to binary
                img = ImageEnhance.Contrast(img).enhance(2)  # Increase contrast
                img = img.filter(ImageFilter.MedianFilter())  # Apply median filter
                
                # Use pytesseract to get the text
                text = pytesseract.image_to_string(img, config='--psm 7 --oem 1 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
                
                # Clean up the text
                solution = re.sub(r'[^0-9A-Za-z]', '', text)
                
                logger.info(f"OCR detected text: {solution}")
                
                if len(solution) >= 4:  # Most CAPTCHAs are at least 4 characters
                    return solution
                return None
                
            finally:
                # Clean up the temporary file
                try:
                    os.unlink(tmp_path)
                except:
                    pass
                
        except Exception as e:
            logger.warning(f"OCR solving attempt failed: {str(e)}")
            return None
    
    async def _solve_with_2captcha(self, page: Page, captcha_element: ElementHandle) -> Optional[str]:
        """
        Solve CAPTCHA using 2Captcha service.
        
        Args:
            page: Page with CAPTCHA
            captcha_element: CAPTCHA image element
            
        Returns:
            CAPTCHA solution or None if failed
        """
        if not self.twocaptcha_key:
            logger.error("2Captcha API key not configured")
            return None
        
        try:
            # Get the image as base64
            image_data = await captcha_element.screenshot()
            base64_image = base64.b64encode(image_data).decode('utf-8')
            
            # Submit to 2Captcha
            url = f"https://2captcha.com/in.php"
            data = {
                "key": self.twocaptcha_key,
                "method": "base64",
                "body": base64_image,
                "json": 1
            }
            
            logger.debug("Submitting CAPTCHA to 2Captcha")
            response = requests.post(url, data=data)
            response_data = response.json()
            
            if response_data["status"] != 1:
                logger.error(f"2Captcha submission error: {response_data.get('request')}")
                return None
            
            captcha_id = response_data["request"]
            logger.debug(f"CAPTCHA submitted, ID: {captcha_id}")
            
            # Wait for the solution
            result_url = f"https://2captcha.com/res.php?key={self.twocaptcha_key}&action=get&id={captcha_id}&json=1"
            start_time = time.time()
            
            while time.time() - start_time < self.timeout:
                await page.wait_for_timeout(5000)  # Wait 5 seconds between checks
                
                response = requests.get(result_url)
                result_data = response.json()
                
                if result_data["status"] == 1:
                    solution = result_data["request"]
                    logger.info(f"2Captcha solution received: {solution}")
                    return solution
                
                if result_data["request"] != "CAPCHA_NOT_READY":
                    logger.error(f"2Captcha error: {result_data['request']}")
                    return None
            
            logger.warning("2Captcha solution timed out")
            return None
            
        except Exception as e:
            logger.error(f"Error using 2Captcha: {str(e)}")
            return None
    
    async def _solve_with_anticaptcha(self, page: Page, captcha_element: ElementHandle) -> Optional[str]:
        """
        Solve CAPTCHA using Anti-Captcha service.
        
        Args:
            page: Page with CAPTCHA
            captcha_element: CAPTCHA image element
            
        Returns:
            CAPTCHA solution or None if failed
        """
        # This is a placeholder for Anti-Captcha implementation
        # Similar to 2Captcha but with different API endpoints
        logger.warning("Anti-Captcha implementation not yet available")
        return None
    
    async def _solve_manually(self, page: Page, captcha_element: ElementHandle) -> Optional[str]:
        """
        Allow user to solve CAPTCHA manually.
        
        Args:
            page: Page with CAPTCHA
            captcha_element: CAPTCHA image element
            
        Returns:
            CAPTCHA solution or None if failed
        """
        try:
            # Take screenshot of the CAPTCHA
            screenshot_buffer = await captcha_element.screenshot()
            
            # Save to a temporary file
            temp_dir = Path("data/temp")
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            captcha_path = temp_dir / f"captcha_{int(time.time())}.png"
            with open(captcha_path, "wb") as f:
                f.write(screenshot_buffer)
            
            logger.info(f"CAPTCHA image saved to {captcha_path}")
            logger.info("Please check the image and enter the CAPTCHA solution:")
            
            # Wait for user input
            # In a real implementation, this might use a UI or wait for a file to be updated
            solution = input("Enter CAPTCHA solution: ")
            
            if solution:
                logger.info(f"Manual CAPTCHA solution entered: {solution}")
                return solution
            
            return None
            
        except Exception as e:
            logger.error(f"Error with manual CAPTCHA solving: {str(e)}")
            return None
    
    async def _enter_captcha_solution(self, page: Page, solution: str) -> bool:
        """
        Enter the CAPTCHA solution on the page.
        
        Args:
            page: Page with CAPTCHA
            solution: CAPTCHA solution to enter
            
        Returns:
            True if solution was entered successfully, False otherwise
        """
        try:
            # Find the CAPTCHA input field
            input_selectors = [
                "input[name*='captcha' i]",
                "input[placeholder*='captcha' i]",
                "input.captcha-input",
                "input[name='verification']",
                "input[placeholder*='characters' i]",
                "input[type='text'][placeholder]"
            ]
            
            for selector in input_selectors:
                input_field = await page.query_selector(selector)
                if input_field:
                    # Clear the field
                    await input_field.fill("")
                    
                    # Type the solution
                    await input_field.type(solution, delay=100)
                    logger.debug(f"Entered CAPTCHA solution in field with selector: {selector}")
                    
                    # Find and click submit button
                    submit_selectors = [
                        "button[type='submit']",
                        "input[type='submit']",
                        "button:has-text('Submit')",
                        "button:has-text('Verify')",
                        "button:has-text('Continue')"
                    ]
                    
                    for submit_selector in submit_selectors:
                        submit_button = await page.query_selector(submit_selector)
                        if submit_button:
                            await submit_button.click()
                            logger.debug(f"Clicked submit button with selector: {submit_selector}")
                            
                            # Wait for response
                            await page.wait_for_timeout(3000)
                            
                            # Check if CAPTCHA is still present
                            has_captcha, _ = await self.detect_captcha(page)
                            if not has_captcha:
                                logger.info("CAPTCHA no longer present, solution was accepted")
                                return True
                            break
                    break
            
            logger.warning("Could not find CAPTCHA input field or submit button")
            return False
            
        except Exception as e:
            logger.error(f"Error entering CAPTCHA solution: {str(e)}")
            return False
    
    async def _solve_recaptcha(self, page: Page) -> bool:
        """
        Solve a ReCAPTCHA challenge.
        
        Args:
            page: Page with ReCAPTCHA
            
        Returns:
            True if solved successfully, False otherwise
        """
        if self.service != "2captcha" or not self.twocaptcha_key:
            logger.error("ReCAPTCHA solving requires 2Captcha service")
            return False
        
        try:
            # Get site key
            site_key = await self._extract_recaptcha_site_key(page)
            if not site_key:
                logger.error("Could not extract ReCAPTCHA site key")
                return False
            
            page_url = page.url
            
            # Submit to 2Captcha
            url = "https://2captcha.com/in.php"
            data = {
                "key": self.twocaptcha_key,
                "method": "userrecaptcha",
                "googlekey": site_key,
                "pageurl": page_url,
                "json": 1
            }
            
            logger.debug("Submitting ReCAPTCHA to 2Captcha")
            response = requests.post(url, data=data)
            response_data = response.json()
            
            if response_data["status"] != 1:
                logger.error(f"2Captcha submission error: {response_data.get('request')}")
                return False
            
            captcha_id = response_data["request"]
            logger.debug(f"ReCAPTCHA submitted, ID: {captcha_id}")
            
            # Wait for the solution
            result_url = f"https://2captcha.com/res.php?key={self.twocaptcha_key}&action=get&id={captcha_id}&json=1"
            start_time = time.time()
            
            while time.time() - start_time < self.timeout:
                await page.wait_for_timeout(5000)  # Wait 5 seconds between checks
                
                response = requests.get(result_url)
                result_data = response.json()
                
                if result_data["status"] == 1:
                    solution = result_data["request"]
                    logger.info("ReCAPTCHA solution received")
                    
                    # Apply solution
                    await page.evaluate(f"""
                        document.querySelector("textarea[name='g-recaptcha-response']").innerHTML = "{solution}";
                        try {{
                            window.___grecaptcha_cfg.clients[0].K.K.callback("{solution}");
                        }} catch (e) {{
                            // Different structure, try alternative approach
                            try {{
                                Object.keys(___grecaptcha_cfg.clients).forEach(function(key) {{
                                    var item = ___grecaptcha_cfg.clients[key];
                                    try {{
                                        item.K.K.callback("{solution}");
                                        return true;
                                    }} catch (e) {{
                                        return false;
                                    }}
                                }});
                            }} catch (e) {{
                                // Fallback for other structures
                                document.getElementById("g-recaptcha-response").innerHTML = "{solution}";
                                document.querySelector("form").submit();
                            }}
                        }}
                    """)
                    
                    # Wait for navigation or page change
                    await page.wait_for_timeout(5000)
                    
                    # Check if ReCAPTCHA is still present
                    recaptcha_present = await page.query_selector("iframe[src*='recaptcha']")
                    if not recaptcha_present:
                        logger.info("ReCAPTCHA no longer present, solution was accepted")
                        return True
                    
                    logger.warning("ReCAPTCHA still present after applying solution")
                    return False
                
                if result_data["request"] != "CAPCHA_NOT_READY":
                    logger.error(f"2Captcha error: {result_data['request']}")
                    return False
            
            logger.warning("ReCAPTCHA solution timed out")
            return False
            
        except Exception as e:
            logger.error(f"Error solving ReCAPTCHA: {str(e)}")
            return False
    
    async def _extract_recaptcha_site_key(self, page: Page) -> Optional[str]:
        """
        Extract ReCAPTCHA site key from the page.
        
        Args:
            page: Page with ReCAPTCHA
            
        Returns:
            Site key or None if not found
        """
        try:
            # Look for site key in data-sitekey attribute
            site_key = await page.evaluate("""
                () => {
                    const element = document.querySelector("[data-sitekey]");
                    if (element) {
                        return element.getAttribute("data-sitekey");
                    }
                    
                    // Check in grecaptcha configs
                    if (typeof ___grecaptcha_cfg !== 'undefined') {
                        return Object.keys(___grecaptcha_cfg.clients).map(key => {
                            return ___grecaptcha_cfg.clients[key].id;
                        })[0];
                    }
                    
                    // Check in page source
                    const match = document.body.innerHTML.match(/['"](6L[\\w-]{38})['"]/);
                    return match ? match[1] : null;
                }
            """)
            
            if site_key:
                logger.debug(f"Extracted ReCAPTCHA site key: {site_key}")
                return site_key
            
            logger.warning("Could not extract ReCAPTCHA site key")
            return None
            
        except Exception as e:
            logger.error(f"Error extracting ReCAPTCHA site key: {str(e)}")
            return None
    
    async def _solve_hcaptcha(self, page: Page) -> bool:
        """
        Solve an hCaptcha challenge.
        
        Args:
            page: Page with hCaptcha
            
        Returns:
            True if solved successfully, False otherwise
        """
        # Similar to ReCAPTCHA but with different parameters
        # This is a placeholder for hCaptcha implementation
        logger.warning("hCaptcha solving not yet implemented")
        return False


# Singleton instance
captcha_solver = CaptchaSolver()