import resend
from app.config import settings

resend.api_key = settings.RESEND_API_KEY


def send_verification_email(to: str, display_name: str, token: str):
    link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    resend.Emails.send({
        "from": settings.RESEND_FROM,
        "to": to,
        "subject": "Xác nhận email Syltalky của bạn",
        "html": f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto">
            <h2>Xin chào {display_name}!</h2>
            <p>Nhấn vào nút bên dưới để xác nhận địa chỉ email của bạn.</p>
            <a href="{link}" style="display:inline-block;padding:12px 24px;background:#00C9B8;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold">
                Xác nhận email
            </a>
            <p style="color:#888;font-size:12px;margin-top:24px">Link có hiệu lực trong 24 giờ.</p>
        </div>
        """,
    })


def send_reset_password_email(to: str, display_name: str, token: str):
    link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    resend.Emails.send({
        "from": settings.RESEND_FROM,
        "to": to,
        "subject": "Đặt lại mật khẩu Syltalky",
        "html": f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto">
            <h2>Xin chào {display_name}!</h2>
            <p>Bạn yêu cầu đặt lại mật khẩu. Nhấn vào nút bên dưới để tiếp tục.</p>
            <a href="{link}" style="display:inline-block;padding:12px 24px;background:#00C9B8;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold">
                Đặt lại mật khẩu
            </a>
            <p style="color:#888;font-size:12px;margin-top:24px">Link có hiệu lực trong 24 giờ. Nếu bạn không yêu cầu, hãy bỏ qua email này.</p>
        </div>
        """,
    })
