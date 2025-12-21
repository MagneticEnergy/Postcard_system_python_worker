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
            
            max_retries = 3
            for attempt in range(max_retries):
                context = None
                page = None
                try:
                    logger.info(f"Attempt {attempt + 1}/{max_retries}")
                    context = await browser.new_context()
                    page = await context.new_page()
                    
                    logger.info("Navigating...")
                    await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                    logger.info(f"Page loaded: {await page.title()}")
                    
                    try:
                        await page.wait_for_selector('img', timeout=10000)
                    except:
                        logger.warning("Timeout waiting for img selector, proceeding anyway")
                    
                    current_url = page.url.lower()
                    
                    # JavaScript to extract ONLY main property images
                    js_code = """
                    (currentUrl) => {
                        const imgs = [];
                        const seenUrls = new Set();
                        
                        function addImage(img, source) {
                            const url = img.src || img.dataset?.src || '';
                            if (!url || !url.startsWith('http')) return;
                            if (seenUrls.has(url)) return;
                            seenUrls.add(url);
                            imgs.push({
                                url: url,
                                width: img.naturalWidth || parseInt(img.width) || 0,
                                height: img.naturalHeight || parseInt(img.height) || 0,
                                alt: img.alt || '',
                                source: source
                            });
                        }
                        
                        // REDFIN: Target main property images only
                        if (currentUrl.includes('redfin.com')) {
                            const mainSelectors = [
                                '.HomeViews img',
                                '.MediaCenter img', 
                                '.PhotosView img',
                                '.HomeMainMedia img',
                                '[data-rf-test-id="gallery-photo"] img',
                                '.carousel img',
                                '.hero-image img',
                                '.main-photo img'
                            ];
                            
                            for (const selector of mainSelectors) {
                                document.querySelectorAll(selector).forEach(img => addImage(img, 'redfin-main'));
                            }
                            
                            if (imgs.length === 0) {
                                const allImgs = document.querySelectorAll('img');
                                allImgs.forEach(img => {
                                    const parent = img.closest('[class*="nearby"], [class*="similar"], [class*="Nearby"], [class*="Similar"], [class*="recommended"], [class*="Recommended"], [data-rf-test-id*="similar"], [data-rf-test-id*="nearby"]');
                                    if (parent) return;
                                    
                                    const section = img.closest('section, div[class*="Section"]');
                                    if (section) {
                                        const headerText = section.querySelector('h2, h3, h4')?.textContent?.toLowerCase() || '';
                                        if (headerText.includes('nearby') || headerText.includes('similar') || 
                                            headerText.includes('sold') || headerText.includes('recommended')) {
                                            return;
                                        }
                                    }
                                    
                                    addImage(img, 'redfin-filtered');
                                });
                            }
                        }
                        // ZILLOW
                        else if (currentUrl.includes('zillow.com')) {
                            const mainSelectors = [
                                '[data-testid="hollywood-vertical-carousel"] img',
                                '.media-stream img',
                                '.photo-carousel img',
                                '.hdp__sc-1s2b8ok img',
                                '[class*="PhotoCarousel"] img',
                                '[class*="MediaGallery"] img'
                            ];
                            
                            for (const selector of mainSelectors) {
                                document.querySelectorAll(selector).forEach(img => addImage(img, 'zillow-main'));
                            }
                            
                            if (imgs.length === 0) {
                                document.querySelectorAll('img').forEach(img => {
                                    const parent = img.closest('[class*="nearby"], [class*="similar"], [class*="Nearby"], [class*="Similar"]');
                                    if (parent) return;
                                    addImage(img, 'zillow-filtered');
                                });
                            }
                        }
                        // OTHER SITES
                        else {
                            const mainSelectors = [
                                '.gallery img',
                                '.photo-gallery img',
                                '.listing-photos img',
                                '.property-photos img',
                                '[class*="Gallery"] img',
                                '[class*="Carousel"] img'
                            ];
                            
                            for (const selector of mainSelectors) {
                                document.querySelectorAll(selector).forEach(img => addImage(img, 'other-main'));
                            }
                            
                            if (imgs.length === 0) {
                                document.querySelectorAll('img').forEach(img => {
                                    const parent = img.closest('[class*="nearby"], [class*="similar"]');
                                    if (parent) return;
                                    addImage(img, 'other-filtered');
                                });
                            }
                        }
                        
                        return imgs;
                    }
                    """
                    
                    images = await page.evaluate(js_code, current_url)
                    logger.info(f"Extracted {len(images)} raw images")
                    
                    # Filter and Sort
                    valid_images = []
                    for img in images:
                        src = img['url'].lower()
                        if any(x in src for x in ['logo', 'icon', 'map', 'avatar', 'profile', 'agent']):
                            continue
                        if img['width'] < 300 or img['height'] < 200:
                            continue
                        valid_images.append(img)
                    
                    valid_images.sort(key=lambda x: x['width'] * x['height'], reverse=True)
                    result = valid_images[:8]
                    
                    logger.info(f"Returning {len(result)} valid images")
                    return result
                    
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
