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
        "version": "4.0-multi-hero"
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

async def scrape_page_with_all_hero_images(url: str):
    """
    Scrape a page and capture ALL hero images.
    For listings with multiple images in a carousel/gallery,
    we capture each one so Claude can analyze and select the best.
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
            hero_images = []
            
            # Method 1: Try to click through carousel and capture each image
            if "redfin.com" in url:
                hero_images = await extract_redfin_hero_images(page)
            elif "zillow.com" in url:
                hero_images = await extract_zillow_hero_images(page)
            else:
                # Generic extraction for other sites
                hero_images = await extract_generic_hero_images(page)
            
            # Fallback: If no individual images found, capture the hero crop
            if len(hero_images) == 0:
                hero_crop = await page.screenshot(
                    clip={"x": 0, "y": 0, "width": 1920, "height": 600}
                )
                hero_images.append({
                    "screenshot_base64": base64.b64encode(hero_crop).decode("utf-8"),
                    "source": "hero_crop_fallback",
                    "index": 0
                })
            
            result = {
                "success": True,
                "url": url,
                "title": title,
                "hero_images": hero_images,
                "hero_image_count": len(hero_images),
                "method": "playwright_web_unlocker_proxy"
            }
            
            # Cache the result
            cache_result(url, {
                "title": title,
                "hero_images": hero_images
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

async def extract_redfin_hero_images(page):
    """Extract hero images from Redfin listing page"""
    hero_images = []
    
    try:
        # Redfin uses a photo carousel - try to find all photos
        # First, try to click on the main photo to open the gallery
        main_photo_selectors = [
            '[data-rf-test-id="dp-photo"]',
            '.PhotosView',
            '.HomeMainStats img',
            '.dp-photos img',
            'img[class*="photo"]'
        ]
        
        # Try to find images in the hero area (top 700px)
        images_in_hero = await page.evaluate('''
            () => {
                const images = [];
                document.querySelectorAll('img').forEach((img, idx) => {
                    const rect = img.getBoundingClientRect();
                    const src = img.src || img.getAttribute('data-src');
                    // Only images in hero area (top 700px) and reasonably sized
                    if (src && rect.top < 700 && rect.width > 150 && rect.height > 100) {
                        // Skip icons, logos, agent photos
                        const srcLower = src.toLowerCase();
                        if (!srcLower.includes('logo') && 
                            !srcLower.includes('icon') && 
                            !srcLower.includes('agent') &&
                            !srcLower.includes('avatar') &&
                            !srcLower.includes('map')) {
                            images.push({
                                src: src,
                                top: rect.top,
                                left: rect.left,
                                width: rect.width,
                                height: rect.height,
                                idx: idx
                            });
                        }
                    }
                });
                // Sort by position (top-left first)
                images.sort((a, b) => a.top - b.top || a.left - b.left);
                return images;
            }
        ''')
        
        # Capture screenshot of each image element
        for i, img_info in enumerate(images_in_hero[:8]):  # Max 8 images
            try:
                # Find the img element by index
                all_imgs = await page.query_selector_all('img')
                if img_info['idx'] < len(all_imgs):
                    img_element = all_imgs[img_info['idx']]
                    # Screenshot the element
                    screenshot = await img_element.screenshot()
                    hero_images.append({
                        "screenshot_base64": base64.b64encode(screenshot).decode("utf-8"),
                        "source": "redfin_hero_img",
                        "url": img_info['src'],
                        "y_position": img_info['top'],
                        "width": img_info['width'],
                        "height": img_info['height'],
                        "index": i
                    })
            except Exception as e:
                continue
        
        # If we found images via element screenshots, return them
        if hero_images:
            return hero_images
        
        # Fallback: Try to capture the main photo container
        for selector in main_photo_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    box = await element.bounding_box()
                    if box and box["y"] < 700 and box["width"] > 200:
                        screenshot = await element.screenshot()
                        hero_images.append({
                            "screenshot_base64": base64.b64encode(screenshot).decode("utf-8"),
                            "source": f"redfin_selector_{selector}",
                            "index": 0
                        })
                        break
            except:
                continue
                
    except Exception as e:
        print(f"Redfin extraction error: {e}")
    
    return hero_images

async def extract_zillow_hero_images(page):
    """Extract hero images from Zillow listing page"""
    hero_images = []
    
    try:
        # Zillow also uses a photo carousel
        images_in_hero = await page.evaluate('''
            () => {
                const images = [];
                document.querySelectorAll('img').forEach((img, idx) => {
                    const rect = img.getBoundingClientRect();
                    const src = img.src || img.getAttribute('data-src');
                    if (src && rect.top < 700 && rect.width > 150 && rect.height > 100) {
                        const srcLower = src.toLowerCase();
                        if (!srcLower.includes('logo') && 
                            !srcLower.includes('icon') && 
                            !srcLower.includes('agent') &&
                            !srcLower.includes('avatar')) {
                            images.push({
                                src: src,
                                top: rect.top,
                                left: rect.left,
                                width: rect.width,
                                height: rect.height,
                                idx: idx
                            });
                        }
                    }
                });
                images.sort((a, b) => a.top - b.top || a.left - b.left);
                return images;
            }
        ''')
        
        for i, img_info in enumerate(images_in_hero[:8]):
            try:
                all_imgs = await page.query_selector_all('img')
                if img_info['idx'] < len(all_imgs):
                    img_element = all_imgs[img_info['idx']]
                    screenshot = await img_element.screenshot()
                    hero_images.append({
                        "screenshot_base64": base64.b64encode(screenshot).decode("utf-8"),
                        "source": "zillow_hero_img",
                        "url": img_info['src'],
                        "y_position": img_info['top'],
                        "index": i
                    })
            except:
                continue
                
    except Exception as e:
        print(f"Zillow extraction error: {e}")
    
    return hero_images

async def extract_generic_hero_images(page):
    """Generic extraction for other real estate sites"""
    hero_images = []
    
    try:
        images_in_hero = await page.evaluate('''
            () => {
                const images = [];
                document.querySelectorAll('img').forEach((img, idx) => {
                    const rect = img.getBoundingClientRect();
                    const src = img.src || img.getAttribute('data-src');
                    if (src && rect.top < 700 && rect.width > 150 && rect.height > 100) {
                        images.push({
                            src: src,
                            top: rect.top,
                            idx: idx
                        });
                    }
                });
                images.sort((a, b) => a.top - b.top);
                return images;
            }
        ''')
        
        for i, img_info in enumerate(images_in_hero[:8]):
            try:
                all_imgs = await page.query_selector_all('img')
                if img_info['idx'] < len(all_imgs):
                    img_element = all_imgs[img_info['idx']]
                    screenshot = await img_element.screenshot()
                    hero_images.append({
                        "screenshot_base64": base64.b64encode(screenshot).decode("utf-8"),
                        "source": "generic_hero_img",
                        "url": img_info['src'],
                        "index": i
                    })
            except:
                continue
                
    except Exception as e:
        print(f"Generic extraction error: {e}")
    
    return hero_images

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
                "cached": True
            })
        
        result = run_async(scrape_page_with_all_hero_images(url))
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
        result = run_async(scrape_page_with_all_hero_images(url))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scrape_single", methods=["POST"])
def scrape_single_image():
    """Get a single hero image by index - uses cache if available"""
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
            from_cache = True
        else:
            result = run_async(scrape_page_with_all_hero_images(url))
            if not result.get("success"):
                return jsonify(result)
            hero_images = result.get("hero_images", [])
            title = result.get("title")
            from_cache = False
        
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
