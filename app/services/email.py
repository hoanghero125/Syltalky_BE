import resend
from app.config import settings

resend.api_key = settings.RESEND_API_KEY


def _base_template(display_name: str, headline: str, body: str, cta_label: str, cta_url: str, footer_note: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="vi">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Syltalky</title></head>
<body style="margin:0;padding:0;background:#07090F;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#07090F;min-height:100vh;padding:48px 0;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0" style="max-width:520px;width:100%;">

        <!-- Logo -->
        <tr><td style="padding:0 0 36px 0;text-align:center;">
          <img src="https://syltalky.pro.vn/images/full_logo_transparent.png" alt="Syltalky" height="40" style="display:inline-block;height:40px;width:auto;"
            onerror="this.style.display='none';this.nextElementSibling.style.display='inline'"/>
          <span style="display:none;font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">Syl<span style="color:#00C9B8;">talky</span></span>
        </td></tr>

        <!-- Card -->
        <tr><td style="background:#0F1117;border-radius:16px;border:1px solid rgba(255,255,255,0.07);overflow:hidden;">

          <!-- Teal top bar -->
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="height:3px;background:linear-gradient(90deg,#00C9B8,#0099CC);"></td></tr>
          </table>

          <!-- Content -->
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:40px 44px 44px;">

              <!-- Greeting -->
              <p style="margin:0 0 8px;font-size:13px;font-weight:600;color:#00C9B8;letter-spacing:0.12em;text-transform:uppercase;">{headline}</p>
              <h1 style="margin:0 0 20px;font-size:26px;font-weight:800;color:#ffffff;letter-spacing:-0.8px;line-height:1.2;">Xin chào, {display_name}!</h1>

              <p style="margin:0 0 32px;font-size:15px;color:rgba(255,255,255,0.5);line-height:1.7;">{body}</p>

              <!-- CTA Button -->
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 36px;">
                <tr><td align="center">
                  <table cellpadding="0" cellspacing="0">
                    <tr><td style="border-radius:10px;background:linear-gradient(135deg,#00C9B8,#0099CC);box-shadow:0 8px 24px rgba(0,201,184,0.35);">
                      <a href="{cta_url}" style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;letter-spacing:0.01em;">{cta_label}</a>
                    </td></tr>
                  </table>
                </td></tr>
              </table>

              <!-- Divider -->
              <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
                <tr><td style="height:1px;background:rgba(255,255,255,0.06);"></td></tr>
              </table>

              <!-- Footer note -->
              <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.25);line-height:1.6;">{footer_note}</p>

            </td></tr>
          </table>
        </td></tr>

        <!-- Bottom -->
        <tr><td style="padding:28px 0 0;text-align:center;">
          <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.18);">© 2026 Syltalky · <a href="https://syltalky.pro.vn" style="color:rgba(255,255,255,0.25);text-decoration:none;">syltalky.pro.vn</a></p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_verification_email(to: str, display_name: str, token: str):
    link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    resend.Emails.send({
        "from": settings.RESEND_FROM,
        "to": to,
        "subject": "Xác nhận email — Syltalky",
        "html": _base_template(
            display_name=display_name,
            headline="Xác nhận tài khoản",
            body="Cảm ơn bạn đã đăng ký Syltalky! Nhấn vào nút bên dưới để xác nhận địa chỉ email và kích hoạt tài khoản của bạn.",
            cta_label="Xác nhận email",
            cta_url=link,
            footer_note="Link có hiệu lực trong 24 giờ. Nếu bạn không tạo tài khoản này, hãy bỏ qua email này.",
        ),
    })


def send_reset_password_email(to: str, display_name: str, token: str):
    link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    resend.Emails.send({
        "from": settings.RESEND_FROM,
        "to": to,
        "subject": "Đặt lại mật khẩu — Syltalky",
        "html": _base_template(
            display_name=display_name,
            headline="Bảo mật tài khoản",
            body="Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn. Nhấn vào nút bên dưới để tiếp tục.",
            cta_label="Đặt lại mật khẩu",
            cta_url=link,
            footer_note="Link có hiệu lực trong 24 giờ. Nếu bạn không yêu cầu điều này, hãy bỏ qua email này — tài khoản của bạn vẫn an toàn.",
        ),
    })
