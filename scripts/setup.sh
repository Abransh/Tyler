#!/bin/bash

# BookMyShow Bot Setup Script

echo "Setting up BookMyShow Ticket Bot..."

# Check if Python 3.11+ is installed
python_version=$(python --version 2>&1 | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+')
python_major=$(echo $python_version | cut -d. -f1)
python_minor=$(echo $python_version | cut -d. -f2)

if [[ -z "$python_version" || "$python_major" -lt 3 || ("$python_major" -eq 3 && "$python_minor" -lt 11) ]]; then
    echo "Error: Python 3.11+ is required but found $python_version"
    echo "Please install Python 3.11 or higher and try again."
    exit 1
fi

echo "Python version $python_version detected."

# Create virtual environment if it doesn't exist
if [[ ! -d "venv" ]]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [[ $? -ne 0 ]]; then
        echo "Error: Failed to create virtual environment."
        exit 1
    fi
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
python -m pip install -r requirements.txt
if [[ $? -ne 0 ]]; then
    echo "Error: Failed to install dependencies."
    exit 1
fi

# Install Playwright browsers
echo "Installing Playwright browsers..."
python -m playwright install
if [[ $? -ne 0 ]]; then
    echo "Error: Failed to install Playwright browsers."
    exit 1
fi

# Create necessary directories
echo "Creating directories..."
mkdir -p logs data/sessions data/events

# Create sample .env file if it doesn't exist
if [[ ! -f ".env" ]]; then
    echo "Creating sample .env file..."
    cat > .env << EOF
# BookMyShow Bot Environment Variables
BOOKMYSHOW_BOT_ENV=default

# Override configuration settings
# BOOKMYSHOW_BOT__BROWSER__HEADLESS=false
# BOOKMYSHOW_BOT__NOTIFICATION__EMAIL__ENABLED=true
# BOOKMYSHOW_BOT__NOTIFICATION__EMAIL__SMTP_SERVER=smtp.gmail.com
# BOOKMYSHOW_BOT__NOTIFICATION__EMAIL__SMTP_USER=your-email@gmail.com
# BOOKMYSHOW_BOT__NOTIFICATION__EMAIL__SMTP_PASSWORD=your-app-password
EOF
fi

echo "Setup complete! You can now use the BookMyShow Ticket Bot."
echo ""
echo "Try adding an event to track:"
echo "python -m src.main add \"https://in.bookmyshow.com/events/event-name/ET00123456\""
echo ""
echo "Then start monitoring:"
echo "python -m src.main monitor"
echo ""
echo "For more information, see the README.md file."