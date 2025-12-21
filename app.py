import os
import json
import asyncio
import logging
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Bright Data Credentials
AUTH = os.environ.get('BRIGHT_DATA_AUTH', 'brd-customer-hl_ead19305-zone-scraping_browser1:f25aiw90s21r')
SBR_WS_CDP = f'wss://{AUTH}@brd.superproxy.io:9222'

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'postcard-worker'}), 200

async def scrape_images(url):
    logger.info(f"Starting scrape for: {url}")
    async with async_playwright() as pw:
        browser = None
        try:
            logger.info("Connecting to Bright Data Scraping Browser...")
            browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
            logger.info("Connected successfully.")
            
            # Retry logic
            max_retries = 3
            for attempt in range(max_retries):
                context = None
                page = None
                try:
                    logger.info(f"Attempt {attempt + 1}/{max_retries}")
                    context = await browser.new_context()
                    page = await context.new_page()
                    
                    # Navigate with lighter wait condition (domcontentloaded is faster/safer)
                    logger.info("Navigating...")
                    await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                    logger.info(f"Page loaded: {await page.title()}")
                    
                    # Wait for images (short wait)
                    try:
                        await page.wait_for_selector('img', timeout=10000)
                    except:
                        logger.warning("Timeout waiting for img selector, proceeding anyway")
                    
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
                    
                    logger.info(f"Extracted {len(images)} raw images")
                    
                    # Filter and Sort
                    valid_images = []
                    for img in images:
                        src = img['url'].lower()
                        if 'logo' in src or 'icon' in src or 'map' in src: continue
                        if img['width'] < 300 or img['height'] < 200: continue
                        valid_images.append(img)
                        
                    valid_images.sort(key=lambda x: x['width'] * x['height'], reverse=True)
                    result = valid_images[:12]
                    
                    logger.info(f"Returning {len(result)} valid images")
                    return result
                    
                except Exception as e:
                    logger.error(f"Error in attempt {attempt + 1}: {str(e)}")
                    if attempt == max_retries - 1:
                        raise e
                    # Wait before retry
                    await asyncio.sleep(2)
                finally:
                    if page:
                        await page.close()
                    if context:
                        await context.close()
                        
        finally:
            if browser:
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
        logger.error(f"Scrape failed: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
