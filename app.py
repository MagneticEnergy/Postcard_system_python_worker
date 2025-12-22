from flask import Flask, request, jsonify
from playwright.async_api import async_playwright
import asyncio
import hashlib
import time
import base64
import os

app = Flask(__name__)

# Session cache - cleared at start of each /scrape request
# Can be used within same request for retries
session_cache = {}

# Bright Data Web Unlocker proxy configuration
PROXY_HOST = "brd.superproxy.io"
PROXY_PORT = 33335
PROXY_USER = "brd-customer-hl_ead19305-zone-web_unlocker1"
PROXY_PASS = "9bra6mx0xptc"


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
        await page.wait_for_load_state('networkidle', timeout=30000)

        # Additional wait for lazy-loaded images
        await asyncio.sleep(3)

        # Scroll down slightly to trigger lazy loading, then back up
        await page.evaluate('window.scrollTo(0, 500)')
        await asyncio.sleep(1)
        await page.evaluate('window.scrollTo(0, 0)')
        await asyncio.sleep(2)

        # JavaScript to extract images from hero area
        js_code = """
            () => {
                const heroHeight = 800;  // Increased from 700
                const images = [];
                const seenUrls = new Set();
                const debug = [];

                const addImage = (url, width, height, top, source) => {
                    if (!url || seenUrls.has(url)) return;
                    if (!url.startsWith('http')) return;
                    if (url.includes('logo') || url.includes('icon') || url.includes('avatar')) return;
                    if (url.includes('sprite') || url.includes('placeholder')) return;
                    // Reduced minimum size to catch more images
                    if (width < 50 || height < 50) return;

                    seenUrls.add(url);
                    images.push({ url, width, height, top, source });
                };

                // Count all elements for debugging
                debug.push('Total img tags: ' + document.querySelectorAll('img').length);
                debug.push('Total picture tags: ' + document.querySelectorAll('picture').length);

                // Method 1: Standard img tags - check ALL attributes
                document.querySelectorAll('img').forEach((img) => {
                    const rect = img.getBoundingClientRect();
                    // Check multiple possible sources
                    const possibleSrcs = [
                        img.src,
                        img.currentSrc,
                        img.dataset.src,
                        img.getAttribute('data-src'),
                        img.getAttribute('data-lazy-src'),
                        img.getAttribute('data-original'),
                        img.srcset ? img.srcset.split(',')[0].trim().split(' ')[0] : null
                    ].filter(Boolean);

                    if (rect.top < heroHeight && possibleSrcs.length > 0) {
                        const src = possibleSrcs[0];
                        addImage(src, rect.width, rect.height, rect.top, 'img_tag');
                    }
                });

                // Method 2: Background images
                document.querySelectorAll('*').forEach((el) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.top < heroHeight && rect.width > 100 && rect.height > 80) {
                        const style = window.getComputedStyle(el);
                        const bgImage = style.backgroundImage;
                        if (bgImage && bgImage !== 'none' && bgImage.includes('url')) {
                            const urlMatch = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
                            if (urlMatch) {
                                addImage(urlMatch[1], rect.width, rect.height, rect.top, 'background');
                            }
                        }
                    }
                });

                // Method 3: Picture/source elements with srcset
                document.querySelectorAll('picture').forEach((picture) => {
                    const rect = picture.getBoundingClientRect();
                    if (rect.top < heroHeight) {
                        // Check source elements
                        picture.querySelectorAll('source').forEach((source) => {
                            const srcset = source.srcset;
                            if (srcset) {
                                const src = srcset.split(',')[0].trim().split(' ')[0];
                                addImage(src, rect.width || 300, rect.height || 200, rect.top, 'picture_source');
                            }
                        });
                        // Check img inside picture
                        const img = picture.querySelector('img');
                        if (img) {
                            const src = img.currentSrc || img.src;
                            addImage(src, rect.width || 300, rect.height || 200, rect.top, 'picture_img');
                        }
                    }
                });

                // Method 4: Redfin-specific selectors
                document.querySelectorAll('[class*="photo"], [class*="Photo"], [class*="image"], [class*="Image"], [class*="media"], [class*="Media"], [class*="gallery"], [class*="Gallery"]').forEach((el) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.top < heroHeight) {
                        const img = el.querySelector('img');
                        if (img) {
                            const src = img.currentSrc || img.src || img.dataset.src;
                            if (src) addImage(src, rect.width, rect.height, rect.top, 'photo_class');
                        }
                        // Also check for background image on the element itself
                        const style = window.getComputedStyle(el);
                        const bgImage = style.backgroundImage;
                        if (bgImage && bgImage !== 'none' && bgImage.includes('url')) {
                            const urlMatch = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
                            if (urlMatch) {
                                addImage(urlMatch[1], rect.width, rect.height, rect.top, 'photo_class_bg');
                            }
                        }
                    }
                });

                // Method 5: Check for Redfin's specific image container
                document.querySelectorAll('[data-rf-test-id*="photo"], [data-rf-test-id*="image"], .HomeViews, .PhotosView, .MediaGallery').forEach((el) => {
                    const imgs = el.querySelectorAll('img');
                    imgs.forEach((img) => {
                        const rect = img.getBoundingClientRect();
                        const src = img.currentSrc || img.src;
                        if (src) addImage(src, rect.width, rect.height, rect.top, 'redfin_specific');
                    });
                });

                debug.push('Images found: ' + images.length);
                console.log('Debug:', debug);

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

    return hero_images


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

    # Generate unique session ID for IP rotation
    session_id = f"session_{int(time.time() * 1000)}_{hashlib.md5(url.encode()).hexdigest()[:8]}"
    proxy_user_with_session = f"{PROXY_USER}-session-{session_id}"

    max_retries = 10

    for attempt in range(max_retries):
        browser = None
        playwright = None
        try:
            print(f"Attempt {attempt + 1}/{max_retries} for {url}")
            print(f"Using session: {session_id}")

            playwright = await async_playwright().start()

            browser = await playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )

            # Create FRESH context - no cookies, no cache
            context = await browser.new_context(
                proxy={
                    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
                    "username": proxy_user_with_session,
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
            result['hero_images'] = await extract_hero_images(page)
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
                proxy_user_with_session = f"{PROXY_USER}-session-{session_id}"
                await asyncio.sleep(2)
            else:
                result['error'] = str(e)

    return result


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'version': '7.8-debug-info',
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
