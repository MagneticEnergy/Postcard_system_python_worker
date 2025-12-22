from flask import Flask, request, jsonify
from playwright.async_api import async_playwright
import asyncio
import hashlib
import time
import base64
import os

app = Flask(__name__)

# Version
VERSION = "7.13-cache-bust"

# Session cache - cleared at start of each /scrape request
# Can be used within same request for retries
session_cache = {}

# Bright Data Web Unlocker proxy configuration
PROXY_HOST = "brd.superproxy.io"
PROXY_PORT = 33335
PROXY_USER_BASE = "brd-customer-hl_ead19305-zone-web_unlocker1"
PROXY_PASS = "9bra6mx0xptc"

def get_proxy_user():
    """Generate unique proxy user with session ID to bypass cache"""
    import uuid
    session_id = str(uuid.uuid4())[:8]
    # Add session ID to force fresh IP and bypass any caching
    return f"{PROXY_USER_BASE}-session-{session_id}"


def clear_session():
    """Clear all cached data - called at START of each request"""
    global session_cache
    session_cache = {}
    print("🧹 SESSION CLEARED - Starting fresh")


async def extract_hero_images(page):
    """Extract images from the hero area (top 700px) of the page"""
    hero_images = []

    try:
        # Wait for page to be fully loaded
        # Skip networkidle - it hangs on complex pages like Redfin
        # Just wait for DOM to be ready and give images time to load
        await page.wait_for_load_state('domcontentloaded', timeout=15000)

        # Additional wait for lazy-loaded images
        await asyncio.sleep(2)  # Reduced from 3s

        # Scroll down slightly to trigger lazy loading, then back up
        await page.evaluate('window.scrollTo(0, 500)')
        await asyncio.sleep(1)
        await page.evaluate('window.scrollTo(0, 0)')
        await asyncio.sleep(2)

        # JavaScript to extract images from hero area
        js_code = """
            () => {
                const heroHeight = 800;
                const images = [];
                const seenUrls = new Set();
                const debug = [];

                const addImage = (url, width, height, top, source) => {
                    if (!url || seenUrls.has(url)) return;
                    if (!url.startsWith('http')) return;
                    if (url.includes('logo') || url.includes('icon') || url.includes('avatar')) return;
                    if (url.includes('sprite') || url.includes('placeholder')) return;
                    if (width < 50 || height < 50) return;

                    seenUrls.add(url);
                    images.push({ url, width, height, top, source });
                };

                // Debug: Count all elements
                debug.push('Total img tags: ' + document.querySelectorAll('img').length);
                debug.push('Total picture tags: ' + document.querySelectorAll('picture').length);

                // Debug: Find ALL img tags and log their details
                const allImgs = document.querySelectorAll('img');
                debug.push('First 10 img details:');
                allImgs.forEach((img, i) => {
                    if (i < 10) {
                        const rect = img.getBoundingClientRect();
                        debug.push(`  img[${i}]: src=${img.src?.substring(0,50)}, top=${rect.top.toFixed(0)}, w=${rect.width.toFixed(0)}, h=${rect.height.toFixed(0)}`);
                    }
                });

                // Method 1: Standard img tags
                document.querySelectorAll('img').forEach((img) => {
                    const rect = img.getBoundingClientRect();
                    const possibleSrcs = [
                        img.src,
                        img.currentSrc,
                        img.dataset.src,
                        img.getAttribute('data-src'),
                        img.srcset ? img.srcset.split(',')[0].trim().split(' ')[0] : null
                    ].filter(Boolean);

                    if (rect.top < heroHeight && possibleSrcs.length > 0) {
                        const src = possibleSrcs[0];
                        addImage(src, rect.width, rect.height, rect.top, 'img_tag');
                    }
                });

                // Method 2: Background images - check ALL elements in hero area
                debug.push('Checking background images...');
                let bgCount = 0;
                document.querySelectorAll('*').forEach((el) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.top < heroHeight && rect.width > 100 && rect.height > 80) {
                        const style = window.getComputedStyle(el);
                        const bgImage = style.backgroundImage;
                        if (bgImage && bgImage !== 'none' && bgImage.includes('url(')) {
                            bgCount++;
                            const urlMatch = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
                            if (urlMatch && urlMatch[1]) {
                                if (bgCount <= 5) {
                                    debug.push(`  bg[${bgCount}]: ${urlMatch[1].substring(0,60)}`);
                                }
                                addImage(urlMatch[1], rect.width, rect.height, rect.top, 'background');
                            }
                        }
                    }
                });
                debug.push('Total background images found: ' + bgCount);

                // Method 3: Picture/source elements
                document.querySelectorAll('picture').forEach((picture) => {
                    const rect = picture.getBoundingClientRect();
                    if (rect.top < heroHeight) {
                        picture.querySelectorAll('source').forEach((source) => {
                            const srcset = source.srcset;
                            if (srcset) {
                                const src = srcset.split(',')[0].trim().split(' ')[0];
                                addImage(src, rect.width || 300, rect.height || 200, rect.top, 'picture_source');
                            }
                        });
                        const img = picture.querySelector('img');
                        if (img) {
                            const src = img.currentSrc || img.src;
                            addImage(src, rect.width || 300, rect.height || 200, rect.top, 'picture_img');
                        }
                    }
                });

                // Method 4: Redfin-specific - look for their photo containers
                debug.push('Checking Redfin-specific selectors...');
                const redfinSelectors = [
                    '.HomeViews img',
                    '.PhotosView img', 
                    '[data-rf-test-id="gallery-photo"] img',
                    '.MediaGallery img',
                    '.photo-carousel img',
                    '.listing-hero img',
                    '.hero-image img',
                    '.main-photo img'
                ];
                redfinSelectors.forEach(selector => {
                    const els = document.querySelectorAll(selector);
                    if (els.length > 0) {
                        debug.push(`  ${selector}: ${els.length} found`);
                        els.forEach(img => {
                            const rect = img.getBoundingClientRect();
                            const src = img.currentSrc || img.src;
                            if (src) addImage(src, rect.width, rect.height, rect.top, 'redfin_specific');
                        });
                    }
                });

                debug.push('Total images extracted: ' + images.length);
                return { images: images, debug: debug };
            }
        """

        js_result = await page.evaluate(js_code)
        hero_images = js_result.get('images', []) if isinstance(js_result, dict) else js_result
        debug_info = js_result.get('debug', []) if isinstance(js_result, dict) else []
        print(f"DEBUG INFO: {debug_info}")
        print(f"Extracted {len(hero_images)} hero images")

    except Exception as e:
        print(f"Error extracting hero images: {e}")

    return hero_images, debug_info


async def scrape_with_playwright(url):
    """Scrape a URL using Playwright with Bright Data proxy"""
    global session_cache

    # Check session cache (for retries within same request)
    cache_key = hashlib.md5(url.encode()).hexdigest()
    if cache_key in session_cache:
        print(f"Using session cache for {url}")
        return session_cache[cache_key]

    result = {
        'success': False,
        'url': url,
        'title': '',
        'hero_images': [],
        'hero_image_count': 0,
        'screenshot_base64': ''
    }

    # Session ID is now generated fresh for each attempt by get_proxy_user()

    max_retries = 10

    for attempt in range(max_retries):
        browser = None
        playwright = None
        try:
            print(f"Attempt {attempt + 1}/{max_retries} for {url}")
            # Session ID is generated fresh for each proxy connection

            playwright = await async_playwright().start()

            browser = await playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )

            # Create FRESH context - no cookies, no cache
            context = await browser.new_context(
                proxy={
                    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
                    "username": get_proxy_user(),  # Fresh session ID each time
                    "password": PROXY_PASS
                },
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                bypass_csp=True,
                ignore_https_errors=True
            )

            page = await context.new_page()

            # Navigate with timeout
            timeout = 60000 if attempt == 0 else 45000
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout)

            # Get title
            result['title'] = await page.title()
            print(f"Page title: {result['title']}")

            # Verify we're on the right page
            current_url = page.url
            print(f"Current URL: {current_url}")

            # Extract hero images
            hero_images, debug_info = await extract_hero_images(page)
            result['hero_images'] = hero_images
            result['debug_info'] = debug_info
            result['hero_image_count'] = len(result['hero_images'])

            # ALWAYS capture screenshot for debugging
            screenshot = await page.screenshot(type='png', full_page=False)
            result['screenshot_base64'] = base64.b64encode(screenshot).decode('utf-8')

            result['success'] = True

            # Cache for this session (in case of retries)
            session_cache[cache_key] = result

            await context.close()
            await browser.close()
            await playwright.stop()

            return result

        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if browser:
                try:
                    await browser.close()
                except:
                    pass
            if playwright:
                try:
                    await playwright.stop()
                except:
                    pass

            if attempt < max_retries - 1:
                # New session ID for retry (new IP)
                session_id = f"session_{int(time.time() * 1000)}_{attempt}_{hashlib.md5(url.encode()).hexdigest()[:8]}"
                proxy_user_with_session = f"{PROXY_USER_BASE}-country-us-session-{session_id}"
                await asyncio.sleep(2)
            else:
                result['error'] = str(e)

    return result


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'version': '7.12-no-networkidle',
        'session_cache_size': len(session_cache)
    })


@app.route('/scrape', methods=['POST'])
def scrape():
    """Main scrape endpoint - CLEARS CACHE AT START"""

    # ========================================
    # STEP 1: CLEAR ALL CACHE/SESSION DATA
    # This is the FIRST thing we do!
    # ========================================
    clear_session()

    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    print("\n" + "="*60)
    print(f"SCRAPING: {url}")
    print("="*60)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(scrape_with_playwright(url))
        loop.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/clear_cache', methods=['POST'])
def clear_cache():
    """Manual cache clear endpoint"""
    clear_session()
    return jsonify({'status': 'session cleared', 'success': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
