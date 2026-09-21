"""Trial onboarding sequence: who is due which email, and what it says.

Pure logic only -- same rule as logic/billing.py: no fastapi, no smtplib,
no db writes in the decision path. ``due_email_key()`` takes an account
row, the account's growth events and "now", and answers with one email key
or None; scripts/run_trial_emails.py is the thin runner that reads those
inputs, re-checks them immediately before sending and writes the log.

Text and conditions come from docs/content/02-trial-email-sequence.md
(Codex' adaptation of 19.09.2026). Facts that document marks as still
unconfirmed are TODO constants below, NOT invented text: render_email()
leaves the doc's own %ПЛЕЙСХОЛДЕР% in place when a constant is empty, and
unfilled_placeholders() is what stops the runner from mailing a half-filled
message (rule 8 of the doc).

The sequence is marketing mail, not transactional: opting out here only
writes trial_email_optouts and is only ever consulted by this module.
Password resets, verification links and payment receipts go through
mailer.py directly and never look at this table.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import config

# Sequence steps in the order the doc lists them. "2" and "2b" are one
# mutually exclusive step (doc rule 7): whichever is sent blocks the other.
EMAIL_KEYS = ("1", "2", "2b", "3", "4", "5", "6", "7")

# --------------------------------------------------------------------------
# Facts from "Что нужно уточнить перед отправкой" in the doc. Filling one in
# is a decision for the owner, not for this module; while a TODO_ constant is
# empty the corresponding email refuses to send (see the placeholder check in
# scripts/run_trial_emails.py).
# --------------------------------------------------------------------------

# Owner's decision of 20.09.2026 for %УСЛОВИЯ_ХРАНЕНИЯ% (email 5) and
# %ПОДТВЕРЖДЁННЫЕ_УСЛОВИЯ_ВОЗВРАТА_И_ХРАНЕНИЯ% (email 7): a floor, never a
# promise of deletion. Nothing here deletes tenant data on a schedule --
# remove_tenant(purge_volume=True) is only ever run by hand -- so "не менее
# N дней" stays true whatever happens, while "удалим через N дней" would not.
# If automatic purging is ever introduced, this constant and the /privacy
# page must be revisited together (see DEPLOY.md, the trial-emails cron).
DATA_RETENTION_MIN_DAYS = 90

DATA_RETENTION = (
    f"после окончания пробы кабинет останавливается, а данные аккаунта "
    f"сохраняются не менее {DATA_RETENTION_MIN_DAYS} дней; если нужно удалить их "
    f"раньше, напишите в поддержку"
)

RETURN_TERMS = (
    f"Данные аккаунта сохраняются не менее {DATA_RETENTION_MIN_DAYS} дней после "
    f"окончания пробы, поэтому работу можно продолжить с того же места; если нужно "
    f"удалить их раньше, напишите в поддержку"
)

# The doc asks to verify screen names and deep links before using them
# ("Интерфейс и ссылки"). Until that is done every CTA points at the
# cabinet root / the billing page, both of which exist for certain
# (config.tenant_url, config.billing_url), and the button text describes
# the step rather than naming a screen. Set this to the confirmed report /
# batches / stock-reconciliation paths when they have been checked.
TODO_SCREEN_DEEP_LINKS = ""

# %КАНАЛ_ПОДДЕРЖКИ%: the address already published on the public /contacts
# page (gateway/templates/contacts.html), so this is a checked fact, not an
# invented one. The doc's unverified bit was the "читаю лично" claim -- it
# is deliberately absent from the texts below.
SUPPORT_CHANNEL = "mvvorokov@yandex.ru"

_PLACEHOLDER_RE = re.compile(r"%[A-ZА-ЯЁ_]{3,}%")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    """ISO string (how accounts/growth_events store timestamps) -> aware dt."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Unsubscribe token
# --------------------------------------------------------------------------

def _secret() -> bytes:
    # Same fallback order growth_web.py uses; no new configuration value.
    value = config.GROWTH_COOKIE_SECRET or config.INTERNAL_API_SECRET
    return value.encode("utf-8") if value else b""


def unsubscribe_token(account_id: int) -> str:
    """Stable, non-expiring "<id>.<mac>" -- an unsubscribe link printed in a
    year-old email must still work, so unlike growth_web's consent cookies
    this carries no ttl."""
    key = _secret()
    if not key:
        return ""
    mac = hmac.new(key, f"trial-emails:unsubscribe:v1:{int(account_id)}".encode(), hashlib.sha256).hexdigest()
    return f"{int(account_id)}.{mac}"


def account_id_from_token(token: str) -> int | None:
    if not token or len(token) > 256 or "." not in token:
        return None
    raw, _, _ = token.partition(".")
    if not raw.isdigit():
        return None
    expected = unsubscribe_token(int(raw))
    if not expected or not hmac.compare_digest(expected, token):
        return None
    return int(raw)


def unsubscribe_url(account_id: int) -> str:
    return f"{config.base_url()}/unsubscribe?token={unsubscribe_token(account_id)}"


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def due_email_key(
    account: Mapping[str, Any],
    events: Mapping[str, datetime | str],
    now: datetime | None = None,
    *,
    sent: Mapping[str, datetime | str] | None = None,
    opted_out: bool = False,
    tenant_status: str = "",
    grace_days: int = config.GRACE_PERIOD_DAYS,
) -> str | None:
    """Which sequence email this account is due right now, or None.

    ``events`` maps growth event names to their occurred_at, ``sent`` maps
    already-sent email keys to their sent_at. Both are plain mappings so the
    caller decides how to read them and this stays testable without a db.

    Every exit condition of the doc is re-evaluated here on every call --
    the runner calls this again immediately before each send, so a payment
    that landed between the scan and the send still stops the mail.
    """
    now = now or _now()
    sent = sent or {}

    # --- unconditional exits, checked before every send (doc rules 1,2,4) ---
    if opted_out:
        return None
    if not _parse(account["email_verified_at"]):
        return None
    # Marketing consent is a separate, optional registration choice. Consent
    # to personal-data processing must never be treated as permission for
    # promotional/onboarding mail. Missing values are always no consent.
    if not _parse(account["marketing_consent_at"]):
        return None
    status = account["status"] or ""
    # A paid customer never re-enters the sequence, even if a later charge
    # fails and drops them back to past_due/canceled.
    if status == "active" or "payment_succeeded" in events:
        return None
    trial_started = _parse(events.get("trial_started"))
    if trial_started is None:
        return None
    # One sequence email per account per day, no exceptions (doc rule 3).
    last_sent = max((d for d in (_parse(v) for v in sent.values()) if d), default=None)
    if last_sent is not None and now - last_sent < timedelta(days=1):
        return None

    # --- email 7: the one win-back contact, after a *confirmed* stop ---
    if status == "canceled":
        past_due_since = _parse(account["past_due_since"])
        if (
            "7" not in sent
            and past_due_since is not None
            and now >= past_due_since + timedelta(days=grace_days)
            # "canceled" alone is not enough: stop_tenant() may have failed.
            and tenant_status == "stopped"
        ):
            return "7"
        return None

    if status != "trialing":
        # past_due is a gap in the sequence by design: emails 5/6 are for a
        # running trial, email 7 waits for the confirmed stop.
        return None

    period_end = _parse(account["current_period_end"])
    remaining = period_end - now if period_end else None

    # --- the two deadline reminders outrank the onboarding steps ---
    # Both are strictly "in this window or never": a late cron run must not
    # deliver "осталось 3 дня" on the final day (doc, emails 5 and 6).
    if remaining is not None and "6" not in sent and timedelta(0) < remaining <= timedelta(days=1):
        return "6"
    if remaining is not None and "5" not in sent and timedelta(days=2) < remaining <= timedelta(days=3):
        return "5"

    age = now - trial_started
    has_report = "first_useful_report" in events
    if "1" not in sent:
        return "1"
    if age >= timedelta(days=2) and "2" not in sent and "2b" not in sent:
        return "2b" if has_report else "2"
    if age >= timedelta(days=5) and "3" not in sent:
        return "3"
    # Email 4 waits for the confirmed report however long that takes -- it
    # is a "next task after your first report" mail and makes no sense
    # otherwise (doc, email 4).
    if age >= timedelta(days=9) and has_report and "4" not in sent:
        return "4"
    return None


def passed_email_keys(
    account: Mapping[str, Any],
    events: Mapping[str, datetime | str],
    now: datetime | None = None,
    *,
    sent: Mapping[str, datetime | str] | None = None,
) -> dict[str, datetime]:
    """Steps this account has already outgrown -> the moment they closed.

    Used only by ``run_trial_emails.py --backfill``: marking these as skipped
    lets an account that was mid-trial when the sequence was switched on
    enter at the step matching its real day instead of restarting at email 1,
    and stops a reminder about a date that has already passed.

    A step is behind once the *next* step is due -- an account on day 9 has
    outgrown 1, 2/2b and 3 and is exactly where email 4 belongs. Emails 4 and
    5 close together, when the trial is inside email 5's window; email 6
    closes with the trial. Email 7 is never listed: it is a win-back that is
    still true after the trial ended, and its own conditions gate it.
    """
    now = now or _now()
    sent = sent or {}
    trial_started = _parse(events.get("trial_started"))
    if trial_started is None:
        return {}
    # Emails 2 and 2b are one step (doc rule 7), so both keys are marked:
    # whichever branch would have fired, the step is behind this account.
    # (key, when it was due, when it stopped being due). The row is stamped
    # with the due moment, not the close moment: a skipped step is logged as
    # of the day it would have gone out, so the one-email-per-day guard in
    # due_email_key does not also hold back the first real message.
    steps: list[tuple[str, datetime, datetime]] = [
        ("1", trial_started, trial_started + timedelta(days=2)),
        ("2", trial_started + timedelta(days=2), trial_started + timedelta(days=5)),
        ("2b", trial_started + timedelta(days=2), trial_started + timedelta(days=5)),
        ("3", trial_started + timedelta(days=5), trial_started + timedelta(days=9)),
    ]
    period_end = _parse(account["current_period_end"])
    if period_end is not None:
        # The reminder windows of due_email_key: email 5 runs from 3 to 2
        # days before the end, email 6 in the last 24 hours. Email 4 is an
        # onboarding task, not a goodbye -- it closes when 5 takes over.
        steps.append(("4", trial_started + timedelta(days=9), period_end - timedelta(days=2)))
        steps.append(("5", period_end - timedelta(days=3), period_end - timedelta(days=2)))
        steps.append(("6", period_end - timedelta(days=1), period_end))
    return {
        key: due
        for key, due, closes in steps
        if closes < now and key not in sent
    }


# --------------------------------------------------------------------------
# Bodies
# --------------------------------------------------------------------------

def _access_after_trial(grace_days: int) -> str:
    """%УСЛОВИЯ_ДОСТУПА_ПОСЛЕ_ПРОБЫ% -- derived from the real configured
    grace period, with no promise about the exact minute of the shutdown.
    The doc forbids the "льготный период N дней" wording at grace 0."""
    if grace_days <= 0:
        return "Если оплата не поступит, доступ будет приостановлен после окончания пробы"
    return (
        f"После окончания пробы предусмотрен льготный период {grace_days} дн. "
        "Если оплата не поступит, доступ будет приостановлен после его завершения"
    )


def _moment(value: datetime | None) -> str:
    """%ДАТА_И_ВРЕМЯ%. The recipient's timezone is unknown, so the doc
    requires naming the zone explicitly rather than saying "today"."""
    if value is None:
        return "%ДАТА_И_ВРЕМЯ%"
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def render_email(
    email_key: str,
    *,
    cabinet_url: str,
    unsubscribe_link: str,
    period_end: datetime | str | None = None,
    grace_days: int = config.GRACE_PERIOD_DAYS,
    price_rub: float = config.SUBSCRIPTION_PRICE_RUB,
    period_days: int = config.BILLING_PERIOD_DAYS,
) -> tuple[str, str]:
    """(subject, plain-text body) for one sequence step.

    Unconfirmed facts stay as the doc's own %ПЛЕЙСХОЛДЕР% tokens; callers
    must run unfilled_placeholders() on the body before sending.
    """
    if email_key not in EMAIL_KEYS:
        raise ValueError(f"Неизвестное письмо цепочки: {email_key!r}")
    ctx = {
        "cabinet_url": cabinet_url,
        "period_end": _parse(period_end),
        "period_days": period_days,
        "price": f"{price_rub:g}",
        "billing": config.billing_url(),
        "access": _access_after_trial(grace_days),
        "support": SUPPORT_CHANNEL or "%КАНАЛ_ПОДДЕРЖКИ%",
        "retention": DATA_RETENTION or "%УСЛОВИЯ_ХРАНЕНИЯ%",
        "return_terms": RETURN_TERMS or "%ПОДТВЕРЖДЁННЫЕ_УСЛОВИЯ_ВОЗВРАТА_И_ХРАНЕНИЯ%",
    }
    subject, body = _BODIES[email_key](ctx)
    return subject, body + _FOOTER.format(unsubscribe_link=unsubscribe_link)


# Every sequence email carries it (doc rule 4); transactional mail does not
# and must not -- this footer lives here, not in mailer.send_email().
_FOOTER = (
    "\n\n--\n"
    "Это письмо из обучающей цепочки MARKETSHELPER для пробного периода.\n"
    "Отказаться от неё: {unsubscribe_link}\n"
    "Письма о пароле, подтверждении email и об оплате приходят отдельно "
    "и отпиской не отключаются."
)


def _email_1(ctx: dict) -> tuple[str, str]:
    return "Кабинет готов. С чего начать — MARKETSHELPER", (
        "Здравствуйте!\n\n"
        f"Ваш кабинет MARKETSHELPER создан и работает. Пробный доступ действует до {_moment(ctx['period_end'])} — "
        "карта для пробы не нужна, ничего не спишется само.\n\n"
        "Начните с одного своего товара. Для первого отчёта нужны данные WB и проверенная себестоимость; "
        "время подготовки зависит от ваших данных и загрузки отчётов.\n\n"
        "1. Подключить Wildberries. Создайте токен в личном кабинете WB с правами, указанными в инструкции "
        "подключения, и сохраните его в кабинете MARKETSHELPER. Дождитесь успешной загрузки данных.\n\n"
        "2. Проверить себестоимость. Выберите товар и сохраните стоимость единицы по своим данным. "
        "Если ведёте партии, подготовьте даты, количества и цены поступлений.\n\n"
        "3. Открыть первый отчёт. Когда данные выбранного товара готовы, откройте расчёт и сверьте его с "
        "исходными документами. Налоги и внешние расходы, которых нет в учёте, в него не входят.\n\n"
        f"Если на каком-то шаге застряли — напишите в поддержку: {ctx['support']}. "
        "Не присылайте API-токен или пароль.\n\n"
        f"Открыть кабинет: {ctx['cabinet_url']}"
    )


def _email_2(ctx: dict) -> tuple[str, str]:
    return "Как подготовить первый отчёт в MARKETSHELPER", (
        "Здравствуйте!\n\n"
        "Если первый отчёт ещё не открыт, начните с подключения и загрузки данных. Если вы уже прошли этот шаг, "
        "переходите к проверке себестоимости выбранного товара.\n\n"
        "Где взять токен: в разделе доступа к API личного кабинета WB. Используйте актуальную инструкцию "
        "подключения MARKETSHELPER и выдайте только права, необходимые для выбранных функций.\n\n"
        "Куда вставить: в разделе подключения WB в кабинете MARKETSHELPER.\n\n"
        "Что дальше: дождитесь успешной полной загрузки, выберите товар с данными реализации и проверьте его "
        "себестоимость. Наличие токена само по себе ещё не означает, что отчёт готов.\n\n"
        "Ключ задаётся в вашем кабинете. Передавайте его только через форму подключения, а не в письме или "
        "чате поддержки.\n\n"
        f"Открыть кабинет: {ctx['cabinet_url']}\n\n"
        f"Застряли на конкретном экране — напишите в поддержку: {ctx['support']}. "
        "На скриншоте скройте токены и персональные данные."
    )


def _email_2b(ctx: dict) -> tuple[str, str]:
    return "Первый отчёт открыт. Что проверить дальше", (
        "Здравствуйте!\n\n"
        "Вы открыли первый отчёт по своему товару. Теперь полезно проверить, какие исходные данные стоят "
        "за результатом.\n\n"
        "Сначала — состав расчёта. Сверьте период, реализацию, удержания и себестоимость. Если получился "
        "отрицательный результат, проверьте полноту и точность затрат, прежде чем менять цену.\n\n"
        "Потом — партии. Если закупочные цены менялись, проверьте даты, количества и стоимость поступлений. "
        "Партийный учёт поможет объяснить, как стоимость распределяется между реализацией и остатком.\n\n"
        f"Открыть отчёт в кабинете: {ctx['cabinet_url']}"
    )


def _email_3(ctx: dict) -> tuple[str, str]:
    return "Как партии меняют оценку себестоимости", (
        "Здравствуйте!\n\n"
        "Короткая история, которая объясняет, зачем в MARKETSHELPER вообще есть партии закупки.\n\n"
        "Условный пример: три поступления по 100 единиц, цены — 400 ₽, 480 ₽ и 520 ₽. Средневзвешенная цена "
        "всех закупок — около 466,67 ₽. По FIFO стоимость списывается сначала из самой ранней доступной партии, "
        "затем из следующей. Поэтому оценка реализованного товара и остатка может отличаться от оценки по средней.\n\n"
        "Методы распределяют одну стоимость закупок между реализацией и запасами. FIFO сам по себе не увеличивает "
        "прибыль и не доказывает, какая физическая единица была продана. Для решения об акции сравните расчёт по "
        "оставшимся слоям с ценой следующей закупки и расходами продажи.\n\n"
        "Для производителя добавляется ещё один уровень: стоимость изделия собирается из использованных партий "
        "разных материалов.\n\n"
        "Для учебной проверки начните с двух партий одного товара по разной цене. Для рабочего учёта сначала "
        "согласуйте начальные остатки и историю движений: две последние закупки не заменяют всю нужную историю.\n\n"
        f"Завести партию закупки в кабинете: {ctx['cabinet_url']}"
    )


def _email_4(ctx: dict) -> tuple[str, str]:
    return "Расхождение остатков с WB: с чего начать", (
        "Здравствуйте!\n\n"
        "Следующая задача после первого отчёта — сверить остатки одного товара.\n\n"
        "Если собственный учёт и данные WB расходятся, сначала приведите их к одной дате отсечки и учтите товары "
        "в пути к клиенту и от клиента. Расхождение ещё не означает потерю.\n\n"
        "Сверка в MARKETSHELPER показывает расхождение по конкретным SKU: сколько по вашему учёту, сколько по "
        "данным WB и какова ориентировочная стоимость разницы по данным себестоимости. Причину расследует "
        "человек. Сервис не подтверждает потерю и не подаёт претензию вместо вас.\n\n"
        "Начните с одного SKU. Время проверки зависит от истории и качества данных; затем определите подходящую "
        "регулярность сверок.\n\n"
        f"Запустить сверку остатков в кабинете: {ctx['cabinet_url']}\n\n"
        "Не исправляйте остаток только ради совпадения цифр: сначала сохраните исходные данные и выясните "
        "причину разницы."
    )


def _email_5(ctx: dict) -> tuple[str, str]:
    return f"Пробный период заканчивается {_moment(ctx['period_end'])}", (
        "Здравствуйте!\n\n"
        f"Пробный период заканчивается {_moment(ctx['period_end'])}. Пишем заранее, чтобы это не стало новостью "
        "в последний момент.\n\n"
        f"Что дальше, если продолжить: {ctx['price']} ₽ за {ctx['period_days']} дней. Состав тарифа и условия "
        "оплаты можно проверить на странице тарифа.\n\n"
        f"Что дальше, если не оплачивать: {ctx['access']}. Условия хранения данных: {ctx['retention']}.\n\n"
        f"Если сервис вам не подошёл, расскажите, что помешало получить нужный результат: {ctx['support']}.\n\n"
        f"Посмотреть тариф: {ctx['billing']}"
    )


def _email_6(ctx: dict) -> tuple[str, str]:
    return "Последний день пробного периода — MARKETSHELPER", (
        "Здравствуйте!\n\n"
        f"Пробный период заканчивается {_moment(ctx['period_end'])}.\n"
        f"{ctx['access']}.\n\n"
        "Удалось ли получить проверяемый результат на своих данных? Оцените, полезен ли он для ваших решений и "
        "сколько времени требует ведение учёта. Само отличие цифры в отчёте ещё не означает экономию или "
        f"окупаемость. Продолжение стоит {ctx['price']} ₽ за {ctx['period_days']} дней.\n\n"
        f"Оплатить и продолжить: {ctx['billing']}\n\n"
        f"Если не подошёл — расскажите, что не сработало: {ctx['support']}."
    )


def _email_7(ctx: dict) -> tuple[str, str]:
    return "Вернуться к работе с кабинетом — MARKETSHELPER", (
        "Здравствуйте!\n\n"
        "Пробный период завершён, доступ к кабинету приостановлен. Возможность вернуться к работе и актуальные "
        f"условия оплаты можно посмотреть в аккаунте. {ctx['return_terms']}.\n\n"
        f"Если не хватило времени или возникла трудность с настройкой, напишите в поддержку: {ctx['support']}. "
        "Опишите, какой шаг не удалось пройти. Возможность дополнительного времени обсуждается отдельно; "
        "это не обещание автоматического продления пробы.\n\n"
        f"Открыть аккаунт: {config.base_url()}/account"
    )


_BODIES = {
    "1": _email_1, "2": _email_2, "2b": _email_2b, "3": _email_3,
    "4": _email_4, "5": _email_5, "6": _email_6, "7": _email_7,
}


def unfilled_placeholders(text: str) -> list[str]:
    """Doc rule 8: never mail a message that still shows a %ПЛЕЙСХОЛДЕР%."""
    return sorted(set(_PLACEHOLDER_RE.findall(text)))


# --------------------------------------------------------------------------
# Thin db helpers (the only non-pure part of this module)
# --------------------------------------------------------------------------

def record_optout(conn, account_id: int, *, at: datetime | None = None) -> None:
    """Idempotent: the same link clicked twice keeps the first timestamp."""
    conn.execute(
        "INSERT INTO trial_email_optouts(account_id, opted_out_at) VALUES (?,?) "
        "ON CONFLICT(account_id) DO NOTHING",
        (int(account_id), (at or _now()).isoformat(timespec="seconds")),
    )


def is_opted_out(conn, account_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM trial_email_optouts WHERE account_id=?", (int(account_id),)
    ).fetchone() is not None


def sent_log(conn, account_id: int) -> dict[str, str]:
    """email_key -> sent_at for everything already claimed, including sends
    that failed at the SMTP step: a failed step is not retried, because a
    retry cannot tell "never left the building" from "delivered twice".
    A disabled transport is different: it never reached SMTP and remains
    eligible for a later run."""
    rows = conn.execute(
        "SELECT email_key, sent_at FROM trial_emails "
        "WHERE account_id=? AND status <> 'disabled'", (int(account_id),)
    ).fetchall()
    return {row["email_key"]: row["sent_at"] for row in rows}


def growth_events(conn, account_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT event_name, MIN(occurred_at) AS occurred_at FROM growth_events "
        "WHERE account_id=? GROUP BY event_name",
        (int(account_id),),
    ).fetchall()
    return {row["event_name"]: row["occurred_at"] for row in rows}


def claim_send(conn, account_id: int, email_key: str, *, at: datetime | None = None,
               status: str = "sending") -> bool:
    """Reserve one (account, step) slot. False means someone already has it.

    Claimed before the message is handed to SMTP, deliberately: losing a
    mail to a crash between the claim and the send is recoverable by a
    human, sending the same step twice is not."""
    if email_key not in EMAIL_KEYS:
        raise ValueError(f"Неизвестное письмо цепочки: {email_key!r}")
    result = conn.execute(
        "INSERT INTO trial_emails(account_id, email_key, sent_at, status) VALUES (?,?,?,?) "
        "ON CONFLICT(account_id, email_key) DO UPDATE SET sent_at=excluded.sent_at,status=excluded.status "
        "WHERE trial_emails.status='disabled'",
        (int(account_id), email_key, (at or _now()).isoformat(timespec="seconds"), status),
    )
    return result.rowcount == 1


def finish_send(conn, account_id: int, email_key: str, *, delivered: bool | str) -> None:
    if isinstance(delivered, bool):
        # Compatibility for existing callers/tests; new callers should pass
        # the explicit mailer state.
        final_status = "sent" if delivered else "failed"
    elif delivered in {"disabled", "accepted", "failed"}:
        final_status = delivered
    else:
        raise ValueError(f"Unsupported mail delivery status: {delivered!r}")
    conn.execute(
        "UPDATE trial_emails SET status=? WHERE account_id=? AND email_key=?",
        (final_status, int(account_id), email_key),
    )
