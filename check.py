import os
import re
from datetime import datetime
from time import sleep
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

TZ = ZoneInfo("Europe/Madrid")
START_URL = "https://icp.administracionelectronica.gob.es/icpplus/index.html"


def in_work_hours(now: datetime) -> bool:
    # 08:00..18:00, последний запуск в 18:00 (строго по времени)
    if now.hour < 8 or now.hour > 18:
        return False
    if now.hour == 18 and now.minute > 0:
        return False
    return True


def tg_send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}

    for attempt in range(3):
        try:
            r = requests.post(url, data=payload, timeout=20)
            r.raise_for_status()
            return
        except Exception:
            if attempt == 2:
                return
            sleep(2 * (attempt + 1))


def parse_ddmmyyyy(s: str):
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()


def click_aceptar(page) -> None:
    page.get_by_role("button", name=re.compile(r"\bAceptar\b", re.I)).first.click()


def select_option_match(select_locator, text: str, exact: bool) -> None:
    rx = f"^{re.escape(text)}$" if exact else re.escape(text)
    options = select_locator.locator("option").filter(has_text=re.compile(rx, re.I))
    count = options.count()
    if count == 0:
        raise RuntimeError(f"Option not found: {text}")
    if exact and count > 1:
        raise RuntimeError(f"Ambiguous exact match: {text}")
    value = options.first.get_attribute("value")
    if not value:
        raise RuntimeError(f"Option has no value: {text}")
    select_locator.select_option(value=value)


def block_heavy_resources(context) -> None:
    # меньше трафика/шансов флаппинга: режем только тяжёлое (CSS оставляем)
    def handler(route, request):
        if request.resource_type in ("image", "font"):
            return route.abort()
        return route.continue_()
    context.route("**/*", handler)


def wait_visible_selects_with_options(page, n: int, timeout_ms: int = 20000) -> None:
    # ждём, пока появятся n видимых select'ов и у каждого будет >1 option (не только заглушка)
    page.wait_for_function(
        f"""() => {{
          const vis = Array.from(document.querySelectorAll('select'))
            .filter(s => s && s.offsetParent !== null);
          if (vis.length < {n}) return false;
          return vis.slice(0,{n}).every(s => (s.options?.length || 0) > 1);
        }}""",
        timeout=timeout_ms,
    )


def main():
    now = datetime.now(TZ)
    if not in_work_hours(now):
        return

    debug = os.getenv("DEBUG_ARTIFACTS") == "1"
    step = "start"

    provincia_label = os.environ["PROVINCIA_LABEL"]
    oficina_match = os.environ["OFICINA_MATCH"]
    tramite_match = os.environ["TRAMITE_MATCH"]
    min_date = parse_ddmmyyyy(os.getenv("MIN_DATE", "04/03/2026"))

    nie = os.environ["NIE"]
    full_name = os.environ["FULL_NAME"]
    phone = os.environ["PHONE"]
    email = os.environ["EMAIL"]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage"],
        )
        context = browser.new_context(locale="es-ES", timezone_id="Europe/Madrid")
        block_heavy_resources(context)

        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(45000)

        try:
            step = "goto"
            page.goto(START_URL, wait_until="domcontentloaded", timeout=45000)

            step = "loaded"
            if debug:
                page.screenshot(path="step_loaded.png", full_page=True)

            # Шаг 1: Provincia
            step = "provincia_wait"
            wait_visible_selects_with_options(page, n=1)
            prov_select = page.locator("select:visible").first
            prov_select.select_option(label=provincia_label)

            step = "provincia_aceptar"
            click_aceptar(page)

            # Шаг 2: Oficina + Trámite
            step = "office_tramite_wait"
            wait_visible_selects_with_options(page, n=2)

            step = "office_tramite"
            selects = page.locator("select:visible")
            office_select = selects.nth(0)
            tramite_select = selects.nth(1)

            select_option_match(office_select, oficina_match, exact=True)
            select_option_match(tramite_select, tramite_match, exact=False)

            step = "office_tramite_aceptar"
            click_aceptar(page)

            # Шаг 3: NIE + Nombre
            step = "personal_wait"
            page.get_by_label(re.compile(r"N\.I\.E\.", re.I)).wait_for()

            step = "personal_before_fill"
            if debug:
                page.screenshot(path="before_personal.png", full_page=True)

            step = "personal_fill"
            page.get_by_label(re.compile(r"N\.I\.E\.", re.I)).fill(nie)
            page.get_by_label(re.compile(r"Nombre y apellidos", re.I)).fill(full_name)
            click_aceptar(page)

            # Шаг 4: меню
            step = "menu_wait"
            page.get_by_role("button", name=re.compile("Solicitar Cita", re.I)).wait_for()

            step = "menu_click"
            page.get_by_role("button", name=re.compile("Solicitar Cita", re.I)).click()

            # Шаг 5: Tel/Email
            step = "contact_wait"
            page.get_by_label(re.compile(r"Tel[eé]fono", re.I)).wait_for()

            step = "contact_fill"
            page.get_by_label(re.compile(r"Tel[eé]fono", re.I)).fill(phone)
            page.get_by_label(re.compile(r"Correo electr[oó]nico$", re.I)).fill(email)
            page.get_by_label(re.compile(r"Repite Correo electr[oó]nico", re.I)).fill(email)
            click_aceptar(page)

            # Финал
            step = "final_read"
            body = page.inner_text("body")

            slots = re.findall(
                r"D[ií]a:\s*([0-9]{2}/[0-9]{2}/[0-9]{4}).*?Hora:\s*([0-9]{2}:[0-9]{2})",
                body,
                re.S,
            )

            if slots:
                good = [(d, h) for (d, h) in slots if parse_ddmmyyyy(d) > min_date]
                if good:
                    lines = [f"{d} {h}" for d, h in good[:10]]
                    tg_send("Сита есть (после 04/03/2026):\n" + "\n".join(lines))
                return

            if re.search(r"No hay citas", body, re.I):
                return

            if re.search(r"captcha", body, re.I):
                tg_send("🧩 CAPTCHA/проверка: дошёл, но список CITA не прочитал. Проверь вручную.")
                return

            tg_send("⚠️ Статус не распознан (нет slots и нет 'No hay citas'). Проверь вручную.")

        except PWTimeout:
            tg_send(f"⏱️ Таймаут на шаге: {step}. Проверь вручную.")
        except Exception as e:
            tg_send(f"⚠️ Ошибка на шаге: {step} ({type(e).__name__}). Проверь вручную.")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
