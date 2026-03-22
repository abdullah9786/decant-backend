import httpx
import os
from app.config.config import settings
from datetime import datetime
from typing import List, Dict, Any

class MailService:
    def __init__(self):
        self.api_url = settings.ZEPTO_API_URL
        self.api_key = settings.ZEPTO_API_KEY
        self.from_email = settings.ZEPTO_FROM_EMAIL
        self.from_name = settings.ZEPTO_FROM_NAME

    async def _send_email(self, recipient_email: str, recipient_name: str, subject: str, html_body: str) -> bool:
        if not self.api_key or not self.from_email:
            print("[MAIL] API key or From Email missing. Skipping.")
            return False

        payload = {
            "from": {"address": self.from_email, "name": self.from_name},
            "to": [{"email_address": {"address": recipient_email, "name": recipient_name or recipient_email}}],
            "subject": subject,
            "htmlbody": html_body,
        }
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Zoho-enczapikey {self.api_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.api_url, json=payload, headers=headers)
                if resp.status_code >= 300:
                    req_id = resp.headers.get("x-request-id") or resp.headers.get("x-zoho-request-id") or ""
                    print(f"[MAIL] Send failed: {resp.status_code} {resp.text} {req_id}")
                    return False
                return True
        except httpx.RequestError as exc:
            print(f"[MAIL] Request error: {exc}")
            return False

    async def send_verification_email(self, email: str, full_name: str, token: str):
        verify_link = f"{settings.APP_BASE_URL}/verify-email?token={token}"
        subject = "Verify your email for DECUME"
        name = full_name or "there"
        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Welcome to DECUME</h2>
            <p>Hi {name},</p>
            <p>Experience the truth of fragrance. Please verify your email to activate your account and start your olfactory journey.</p>
            <div style="margin: 30px 0;">
                <a href="{verify_link}" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">Verify Email</a>
            </div>
            <p style="font-size: 12px; color: #6b7280;">If you did not create this account, you can safely ignore this email.</p>
        </div>
        """
        return await self._send_email(email, full_name, subject, html_body)

    async def send_reset_email(self, email: str, full_name: str, token: str):
        reset_link = f"{settings.APP_BASE_URL}/reset-password?token={token}"
        subject = "Reset your DECUME password"
        name = full_name or "there"
        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Password Reset</h2>
            <p>Hi {name},</p>
            <p>We received a request to reset your password. If this was you, please click the button below:</p>
            <div style="margin: 30px 0;">
                <a href="{reset_link}" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">Reset Password</a>
            </div>
            <p style="font-size: 12px; color: #6b7280;">If you did not request this, your password will remain unchanged.</p>
        </div>
        """
        return await self._send_email(email, full_name, subject, html_body)

    async def send_order_confirmation(self, email: str, customer_name: str, order: Dict[str, Any]):
        subject = f"Order Confirmed: #{str(order.get('_id', ''))[:8].upper()}"
        items_html = ""
        for item in order.get('items', []):
            items_html += f"<tr><td>{item['name']} ({item['size_ml']}ml)</td><td style='text-align: right;'>x{item['quantity']}</td><td style='text-align: right;'>₹{item['price'] * item['quantity']}</td></tr>"

        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Order Confirmed</h2>
            <p>Hi {customer_name},</p>
            <p>Thank you for your order. We've received your payment and are preparing your decants.</p>
            
            <table style="width: 100%; margin-top: 20px; border-collapse: collapse;">
                <thead style="background-color: #f9fafb; font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em;">
                    <tr><th style="text-align: left; padding: 10px;">Item</th><th style="padding: 10px; text-align: right;">Qty</th><th style="padding: 10px; text-align: right;">Total</th></tr>
                </thead>
                <tbody style="font-size: 14px;">
                    {items_html}
                </tbody>
                <tfoot>
                    <tr><td colspan="2" style="padding: 20px 10px 10px; font-weight: bold; text-align: right;">Grand Total:</td><td style="padding: 20px 10px 10px; font-weight: bold; text-align: right; color: #059669;">₹{order['total_amount']}</td></tr>
                </tfoot>
            </table>

            <div style="margin: 30px 0; padding: 20px; background: #f9fafb; border-radius: 5px;">
                <p style="margin: 0; font-size: 12px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em;">Shipping To:</p>
                <p style="margin: 5px 0 0; font-size: 14px; color: #374151;">{order['shipping_address']}</p>
            </div>

            <p style="font-size: 12px; color: #6b7280;">You can track your order status on our website at any time.</p>
        </div>
        """
        return await self._send_email(email, customer_name, subject, html_body)

    async def send_delivery_notification(self, email: str, customer_name: str, order_id: str):
        subject = "Fragrance Delivered: Enjoy your Scent Ritual"
        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Delivered</h2>
            <p>Hi {customer_name},</p>
            <p>Your order #{str(order_id)[:8].upper()} has been successfully delivered. We hope these fragrances bring a touch of luxury to your day.</p>
            <p>If you have any issues with your delivery, please contact our support team immediately.</p>
            <div style="margin: 30px 0;">
                <a href="{settings.APP_BASE_URL}/profile" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">View My Orders</a>
            </div>
        </div>
        """
        return await self._send_email(email, customer_name, subject, html_body)

    async def send_admin_new_order_alert(self, order: Dict[str, Any]):
        # This one is for the admin email
        admin_email = os.getenv("ADMIN_EMAIL", "abdullahansari9768@gmail.com")
        print("admin_email", admin_email, self.from_email)
        subject = f"NEW ORDER ALERT: ₹{order['total_amount']} (Order #{str(order.get('_id', ''))[:8].upper()})"
        html_body = f"""
        <div style="font-family: sans-serif; color: #1e293b; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0;">
            <h2 style="color: #020617;">New Order Received</h2>
            <p><strong>Customer:</strong> {order.get('customer_name')}</p>
            <p><strong>Amount:</strong> ₹{order['total_amount']}</p>
            <p><strong>Order ID:</strong> {str(order.get('_id', ''))}</p>
            <div style="margin: 20px 0;">
                <a href="{settings.APP_BASE_URL.replace('3000', '3001')}/orders" style="background-color: #4f46e5; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Manage in Admin Panel</a>
            </div>
        </div>
        """
        return await self._send_email(admin_email, "Admin", subject, html_body)
