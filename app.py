import os
import json
import asyncio
import logging
import base64
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

async def get_all_property_images(page, url):
    """
    Extract all property images from the page in order from top to bottom.
    Includes: img tags, background images, picture/source elements, lazy-loaded images.
    """
    
    # Wait for page to fully render
    await asyncio.sleep(5)
    
    # Scroll down a bit to trigger lazy loading
    await page.evaluate('window.scrollBy(0, 500)')
    await asyncio.sleep(2)
    await page.evaluate('window.scrollTo(0, 0)')
    await asyncio.sleep(1)
    
    # Extract all images including background images and picture elements
    images = await page.evaluate('''
        () => {
            const results = [];
            const seen = new Set();
            
            // Helper to add image if valid
            function addImage(url, source, top = 0) {
                if (!url || !url.startsWith('http') || seen.has(url)) return;
                
                // Skip tiny/icon images
                const urlLower = url.toLowerCase();
                if (urlLower.includes('logo') || urlLower.includes('icon') || 
                    urlLower.includes('avatar') || urlLower.includes('sprite') ||
                    urlLower.includes('badge') || urlLower.includes('button') ||
                    urlLower.includes('1x1') || urlLower.includes('pixel')) return;
                
                // For Redfin, only include CDN images
                const isRedfin = window.location.hostname.includes('redfin.com');
                if (isRedfin && !url.includes('ssl.cdn-redfin.com/photo') && !url.includes('ssl.cdn-redfin.com/v')) return;
                
                // For Zillow, only include zillowstatic images
                const isZillow = window.location.hostname.includes('zillow.com');
                if (isZillow && !url.includes('zillowstatic.com')) return;
                
                seen.add(url);
                results.push({ url, source, top });
            }
            
            // 1. Check for background images in hero section
            const heroSelectors = [
                '[data-rf-test-id="abp-photos"]',
                '.HomeViews',
                '.hero-image',
                '.PhotosView',
                '.MediaCarousel',
                '.hdp-hero',
                '[class*="hero"]',
                '[class*="Hero"]',
                '[class*="photo"]',
                '[class*="Photo"]'
            ];
            
            for (const selector of heroSelectors) {
                try {
                    const elements = document.querySelectorAll(selector);
                    elements.forEach(el => {
                        const style = window.getComputedStyle(el);
                        const bgImage = style.backgroundImage;
                        if (bgImage && bgImage !== 'none') {
                            const match = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
                            if (match) {
                                const rect = el.getBoundingClientRect();
                                addImage(match[1], 'background-' + selector, rect.top + window.scrollY);
                            }
                        }
                    });
                } catch (e) {}
            }
            
            // 2. Check all elements for background images (top 1000px of page)
            const allElements = document.querySelectorAll('*');
            allElements.forEach(el => {
                try {
                    const rect = el.getBoundingClientRect();
                    if (rect.top > 1000) return; // Only check top of page
                    
                    const style = window.getComputedStyle(el);
                    const bgImage = style.backgroundImage;
                    if (bgImage && bgImage !== 'none' && bgImage.includes('url')) {
                        const match = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
                        if (match) {
                            addImage(match[1], 'background', rect.top + window.scrollY);
                        }
                    }
                } catch (e) {}
            });
            
            // 3. Check picture/source elements
            document.querySelectorAll('picture').forEach(picture => {
                const rect = picture.getBoundingClientRect();
                
                // Check source elements
                picture.querySelectorAll('source').forEach(source => {
                    const srcset = source.srcset;
                    if (srcset) {
                        // Get the largest image from srcset
                        const urls = srcset.split(',').map(s => s.trim().split(' ')[0]);
                        urls.forEach(url => addImage(url, 'picture-source', rect.top + window.scrollY));
                    }
                });
                
                // Check img inside picture
                const img = picture.querySelector('img');
                if (img) {
                    addImage(img.src, 'picture-img', rect.top + window.scrollY);
                    addImage(img.dataset.src, 'picture-datasrc', rect.top + window.scrollY);
                }
            });
            
            // 4. Check all img tags
            document.querySelectorAll('img').forEach(img => {
                const rect = img.getBoundingClientRect();
                const top = rect.top + window.scrollY;
                
                // Skip images too far down the page (likely nearby homes)
                if (top > 2000) return;
                
                // Check various src attributes
                addImage(img.src, 'img-src', top);
                addImage(img.dataset.src, 'img-datasrc', top);
                addImage(img.dataset.lazySrc, 'img-lazysrc', top);
                
                // Check srcset
                if (img.srcset) {
                    const urls = img.srcset.split(',').map(s => s.trim().split(' ')[0]);
                    urls.forEach(url => addImage(url, 'img-srcset', top));
                }
            });
            
            // 5. Look for image URLs in inline styles
            document.querySelectorAll('[style*="background"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.top > 1000) return;
                
                const style = el.getAttribute('style') || '';
                const matches = style.match(/url\(["']?([^"')]+)["']?\)/g);
                if (matches) {
                    matches.forEach(match => {
                        const url = match.match(/url\(["']?([^"')]+)["']?\)/)[1];
                        addImage(url, 'inline-style', rect.top + window.scrollY);
                    });
                }
            });
            
            // Sort by vertical position (top to bottom)
            results.sort((a, b) => a.top - b.top);
            
            return results;
        }
    ''')
    
    return images

async def scrape_single_image(url, image_index=0, capture_screenshot=False):
    """
    Scrape a single image from the page at the given index.
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
                await page.goto(url, timeout=2*60*1000, wait_until='networkidle')
                
                title = await page.title()
                current_url = page.url
                logger.info(f"Page title: {title}")
                
                # Get all property images
                all_images = await get_all_property_images(page, url)
                logger.info(f"Found {len(all_images)} total property images")
                
                # Log first few for debugging
                for i, img in enumerate(all_images[:5]):
                    logger.info(f"  Image {i}: {img['source']} - {img['url'][:60]}...")
                
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
                await page.goto(url, timeout=2*60*1000, wait_until='networkidle')
                
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
