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

async def scrape_images(url, capture_screenshot=False, max_images=6):
    logger.info(f"Starting scrape for: {url}")
    
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
                logger.info(f"Current URL: {current_url}")
                
                # Wait for main content to load
                await asyncio.sleep(3)
                
                images = []
                screenshot_b64 = None
                
                # REDFIN SPECIFIC EXTRACTION
                if 'redfin.com' in url:
                    logger.info("Redfin detected - using specific selectors")
                    
                    # Wait for the main photo container to load
                    try:
                        await page.wait_for_selector('[data-rf-test-id="abp-photos"], .HomeViews, .MediaCarousel, .hero-image', timeout=10000)
                        logger.info("Found main photo container")
                    except:
                        logger.warning("Main photo container not found, continuing anyway")
                    
                    # Try multiple extraction methods for Redfin
                    
                    # Method 1: Extract from the main hero/carousel section
                    hero_images = await page.evaluate('''
                        () => {
                            const results = [];
                            const seen = new Set();
                            
                            // Target the main photo section specifically
                            // Redfin uses various containers for the main property photos
                            const mainContainers = [
                                '[data-rf-test-id="abp-photos"]',
                                '.HomeViews',
                                '.MediaCarousel', 
                                '.PhotosView',
                                '.hero-image',
                                '.HomeCard__Photo',
                                '[data-rf-test-id="basic-card-photo"]'
                            ];
                            
                            // First, try to find images in the main property section
                            for (const selector of mainContainers) {
                                const container = document.querySelector(selector);
                                if (container) {
                                    const imgs = container.querySelectorAll('img');
                                    imgs.forEach(img => {
                                        const url = img.src || img.dataset.src;
                                        if (url && url.startsWith('http') && !seen.has(url)) {
                                            seen.add(url);
                                            // Skip thumbnails and small images
                                            if (!url.includes('_s.') && !url.includes('_t.')) {
                                                results.push({
                                                    url: url,
                                                    width: img.naturalWidth || img.width || 0,
                                                    height: img.naturalHeight || img.height || 0,
                                                    source: 'redfin-main',
                                                    selector: selector
                                                });
                                            }
                                        }
                                    });
                                }
                            }
                            
                            // If no images found in containers, look for the main hero image
                            if (results.length === 0) {
                                // The main hero image is usually the first large image on the page
                                const allImgs = document.querySelectorAll('img');
                                for (const img of allImgs) {
                                    const url = img.src;
                                    if (!url || !url.startsWith('http') || seen.has(url)) continue;
                                    
                                    // Check if it's a Redfin CDN image (property photo)
                                    if (url.includes('ssl.cdn-redfin.com/photo')) {
                                        // Skip if in nearby/similar section
                                        let el = img.parentElement;
                                        let isNearby = false;
                                        for (let i = 0; i < 15 && el; i++) {
                                            const cls = (el.className || '').toLowerCase();
                                            const id = (el.id || '').toLowerCase();
                                            if (cls.includes('nearby') || cls.includes('similar') ||
                                                cls.includes('recommended') || cls.includes('sold') ||
                                                cls.includes('homecard') || id.includes('nearby') ||
                                                id.includes('similar')) {
                                                isNearby = true;
                                                break;
                                            }
                                            el = el.parentElement;
                                        }
                                        
                                        if (!isNearby) {
                                            seen.add(url);
                                            results.push({
                                                url: url,
                                                width: img.naturalWidth || img.width || 0,
                                                height: img.naturalHeight || img.height || 0,
                                                source: 'redfin-cdn'
                                            });
                                        }
                                    }
                                }
                            }
                            
                            return results;
                        }
                    ''')
                    
                    images = hero_images
                    logger.info(f"Redfin extraction found {len(images)} images")
                    
                    # If still no images, try clicking on the photo to open gallery
                    if len(images) == 0:
                        logger.info("No images found, trying to open photo gallery...")
                        try:
                            # Click on the main photo to open the gallery
                            await page.click('[data-rf-test-id="abp-photos"], .HomeViews img, .hero-image')
                            await asyncio.sleep(2)
                            
                            # Now extract from the gallery modal
                            gallery_images = await page.evaluate('''
                                () => {
                                    const results = [];
                                    const seen = new Set();
                                    
                                    // Look for gallery/modal images
                                    const galleryImgs = document.querySelectorAll('.PhotosModal img, .lightbox img, [role="dialog"] img');
                                    galleryImgs.forEach(img => {
                                        const url = img.src;
                                        if (url && url.startsWith('http') && !seen.has(url)) {
                                            seen.add(url);
                                            results.push({
                                                url: url,
                                                width: img.naturalWidth || 0,
                                                height: img.naturalHeight || 0,
                                                source: 'redfin-gallery'
                                            });
                                        }
                                    });
                                    
                                    return results;
                                }
                            ''')
                            
                            images = gallery_images
                            logger.info(f"Gallery extraction found {len(images)} images")
                        except Exception as e:
                            logger.warning(f"Could not open gallery: {e}")
                
                # ZILLOW SPECIFIC EXTRACTION
                elif 'zillow.com' in url:
                    logger.info("Zillow detected - using specific selectors")
                    
                    # Wait for Zillow's photo carousel
                    try:
                        await page.wait_for_selector('[data-testid="hollywood-image"], .media-stream-tile, .hdp__sc-1s2b8ok-0', timeout=10000)
                    except:
                        logger.warning("Zillow photo container not found")
                    
                    zillow_images = await page.evaluate('''
                        () => {
                            const results = [];
                            const seen = new Set();
                            
                            // Zillow main photo selectors
                            const selectors = [
                                '[data-testid="hollywood-image"] img',
                                '.media-stream-tile img',
                                '.hdp__sc-1s2b8ok-0 img',
                                'picture img'
                            ];
                            
                            for (const selector of selectors) {
                                document.querySelectorAll(selector).forEach(img => {
                                    const url = img.src || img.dataset.src;
                                    if (url && url.startsWith('http') && !seen.has(url)) {
                                        // Only Zillow static photos
                                        if (url.includes('zillowstatic.com') || url.includes('zillow.com')) {
                                            seen.add(url);
                                            results.push({
                                                url: url,
                                                width: img.naturalWidth || 0,
                                                height: img.naturalHeight || 0,
                                                source: 'zillow-main'
                                            });
                                        }
                                    }
                                });
                            }
                            
                            return results;
                        }
                    ''')
                    
                    images = zillow_images
                    logger.info(f"Zillow extraction found {len(images)} images")
                
                # GENERIC FALLBACK
                else:
                    logger.info("Generic site - using fallback extraction")
                    
                    generic_images = await page.evaluate('''
                        () => {
                            const results = [];
                            const seen = new Set();
                            
                            document.querySelectorAll('img').forEach(img => {
                                const url = img.src;
                                if (!url || !url.startsWith('http') || seen.has(url)) return;
                                
                                const w = img.naturalWidth || img.width || 0;
                                const h = img.naturalHeight || img.height || 0;
                                if (w < 300 || h < 200) return;
                                
                                const lower = url.toLowerCase();
                                if (lower.includes('logo') || lower.includes('icon') || 
                                    lower.includes('avatar') || lower.includes('agent')) return;
                                
                                seen.add(url);
                                results.push({
                                    url: url,
                                    width: w,
                                    height: h,
                                    source: 'generic'
                                });
                            });
                            
                            return results;
                        }
                    ''')
                    
                    images = generic_images
                
                # Capture screenshot if requested
                if capture_screenshot:
                    screenshot_bytes = await page.screenshot(full_page=False)
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                
                # Sort by size and limit
                images.sort(key=lambda x: x.get('width', 0) * x.get('height', 0), reverse=True)
                
                return {
                    'images': images[:max_images],
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
    max_images = data.get('max_images', 6)
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    try:
        result = asyncio.run(scrape_images(url, capture_screenshot, max_images))
        return jsonify({
            'success': True,
            'images': result['images'],
            'debug': {
                'page_title': result['page_title'],
                'page_url': result['page_url']
            },
            'screenshot': result.get('screenshot')
        })
    except Exception as e:
        logger.error(f"Scrape failed: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
