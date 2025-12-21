# How to Host the Python Worker

Since this worker uses **Playwright** (a headless browser), it requires a hosting service that supports **Docker Containers**. Standard "Python Hosting" (like PythonAnywhere) often fails because they don't allow installing browsers.

Here are the two easiest, inexpensive options:

## Option 1: Railway (Recommended)
**Cost**: Pay-as-you-go (approx $5/mo), very stable.
1.  Create an account at [railway.app](https://railway.app/).
2.  Click **"New Project"** -> **"Empty Project"**.
3.  Choose **"GitHub Repo"** (if you push this code to GitHub) OR install the **Railway CLI** to deploy from your computer.
4.  Railway will automatically detect the `Dockerfile` and build it.
5.  Once deployed, go to **Settings** -> **Generate Domain**.
6.  Copy that URL (e.g., `https://postcard-worker.up.railway.app`).
7.  **Update N8N**: Paste `https://postcard-worker.up.railway.app/scrape` into your V23 workflow.

## Option 2: Render
**Cost**: Free tier available (spins down after inactivity), Paid starts at $7/mo.
1.  Create an account at [render.com](https://render.com/).
2.  Click **"New +"** -> **"Web Service"**.
3.  Connect your GitHub repository containing this folder.
4.  Select **"Docker"** as the Runtime.
5.  Click **"Create Web Service"**.
6.  Copy the URL provided (e.g., `https://postcard-worker.onrender.com`).
7.  **Update N8N**: Paste `https://postcard-worker.onrender.com/scrape` into your V23 workflow.

## Why not PythonAnywhere?
Services like PythonAnywhere are great for simple scripts, but they usually **block** the installation of Chrome/Chromium, which this worker needs to run the Bright Data browser connection.
