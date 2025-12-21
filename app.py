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
    Extract all property images from the page in order from top to bottom.
    Returns a list of image URLs.
    """
    
    # Wait for page to fully render
    await asyncio.sleep(3)
    
    # Extract all images that could be property photos
    images = await page.evaluate('''
        () => {
            const results = [];
            const seen = new Set();
            
            // Get ALL images on the page in DOM order (top to bottom)
            const allImgs = document.querySelectorAll('img');
            
            for (const img of allImgs) {
                const url = img.src || img.dataset.src || '';
                
                // Skip if no URL, not http, or already seen
                if (!url || !url.startsWith('http') || seen.has(url)) continue;
                
                // Skip tiny images (icons, logos)
                const w = img.naturalWidth || img.width || parseInt(img.getAttribute('width')) || 0;
                const h = img.naturalHeight || img.height || parseInt(img.getAttribute('height')) || 0;
                if (w > 0 && w < 100) continue;
                if (h > 0 && h < 100) continue;
                
                // Skip obvious non-property images
                const urlLower = url.toLowerCase();
                if (urlLower.includes('logo') || urlLower.includes('icon') || 
                    urlLower.includes('avatar') || urlLower.includes('sprite') ||
                    urlLower.includes('badge') || urlLower.includes('button')) continue;
                
                // For Redfin, only include CDN images (actual property photos)
                const isRedfin = window.location.hostname.includes('redfin.com');
                if (isRedfin && !url.includes('ssl.cdn-redfin.com/photo')) continue;
                
                // For Zillow, only include zillowstatic images
                const isZillow = window.location.hostname.includes('zillow.com');
                if (isZillow && !url.includes('zillowstatic.com')) continue;
                
                seen.add(url);
                
                // Get position on page for sorting
                const rect = img.getBoundingClientRect();
                
                results.push({
                    url: url,
                    width: w,
                    height: h,
                    top: rect.top + window.scrollY,
                    left: rect.left
                });
            }
            
            // Sort by vertical position (top to bottom)
            results.sort((a, b) => a.top - b.top);
            
            return results;
        }
    ''')
    
    return images

async def scrape_single_image(url, image_index=0, capture_screenshot=False):
    """
    Scrape a single image from the page at the given index.
    Index 0 = first/top image, 1 = second, etc.
    """
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
                
                # Get the requested image
                image = None
                if image_index < len(all_images):
                    image = all_images[image_index]
                    logger.info(f"Returning image at index {image_index}: {image['url'][:60]}...")
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
    """
    Scrape all property images from the page (up to max_images).
    Returns images in order from top to bottom.
    """
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

# Original endpoint - scrape multiple images
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

# New endpoint - scrape single image by index
@app.route('/scrape_single', methods=['POST'])
def scrape_single():
    """
    Scrape a single image from the page at the specified index.
    
    Request body:
    - url: The listing URL to scrape
    - image_index: Which image to get (0 = first/top, 1 = second, etc.)
    - screenshot: Whether to capture a screenshot (optional)
    
    Response:
    - image: The image object (url, width, height) or null if index out of range
    - total_images: Total number of property images found on page
    - has_more: Whether there are more images after this index
    """
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
