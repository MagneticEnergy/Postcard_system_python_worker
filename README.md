# Neighborhood Postcard System - Python Worker

This service runs the Playwright + Bright Data integration locally or on a server, bypassing N8N Cloud limitations.

## Setup
1. Install Docker.
2. Build the image:
   ```bash
   docker build -t postcard-worker .
   ```
3. Run the container:
   ```bash
   docker run -p 5000:5000 postcard-worker
   ```

## Usage with N8N
- Use an **HTTP Request Node** in N8N.
- Method: `POST`
- URL: `http://YOUR_SERVER_IP:5000/scrape` (or `http://host.docker.internal:5000/scrape` if running N8N locally)
- Body: `{"url": "https://www.redfin.com/..."}`
