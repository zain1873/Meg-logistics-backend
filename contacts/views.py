import json
import re
import logging
import threading

import resend

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.cache import cache
from django.utils import timezone

from .models import ContactSubmission, TRUCK_TYPES

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Email Sending Utility
# ──────────────────────────────────────────────

def send_email(to_email, subject, html_body):
    """
    Send an email via the Resend API. Returns True on success, False on failure.
    Failures are logged but not re-raised so they don't crash the request.
    """
    api_key = settings.RESEND_API_KEY
    if not api_key:
        logger.warning("RESEND_API_KEY not configured – skipping email to %s", to_email)
        return False

    try:
        resend.api_key = api_key
        response = resend.Emails.send({
            "from": "Meg Logistics <noreply@meglogistic.com>",
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        })
        logger.info("Email sent successfully to %s (Resend ID: %s)", to_email, response.get("id"))
        return True
    except Exception as exc:
        logger.error("Resend API error sending to %s: %s", to_email, exc)
        return False


# ──────────────────────────────────────────────
# Notification & Auto-reply Templates
# ──────────────────────────────────────────────

def build_notification_email(submission):
    """Build the HTML email sent to the company dispatch team."""
    sms_text = "Yes" if submission.sms_consent else "No"
    phone = submission.phone or "—"
    truck = submission.truck or "—"
    return f"""
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
    <div style="background: #1a3c6e; padding: 20px; text-align: center;">
        <h1 style="color: #fff; margin: 0;">New Contact Form Submission</h1>
    </div>
    <div style="padding: 20px; border: 1px solid #ddd; border-top: none;">
        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="padding: 8px; font-weight: bold; width: 140px;">Name:</td>
                <td style="padding: 8px;">{submission.name}</td>
            </tr>
            <tr style="background: #f5f7fa;">
                <td style="padding: 8px; font-weight: bold;">Email:</td>
                <td style="padding: 8px;">{submission.email}</td>
            </tr>
            <tr>
                <td style="padding: 8px; font-weight: bold;">Phone:</td>
                <td style="padding: 8px;">{phone}</td>
            </tr>
            <tr style="background: #f5f7fa;">
                <td style="padding: 8px; font-weight: bold;">Truck Type:</td>
                <td style="padding: 8px;">{truck}</td>
            </tr>
            <tr>
                <td style="padding: 8px; font-weight: bold; vertical-align: top;">Message:</td>
                <td style="padding: 8px;">{submission.message}</td>
            </tr>
            <tr style="background: #f5f7fa;">
                <td style="padding: 8px; font-weight: bold;">SMS Consent:</td>
                <td style="padding: 8px;">{sms_text}</td>
            </tr>
            <tr>
                <td style="padding: 8px; font-weight: bold;">Submitted At:</td>
                <td style="padding: 8px;">{submission.created_at.strftime("%Y-%m-%d %H:%M UTC")}</td>
            </tr>
        </table>
    </div>
</body>
</html>
"""


def build_auto_reply_email(submission):
    """Build the auto-reply HTML email sent to the form submitter."""
    return f"""
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
    <div style="background: #1a3c6e; padding: 30px 20px; text-align: center;">
        <h1 style="color: #fff; margin: 0; font-size: 24px;">Thank You for Contacting Meg Logistics</h1>
    </div>
    <div style="padding: 30px 20px; border: 1px solid #ddd; border-top: none;">
        <p>Dear <strong>{submission.name}</strong>,</p>
        <p>Thank you for reaching out to us. We have received your inquiry and our dispatch team will review your message shortly.</p>
        <p style="background: #f5f7fa; padding: 15px; border-left: 4px solid #1a3c6e;">
            <strong>What to expect:</strong> A member of our team will contact you within <strong>24 hours</strong> to discuss your needs and provide the best logistics solutions for you.
        </p>
        <p>If you have any urgent concerns, please feel free to call us directly at <strong>+1 (303) 879-4908</strong> or reply to this email.</p>
        <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
        <p style="color: #888; font-size: 14px;">
            Meg Logistics LLC<br>
            info@meglogistic.com<br>
            www.meglogistic.com
        </p>
    </div>
</body>
</html>
"""


# ──────────────────────────────────────────────
# Background Email Sender
# ──────────────────────────────────────────────

def send_contact_emails(submission):
    """
    Send both notification + auto-reply emails in a background thread.
    Logs errors but never raises — the request has already returned.
    """
    background_logger = logging.getLogger(__name__)
    try:
        notification_body = build_notification_email(submission)
        send_email(
            to_email=settings.NOTIFICATION_EMAIL,
            subject=f"New Contact Form Submission — {submission.name}",
            html_body=notification_body,
        )
    except Exception as exc:
        background_logger.exception("Failed to send notification email: %s", exc)

    try:
        auto_reply_body = build_auto_reply_email(submission)
        send_email(
            to_email=submission.email,
            subject="Thank You for Contacting Meg Logistics",
            html_body=auto_reply_body,
        )
    except Exception as exc:
        background_logger.exception("Failed to send auto-reply email: %s", exc)


# ──────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────

ALLOWED_TRUCK_VALUES = [t[0] for t in TRUCK_TYPES]
PHONE_REGEX = re.compile(r"^[\d\s\-\+\(\)\.]{7,20}$")
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def validate_contact_payload(data):
    """
    Validate the incoming JSON body.
    Returns a list of error dicts (empty list = valid).
    """
    errors = []

    # ── name ──
    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append({"field": "name", "message": "Full name is required."})
    elif len(name.strip()) > 255:
        errors.append({"field": "name", "message": "Full name must be 255 characters or fewer."})

    # ── email ──
    email = data.get("email")
    if not email or not isinstance(email, str) or not email.strip():
        errors.append({"field": "email", "message": "A valid email address is required."})
    elif not EMAIL_REGEX.match(email.strip()):
        errors.append({"field": "email", "message": "A valid email address is required."})
    elif len(email.strip()) > 255:
        errors.append({"field": "email", "message": "Email must be 255 characters or fewer."})

    # ── phone (optional) ──
    phone = data.get("phone")
    if phone and isinstance(phone, str) and phone.strip():
        if not PHONE_REGEX.match(phone.strip()):
            errors.append({"field": "phone", "message": "Please provide a valid phone number."})

    # ── truck (optional) ──
    truck = data.get("truck")
    if truck and isinstance(truck, str) and truck.strip():
        if truck.strip() not in ALLOWED_TRUCK_VALUES:
            errors.append({
                "field": "truck",
                "message": f"Invalid truck type. Must be one of: {', '.join(ALLOWED_TRUCK_VALUES)}",
            })

    # ── message ──
    message = data.get("message")
    if not message or not isinstance(message, str) or not message.strip():
        errors.append({"field": "message", "message": "Message is required."})
    elif len(message.strip()) > 5000:
        errors.append({"field": "message", "message": "Message must be 5000 characters or fewer."})

    # ── smsConsent ──
    sms_consent = data.get("smsConsent")
    if sms_consent is not True:
        errors.append({"field": "smsConsent", "message": "You must agree to receive SMS messages."})

    return errors


# ──────────────────────────────────────────────
# Rate Limiter (sliding window, in-memory)
# ──────────────────────────────────────────────

RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 3600  # seconds (1 hour)


def _rate_limit_key(ip):
    return f"contact_rate_limit:{ip}"


def check_rate_limit(ip):
    """
    Sliding-window rate limiter using Django's cache framework.
    Returns (is_allowed, remaining, reset_time_seconds).
    """
    key = _rate_limit_key(ip)
    now = timezone.now().timestamp()

    # Get the current list of request timestamps for this IP
    timestamps = cache.get(key, [])
    # Remove timestamps outside the sliding window
    timestamps = [ts for ts in timestamps if ts > now - RATE_LIMIT_WINDOW]

    if len(timestamps) >= RATE_LIMIT_MAX:
        # Calculate when the oldest timestamp expires
        next_reset = int(timestamps[0] + RATE_LIMIT_WINDOW - now)
        return False, 0, next_reset

    # Add current timestamp and store
    timestamps.append(now)
    cache.set(key, timestamps, timeout=RATE_LIMIT_WINDOW)

    remaining = RATE_LIMIT_MAX - len(timestamps)
    return True, remaining, 0


# ──────────────────────────────────────────────
# View
# ──────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["GET", "POST", "OPTIONS"])
def contact_submit(request):
    """Handle POST /api/contact – validate, store, notify."""

    # ── CORS preflight ──
    if request.method == "OPTIONS":
        response = JsonResponse({"success": True})
        _set_cors_headers(response)
        return response

    # ── GET – show a simple status page ──
    if request.method == "GET":
        response = JsonResponse({
            "success": True,
            "message": "Contact API is running. Send a POST request to submit the form.",
            "endpoint": "POST /api/contact",
            "content_type": "application/json",
        })
        _set_cors_headers(response)
        return response

    # ── CORS headers on all responses ──
    def json_resp(data, status=200):
        resp = JsonResponse(data, status=status)
        _set_cors_headers(resp)
        return resp

    # ── Rate limiting ──
    ip = request.META.get("REMOTE_ADDR", "127.0.0.1")
    # Respect X-Forwarded-For if behind a proxy
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        ip = forwarded.split(",")[0].strip()

    allowed, remaining, reset_after = check_rate_limit(ip)
    if not allowed:
        resp = json_resp(
            {"success": False, "message": "Too many requests. Please try again later."},
            status=429,
        )
        resp["Retry-After"] = str(reset_after)
        resp["X-RateLimit-Remaining"] = "0"
        return resp

    # ── Parse body ──
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return json_resp(
            {"success": False, "message": "Invalid JSON in request body."},
            status=400,
        )

    # ── Validate ──
    errors = validate_contact_payload(data)
    if errors:
        return json_resp(
            {"success": False, "message": "Validation failed.", "errors": errors},
            status=422,
        )

    # ── Save to database ──
    try:
        submission = ContactSubmission.objects.create(
            name=data["name"].strip(),
            email=data["email"].strip(),
            phone=data.get("phone", "").strip() or None,
            truck=data.get("truck", "").strip() or None,
            message=data["message"].strip(),
            sms_consent=True,
        )
    except Exception as exc:
        logger.exception("Database error saving contact submission: %s", exc)
        return json_resp(
            {"success": False, "message": "Something went wrong. Please try again later."},
            status=500,
        )

    # ── Send both emails in background (response returns immediately) ──
    threading.Thread(
        target=send_contact_emails,
        args=(submission,),
        daemon=True,
    ).start()

    # ── Success response ──
    data = {
        "id": str(submission.id),
        "createdAt": submission.created_at.isoformat().replace("+00:00", ".000Z"),
    }
    return json_resp(
        {
            "success": True,
            "message": "Your message has been received. Our dispatch team will contact you within 24 hours.",
            "data": data,
        },
        status=201,
    )


def _set_cors_headers(response):
    """Apply CORS headers from settings."""
    origin = getattr(settings, "FRONTEND_ORIGIN", "*")
    response["Access-Control-Allow-Origin"] = origin
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    return response