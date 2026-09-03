FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

# Install system dependencies and Python packages
RUN apt-get update && apt-get install -y jq && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests playwright 
RUN playwright install chromium --with-deps

# Create app directory
WORKDIR /app

# Copy application files
COPY mod_download_counter.py /app/
COPY run.sh /app/

# Make run script executable
RUN chmod +x /app/run.sh

# Run the script
CMD ["/app/run.sh"]