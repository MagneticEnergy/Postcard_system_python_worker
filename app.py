import os
from flask import Flask, request, jsonify
import base64
import requests

app = Flask(__name__)

# Version
VERSION = "7.22-syntax-fix"
print(f"=== STARTING WORKER VERSION {VERSION} ===")


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'version': VERSION
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

        print(f"Generated QR code for URL: {url[:50]}...")

        return jsonify({
            "success": True,
            "qr_image": f"data:image/png;base64,{base64_image}",
            "qr_url": url,
            "size": size
        })

    except Exception as e:
        print(f"QR generation error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route('/update-highlevel-contact', methods=['POST'])
def update_highlevel_contact():
    """Update HighLevel contact with trigger URL and QR data"""

    data = request.json
    print("=== UPDATE HIGHLEVEL CONTACT ===")
    print(f"Received data: {data}")

    contact_id = data.get('contact_id')
    trigger_url = data.get('trigger_url')
    qr_image = data.get('qr_image')
    qr_url = data.get('qr_url') or trigger_url
    neighbor_tag = data.get('neighbor_tag')

    # Validate required fields
    if not contact_id:
        print("ERROR: No contact_id provided")
        return jsonify({"error": "contact_id is required", "success": False}), 400

    if not trigger_url:
        print("ERROR: No trigger_url provided")
        return jsonify({"error": "trigger_url is required", "success": False}), 400

    # HighLevel API configuration
    url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"

    hl_token = os.environ.get('HIGHLEVEL_TOKEN', 'pit-b553bc1f-b684-4032-ab89-f5fe5550881d')

    headers = {
        "Authorization": f"Bearer {hl_token}",
        "Content-Type": "application/json",
        "Version": "2021-07-28"
    }

    # Custom field IDs from HighLevel
    field_ids = {
        "custom_preview_url_triggerlink": "yhS3VdK90AqkuaDzUwbV",
        "custom_preview_qr_url": "Cy3UNg2N0zTql32AxKo9",
        "installed_neighbor": "qlC98frkc0DU0sFIF2Dk"
    }

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

    print(f"API URL: {url}")
    print(f"Payload: {payload}")

    try:
        response = requests.put(url, json=payload, headers=headers, timeout=30)

        print(f"Response Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print("SUCCESS: Contact updated")
            return jsonify({
                "success": True,
                "status": response.status_code,
                "contact_id": contact_id,
                "message": "Contact updated successfully"
            })
        else:
            error_text = response.text
            print(f"ERROR: {error_text}")
            return jsonify({
                "success": False,
                "status": response.status_code,
                "error": error_text
            }), response.status_code

    except requests.exceptions.Timeout:
        print("ERROR: Request timeout")
        return jsonify({"success": False, "error": "Request timeout"}), 504
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
