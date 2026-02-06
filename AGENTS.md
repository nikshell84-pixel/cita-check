## Review guidelines
- Never hardcode secrets (Telegram token, API keys). Use env vars only.
- Validate schedule logic in Europe/Madrid timezone.
- Monitor only 08:00–18:00, every 30 minutes.
- Alert only when appointment date is strictly after 2026-03-04.
- Ensure retries/backoff and no duplicate alerts.
- Fail safely on site timeouts/captcha/HTML changes.
