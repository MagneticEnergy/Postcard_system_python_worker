import os
import json
import asyncio
import logging
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

async def get_all_property_images(page, url):
    """
    Extract all property images from the page.
    For Redfin: Extract from __NEXT_DATA__ JSON (Next.js app)
    For Zillow: Extract from page data
    For others: Extract from DOM
    """
    
    # Wait for page to render
    await asyncio.sleep(3)
    
    images = []
    
    # REDFIN: Extract from __NEXT_DATA__ script tag
    if 'redfin.com' in url:
        logger.info("Redfin detected - extracting from __NEXT_DATA__")
        
        next_data = await page.evaluate('''
            () => {
                const script = document.querySelector('script#__NEXT_DATA__');
                if (script) {
                    try {
                        return JSON.parse(script.textContent);
                    } catch (e) {
                        return { error: e.message };
                    }
                }
                return null;
            }
        ''')
        
        if next_data and not next_data.get('error'):
            logger.info("Found __NEXT_DATA__")
            
            # Navigate the JSON structure to find photos
            # Structure: props.pageProps.initialReduxState.home.homeByPropertyId[id].photos
            # Or: props.pageProps.listing.photos
            
            def find_photos_recursive(obj, depth=0):
                """Recursively search for photo arrays in the JSON"""
                if depth > 20:
                    return []
                
                photos = []
                
                if isinstance(obj, dict):
                    # Check for photo arrays
                    for key in ['photos', 'photoUrls', 'images', 'media', 'propertyPhotos']:
                        if key in obj:
                            val = obj[key]
                            if isinstance(val, list):
                                for item in val:
                                    if isinstance(item, str) and item.startswith('http'):
                                        photos.append({'url': item, 'source': f'nextdata-{key}'})
                                    elif isinstance(item, dict):
                                        # Look for URL in various keys
                                        for url_key in ['url', 'photoUrl', 'src', 'href', 'fullUrl', 'photoUrlHiRes']:
                                            if url_key in item and isinstance(item[url_key], str) and item[url_key].startswith('http'):
                                                photos.append({
                                                    'url': item[url_key],
                                                    'source': f'nextdata-{key}',
                                                    'width': item.get('width', 0),
                                                    'height': item.get('height', 0)
                                                })
                                                break
                    
                    # Recurse into nested objects
                    for v in obj.values():
                        photos.extend(find_photos_recursive(v, depth + 1))
                
                elif isinstance(obj, list):
                    for item in obj:
                        photos.extend(find_photos_recursive(item, depth + 1))
                
                return photos
            
            found_photos = find_photos_recursive(next_data)
            
            # Deduplicate by URL
            seen = set()
            for photo in found_photos:
                url = photo.get('url', '')
                if url and url not in seen:
                    # Only include Redfin CDN images
                    if 'ssl.cdn-redfin.com' in url:
                        seen.add(url)
                        images.append(photo)
            
            logger.info(f"Found {len(images)} photos from __NEXT_DATA__")
        else:
            logger.warning(f"No __NEXT_DATA__ found or error: {next_data}")
            # Fall back to DOM extraction
            images = await extract_from_dom(page)
    
    # ZILLOW: Extract from window data or DOM
    elif 'zillow.com' in url:
        logger.info("Zillow detected")
        
        # Try to get from window.__INITIAL_STATE__ or similar
        zillow_data = await page.evaluate('''
            () => {
                // Try various Zillow data sources
                if (window.__INITIAL_STATE__) return window.__INITIAL_STATE__;
                if (window.__PRELOADED_STATE__) return window.__PRELOADED_STATE__;
                
                // Try to find in script tags
                const scripts = document.querySelectorAll('script');
                for (const script of scripts) {
                    const text = script.textContent || '';
                    if (text.includes('"photos"') || text.includes('"images"')) {
                        try {
                            // Try to extract JSON
                            const match = text.match(/\{[\s\S]*"photos"[\s\S]*\}/);
                            if (match) {
                                return JSON.parse(match[0]);
                            }
                        } catch (e) {}
                    }
                }
                return null;
            }
        ''')
        
        if zillow_data:
            # Similar recursive extraction
            def find_zillow_photos(obj, depth=0):
                if depth > 15:
                    return []
                photos = []
                if isinstance(obj, dict):
                    for key in ['photos', 'images', 'media', 'responsivePhotos']:
                        if key in obj and isinstance(obj[key], list):
                            for item in obj[key]:
                                if isinstance(item, str) and 'zillowstatic' in item:
                                    photos.append({'url': item, 'source': 'zillow-data'})
                                elif isinstance(item, dict):
                                    for url_key in ['url', 'src', 'href', 'mixedSources']:
                                        if url_key in item:
                                            val = item[url_key]
                                            if isinstance(val, str) and 'zillowstatic' in val:
                                                photos.append({'url': val, 'source': 'zillow-data'})
                                            elif isinstance(val, dict):
                                                # Handle nested sources
                                                for v in val.values():
                                                    if isinstance(v, list):
                                                        for u in v:
                                                            if isinstance(u, dict) and 'url' in u:
                                                                photos.append({'url': u['url'], 'source': 'zillow-data'})
                    for v in obj.values():
                        photos.extend(find_zillow_photos(v, depth + 1))
                elif isinstance(obj, list):
                    for item in obj:
                        photos.extend(find_zillow_photos(item, depth + 1))
                return photos
            
            found = find_zillow_photos(zillow_data)
            seen = set()
            for p in found:
                if p['url'] not in seen:
                    seen.add(p['url'])
                    images.append(p)
        
        if not images:
            images = await extract_from_dom(page)
    
    # OTHER SITES: DOM extraction
    else:
        images = await extract_from_dom(page)
    
    return images

async def extract_from_dom(page):
    """Fallback DOM extraction for images"""
    logger.info("Using DOM extraction fallback")
    
    images = await page.evaluate('''
        () => {
            const results = [];
            const seen = new Set();
            
            document.querySelectorAll('img').forEach(img => {
                const url = img.src || img.dataset.src;
                if (!url || !url.startsWith('http') || seen.has(url)) return;
                
                const w = img.naturalWidth || img.width || 0;
                const h = img.naturalHeight || img.height || 0;
                if (w > 0 && w < 100) return;
                if (h > 0 && h < 100) return;
                
                const urlLower = url.toLowerCase();
                if (urlLower.includes('logo') || urlLower.includes('icon') || 
                    urlLower.includes('avatar') || urlLower.includes('sprite')) return;
                
                seen.add(url);
                results.push({
                    url: url,
                    width: w,
                    height: h,
                    source: 'dom'
                });
            });
            
            return results;
        }
    ''')
    
    return images

async def scrape_single_image(url, image_index=0, capture_screenshot=False):
    """Scrape a single image from the page at the given index."""
    logger.info(f"Scraping image index {image_index} from: {url}")
    
    async with async_playwright() as pw:
        browser = None
        try:
            logger.info("Connecting to Bright Data Scraping Browser...")
            browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
            logger.info("Connected!")
            
            page = await browser.new_page()
            
            try:
                logger.info(f"Navigating to {url}...")
                await page.goto(url, timeout=2*60*1000, wait_until='domcontentloaded')
                
                title = await page.title()
                current_url = page.url
                logger.info(f"Page title: {title}")
                
                # Get all property images
                all_images = await get_all_property_images(page, url)
                logger.info(f"Found {len(all_images)} total property images")
                
                # Log first few for debugging
                for i, img in enumerate(all_images[:5]):
                    logger.info(f"  Image {i}: {img.get('source', 'N/A')} - {img.get('url', '')[:60]}...")
                
                # Get the requested image
                image = None
                if image_index < len(all_images):
                    image = all_images[image_index]
                    logger.info(f"Returning image at index {image_index}")
                else:
                    logger.info(f"Index {image_index} out of range (only {len(all_images)} images)")
                
                # Capture screenshot if requested
                screenshot_b64 = None
                if capture_screenshot:
                    screenshot_bytes = await page.screenshot(full_page=False)
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                
                return {
                    'image': image,
                    'total_images': len(all_images),
                    'page_title': title,
                    'page_url': current_url,
                    'screenshot': screenshot_b64
                }
                
            finally:
                await page.close()
                
        finally:
            if browser:
                await browser.close()

async def scrape_all_images(url, max_images=8, capture_screenshot=False):
    """Scrape all property images from the page (up to max_images)."""
    logger.info(f"Scraping up to {max_images} images from: {url}")
    
    async with async_playwright() as pw:
        browser = None
        try:
            logger.info("Connecting to Bright Data Scraping Browser...")
            browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
            logger.info("Connected!")
            
            page = await browser.new_page()
            
            try:
                logger.info(f"Navigating to {url}...")
                await page.goto(url, timeout=2*60*1000, wait_until='domcontentloaded')
                
                title = await page.title()
                current_url = page.url
                logger.info(f"Page title: {title}")
                
                # Get all property images
                all_images = await get_all_property_images(page, url)
                logger.info(f"Found {len(all_images)} total property images")
                
                # Capture screenshot if requested
                screenshot_b64 = None
                if capture_screenshot:
                    screenshot_bytes = await page.screenshot(full_page=False)
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                
                return {
                    'images': all_images[:max_images],
                    'total_images': len(all_images),
                    'page_title': title,
                    'page_url': current_url,
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
    max_images = data.get('max_images', 8)
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    try:
        result = asyncio.run(scrape_all_images(url, max_images, capture_screenshot))
        return jsonify({
            'success': True,
            'images': result['images'],
            'total_images': result['total_images'],
            'debug': {
                'page_title': result['page_title'],
                'page_url': result['page_url']
            },
            'screenshot': result.get('screenshot')
        })
    except Exception as e:
        logger.error(f"Scrape failed: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/scrape_single', methods=['POST'])
def scrape_single():
    data = request.json
    url = data.get('url')
    image_index = data.get('image_index', 0)
    capture_screenshot = data.get('screenshot', False)
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    try:
        result = asyncio.run(scrape_single_image(url, image_index, capture_screenshot))
        
        has_more = (image_index + 1) < result['total_images']
        
        return jsonify({
            'success': True,
            'image': result['image'],
            'image_index': image_index,
            'total_images': result['total_images'],
            'has_more': has_more,
            'debug': {
                'page_title': result['page_title'],
                'page_url': result['page_url']
            },
            'screenshot': result.get('screenshot')
        })
    except Exception as e:
        logger.error(f"Scrape single failed: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
