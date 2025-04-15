# Use Python 3.11 as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    gnupg \
    ca-certificates \
    apt-transport-https \
    libssl-dev \
    tesseract-ocr \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Microsoft Playwright dependencies
RUN pip install playwright && \
    playwright install-deps && \
    playwright install

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create necessary directories
RUN mkdir -p data/sessions data/events data/screenshots data/bookings

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV BOOKMYSHOW_BOT_ENV=production

# Create a volume for persistent data
VOLUME /app/data
VOLUME /app/config
VOLUME /app/logs

# Port for potential future web interface
EXPOSE 8080

# Run the application
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--help"]