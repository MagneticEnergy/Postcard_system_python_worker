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
    
    # Extract listing ID from URL for Redfin
    listing_id = None
    if 'redfin.com' in url:
        match = re.search(r'/home/(\d+)', url)
        if match:
            listing_id = match.group(1)
            logger.info(f"Extracted Redfin listing ID: {listing_id}")
    
    async with async_playwright() as pw:
        browser = None
        try:
            logger.info("Connecting to Bright Data Scraping Browser...")
            browser = await pw.chromium.connect_over_cdp(SBR_WS_CDP)
            logger.info("Connected successfully.")
            
            max_retries = 3
            for attempt in range(max_retries):
                context = None
                page = None
                try:
                    logger.info(f"Attempt {attempt + 1}/{max_retries}")
                    context = await browser.new_context(
                        viewport={'width': 1920, 'height': 1080},
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    )
                    page = await context.new_page()
                    
                    # Navigate and wait for network to be idle
                    logger.info("Navigating...")
                    await page.goto(url, timeout=60000, wait_until='networkidle')
                    logger.info(f"Page loaded: {await page.title()}")
                    
                    # Wait a bit for dynamic content
                    await asyncio.sleep(2)
                    
                    # Scroll down to trigger lazy loading
                    logger.info("Scrolling to load content...")
                    await page.evaluate('window.scrollTo(0, 500)')
                    await asyncio.sleep(1)
                    await page.evaluate('window.scrollTo(0, 0)')
                    await asyncio.sleep(1)
                    
                    # For Redfin, try to click on the main photo to open gallery
                    if 'redfin.com' in url:
                        try:
                            logger.info("Trying to click main photo gallery...")
                            # Try various selectors for the main photo
                            selectors = [
                                '[data-rf-test-id="gallery-photo"]',
                                '.HomeMainMedia img',
                                '.MediaCenter img',
                                '.hero-image',
                                'img[src*="redfin"]'
                            ]
                            for sel in selectors:
                                elem = await page.query_selector(sel)
                                if elem:
                                    logger.info(f"Found element with selector: {sel}")
                                    break
                        except Exception as e:
                            logger.warning(f"Could not interact with gallery: {e}")
                    
                    # Extract images with detailed info
                    js_code = """
                    (listingId) => {
                        const results = {
                            images: [],
                            debug: {
                                totalImages: 0,
                                pageUrl: window.location.href,
                                pageTitle: document.title
                            }
                        };
                        
                        const seenUrls = new Set();
                        const allImgs = document.querySelectorAll('img');
                        results.debug.totalImages = allImgs.length;
                        
                        // Log all image sources for debugging
                        const allSources = [];
                        
                        allImgs.forEach((img, idx) => {
                            const url = img.src || '';
                            if (!url || !url.startsWith('http')) return;
                            if (seenUrls.has(url)) return;
                            seenUrls.add(url);
                            
                            // Get parent info for debugging
                            const parentClasses = [];
                            let parent = img.parentElement;
                            for (let i = 0; i < 5 && parent; i++) {
                                if (parent.className) parentClasses.push(parent.className);
                                parent = parent.parentElement;
                            }
                            
                            // Check if this is a "nearby" or "similar" image
                            const parentChain = parentClasses.join(' ').toLowerCase();
                            const isNearby = parentChain.includes('nearby') || 
                                           parentChain.includes('similar') ||
                                           parentChain.includes('recommended') ||
                                           parentChain.includes('sold');
                            
                            // For Redfin, check if URL contains the listing ID
                            let matchesListing = true;
                            if (listingId && url.includes('redfin')) {
                                // Redfin photo URLs don't contain listing ID directly
                                // But we can check if it's in the main content area
                                matchesListing = !isNearby;
                            }
                            
                            const imgData = {
                                url: url,
                                width: img.naturalWidth || parseInt(img.width) || 0,
                                height: img.naturalHeight || parseInt(img.height) || 0,
                                alt: img.alt || '',
                                isNearby: isNearby,
                                matchesListing: matchesListing,
                                parentClasses: parentClasses.slice(0, 3)
                            };
                            
                            allSources.push(imgData);
                            
                            // Only add if it's a main listing image
                            if (matchesListing && !isNearby) {
                                // Filter out small images, logos, icons
                                if (imgData.width >= 300 && imgData.height >= 200) {
                                    const srcLower = url.toLowerCase();
                                    if (!srcLower.includes('logo') && 
                                        !srcLower.includes('icon') && 
                                        !srcLower.includes('avatar') &&
                                        !srcLower.includes('agent') &&
                                        !srcLower.includes('map')) {
                                        results.images.push({
                                            url: url,
                                            width: imgData.width,
                                            height: imgData.height,
                                            alt: imgData.alt,
                                            source: 'main-listing'
                                        });
                                    }
                                }
                            }
                        });
                        
                        results.debug.allSources = allSources.slice(0, 20); // First 20 for debugging
                        return results;
                    }
                    """
                    
                    result = await page.evaluate(js_code, listing_id)
                    
                    images = result.get('images', [])
                    debug_info = result.get('debug', {})
                    
                    logger.info(f"Page: {debug_info.get('pageTitle')}")
                    logger.info(f"Total images on page: {debug_info.get('totalImages')}")
                    logger.info(f"Filtered main listing images: {len(images)}")
                    
                    # Log some debug info about what we found
                    all_sources = debug_info.get('allSources', [])
                    nearby_count = sum(1 for s in all_sources if s.get('isNearby'))
                    logger.info(f"Images marked as nearby/similar: {nearby_count}")
                    
                    # Sort by size
                    images.sort(key=lambda x: x['width'] * x['height'], reverse=True)
                    
                    # Return top 8
                    return images[:8]
                    
                except Exception as e:
                    logger.error(f"Error in attempt {attempt + 1}: {str(e)}")
                    if attempt == max_retries - 1:
                        raise e
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
