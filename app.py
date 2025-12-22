from flask import Flask, request, jsonify
import asyncio
import hashlib
import time
import base64
from playwright.async_api import async_playwright

app = Flask(__name__)

# Version
VERSION = "8.0-scraping-browser"

# BrightData Scraping Browser endpoint (real browser, no caching issues)
SCRAPING_BROWSER_WS = "wss://brd-customer-hl_ead19305-zone-scraping_browser1:f25aiw90s21r@brd.superproxy.io:9222"

# Session cache for within-request caching only
session_cache = {}


def clear_session():
    """Clear all cached data - called at START of each request"""
    global session_cache
    session_cache = {}
    print("🧹 SESSION CLEARED - Starting fresh")


async def extract_hero_images(page):
    """Extract images from the hero area (top 700px) of the page"""
    hero_images = []

    try:
        # Wait for images to load
        await page.wait_for_load_state('domcontentloaded')
        await asyncio.sleep(2)  # Give time for lazy-loaded images

        # Get viewport height for hero area calculation
        hero_height = 700

        # Extract images using JavaScript
        images = await page.evaluate(f'''() => {{
            const heroHeight = {hero_height};
            const images = [];

            // Get all img elements
            document.querySelectorAll('img').forEach(img => {{
                const rect = img.getBoundingClientRect();
                const src = img.src || img.dataset.src || img.getAttribute('data-lazy-src');

                // Only include images in hero area (top portion of page)
                if (rect.top < heroHeight && src && src.startsWith('http')) {{
                    // Filter out tiny images, icons, logos
                    if (rect.width > 100 && rect.height > 100) {{
                        images.push({{
                            url: src,
                            width: rect.width,
                            height: rect.height,
                            top: rect.top,
                            alt: img.alt || ''
                        }});
                    }}
                }}
            }});

            // Also check for background images in hero area
            document.querySelectorAll('div, section, header').forEach(el => {{
                const rect = el.getBoundingClientRect();
                if (rect.top < heroHeight) {{
                    const style = window.getComputedStyle(el);
                    const bgImage = style.backgroundImage;
                    if (bgImage && bgImage !== 'none' && bgImage.includes('url(')) {{
                        const match = bgImage.match(/url\(["']?([^"')]+)["']?\)/);
                        if (match && match[1] && match[1].startsWith('http')) {{
                            images.push({{
                                url: match[1],
                                width: rect.width,
                                height: rect.height,
                                top: rect.top,
                                alt: 'background-image',
                                source: 'background'
                            }});
                        }}
                    }}
                }}
            }});

            return images;
        }}''')

        # Sort by position (top first) and deduplicate
        seen_urls = set()
        for img in sorted(images, key=lambda x: x.get('top', 0)):
            url = img.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                hero_images.append(img)

        print(f"Found {len(hero_images)} hero images")

    except Exception as e:
        print(f"Error extracting hero images: {e}")

    return hero_images[:8]  # Limit to 8 images


async def scrape_with_playwright(url):
    """Scrape a URL using Playwright with BrightData Scraping Browser"""
    global session_cache

    result = {
        'success': False,
        'url': url,
        'title': '',
        'hero_images': [],
        'hero_image_count': 0,
        'screenshot_base64': ''
    }

    max_retries = 5
    browser = None
    playwright = None

    for attempt in range(max_retries):
        try:
            print(f"Attempt {attempt + 1}/{max_retries} for {url}")
            print(f"Connecting to BrightData Scraping Browser...")

            playwright = await async_playwright().start()

            # Connect to BrightData's Scraping Browser via CDP
            browser = await playwright.chromium.connect_over_cdp(
                SCRAPING_BROWSER_WS,
                timeout=60000
            )

            print("Connected to Scraping Browser!")

            # Get the default context and page
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            # Navigate to the URL
            print(f"Navigating to {url}...")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            # Wait for content to load
            await asyncio.sleep(3)

            # Get page title
            result['title'] = await page.title()
            print(f"Page title: {result['title']}")

            # Capture screenshot
            try:
                screenshot_bytes = await page.screenshot(type='png', full_page=False)
                result['screenshot_base64'] = base64.b64encode(screenshot_bytes).decode('utf-8')
                print("Screenshot captured")
            except Exception as e:
                print(f"Screenshot failed: {e}")

            # Extract hero images
            result['hero_images'] = await extract_hero_images(page)
            result['hero_image_count'] = len(result['hero_images'])
            result['success'] = True

            print(f"Successfully extracted {result['hero_image_count']} images")
            break

        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in 2 seconds...")
                await asyncio.sleep(2)
            else:
                result['error'] = str(e)

        finally:
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

    return result


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'version': VERSION,
        'session_cache_size': len(session_cache)
    })


@app.route('/scrape', methods=['POST'])
def scrape():
    """Main scrape endpoint"""
    # Clear session at start of each request
    clear_session()

    data = request.get_json()
    url = data.get('url', '')

    if not url:
        return jsonify({'error': 'No URL provided', 'success': False}), 400

    print(f"
{'='*60}")
    print(f"SCRAPE REQUEST: {url}")
    print(f"{'='*60}")

    # Run the async scrape
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(scrape_with_playwright(url))
    finally:
        loop.close()

    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
