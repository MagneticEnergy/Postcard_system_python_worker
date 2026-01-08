import os
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright
import asyncio
import hashlib
import time
import base64
import os
import requests

app = Flask(__name__)

# Version
VERSION = "7.20-highlevel-fix"
print(f"=== STARTING WORKER VERSION {VERSION} ===")

# Session cache - cleared at start of each /scrape request
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
    return f"{PROXY_USER_BASE}-country-us-session-{session_id}"


def clear_session():
    """Clear all cached data - called at START of each request"""
    global session_cache
    session_cache = {}
    print("🧹 SESSION CLEARED - Starting fresh")


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'version': VERSION,
        'session_cache_size': len(session_cache)
    })


@app.route('/generate-qr', methods=['POST'])
def generate_qr():
    """Generate QR code from URL and return base64 PNG"""
    import qrcode
    from PIL import Image
    import io

    data = request.get_json()
    url = data.get('url', '')
    size = data.get('size', 400)
    border_size = data.get('border_size', 4)

    if not url:
        return jsonify({"error": "URL is required", "success": False}), 400

    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=border_size,
        )
        qr.add_data(url)
        qr.make(fit=True)

        qr_image = qr.make_image(fill_color="black", back_color="white")

        if size and size > 0:
            qr_image = qr_image.resize((size, size), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        qr_image.save(buffer, format="PNG")
        base64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')

        print(f"✓ Generated QR code for URL: {url[:50]}...")

        return jsonify({
            "success": True,
            "qr_image": f"data:image/png;base64,{base64_image}",
            "qr_url": url,  # Return the original URL as qr_url
            "size": size
        })

    except Exception as e:
        print(f"✗ QR generation error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route('/update-highlevel-contact', methods=['POST'])
def update_highlevel_contact():
    """Update HighLevel contact with trigger URL and QR data"""

    data = request.json
    print(f"
=== UPDATE HIGHLEVEL CONTACT ===")
    print(f"Received data: {data}")

    contact_id = data.get('contact_id')
    trigger_url = data.get('trigger_url')
    qr_image = data.get('qr_image')  # base64 data URL
    qr_url = data.get('qr_url') or trigger_url  # Use trigger_url if qr_url not provided
    neighbor_tag = data.get('neighbor_tag')

    # Validate required fields
    if not contact_id:
        print("✗ ERROR: No contact_id provided")
        return jsonify({"error": "contact_id is required", "success": False}), 400

    if not trigger_url:
        print("✗ ERROR: No trigger_url provided")
        return jsonify({"error": "trigger_url is required", "success": False}), 400

    # HighLevel API configuration
    url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"

    # Get token from environment or use default
    hl_token = os.environ.get('HIGHLEVEL_TOKEN', 'pit-b553bc1f-b684-4032-ab89-f5fe5550881d')

    headers = {
        "Authorization": f"Bearer {hl_token}",
        "Content-Type": "application/json",
        "Version": "2021-07-28"
    }

    # Custom field IDs from HighLevel
    field_ids = {
        "custom_preview_url_triggerlink": "yhS3VdK90AqkuaDzUwbV",  # TEXT field
        "custom_preview_qr_url": "Cy3UNg2N0zTql32AxKo9",  # TEXT field
        "installed_neighbor": "qlC98frkc0DU0sFIF2Dk"  # TEXT field for neighbor name
    }
    # Note: custom_preview_qr_image (Qx6Tl0WiqtpuaxfnKkhU) is FILE_UPLOAD type
    # We skip it for now as it requires file upload, not base64

    # Build payload with customFields array
    custom_fields = [
        {
            "id": field_ids["custom_preview_url_triggerlink"],
            "value": trigger_url
        },
        {
            "id": field_ids["custom_preview_qr_url"],
            "value": qr_url
        }
    ]

    # Add neighbor name to installed_neighbor field if we have it
    if neighbor_tag:
        # Extract just the name from "Installed Neighbor XYZ" format
        neighbor_name = neighbor_tag.replace("Installed Neighbor ", "").strip() if neighbor_tag.startswith("Installed Neighbor ") else neighbor_tag
        custom_fields.append({
            "id": field_ids["installed_neighbor"],
            "value": neighbor_name
        })

    payload = {
        "customFields": custom_fields
    }

    # Add tag if neighbor_tag exists
    if neighbor_tag:
        payload["tags"] = [neighbor_tag]

    print(f"
API URL: {url}")
    print(f"Payload: {payload}")

    try:
        response = requests.put(url, json=payload, headers=headers, timeout=30)

        print(f"
Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"✓ SUCCESS: Contact updated")
            return jsonify({
                "success": True,
                "status": response.status_code,
                "contact_id": contact_id,
                "message": "Contact updated successfully"
            })
        else:
            error_text = response.text
            print(f"✗ ERROR: {error_text}")
            return jsonify({
                "success": False,
                "status": response.status_code,
                "error": error_text
            }), response.status_code

    except requests.exceptions.Timeout:
        print("✗ ERROR: Request timeout")
        return jsonify({"success": False, "error": "Request timeout"}), 504
    except Exception as e:
        print(f"✗ ERROR: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
