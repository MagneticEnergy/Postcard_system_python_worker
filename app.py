import os
import json
import asyncio
import logging
import re
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

@app.route('/debug', methods=['POST'])
async def debug_page():
    """Debug endpoint to see what the page actually looks like"""
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    try:
        result = asyncio.run(get_page_debug(url))
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

async def get_page_debug(url):
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
        try:
            page = await browser.new_page()
            await page.goto(url, timeout=120000)
            
            title = await page.title()
            current_url = page.url
            
            # Check for __NEXT_DATA__
            next_data_exists = await page.evaluate('''
                () => {
                    const script = document.querySelector('script#__NEXT_DATA__');
                    return script ? true : false;
                }
            ''')
            
            # Get first 5000 chars of HTML
            html_sample = await page.evaluate('() => document.documentElement.outerHTML.slice(0, 5000)')
            
            # Count images
            img_count = await page.evaluate('() => document.querySelectorAll("img").length')
            
            # Get all image URLs
            img_urls = await page.evaluate('''
                () => Array.from(document.querySelectorAll('img')).map(img => img.src).filter(s => s.startsWith('http')).slice(0, 20)
            ''')
            
            return {
                'title': title,
                'url': current_url,
                'next_data_exists': next_data_exists,
                'img_count': img_count,
                'img_urls': img_urls,
                'html_sample': html_sample
            }
        finally:
            await browser.close()

async def scrape_images(url):
    logger.info(f"Starting scrape for: {url}")
    
    async with async_playwright() as pw:
        browser = None
        try:
            logger.info("Connecting to Bright Data Browser API...")
            browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
            logger.info("Connected!")
            
            page = await browser.new_page()
            
            try:
                logger.info(f"Navigating to {url}...")
                await page.goto(url, timeout=120000)
                
                title = await page.title()
                logger.info(f"Page loaded: {title}")
                logger.info(f"Current URL: {page.url}")
                
                # Wait for content
                await asyncio.sleep(3)
                
                images = []
                
                # REDFIN: Try __NEXT_DATA__ first
                if 'redfin.com' in url:
                    logger.info("Redfin detected - checking for __NEXT_DATA__")
                    
                    next_data = await page.evaluate('''
                        () => {
                            const script = document.querySelector('script#__NEXT_DATA__');
                            if (script) {
                                try {
                                    return JSON.parse(script.textContent);
                                } catch (e) {
                                    return {error: e.message};
                                }
                            }
                            return null;
                        }
                    ''')
                    
                    if next_data and not next_data.get('error'):
                        logger.info("Found __NEXT_DATA__!")
                        logger.info(f"Keys: {list(next_data.keys()) if isinstance(next_data, dict) else 'not a dict'}")
                        
                        # Try to find photos
                        def find_photos_recursive(obj, depth=0):
                            if depth > 10:
                                return []
                            photos = []
                            if isinstance(obj, dict):
                                if 'photos' in obj and isinstance(obj['photos'], list):
                                    for p in obj['photos']:
                                        if isinstance(p, dict) and p.get('url'):
                                            photos.append(p)
                                        elif isinstance(p, str) and p.startswith('http'):
                                            photos.append({'url': p})
                                for v in obj.values():
                                    photos.extend(find_photos_recursive(v, depth+1))
                            elif isinstance(obj, list):
                                for item in obj:
                                    photos.extend(find_photos_recursive(item, depth+1))
                            return photos
                        
                        found_photos = find_photos_recursive(next_data)
                        logger.info(f"Found {len(found_photos)} photos in __NEXT_DATA__")
                        
                        for photo in found_photos:
                            images.append({
                                'url': photo.get('url', photo) if isinstance(photo, dict) else photo,
                                'width': photo.get('width', 0) if isinstance(photo, dict) else 0,
                                'height': photo.get('height', 0) if isinstance(photo, dict) else 0,
                                'source': 'redfin-nextdata'
                            })
                    else:
                        logger.warning(f"No __NEXT_DATA__ found or error: {next_data}")
                
                # Fallback: DOM extraction with strict filtering
                if not images:
                    logger.info("Falling back to DOM extraction")
                    
                    dom_images = await page.evaluate('''
                        () => {
                            const imgs = [];
                            const seenUrls = new Set();
                            
                            document.querySelectorAll('img').forEach(img => {
                                const url = img.src;
                                if (!url || !url.startsWith('http')) return;
                                if (seenUrls.has(url)) return;
                                seenUrls.add(url);
                                
                                if (img.naturalWidth < 300 || img.naturalHeight < 200) return;
                                
                                const srcLower = url.toLowerCase();
                                if (srcLower.includes('logo') || srcLower.includes('icon') || 
                                    srcLower.includes('avatar') || srcLower.includes('agent') ||
                                    srcLower.includes('map')) return;
                                
                                // Check parent chain for nearby/similar
                                let parent = img.parentElement;
                                let isNearby = false;
                                for (let i = 0; i < 10 && parent; i++) {
                                    const classes = (parent.className || '').toLowerCase();
                                    if (classes.includes('nearby') || classes.includes('similar') ||
                                        classes.includes('recommended') || classes.includes('sold')) {
                                        isNearby = true;
                                        break;
                                    }
                                    parent = parent.parentElement;
                                }
                                
                                if (!isNearby) {
                                    imgs.push({
                                        url: url,
                                        width: img.naturalWidth,
                                        height: img.naturalHeight,
                                        source: 'dom-filtered'
                                    });
                                }
                            });
                            
                            return imgs;
                        }
                    ''')
                    
                    images = dom_images
                    logger.info(f"DOM extraction found {len(images)} images")
                
                # Sort and return
                images.sort(key=lambda x: x.get('width', 0) * x.get('height', 0), reverse=True)
                return images[:8]
                
            finally:
                await page.close()
                
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
