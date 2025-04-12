"""
Tests for the configuration module.
"""

import os
import tempfile
from pathlib import Path
import pytest
import yaml

from src.config import Config, ConfigError


def test_config_loading():
    """Test that config can be loaded from a file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir) / "config"
        config_dir.mkdir()
        
        # Create test config file
        config_file = config_dir / "default.yaml"
        test_config = {
            "app": {
                "name": "Test App",
                "version": "1.0.0"
            },
            "browser": {
                "headless": True,
                "timeout": 30000
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(test_config, f)
        
        # Load the config
        config = Config(config_dir=str(config_dir))
        config.initialize()
        
        # Check values
        assert config.get("app.name") == "Test App"
        assert config.get("app.version") == "1.0.0"
        assert config.get("browser.headless") is True
        assert config.get("browser.timeout") == 30000


def test_config_environment_override():
    """Test that environment variables override config values."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir) / "config"
        config_dir.mkdir()
        
        # Create test config file
        config_file = config_dir / "default.yaml"
        test_config = {
            "app": {
                "name": "Test App",
                "log_level": "INFO"
            },
            "browser": {
                "headless": True
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(test_config, f)
        
        # Set environment variables
        os.environ["BOOKMYSHOW_BOT__APP__LOG_LEVEL"] = "DEBUG"
        os.environ["BOOKMYSHOW_BOT__BROWSER__HEADLESS"] = "false"
        
        # Load the config
        config = Config(config_dir=str(config_dir))
        config.initialize()
        
        # Check values (should be overridden by env vars)
        assert config.get("app.log_level") == "DEBUG"
        assert config.get("browser.headless") is False
        
        # Clean up
        del os.environ["BOOKMYSHOW_BOT__APP__LOG_LEVEL"]
        del os.environ["BOOKMYSHOW_BOT__BROWSER__HEADLESS"]


def test_config_missing_directory():
    """Test that an error is raised when the config directory doesn't exist."""
    config = Config(config_dir="/nonexistent/directory")
    with pytest.raises(ConfigError):
        config.initialize()


def test_config_dict_access():
    """Test dictionary-style access to config values."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir) / "config"
        config_dir.mkdir()
        
        # Create test config file
        config_file = config_dir / "default.yaml"
        test_config = {
            "app": {
                "name": "Test App"
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(test_config, f)
        
        # Load the config
        config = Config(config_dir=str(config_dir))
        config.initialize()
        
        # Test dictionary access
        assert config["app.name"] == "Test App"
        
        # Test non-existent key
        with pytest.raises(KeyError):
            _ = config["nonexistent.key"]


def test_config_default_values():
    """Test that default values are returned for missing keys."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir) / "config"
        config_dir.mkdir()
        
        # Create test config file
        config_file = config_dir / "default.yaml"
        test_config = {
            "app": {
                "name": "Test App"
            }
        }
        
        with open(config_file, 'w') as f:
            yaml.dump(test_config, f)
        
        # Load the config
        config = Config(config_dir=str(config_dir))
        config.initialize()
        
        # Test default value
        assert config.get("nonexistent.key", "default") == "default"
        assert config.get("app.nonexistent", 123) == 123