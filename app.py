from flask import Flask, request, jsonify
import asyncio
import os
import base64
import json
from datetime import datetime
from playwright.async_api import async_playwright

app = Flask(__name__)

# Web Unlocker proxy configuration
WEB_UNLOCKER_PROXY = {
    "server": "http://brd.superproxy.io:33335",
    "username": "brd-customer-hl_ead19305-zone-web_unlocker1",
    "password": os.environ.get("WEB_UNLOCKER_PASSWORD", "9bra6mx0xptc")
}

# Cache for scraped pages
page_cache = {}
CACHE_TTL_SECONDS = 300

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "proxy": "web_unlocker1",
        "version": "3.0-hero-screenshot"
    })

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

def get_cached_result(url):
    if url in page_cache:
        cached = page_cache[url]
        age = (datetime.now() - cached["timestamp"]).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return cached
    return None

def cache_result(url, data):
    page_cache[url] = {
        "timestamp": datetime.now(),
        **data
    }

async def scrape_page_with_hero_screenshots(url: str):
    """
    Scrape a page and capture screenshots of the hero area.
    Since Playwright CAN render the page correctly (we see it in screenshots),
    we capture the hero area directly as images.
    """
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--ignore-certificate-errors"]
        )
        
        context = await browser.new_context(
            proxy={
                "server": WEB_UNLOCKER_PROXY["server"],
                "username": WEB_UNLOCKER_PROXY["username"],
                "password": WEB_UNLOCKER_PROXY["password"]
            },
            ignore_https_errors=True,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)  # Wait for JS to render
            
            title = await page.title()
            
            # Capture full viewport screenshot
            full_screenshot = await page.screenshot(full_page=False)
            full_screenshot_b64 = base64.b64encode(full_screenshot).decode("utf-8")
            
            # Try to find and capture the hero image element directly
            hero_images = []
            
            # Method 1: Try to find the main photo container and screenshot it
            hero_selectors = [
                '[data-rf-test-id="dp-photo"]',  # Redfin main photo
                '.PhotosView',
                '.HomeMainStats img',
                '.dp-photos',
                '.hero-image',
                '.main-photo',
                '[class*="hero"] img',
                '[class*="main-photo"]',
                '.photo-carousel img:first-child',
                'img[class*="photo"]'
            ]
            
            for selector in hero_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        # Check if element is visible and in hero area
                        box = await element.bounding_box()
                        if box and box["y"] < 800 and box["width"] > 200 and box["height"] > 150:
                            # Screenshot this specific element
                            element_screenshot = await element.screenshot()
                            element_screenshot_b64 = base64.b64encode(element_screenshot).decode("utf-8")
                            hero_images.append({
                                "screenshot_base64": element_screenshot_b64,
                                "selector": selector,
                                "y_position": box["y"],
                                "width": box["width"],
                                "height": box["height"],
                                "source": "element_screenshot"
                            })
                except Exception as e:
                    continue
            
            # Method 2: Also try to extract actual image URLs from the page
            try:
                image_urls = await page.evaluate('''
                    () => {
                        const images = [];
                        // Get all images in the top 800px
                        document.querySelectorAll('img').forEach(img => {
                            const rect = img.getBoundingClientRect();
                            const src = img.src || img.getAttribute('data-src');
                            if (src && rect.top < 800 && rect.width > 200 && rect.height > 150) {
                                images.push({
                                    url: src,
                                    y: rect.top,
                                    width: rect.width,
                                    height: rect.height
                                });
                            }
                        });
                        // Sort by Y position (top first)
                        images.sort((a, b) => a.y - b.y);
                        return images;
                    }
                ''')
                
                for img in image_urls[:6]:
                    if is_valid_listing_image(img.get("url", "")):
                        hero_images.append({
                            "url": img["url"],
                            "y_position": img["y"],
                            "width": img["width"],
                            "height": img["height"],
                            "source": "img_tag"
                        })
            except:
                pass
            
            # Method 3: Capture a cropped screenshot of just the hero area (top 600px)
            # This is our fallback - we KNOW this shows the correct image
            hero_crop_screenshot = await page.screenshot(
                clip={"x": 0, "y": 0, "width": 1920, "height": 600}
            )
            hero_crop_b64 = base64.b64encode(hero_crop_screenshot).decode("utf-8")
            
            result = {
                "success": True,
                "url": url,
                "title": title,
                "hero_images": hero_images,
                "hero_image_count": len(hero_images),
                "full_screenshot": full_screenshot_b64,
                "hero_crop_screenshot": hero_crop_b64,  # Cropped hero area
                "method": "playwright_web_unlocker_proxy"
            }
            
            # Cache the result
            cache_result(url, {
                "title": title,
                "hero_images": hero_images,
                "hero_crop_screenshot": hero_crop_b64
            })
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "url": url
            }
        finally:
            await context.close()
            await browser.close()

def is_valid_listing_image(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    exclude = ["logo", "icon", "avatar", "agent", "map", "sprite", "placeholder", 
               "facebook", "twitter", "badge", "1x1", "pixel", "data:image", "svg"]
    for pattern in exclude:
        if pattern in url_lower:
            return False
    return any(ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp"]) or "cdn" in url_lower

# ============================================================
# ENDPOINTS
# ============================================================

@app.route("/scrape", methods=["POST"])
def scrape_endpoint():
    try:
        data = request.get_json()
        url = data.get("url")
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        cached = get_cached_result(url)
        if cached:
            return jsonify({
                "success": True,
                "url": url,
                "title": cached.get("title"),
                "hero_images": cached.get("hero_images", []),
                "hero_image_count": len(cached.get("hero_images", [])),
                "hero_crop_screenshot": cached.get("hero_crop_screenshot"),
                "cached": True
            })
        
        result = run_async(scrape_page_with_hero_screenshots(url))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scrape_with_screenshot", methods=["POST"])
def scrape_with_screenshot_endpoint():
    try:
        data = request.get_json()
        url = data.get("url")
        if not url:
            return jsonify({"error": "URL is required"}), 400
        result = run_async(scrape_page_with_hero_screenshots(url))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scrape_single", methods=["POST"])
def scrape_single_image():
    """Get a single hero image by index"""
    try:
        data = request.get_json()
        url = data.get("url")
        index = data.get("index", 0)
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        cached = get_cached_result(url)
        if cached:
            hero_images = cached.get("hero_images", [])
            title = cached.get("title")
            hero_crop = cached.get("hero_crop_screenshot")
            from_cache = True
        else:
            result = run_async(scrape_page_with_hero_screenshots(url))
            if not result.get("success"):
                return jsonify(result)
            hero_images = result.get("hero_images", [])
            title = result.get("title")
            hero_crop = result.get("hero_crop_screenshot")
            from_cache = False
        
        # If no hero images found via selectors, return the cropped screenshot
        if index == 0 and len(hero_images) == 0 and hero_crop:
            return jsonify({
                "success": True,
                "has_image": True,
                "image": {
                    "screenshot_base64": hero_crop,
                    "source": "hero_crop_fallback",
                    "note": "Cropped screenshot of hero area (top 600px)"
                },
                "index": 0,
                "total_hero_images": 1,
                "has_more": False,
                "title": title,
                "cached": from_cache
            })
        
        if index >= len(hero_images):
            return jsonify({
                "success": True,
                "has_image": False,
                "index": index,
                "total_hero_images": len(hero_images),
                "message": f"No more hero images. Only {len(hero_images)} found.",
                "cached": from_cache
            })
        
        image = hero_images[index]
        return jsonify({
            "success": True,
            "has_image": True,
            "image": image,
            "index": index,
            "total_hero_images": len(hero_images),
            "has_more": index < len(hero_images) - 1,
            "title": title,
            "cached": from_cache
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/clear_cache", methods=["POST"])
def clear_cache():
    global page_cache
    count = len(page_cache)
    page_cache = {}
    return jsonify({"success": True, "cleared": count})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
