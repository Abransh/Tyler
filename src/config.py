"""
Configuration management module for the BookMyShow Bot.

This module handles loading, validating, and providing access to configuration
settings from YAML files and environment variables.
"""

import os
import logging
from typing import Any, Dict, Optional
import yaml
from pathlib import Path


class ConfigError(Exception):
    """Exception raised for configuration errors."""
    pass


class Config:
    """
    Configuration manager for the BookMyShow Bot.
    
    Handles loading configuration from YAML files and environment variables,
    with support for different environments (development, production).
    """

    def __init__(self, config_dir: str = "config", env: Optional[str] = None):
        """
        Initialize the configuration manager.
        
        Args:
            config_dir: Directory containing configuration files
            env: Environment to use (default, production, etc.). If None, will be read
                 from BOOKMYSHOW_BOT_ENV environment variable or default to 'default'
        """
        self.config_dir = Path(config_dir)
        self.env = env or os.environ.get('BOOKMYSHOW_BOT_ENV', 'default')
        self._config: Dict[str, Any] = {}
        self._initialized = False
        self._logger = logging.getLogger(__name__)

    def initialize(self) -> None:
        """Load configuration files and environment variables."""
        if self._initialized:
            return

        # Ensure config directory exists
        if not self.config_dir.exists():
            raise ConfigError(f"Configuration directory '{self.config_dir}' does not exist")

        # Load default configuration
        default_config_path = self.config_dir / "default.yaml"
        if not default_config_path.exists():
            raise ConfigError(f"Default configuration file '{default_config_path}' does not exist")
        
        with open(default_config_path, 'r') as f:
            self._config = yaml.safe_load(f) or {}
            
        # Load environment-specific configuration if it exists
        if self.env != 'default':
            env_config_path = self.config_dir / f"{self.env}.yaml"
            if env_config_path.exists():
                with open(env_config_path, 'r') as f:
                    env_config = yaml.safe_load(f) or {}
                    # Deep merge configurations
                    self._deep_update(self._config, env_config)
        
        # Override with environment variables
        self._load_from_env()
        
        self._initialized = True
        self._logger.info(f"Configuration loaded for environment: {self.env}")

    def _deep_update(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """
        Deep update target dict with source.
        
        Args:
            target: The target dictionary to update
            source: The source dictionary to update from
        """
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                self._deep_update(target[key], value)
            else:
                target[key] = value

    def _load_from_env(self) -> None:
        """
        Override configuration values from environment variables.
        
        Environment variables should be in the format:
        BOOKMYSHOW_BOT__SECTION__KEY=value
        
        Example:
        BOOKMYSHOW_BOT__BROWSER__HEADLESS=true
        """
        prefix = "BOOKMYSHOW_BOT__"
        for env_key, env_value in os.environ.items():
            if env_key.startswith(prefix):
                parts = env_key[len(prefix):].lower().split("__")
                
                if len(parts) < 2:
                    continue
                
                # Navigate to the correct config section
                config_section = self._config
                for part in parts[:-1]:
                    if part not in config_section:
                        config_section[part] = {}
                    config_section = config_section[part]
                
                # Set the value, converting strings to appropriate types
                config_section[parts[-1]] = self._convert_value(env_value)

    @staticmethod
    def _convert_value(value: str) -> Any:
        """
        Convert string value to appropriate Python type.
        
        Args:
            value: String value to convert
            
        Returns:
            Converted value as appropriate type
        """
        # Boolean conversion
        if value.lower() in ('true', 'yes', '1'):
            return True
        if value.lower() in ('false', 'no', '0'):
            return False
        
        # Try numeric conversion
        try:
            if '.' in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        
        # Return as string if no other conversion applies
        return value

    def get(self, path: str, default: Any = None) -> Any:
        """
        Get a configuration value by path.
        
        Args:
            path: Dot-separated path to configuration value (e.g., 'browser.headless')
            default: Default value to return if path not found
            
        Returns:
            Configuration value or default if not found
        """
        if not self._initialized:
            self.initialize()
            
        parts = path.split('.')
        value = self._config
        
        try:
            for part in parts:
                value = value[part]
            return value
        except (KeyError, TypeError):
            return default

    def __getitem__(self, path: str) -> Any:
        """
        Get a configuration value by path using dictionary syntax.
        
        Args:
            path: Dot-separated path to configuration value
            
        Returns:
            Configuration value
            
        Raises:
            KeyError: If path not found
        """
        value = self.get(path)
        if value is None:
            raise KeyError(f"Configuration path '{path}' not found")
        return value

    def as_dict(self) -> Dict[str, Any]:
        """
        Get the entire configuration as a dictionary.
        
        Returns:
            Dictionary containing all configuration values
        """
        if not self._initialized:
            self.initialize()
        return self._config.copy()


# Singleton instance for global access
config = Config()