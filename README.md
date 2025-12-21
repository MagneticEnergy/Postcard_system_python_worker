# Neighborhood Postcard System - Python Worker

This service runs the Playwright + Bright Data integration locally or on a server, bypassing N8N Cloud limitations.

## 🚀 Deployment on Railway

1.  **Push to GitHub**: Upload this folder to a new GitHub repository.
2.  **New Project**: In Railway, click "New Project" -> "GitHub Repo" and select your repo.
3.  **Variables**: Go to the **Variables** tab in Railway and add:
    -   `BRIGHT_DATA_AUTH`: `brd-customer-hl_ead19305-zone-scraping_browser1:f25aiw90s21r`
    -   `PORT`: `5000` (Railway usually sets this automatically, but good to verify)
4.  **Deploy**: Railway will build and deploy the worker.
5.  **URL**: Go to **Settings** -> **Networking** -> **Generate Domain** to get your public URL.

## Usage with N8N
-   **Method**: `POST`
-   **URL**: `https://your-railway-app.up.railway.app/scrape`
-   **Body**: `{"url": "https://www.redfin.com/..."}`
