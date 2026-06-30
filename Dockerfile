FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HEADLESS=false

WORKDIR /app

# Install Python dependencies, Chromium with its OS packages, and Xvfb so the
# non-headless browser that Costco requires can run without a physical display.
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

EXPOSE 8000

# Run under a virtual display so headed Chromium works inside the container.
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1280x1024x24", \
     "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
