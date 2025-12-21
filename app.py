import os
import json
import asyncio
import logging
import re
import base64
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

AUTH = os.environ.get('BRIGHT_DATA_AUTH', 'brd-customer-hl_ead19305-zone-scraping_browser1:f25aiw90s21r')
SBR_WS_CDP = f'wss://{AUTH}@brd.superproxy.io:9222'

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'postcard-worker'}), 200

async def scrape_images(url, capture_screenshot=False):
    logger.info(f"Starting scrape for: {url}")
    
    async with async_playwright() as pw:
        browser = None
        try:
            logger.info("Connecting to Bright Data Scraping Browser...")
            browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
            logger.info("Connected!")
            
            # Create page directly as per Bright Data docs
            page = await browser.new_page()
            
            try:
                logger.info(f"Navigating to {url}...")
                # Use longer timeout and wait for load
                await page.goto(url, timeout=2*60*1000, wait_until='load')
                
                # Get page info
                title = await page.title()
                current_url = page.url
                logger.info(f"Page title: {title}")
                logger.info(f"Current URL: {current_url}")
                
                # Wait for content to render
                await asyncio.sleep(5)
                
                # Capture screenshot if requested
                screenshot_b64 = None
                if capture_screenshot:
                    screenshot_bytes = await page.screenshot(full_page=False)
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                    logger.info("Screenshot captured")
                
                # Check for CAPTCHA or block page
                page_content = await page.content()
                is_blocked = any(x in page_content.lower() for x in [
                    'captcha', 'robot', 'blocked', 'access denied', 
                    'please verify', 'security check'
                ])
                
                if is_blocked:
                    logger.warning("Page appears to be blocked or showing CAPTCHA")
                
                images = []
                
                # REDFIN: Try to extract from __NEXT_DATA__ first
                if 'redfin.com' in url:
                    logger.info("Redfin detected")
                    
                    # Check for __NEXT_DATA__
                    next_data = await page.evaluate('''
                        () => {
                            const script = document.querySelector('script#__NEXT_DATA__');
                            if (script) {
                                try {
                                    return JSON.parse(script.textContent);
                                } catch (e) {
                                    return {parseError: e.message};
                                }
                            }
                            return null;
                        }
                    ''')
                    
                    if next_data and not next_data.get('parseError'):
                        logger.info("Found __NEXT_DATA__")
                        # Try to find photos in the data
                        def find_photos(obj, depth=0):
                            if depth > 15:
                                return []
                            photos = []
                            if isinstance(obj, dict):
                                # Look for photo arrays
                                for key in ['photos', 'photoUrls', 'images', 'media']:
                                    if key in obj and isinstance(obj[key], list):
                                        for item in obj[key]:
                                            if isinstance(item, str) and item.startswith('http'):
                                                photos.append({'url': item, 'source': 'nextdata'})
                                            elif isinstance(item, dict):
                                                for url_key in ['url', 'photoUrl', 'src', 'href']:
                                                    if url_key in item and isinstance(item[url_key], str):
                                                        photos.append({
                                                            'url': item[url_key],
                                                            'width': item.get('width', 0),
                                                            'height': item.get('height', 0),
                                                            'source': 'nextdata'
                                                        })
                                                        break
                                for v in obj.values():
                                    photos.extend(find_photos(v, depth+1))
                            elif isinstance(obj, list):
                                for item in obj:
                                    photos.extend(find_photos(item, depth+1))
                            return photos
                        
                        found = find_photos(next_data)
                        logger.info(f"Found {len(found)} photos in __NEXT_DATA__")
                        images.extend(found)
                    else:
                        logger.warning(f"No __NEXT_DATA__ or parse error: {next_data}")
                
                # ZILLOW: Try __INITIAL_STATE__ or window data
                elif 'zillow.com' in url:
                    logger.info("Zillow detected")
                    # Similar extraction logic for Zillow
                
                # Fallback: DOM extraction
                if not images:
                    logger.info("Falling back to DOM extraction")
                    
                    dom_images = await page.evaluate('''
                        () => {
                            const results = [];
                            const seen = new Set();
                            
                            document.querySelectorAll('img').forEach(img => {
                                const url = img.src;
                                if (!url || !url.startsWith('http') || seen.has(url)) return;
                                seen.add(url);
                                
                                // Skip small images
                                const w = img.naturalWidth || img.width || 0;
                                const h = img.naturalHeight || img.height || 0;
                                if (w < 200 || h < 150) return;
                                
                                // Skip non-property images
                                const lower = url.toLowerCase();
                                if (lower.includes('logo') || lower.includes('icon') || 
                                    lower.includes('avatar') || lower.includes('agent') ||
                                    lower.includes('map') || lower.includes('sprite')) return;
                                
                                // Check parent chain for nearby/similar sections
                                let el = img.parentElement;
                                let isNearby = false;
                                for (let i = 0; i < 10 && el; i++) {
                                    const cls = (el.className || '').toLowerCase();
                                    const txt = (el.innerText || '').toLowerCase().slice(0, 100);
                                    if (cls.includes('nearby') || cls.includes('similar') ||
                                        cls.includes('recommended') || cls.includes('sold') ||
                                        txt.includes('nearby home') || txt.includes('similar home')) {
                                        isNearby = true;
                                        break;
                                    }
                                    el = el.parentElement;
                                }
                                
                                if (!isNearby) {
                                    results.push({
                                        url: url,
                                        width: w,
                                        height: h,
                                        alt: img.alt || '',
                                        source: 'dom'
                                    });
                                }
                            });
                            
                            return results;
                        }
                    ''')
                    
                    images = dom_images
                    logger.info(f"DOM extraction found {len(images)} images")
                
                # Sort by size
                images.sort(key=lambda x: x.get('width', 0) * x.get('height', 0), reverse=True)
                
                return {
                    'images': images[:8],
                    'page_title': title,
                    'page_url': current_url,
                    'is_blocked': is_blocked,
                    'screenshot': screenshot_b64
                }
                
            finally:
                await page.close()
                
        finally:
            if browser:
                await browser.close()

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.json
    url = data.get('url')
    capture_screenshot = data.get('screenshot', False)
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    try:
        result = asyncio.run(scrape_images(url, capture_screenshot))
        return jsonify({
            'success': True,
            'images': result['images'],
            'debug': {
                'page_title': result['page_title'],
                'page_url': result['page_url'],
                'is_blocked': result['is_blocked']
            },
            'screenshot': result.get('screenshot')
        })
    except Exception as e:
        logger.error(f"Scrape failed: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
