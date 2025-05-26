"""
config_loader.py
---------------
Configuration loader for Tax Automation Project

Handles secure loading of API keys and other configuration from:
1. Environment variables
2. .env files
3. Config files

Author: Tax Automation Team
"""

import os
from pathlib import Path
from typing import Optional

def load_config_from_file(config_file: str = "config.env") -> dict:
    """Load configuration from .env file"""
    config = {}
    config_path = Path(config_file)
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
    
    return config

def get_gemini_api_key() -> Optional[str]:
    """
    Get Gemini API key from environment variables or config file
    
    Priority:
    1. Environment variable GEMINI_API_KEY
    2. config.env file
    """
    # Try environment variable first
    api_key = os.getenv('GEMINI_API_KEY')
    if api_key:
        return api_key
    
    # Try config file
    config = load_config_from_file()
    return config.get('GEMINI_API_KEY')

def get_gemini_model_name() -> str:
    """Get Gemini model name with fallback to default"""
    # Try environment variable first
    model_name = os.getenv('GEMINI_MODEL_NAME')
    if model_name:
        return model_name
    
    # Try config file
    config = load_config_from_file()
    return config.get('GEMINI_MODEL_NAME', 'gemini-2.0-flash')

def validate_config() -> bool:
    """Validate that required configuration is available"""
    api_key = get_gemini_api_key()
    if not api_key:
        print("ERROR: Gemini API key not found!")
        print("Please set GEMINI_API_KEY environment variable or add it to config.env")
        return False
    
    print(f"✅ Configuration loaded successfully")
    print(f"✅ Gemini model: {get_gemini_model_name()}")
    return True

if __name__ == "__main__":
    # Test configuration loading
    validate_config() 