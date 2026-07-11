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

    def _frontend_url(self, path: str) -> str:
        base = settings.APP_BASE_URL.rstrip("/")
        return f"{base}{path}"

    async def send_verification_email(self, email: str, full_name: str, token: str):
        verify_link = self._frontend_url(f"/verify-email?token={token}")
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
        reset_link = self._frontend_url(f"/reset-password?token={token}")
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
        full_id = str(order.get('_id', ''))
        track_url = f"{settings.APP_BASE_URL}/track-order?orderId={full_id}"
        items_html = ""
        for item in order.get('items', []):
            items_html += f"<tr><td>{item['name']} ({item['size_ml']}ml)</td><td style='text-align: right;'>x{item['quantity']}</td><td style='text-align: right;'>₹{item['price'] * item['quantity']}</td></tr>"

        is_cod = order.get("payment_method") == "cod"
        cod_fee = float(order.get("cod_fee") or 0)
        total_amount = order.get("total_amount", 0)

        mystery_gift = order.get("mystery_gift") or {}
        mystery_block = ""
        if mystery_gift.get("name"):
            accent = mystery_gift.get("accent_color") or "#7c3aed"
            tagline = mystery_gift.get("tagline") or "A free surprise ships with your order."
            mystery_block = f"""
            <div style="margin: 20px 0; padding: 16px; background: {accent}14; border: 1px solid {accent}55; border-radius: 8px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: {accent};">Mystery Gift Unlocked</p>
                <p style="margin: 6px 0 2px; font-size: 16px; font-weight: bold; color: #022c22;">&#127873; {mystery_gift['name']}</p>
                <p style="margin: 0; font-size: 13px; color: #374151;">{tagline}</p>
            </div>
            """

        if is_cod:
            subject = "Order Confirmed (Cash on Delivery) — DECUME"
            intro = (
                "Thank you for your order. We've reserved your decants and our "
                "courier partner will collect the amount below at delivery."
            )
            payment_block = f"""
            <div style="margin: 20px 0; padding: 15px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 5px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: #92400e;">Payment Method</p>
                <p style="margin: 4px 0 8px; font-size: 14px; font-weight: bold; color: #78350f;">Cash on Delivery</p>
                <p style="margin: 0; font-size: 12px; color: #92400e;">Please keep <strong>₹{total_amount}</strong> ready at delivery. A ₹{int(cod_fee)} handling fee is included in this amount.</p>
            </div>
            """
            fee_row = (
                f"<tr><td colspan=\"2\" style=\"padding: 4px 10px; text-align: right; font-size: 12px; color: #6b7280;\">COD handling fee</td>"
                f"<td style=\"padding: 4px 10px; text-align: right; font-size: 12px; color: #6b7280;\">₹{int(cod_fee)}</td></tr>"
                if cod_fee > 0 else ""
            )
            grand_total_label = "Collect on Delivery"
        else:
            subject = "Order Confirmed — DECUME"
            intro = "Thank you for your order. We've received your payment and are preparing your decants."
            payment_block = ""
            fee_row = ""
            grand_total_label = "Grand Total"

        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Order Confirmed</h2>
            <p>Hi {customer_name},</p>
            <p>{intro}</p>

            <div style="margin: 20px 0; padding: 15px; background: #f9fafb; border-radius: 5px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: #6b7280;">Order ID</p>
                <p style="margin: 4px 0 0; font-size: 14px; font-weight: bold; color: #022c22; word-break: break-all;">{full_id}</p>
            </div>

            {payment_block}

            <table style="width: 100%; margin-top: 20px; border-collapse: collapse;">
                <thead style="background-color: #f9fafb; font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em;">
                    <tr><th style="text-align: left; padding: 10px;">Item</th><th style="padding: 10px; text-align: right;">Qty</th><th style="padding: 10px; text-align: right;">Total</th></tr>
                </thead>
                <tbody style="font-size: 14px;">
                    {items_html}
                </tbody>
                <tfoot>
                    {fee_row}
                    <tr><td colspan="2" style="padding: 20px 10px 10px; font-weight: bold; text-align: right;">{grand_total_label}:</td><td style="padding: 20px 10px 10px; font-weight: bold; text-align: right; color: #059669;">₹{total_amount}</td></tr>
                </tfoot>
            </table>

            {mystery_block}

            <div style="margin: 20px 0; padding: 15px; background: #f9fafb; border-radius: 5px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: #6b7280;">Shipping To</p>
                <p style="margin: 4px 0 0; font-size: 14px; color: #374151;">{order['shipping_address']}</p>
            </div>

            <div style="margin: 30px 0;">
                <a href="{track_url}" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">Track Order</a>
            </div>
        </div>
        """
        return await self._send_email(email, customer_name, subject, html_body)

    async def send_shipped_notification(
        self,
        email: str,
        customer_name: str,
        order_id: str,
        order: Dict[str, Any],
    ):
        full_id = str(order_id)
        decume_track = f"{settings.APP_BASE_URL}/track-order?orderId={full_id}"
        tracking_id = (order.get("tracking_id") or "").strip()
        tracking_url = (order.get("tracking_url") or "").strip()
        courier_name = (order.get("courier_name") or "").strip()

        tracking_lines = ""
        if courier_name:
            tracking_lines += f'<p style="margin: 0 0 8px; font-size: 14px; color: #374151;"><strong>Courier:</strong> {courier_name}</p>'
        if tracking_id:
            tracking_lines += f'<p style="margin: 0 0 8px; font-size: 14px; color: #374151;"><strong>Tracking ID:</strong> {tracking_id}</p>'

        cta_href = tracking_url or decume_track
        cta_label = "Track on courier site" if tracking_url else "Track order"

        subject = "Your DECUME order has shipped"
        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Order Shipped</h2>
            <p>Hi {customer_name},</p>
            <p>Good news — your order is on its way. Use the details below to follow your parcel.</p>
            <div style="margin: 20px 0; padding: 15px; background: #f9fafb; border-radius: 5px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: #6b7280;">Order ID</p>
                <p style="margin: 4px 0 12px; font-size: 14px; font-weight: bold; color: #022c22; word-break: break-all;">{full_id}</p>
                {tracking_lines}
            </div>
            <div style="margin: 30px 0;">
                <a href="{cta_href}" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">{cta_label}</a>
            </div>
            <p style="font-size: 12px; color: #6b7280;">You can also track anytime at <a href="{decume_track}" style="color: #059669;">decume.in/track-order</a>.</p>
        </div>
        """
        return await self._send_email(email, customer_name, subject, html_body)

    async def send_delivery_notification(self, email: str, customer_name: str, order_id: str):
        subject = "Fragrance Delivered: Enjoy your Scent Ritual"
        full_id = str(order_id)
        track_url = f"{settings.APP_BASE_URL}/track-order?orderId={full_id}"
        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Delivered</h2>
            <p>Hi {customer_name},</p>
            <p>Your order has been successfully delivered. We hope these fragrances bring a touch of luxury to your day.</p>
            <div style="margin: 15px 0; padding: 15px; background: #f9fafb; border-radius: 5px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: #6b7280;">Order ID</p>
                <p style="margin: 4px 0 0; font-size: 14px; font-weight: bold; color: #022c22; word-break: break-all;">{full_id}</p>
            </div>
            <p>If you have any issues with your delivery, please contact our support team immediately.</p>
            <div style="margin: 30px 0;">
                <a href="{track_url}" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">Track Order</a>
            </div>
        </div>
        """
        return await self._send_email(email, customer_name, subject, html_body)

    async def send_order_cancellation(self, email: str, customer_name: str, order: Dict[str, Any]):
        full_id = str(order.get("_id", ""))
        subject = f"Order Cancelled — DECUME"
        name = customer_name or "there"
        # COD orders cancelled before delivery never collected money, so the
        # "refund initiated" line would be misleading.
        is_cod_unpaid = (
            order.get("payment_method") == "cod"
            and order.get("payment_status") != "paid"
        )
        refund_line = (
            ""
            if is_cod_unpaid
            else "<p>If your payment was already captured, a full refund has been initiated and should reflect in your account within 5–7 business days.</p>"
        )
        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #dc2626; padding-bottom: 10px;">Order Cancelled</h2>
            <p>Hi {name},</p>
            <div style="margin: 15px 0; padding: 15px; background: #fef2f2; border-radius: 5px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: #6b7280;">Order ID</p>
                <p style="margin: 4px 0 0; font-size: 14px; font-weight: bold; color: #022c22; word-break: break-all;">{full_id}</p>
            </div>
            <p>Your order for <strong>₹{order.get('total_amount', 0)}</strong> has been cancelled as requested.</p>
            {refund_line}
            <p style="font-size: 12px; color: #6b7280; margin-top: 30px;">If you did not request this cancellation, please contact our support team immediately.</p>
        </div>
        """
        return await self._send_email(email, name, subject, html_body)

    async def send_admin_new_order_alert(self, order: Dict[str, Any]):
        # This one is for the admin email
        admin_email = os.getenv("ADMIN_EMAIL", "abdullahansari9768@gmail.com")
        print("admin_email", admin_email, self.from_email)
        is_cod = order.get("payment_method") == "cod"
        subject = (
            f"NEW COD ORDER: ₹{order['total_amount']} — collect on delivery"
            if is_cod
            else f"NEW ORDER ALERT: ₹{order['total_amount']}"
        )
        method_badge = (
            "<span style=\"display: inline-block; background: #fde68a; color: #78350f; padding: 4px 10px; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 3px;\">Cash on Delivery</span>"
            if is_cod
            else "<span style=\"display: inline-block; background: #dbeafe; color: #1e3a8a; padding: 4px 10px; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 3px;\">Prepaid</span>"
        )
        mystery_gift = order.get("mystery_gift") or {}
        mystery_line = (
            f"<p><strong>Mystery Gift:</strong> &#127873; {mystery_gift['name']} "
            f"(pack offline)</p>"
            if mystery_gift.get("name")
            else ""
        )
        html_body = f"""
        <div style="font-family: sans-serif; color: #1e293b; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0;">
            <h2 style="color: #020617;">New Order Received</h2>
            <p>{method_badge}</p>
            <p><strong>Customer:</strong> {order.get('customer_name')}</p>
            <p><strong>Amount:</strong> ₹{order['total_amount']}{' (collect at delivery)' if is_cod else ''}</p>
            <p><strong>Order ID:</strong> {str(order.get('_id', ''))}</p>
            {mystery_line}
            <div style="margin: 20px 0;">
                <a href="{settings.APP_BASE_URL.replace('3000', '3001')}/orders" style="background-color: #4f46e5; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Manage in Admin Panel</a>
            </div>
        </div>
        """
        return await self._send_email(admin_email, "Admin", subject, html_body)

    async def send_instagram_promo_invite(
        self,
        email: str,
        customer_name: str,
        order_id: str,
        submission: Dict[str, Any],
        config: Dict[str, Any],
        display: Dict[str, Any],
    ):
        if not email:
            return False
        name = customer_name or "there"
        promo_url = self._frontend_url(f"/instagram-promo?orderId={order_id}")
        deadline = submission.get("deadline_at")
        deadline_str = ""
        if deadline:
            if hasattr(deadline, "strftime"):
                deadline_str = deadline.strftime("%d %b %Y")
            else:
                deadline_str = str(deadline)

        mention = config.get("required_mention") or "@decume.in"
        hashtags = ", ".join(config.get("required_hashtags") or ["#decume"])
        min_followers = config.get("min_followers", 100)

        subject = "Enter our Instagram promo — chance to win a free decant"
        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Instagram Promo</h2>
            <p>Hi {name},</p>
            <p>Your order has been delivered. You opted in to our Instagram promo — submit a short video for a <strong>chance to win</strong> a free decant. Winners are selected by our team; submitting does not guarantee a prize.</p>
            <div style="margin: 15px 0; padding: 15px; background: #f9fafb; border-radius: 5px;">
                <p style="margin: 0; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; color: #6b7280;">Rules</p>
                <ul style="margin: 8px 0 0; padding-left: 18px; font-size: 13px; color: #374151;">
                    <li>Post from any public Instagram account (yours or a friend's)</li>
                    <li>Account must be public with at least {min_followers} followers</li>
                    <li>Tag {mention} and use {hashtags}</li>
                    <li>Submit by: <strong>{deadline_str or 'see promo page'}</strong></li>
                </ul>
            </div>
            <p style="font-size: 13px; color: #374151;">If you win, the free decant ships to your order address — not to the Instagram poster.</p>
            <div style="margin: 30px 0;">
                <a href="{promo_url}" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">Submit Promo Video</a>
            </div>
        </div>
        """
        return await self._send_email(email, name, subject, html_body)

    async def send_instagram_promo_status_update(
        self,
        email: str,
        customer_name: str,
        submission: Dict[str, Any],
        event: str,
    ):
        if not email:
            return False
        name = customer_name or "there"
        order_id = submission.get("order_id", "")
        promo_url = self._frontend_url(f"/instagram-promo?orderId={order_id}")
        prize_label = (submission.get("prize_snapshot") or {}).get("label") or "Free decant"
        reason = submission.get("rejection_reason") or ""

        if event == "approved":
            subject = "Your video is approved — you've won a gift!"
            body = f"""
            <p>Great news! Your Instagram promo video has been <strong>approved</strong>.</p>
            <p>You have won: <strong>{prize_label}</strong></p>
            <p>We will ship your gift to the address on your original order.</p>
            """
        elif event == "rejected":
            subject = "Instagram promo — not selected"
            body = f"""
            <p>Thank you for participating in our Instagram promo.</p>
            <p>Your entry was <strong>not selected</strong> this time.</p>
            {f'<p style="font-size: 13px; color: #6b7280;">Note: {reason}</p>' if reason else ''}
            """
        elif event == "fulfilled":
            subject = "Your Instagram promo prize has been shipped"
            body = f"""
            <p>Your promo prize (<strong>{prize_label}</strong>) has been shipped.</p>
            <p>We hope you enjoy your complimentary decant!</p>
            """
        else:
            return False

        html_body = f"""
        <div style="font-family: serif; color: #022c22; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #f0fdf4;">
            <h2 style="text-transform: uppercase; letter-spacing: 0.2em; border-bottom: 2px solid #059669; padding-bottom: 10px;">Instagram Promo Update</h2>
            <p>Hi {name},</p>
            {body}
            <div style="margin: 30px 0;">
                <a href="{promo_url}" style="background-color: #022c22; color: white; padding: 12px 25px; text-decoration: none; font-weight: bold; text-transform: uppercase; letter-spacing: 0.1em; border-radius: 5px;">View Promo Status</a>
            </div>
        </div>
        """
        return await self._send_email(email, name, subject, html_body)
