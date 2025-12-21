import os
import json
import asyncio
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright

app = Flask(__name__)

# Bright Data Credentials
AUTH = 'brd-customer-hl_ead19305-zone-scraping_browser1:f25aiw90s21r'
SBR_WS_CDP = f'wss://{AUTH}@brd.superproxy.io:9222'

async def scrape_images(url):
    print(f"Connecting to Scraping Browser for {url}...")
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
        try:
            page = await browser.new_page()
            await page.goto(url, timeout=120000)
            print(f"Page loaded: {await page.title()}")
            
            # Wait for images to load
            await page.wait_for_selector('img', timeout=30000)
            
            # Extract images
            images = await page.evaluate('''() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                return imgs.map(img => ({
                    url: img.src,
                    width: img.naturalWidth,
                    height: img.naturalHeight,
                    alt: img.alt
                })).filter(img => img.url.startsWith('http'));
            }''')
            
            # Filter and Sort (Logic from V22)
            valid_images = []
            for img in images:
                src = img['url'].lower()
                if 'logo' in src or 'icon' in src or 'map' in src: continue
                if img['width'] < 300 or img['height'] < 200: continue
                valid_images.append(img)
                
            # Sort by size (descending)
            valid_images.sort(key=lambda x: x['width'] * x['height'], reverse=True)
            
            return valid_images[:12] # Limit to 12
            
        finally:
            await browser.close()

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    try:
        images = asyncio.run(scrape_images(url))
        return jsonify({'success': True, 'images': images})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
