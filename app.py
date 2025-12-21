from flask import Flask, request, jsonify
import asyncio
import os
import base64
from datetime import datetime
from playwright.async_api import async_playwright

app = Flask(__name__)

# Web Unlocker proxy configuration (CHEAPER than Browser API!)
# Cost: ~$1.50 per 1000 requests vs $8/GB for Browser API
WEB_UNLOCKER_PROXY = {
    "server": "http://brd.superproxy.io:33335",
    "username": "brd-customer-hl_ead19305-zone-web_unlocker1",
    "password": os.environ.get("WEB_UNLOCKER_PASSWORD", "9bra6mx0xptc")
}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat(), "proxy": "web_unlocker1"})

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

async def scrape_with_playwright(url: str, capture_screenshot: bool = False):
    """Scrape a URL using Playwright with Web Unlocker proxy"""
    
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
            # Navigate to URL with longer timeout for proxy
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            
            # Wait for content to load
            await page.wait_for_timeout(5000)
            
            # Get page title for verification
            title = await page.title()
            
            # Capture screenshot if requested
            screenshot_base64 = None
            if capture_screenshot:
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            
            # Extract images based on site type
            images = await extract_images(page, url)
            
            result = {
                "success": True,
                "url": url,
                "title": title,
                "images": images,
                "image_count": len(images),
                "method": "playwright_web_unlocker_proxy",
                "cost_estimate": "$0.0015 per request"
            }
            
            if screenshot_base64:
                result["screenshot"] = screenshot_base64
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "url": url,
                "method": "playwright_web_unlocker_proxy"
            }
        finally:
            await context.close()
            await browser.close()

async def extract_images(page, url: str):
    """Extract images from the page - sequential from top to bottom"""
    images = []
    
    if "redfin.com" in url:
        images = await extract_redfin_images(page)
    elif "zillow.com" in url:
        images = await extract_zillow_images(page)
    else:
        images = await extract_generic_images(page)
    
    # Limit to 8 images max
    return images[:8]

async def extract_redfin_images(page):
    """Extract images from Redfin - prioritize hero/main images"""
    images = []
    seen_urls = set()
    
    # Priority 1: Main hero image (the big one at the top)
    hero_selectors = [
        "img.img-card",
        ".PhotosView img",
        ".HomeMainStats img",
        "[data-rf-test-id='basic-card-photo'] img",
        ".dp-photos img"
    ]
    
    for selector in hero_selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements[:3]:  # Only first 3 from each selector
                src = await el.get_attribute("src")
                if src and is_valid_listing_image(src) and src not in seen_urls:
                    seen_urls.add(src)
                    # Get position for sorting
                    box = await el.bounding_box()
                    y_pos = box["y"] if box else 9999
                    images.append({
                        "url": src,
                        "source": "redfin",
                        "selector": selector,
                        "y_position": y_pos
                    })
        except Exception as e:
            pass
    
    # Priority 2: All images with photo CDN URLs
    try:
        all_imgs = await page.query_selector_all("img[src*='ssl.cdn-redfin.com/photo']")
        for img in all_imgs[:10]:
            src = await img.get_attribute("src")
            if src and is_valid_listing_image(src) and src not in seen_urls:
                seen_urls.add(src)
                box = await img.bounding_box()
                y_pos = box["y"] if box else 9999
                images.append({
                    "url": src,
                    "source": "redfin_cdn",
                    "y_position": y_pos
                })
    except:
        pass
    
    # Sort by Y position (top to bottom)
    images.sort(key=lambda x: x.get("y_position", 9999))
    
    return images

async def extract_zillow_images(page):
    """Extract images from Zillow - prioritize main gallery"""
    images = []
    seen_urls = set()
    
    selectors = [
        "[data-testid='hdp-photo-carousel'] img",
        ".media-stream img",
        "picture img",
        ".photo-carousel img"
    ]
    
    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements[:5]:
                src = await el.get_attribute("src")
                if src and is_valid_listing_image(src) and src not in seen_urls:
                    seen_urls.add(src)
                    box = await el.bounding_box()
                    y_pos = box["y"] if box else 9999
                    images.append({
                        "url": src,
                        "source": "zillow",
                        "selector": selector,
                        "y_position": y_pos
                    })
        except:
            pass
    
    images.sort(key=lambda x: x.get("y_position", 9999))
    return images

async def extract_generic_images(page):
    """Extract images from any page"""
    images = []
    seen_urls = set()
    
    try:
        all_imgs = await page.query_selector_all("img")
        for img in all_imgs[:20]:
            src = await img.get_attribute("src")
            if src and is_valid_listing_image(src) and src not in seen_urls:
                seen_urls.add(src)
                box = await img.bounding_box()
                y_pos = box["y"] if box else 9999
                images.append({
                    "url": src,
                    "source": "generic",
                    "y_position": y_pos
                })
    except:
        pass
    
    images.sort(key=lambda x: x.get("y_position", 9999))
    return images

def is_valid_listing_image(url: str) -> bool:
    """Check if URL is a valid listing image (not icon, logo, etc.)"""
    if not url:
        return False
    
    url_lower = url.lower()
    
    # Exclude patterns
    exclude_patterns = [
        "logo", "icon", "avatar", "profile", "agent",
        "map", "satellite", "streetview", "street-view",
        "sprite", "placeholder", "loading", "spinner",
        "facebook", "twitter", "instagram", "social",
        "badge", "seal", "certificate", "award",
        "1x1", "pixel", "tracking", "beacon",
        "data:image", "svg+xml"
    ]
    
    for pattern in exclude_patterns:
        if pattern in url_lower:
            return False
    
    # Must be an image file or CDN URL
    valid_extensions = [".jpg", ".jpeg", ".png", ".webp"]
    has_extension = any(ext in url_lower for ext in valid_extensions)
    is_cdn = "cdn" in url_lower or "photo" in url_lower
    
    return has_extension or is_cdn

@app.route("/scrape", methods=["POST"])
def scrape_sync():
    """Main scrape endpoint"""
    try:
        data = request.get_json()
        url = data.get("url")
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        result = run_async(scrape_with_playwright(url, capture_screenshot=False))
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scrape_with_screenshot", methods=["POST"])
def scrape_with_screenshot_sync():
    """Scrape endpoint with screenshot for verification"""
    try:
        data = request.get_json()
        url = data.get("url")
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        result = run_async(scrape_with_playwright(url, capture_screenshot=True))
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scrape_single", methods=["POST"])
def scrape_single_image():
    """Get a single image by index for sequential processing"""
    try:
        data = request.get_json()
        url = data.get("url")
        index = data.get("index", 0)
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        result = run_async(scrape_with_playwright(url, capture_screenshot=False))
        
        if result.get("success") and result.get("images"):
            images = result["images"]
            if index < len(images):
                return jsonify({
                    "success": True,
                    "image": images[index],
                    "index": index,
                    "total_images": len(images),
                    "has_more": index < len(images) - 1
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"Index {index} out of range. Only {len(images)} images found.",
                    "total_images": len(images)
                })
        else:
            return jsonify(result)
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
