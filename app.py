from flask import Flask, request, jsonify
import asyncio
import os
import base64
from datetime import datetime
from playwright.async_api import async_playwright

app = Flask(__name__)

# Web Unlocker proxy configuration
WEB_UNLOCKER_PROXY = {
    "server": "http://brd.superproxy.io:33335",
    "username": "brd-customer-hl_ead19305-zone-web_unlocker1",
    "password": os.environ.get("WEB_UNLOCKER_PASSWORD", "9bra6mx0xptc")
}

# Hero area threshold - images above this Y position are considered hero images
HERO_Y_THRESHOLD = 600  # pixels from top

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "proxy": "web_unlocker1",
        "hero_threshold": HERO_Y_THRESHOLD
    })

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

async def scrape_page(url: str, capture_screenshot: bool = False):
    """Scrape a URL and extract ONLY hero area images"""
    
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
            await page.wait_for_timeout(5000)
            
            title = await page.title()
            
            screenshot_base64 = None
            if capture_screenshot:
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            
            # Extract ONLY hero area images
            hero_images = await extract_hero_images(page, url)
            
            result = {
                "success": True,
                "url": url,
                "title": title,
                "hero_images": hero_images,
                "hero_image_count": len(hero_images),
                "method": "playwright_web_unlocker_proxy",
                "hero_threshold": HERO_Y_THRESHOLD
            }
            
            if screenshot_base64:
                result["screenshot"] = screenshot_base64
            
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

async def extract_hero_images(page, url: str):
    """Extract ONLY images from the hero area (top of page)"""
    hero_images = []
    seen_urls = set()
    
    # Get all images on the page
    all_imgs = await page.query_selector_all("img")
    
    for img in all_imgs:
        try:
            src = await img.get_attribute("src")
            if not src or not is_valid_listing_image(src):
                continue
            
            if src in seen_urls:
                continue
            
            # Get bounding box to check Y position
            box = await img.bounding_box()
            if not box:
                continue
            
            y_position = box["y"]
            width = box["width"]
            height = box["height"]
            
            # ONLY include images in hero area (top of page)
            if y_position > HERO_Y_THRESHOLD:
                continue
            
            # Skip tiny images (icons, etc.)
            if width < 100 or height < 100:
                continue
            
            seen_urls.add(src)
            hero_images.append({
                "url": src,
                "y_position": y_position,
                "width": width,
                "height": height,
                "area": width * height
            })
            
        except Exception as e:
            continue
    
    # Sort by Y position (top to bottom), then by area (larger first)
    hero_images.sort(key=lambda x: (x["y_position"], -x["area"]))
    
    return hero_images

def is_valid_listing_image(url: str) -> bool:
    """Check if URL is a valid listing image"""
    if not url:
        return False
    
    url_lower = url.lower()
    
    exclude_patterns = [
        "logo", "icon", "avatar", "profile", "agent",
        "map", "satellite", "streetview", "street-view",
        "sprite", "placeholder", "loading", "spinner",
        "facebook", "twitter", "instagram", "social",
        "badge", "seal", "certificate", "award",
        "1x1", "pixel", "tracking", "beacon",
        "data:image", "svg+xml", "base64"
    ]
    
    for pattern in exclude_patterns:
        if pattern in url_lower:
            return False
    
    valid_extensions = [".jpg", ".jpeg", ".png", ".webp"]
    has_extension = any(ext in url_lower for ext in valid_extensions)
    is_cdn = "cdn" in url_lower or "photo" in url_lower
    
    return has_extension or is_cdn

# ============================================================
# ENDPOINTS
# ============================================================

@app.route("/scrape", methods=["POST"])
def scrape_endpoint():
    """Scrape page and return all hero images"""
    try:
        data = request.get_json()
        url = data.get("url")
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        result = run_async(scrape_page(url, capture_screenshot=False))
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scrape_with_screenshot", methods=["POST"])
def scrape_with_screenshot_endpoint():
    """Scrape page with screenshot for verification"""
    try:
        data = request.get_json()
        url = data.get("url")
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        result = run_async(scrape_page(url, capture_screenshot=True))
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scrape_single", methods=["POST"])
def scrape_single_image():
    """
    Get a SINGLE hero image by index.
    Use this for sequential analysis - request index 0, analyze,
    if not good enough request index 1, etc.
    """
    try:
        data = request.get_json()
        url = data.get("url")
        index = data.get("index", 0)
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        result = run_async(scrape_page(url, capture_screenshot=False))
        
        if not result.get("success"):
            return jsonify(result)
        
        hero_images = result.get("hero_images", [])
        
        if index >= len(hero_images):
            return jsonify({
                "success": True,
                "has_image": False,
                "index": index,
                "total_hero_images": len(hero_images),
                "message": f"No more hero images. Only {len(hero_images)} found."
            })
        
        image = hero_images[index]
        return jsonify({
            "success": True,
            "has_image": True,
            "image": image,
            "index": index,
            "total_hero_images": len(hero_images),
            "has_more": index < len(hero_images) - 1,
            "title": result.get("title")
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
