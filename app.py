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
    """Update HighLevel contact with trigger URL, QR data, and neighbor info"""

    data = request.json
    print("=== UPDATE HIGHLEVEL CONTACT ===")
    print(f"Received data: {data}")

    contact_id = data.get('contact_id')
    trigger_url = data.get('trigger_url')  # Full URL for QR code
    qr_image_base64 = data.get('qr_image')  # Base64 encoded QR image
    neighbor_tag = data.get('neighbor_tag')

    # Validate required fields
    if not contact_id:
        print("ERROR: No contact_id provided")
        return jsonify({"error": "contact_id is required", "success": False}), 400

    if not trigger_url:
        print("ERROR: No trigger_url provided")
        return jsonify({"error": "trigger_url is required", "success": False}), 400

    # HighLevel API configuration
    hl_token = os.environ.get('HIGHLEVEL_TOKEN', 'pit-b553bc1f-b684-4032-ab89-f5fe5550881d')
    location_id = 'XBny1dU0QeSvwdTLiMBu'

    headers = {
        "Authorization": f"Bearer {hl_token}",
        "Content-Type": "application/json",
        "Version": "2021-07-28"
    }

    # Custom field IDs from HighLevel
    field_ids = {
        "custom_preview_url_triggerlink": "yhS3VdK90AqkuaDzUwbV",
        "custom_preview_qr_url": "Cy3UNg2N0zTql32AxKo9",
        "custom_preview_qr_image": "Qx6Tl0WiqtpuaxfnKkhU",
        "installed_neighbor_lastname": "HmGAlm8iqMIx66Ymvcte"
    }

    short_trigger_url = trigger_url  # Default to full URL
    qr_image_url = None

    # Step 1: Create HighLevel Trigger Link (short URL)
    try:
        print("Creating HighLevel trigger link...")
        link_response = requests.post(
            'https://services.leadconnectorhq.com/links/',
            headers=headers,
            json={
                "locationId": location_id,
                "name": f"Preview Link - {contact_id[:8]}",
                "redirectTo": trigger_url
            },
            timeout=30
        )
        if link_response.status_code == 200 or link_response.status_code == 201:
            link_data = link_response.json()
            link_id = link_data.get('link', {}).get('id')
            if link_id:
                # Construct the short URL format
                short_trigger_url = link_data.get("link", {}).get("fieldKey", trigger_url)
                print(f"Created short trigger link: {short_trigger_url}")
        else:
            print(f"Warning: Could not create trigger link: {link_response.text}")
    except Exception as e:
        print(f"Warning: Trigger link creation failed: {str(e)}")

    # Step 2: Upload QR Image to HighLevel Media
    if qr_image_base64:
        try:
            print("Uploading QR image to HighLevel...")
            import base64
            import io

            # Decode base64 image
            # Handle data URL format if present
            if ',' in qr_image_base64:
                qr_image_base64 = qr_image_base64.split(',')[1]

            image_data = base64.b64decode(qr_image_base64)

            # Upload to HighLevel media
            files = {
                'file': ('qr_code.png', io.BytesIO(image_data), 'image/png')
            }
            upload_data = {
                'locationId': location_id,
                'name': f'QR_Code_{contact_id[:8]}'
            }

            upload_headers = {
                "Authorization": f"Bearer {hl_token}",
                "Version": "2021-07-28"
            }

            upload_response = requests.post(
                'https://services.leadconnectorhq.com/medias/upload-file',
                headers=upload_headers,
                files=files,
                data=upload_data,
                timeout=60
            )

            if upload_response.status_code == 200 or upload_response.status_code == 201:
                upload_result = upload_response.json()
                qr_image_url = upload_result.get('url')
                print(f"Uploaded QR image: {qr_image_url}")
            else:
                print(f"Warning: QR image upload failed: {upload_response.text}")
        except Exception as e:
            print(f"Warning: QR image upload failed: {str(e)}")

    # Step 3: Update Contact with all fields
    contact_url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"

    custom_fields = [
        {
            "id": field_ids["custom_preview_url_triggerlink"],
            "value": short_trigger_url  # Short URL for SMS
        },
        {
            "id": field_ids["custom_preview_qr_url"],
            "value": trigger_url  # Full URL (what QR code points to)
        }
    ]

    # Add QR image URL if uploaded successfully
    if qr_image_url:
        custom_fields.append({
            "id": field_ids["custom_preview_qr_image"],
            "value": qr_image_url
        })

    # Add neighbor lastname if we have the tag
    if neighbor_tag:
        neighbor_name = neighbor_tag.replace("Installed Neighbor ", "").strip() if neighbor_tag.startswith("Installed Neighbor ") else neighbor_tag
        custom_fields.append({
            "id": field_ids["installed_neighbor_lastname"],
            "value": neighbor_name
        })

    payload = {
        "customFields": custom_fields
    }

    # Add tag if neighbor_tag exists
    if neighbor_tag:
        payload["tags"] = [neighbor_tag]

    print(f"Updating contact: {contact_url}")
    print(f"Payload: {payload}")

    try:
        response = requests.put(contact_url, json=payload, headers=headers, timeout=30)

        print(f"Response Status: {response.status_code}")

        if response.status_code == 200:
            print("SUCCESS: Contact updated")
            return jsonify({
                "success": True,
                "status": response.status_code,
                "contact_id": contact_id,
                "message": "Contact updated successfully",
                "short_trigger_url": short_trigger_url,
                "qr_url": trigger_url,
                "qr_image_url": qr_image_url
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
