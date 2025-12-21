import os
import json
import asyncio
import logging
import re
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
            logger.info("Connecting to Bright Data Browser API...")
            browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
            logger.info("Connected!")
            
            # Use browser.newPage() directly as per Bright Data docs
            page = await browser.new_page()
            
            try:
                logger.info(f"Navigating to {url}...")
                await page.goto(url, timeout=120000)
                logger.info(f"Page loaded: {await page.title()}")
                
                # Wait for content to load
                await asyncio.sleep(3)
                
                images = []
                
                # REDFIN: Extract from __NEXT_DATA__ JSON
                if 'redfin.com' in url:
                    logger.info("Redfin detected - extracting from __NEXT_DATA__")
                    
                    next_data = await page.evaluate('''
                        () => {
                            const script = document.querySelector('script#__NEXT_DATA__');
                            if (script) {
                                try {
                                    return JSON.parse(script.textContent);
                                } catch (e) {
                                    return null;
                                }
                            }
                            return null;
                        }
                    ''')
                    
                    if next_data:
                        logger.info("Found __NEXT_DATA__!")
                        
                        # Navigate the JSON structure to find photos
                        try:
                            # Try different paths where photos might be
                            photos = None
                            
                            # Path 1: props.pageProps.listing.photos
                            if next_data.get('props', {}).get('pageProps', {}).get('listing', {}).get('photos'):
                                photos = next_data['props']['pageProps']['listing']['photos']
                                logger.info(f"Found {len(photos)} photos in listing.photos")
                            
                            # Path 2: props.pageProps.initialReduxState
                            elif next_data.get('props', {}).get('pageProps', {}).get('initialReduxState'):
                                redux_state = next_data['props']['pageProps']['initialReduxState']
                                # Look for photos in various places
                                if redux_state.get('home', {}).get('photos'):
                                    photos = redux_state['home']['photos']
                                    logger.info(f"Found {len(photos)} photos in home.photos")
                            
                            # Path 3: Look for any photos array in the data
                            if not photos:
                                def find_photos(obj, path=""):
                                    if isinstance(obj, dict):
                                        if 'photos' in obj and isinstance(obj['photos'], list) and len(obj['photos']) > 0:
                                            return obj['photos']
                                        for k, v in obj.items():
                                            result = find_photos(v, f"{path}.{k}")
                                            if result:
                                                return result
                                    elif isinstance(obj, list):
                                        for i, item in enumerate(obj):
                                            result = find_photos(item, f"{path}[{i}]")
                                            if result:
                                                return result
                                    return None
                                
                                photos = find_photos(next_data)
                                if photos:
                                    logger.info(f"Found {len(photos)} photos via deep search")
                            
                            if photos:
                                for photo in photos:
                                    photo_url = None
                                    if isinstance(photo, dict):
                                        photo_url = photo.get('url') or photo.get('photoUrl') or photo.get('src')
                                    elif isinstance(photo, str):
                                        photo_url = photo
                                    
                                    if photo_url:
                                        images.append({
                                            'url': photo_url,
                                            'width': photo.get('width', 0) if isinstance(photo, dict) else 0,
                                            'height': photo.get('height', 0) if isinstance(photo, dict) else 0,
                                            'source': 'redfin-nextdata'
                                        })
                        except Exception as e:
                            logger.error(f"Error parsing __NEXT_DATA__: {e}")
                    else:
                        logger.warning("No __NEXT_DATA__ found")
                
                # ZILLOW: Extract from __INITIAL_STATE__
                elif 'zillow.com' in url:
                    logger.info("Zillow detected - extracting from page data")
                    
                    initial_state = await page.evaluate('''
                        () => {
                            // Try __INITIAL_STATE__
                            const script = document.querySelector('script#__INITIAL_STATE__');
                            if (script) {
                                try {
                                    return JSON.parse(script.textContent);
                                } catch (e) {}
                            }
                            // Try hdpData in window
                            if (window.__INITIAL_DATA__) {
                                return window.__INITIAL_DATA__;
                            }
                            return null;
                        }
                    ''')
                    
                    if initial_state:
                        logger.info("Found Zillow data!")
                        # Extract photos from Zillow structure
                        # ... similar logic
                
                # Fallback: Extract from DOM but be more careful
                if not images:
                    logger.info("Falling back to DOM extraction")
                    
                    dom_images = await page.evaluate('''
                        () => {
                            const imgs = [];
                            const seenUrls = new Set();
                            
                            // Get all images
                            document.querySelectorAll('img').forEach(img => {
                                const url = img.src;
                                if (!url || !url.startsWith('http')) return;
                                if (seenUrls.has(url)) return;
                                seenUrls.add(url);
                                
                                // Skip small images
                                if (img.naturalWidth < 300 || img.naturalHeight < 200) return;
                                
                                // Skip logos, icons, etc
                                const srcLower = url.toLowerCase();
                                if (srcLower.includes('logo') || srcLower.includes('icon') || 
                                    srcLower.includes('avatar') || srcLower.includes('agent') ||
                                    srcLower.includes('map')) return;
                                
                                // Check if in nearby/similar section
                                let parent = img.parentElement;
                                let isNearby = false;
                                for (let i = 0; i < 10 && parent; i++) {
                                    const classes = (parent.className || '').toLowerCase();  
                                    const text = (parent.textContent || '').toLowerCase().slice(0, 200);
                                    if (classes.includes('nearby') || classes.includes('similar') ||
                                        classes.includes('recommended') || classes.includes('sold') ||
                                        text.includes('nearby homes') || text.includes('similar homes')) {
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
                
                # Sort by size and return top 8
                images.sort(key=lambda x: x.get('width', 0) * x.get('height', 0), reverse=True)
                result = images[:8]
                
                logger.info(f"Returning {len(result)} images")
                return result
                
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
