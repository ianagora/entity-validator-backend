# Use Python 3.11 slim image (updated tag for better Railway compatibility)
FROM python:3.11.7-slim-bookworm

# Set working directory
WORKDIR /app

# Install only essential system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway uses dynamic PORT env var)
EXPOSE 8000

# Run the application using main.py
CMD ["python", "main.py"]
