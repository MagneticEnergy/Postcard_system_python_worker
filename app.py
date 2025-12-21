from flask import Flask, request, jsonify
from playwright.async_api import async_playwright
import asyncio
import os
from datetime import datetime
import random
import string

app = Flask(__name__)

# Web Unlocker proxy configuration
WEB_UNLOCKER_BASE = {
    "server": "http://brd.superproxy.io:33335",
    "customer": "hl_ead19305",
    "zone": "web_unlocker1",
    "password": os.environ.get("WEB_UNLOCKER_PASSWORD", "9bra6mx0xptc")
}

# Cache for scraped results
SCRAPE_CACHE = {}

def generate_session_id():
    """Generate a unique session ID to force IP rotation"""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))

def get_proxy_config(force_new_ip=True):
    """Get proxy configuration with optional session for IP rotation"""
    base_username = f"brd-customer-{WEB_UNLOCKER_BASE['customer']}-zone-{WEB_UNLOCKER_BASE['zone']}"

    if force_new_ip:
        # Add unique session ID to force new IP
        session_id = generate_session_id()
        username = f"{base_username}-session-{session_id}"
    else:
        username = base_username

    return {
        "server": WEB_UNLOCKER_BASE["server"],
        "username": username,
        "password": WEB_UNLOCKER_BASE["password"]
    }

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "proxy": "web_unlocker1",
        "version": "5.0-ip-rotation",
        "features": ["session-based-rotation", "retry-on-failure"]
    })

def get_cached_result(url):
    """Get cached result if available"""
    return SCRAPE_CACHE.get(url)

def cache_result(url, result):
    """Cache a successful result"""
    SCRAPE_CACHE[url] = result

async def extract_hero_images(page):
    """Extract images from the hero area (top 700px) of the page"""
    hero_images = []

    try:
        # JavaScript to extract images from hero area
        js_code = """
            () => {
                const heroHeight = 700;
                const images = [];

                // Get all img elements
                document.querySelectorAll('img').forEach((img, index) => {
                    const rect = img.getBoundingClientRect();
                    if (rect.top < heroHeight && rect.width > 100 && rect.height > 100) {
                        const src = img.src || img.dataset.src || img.getAttribute('data-src');
                        if (src && src.startsWith('http')) {
                            images.push({
                                url: src,
                                width: rect.width,
                                height: rect.height,
                                top: rect.top,
                                index: index,
                                source: 'img_tag'
                            });
                        }
                    }
                });

                // Also check for background images in hero area
                document.querySelectorAll('[style*="background"]').forEach((el, index) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.top < heroHeight && rect.width > 200 && rect.height > 150) {
                        const style = window.getComputedStyle(el);
                        const bgImage = style.backgroundImage;
                        if (bgImage && bgImage !== 'none') {
                            const urlMatch = bgImage.match(/url\\(["']?([^"')]+)["']?\\)/);
                            if (urlMatch && urlMatch[1].startsWith('http')) {
                                images.push({
                                    url: urlMatch[1],
                                    width: rect.width,
                                    height: rect.height,
                                    top: rect.top,
                                    index: index,
                                    source: 'background_image'
                                });
                            }
                        }
                    }
                });

                return images;
            }
        """

        images = await page.evaluate(js_code)

        # Sort by position (top to bottom) and limit to 8
        images.sort(key=lambda x: x.get('top', 0))
        hero_images = images[:8]

    except Exception as e:
        print(f"Error extracting hero images: {e}")

    return hero_images

async def scrape_with_retry(url, max_retries=3):
    """Scrape a page with retry logic and IP rotation on failure"""
    last_error = None

    for attempt in range(max_retries):
        try:
            # Force new IP on each attempt
            proxy_config = get_proxy_config(force_new_ip=True)
            print(f"Attempt {attempt + 1}/{max_retries} with new session ID")

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)

                context = await browser.new_context(
                    proxy=proxy_config,
                    ignore_https_errors=True,
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )

                page = await context.new_page()

                # Set shorter timeout for faster retries
                timeout = 60000 if attempt == 0 else 45000

                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

                # Wait for content to load
                await page.wait_for_timeout(3000)

                # Get page title
                title = await page.title()

                # Extract hero images from top of page
                hero_images = await extract_hero_images(page)

                await browser.close()

                result = {
                    "success": True,
                    "url": url,
                    "title": title,
                    "hero_images": hero_images,
                    "hero_image_count": len(hero_images),
                    "attempt": attempt + 1,
                    "method": "playwright_web_unlocker_with_rotation"
                }

                # Cache successful result
                cache_result(url, result)

                return result

        except Exception as e:
            last_error = str(e)
            print(f"Attempt {attempt + 1} failed: {last_error}")

            # If it's a timeout or connection error, retry with new IP
            if attempt < max_retries - 1:
                print(f"Retrying with new IP...")
                await asyncio.sleep(2)  # Brief pause before retry
            continue

    # All retries failed
    return {
        "success": False,
        "url": url,
        "error": f"All {max_retries} attempts failed. Last error: {last_error}",
        "attempts": max_retries
    }

def run_async(coro):
    """Run async function in sync context"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@app.route("/scrape", methods=["POST"])
def scrape_endpoint():
    try:
        data = request.get_json()
        url = data.get("url")
        if not url:
            return jsonify({"error": "URL is required"}), 400

        # Check cache first
        cached = get_cached_result(url)
        if cached:
            return jsonify({
                "success": True,
                "url": url,
                "title": cached.get("title"),
                "hero_images": cached.get("hero_images", []),
                "hero_image_count": len(cached.get("hero_images", [])),
                "cached": True
            })

        # Scrape with retry and IP rotation
        result = run_async(scrape_with_retry(url, max_retries=3))
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500

@app.route("/scrape_single", methods=["POST"])
def scrape_single_endpoint():
    """Return a single image at a time from cached results"""
    try:
        data = request.get_json()
        url = data.get("url")
        index = data.get("index", 0)

        if not url:
            return jsonify({"error": "URL is required"}), 400

        # Check cache first
        cached = get_cached_result(url)
        if not cached:
            # Scrape if not cached
            cached = run_async(scrape_with_retry(url, max_retries=3))

        if not cached.get("success"):
            return jsonify(cached)

        hero_images = cached.get("hero_images", [])

        if index >= len(hero_images):
            return jsonify({
                "success": True,
                "url": url,
                "image": None,
                "index": index,
                "total_images": len(hero_images),
                "has_more": False
            })

        return jsonify({
            "success": True,
            "url": url,
            "image": hero_images[index],
            "index": index,
            "total_images": len(hero_images),
            "has_more": index < len(hero_images) - 1
        })

    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500

@app.route("/clear_cache", methods=["POST"])
def clear_cache():
    """Clear the scrape cache"""
    global SCRAPE_CACHE
    count = len(SCRAPE_CACHE)
    SCRAPE_CACHE = {}
    return jsonify({"success": True, "cleared": count})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
