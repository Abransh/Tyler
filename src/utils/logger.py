"""
Logging module for the BookMyShow Bot.

This module sets up logging for the entire application with support for
different output formats, log levels, and destinations.
"""

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from typing import Optional, Dict, Any, Union

from ..config import config


class Logger:
    """
    Logger setup and management for the BookMyShow Bot.
    
    Provides centralized logging configuration with support for
    console and file output, different log levels, and formatting.
    """
    
    def __init__(self):
        """Initialize the logger but don't configure it yet."""
        self._initialized = False
        self._root_logger = logging.getLogger()
        self._app_logger = logging.getLogger("bookmyshow_bot")
    
    def initialize(self, log_level: Optional[str] = None, log_file: Optional[str] = None) -> None:
        """
        Configure the logging system.
        
        Args:
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            log_file: Path to log file
        """
        if self._initialized:
            return
        
        # Get configuration from config if not explicitly provided
        log_level = log_level or config.get("app.log_level", "INFO")
        log_file = log_file or config.get("app.log_file")
        log_format = config.get("app.log_format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        
        # Convert log level string to constant
        numeric_level = self._get_log_level(log_level)
        
        # Configure root logger
        self._root_logger.setLevel(numeric_level)
        
        # Remove existing handlers if any
        for handler in self._root_logger.handlers[:]:
            self._root_logger.removeHandler(handler)
        
        # Create formatters
        formatter = logging.Formatter(log_format)
        
        # Create console handler
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        self._root_logger.addHandler(console_handler)
        
        # Create file handler if log file is specified
        if log_file:
            log_path = Path(log_file)
            
            # Ensure log directory exists
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create rotating file handler to avoid huge log files
            file_handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding="utf-8"
            )
            file_handler.setLevel(numeric_level)
            file_handler.setFormatter(formatter)
            self._root_logger.addHandler(file_handler)
        
        self._initialized = True
        self._app_logger.info(f"Logging initialized at level {log_level}")
    
    @staticmethod
    def _get_log_level(level: Union[str, int]) -> int:
        """
        Convert log level string to numeric constant.
        
        Args:
            level: Log level as string (DEBUG, INFO, etc.) or numeric constant
            
        Returns:
            Numeric log level constant
            
        Raises:
            ValueError: If level string is invalid
        """
        if isinstance(level, int):
            return level
            
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        
        if level.upper() not in level_map:
            raise ValueError(f"Invalid log level: {level}")
        
        return level_map[level.upper()]
    
    def get_logger(self, name: str) -> logging.Logger:
        """
        Get a logger with the specified name.
        
        The logger will be a child of the application logger.
        
        Args:
            name: Logger name (usually the module name)
            
        Returns:
            Logger instance
        """
        if not self._initialized:
            self.initialize()
            
        return logging.getLogger(f"bookmyshow_bot.{name}")


# Singleton instance
logger = Logger()

# Function to get a logger for a module
def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a module.
    
    Args:
        name: Module name or other identifier
        
    Returns:
        Configured logger instance
    """
    # Extract just the module name if __name__ was passed
    if name.startswith("__"):
        name = name.split(".")[-1]
        
    return logger.get_logger(name)