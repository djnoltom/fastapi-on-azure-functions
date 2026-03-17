from __future__ import annotations

from datetime import date, datetime


USER_DATE_FORMAT = "%m/%d/%Y"
EDI_DATE_FORMAT = "%Y%m%d"


def parse_user_date(value: str) -> date:
    clean = value.strip()
    if not clean:
        raise ValueError("La fecha no puede estar vacia.")

    for fmt in (USER_DATE_FORMAT, EDI_DATE_FORMAT):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha invalida: {value}. Usa MM/DD/YYYY.")


def format_user_date(value: str | date | datetime | None) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime(USER_DATE_FORMAT)
    if isinstance(value, date):
        return value.strftime(USER_DATE_FORMAT)
    return parse_user_date(value).strftime(USER_DATE_FORMAT)


def format_edi_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime(EDI_DATE_FORMAT)
    if isinstance(value, date):
        return value.strftime(EDI_DATE_FORMAT)
    return parse_user_date(value).strftime(EDI_DATE_FORMAT)


def today_user_date() -> str:
    return datetime.now().strftime(USER_DATE_FORMAT)


def add_user_date_months(value: str | date | datetime, months: int) -> str:
    base_date = parse_user_date(value) if isinstance(value, str) else value.date() if isinstance(value, datetime) else value
    month_index = (base_date.month - 1) + months
    year = base_date.year + (month_index // 12)
    month = (month_index % 12) + 1
    month_lengths = (
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    day = min(base_date.day, month_lengths[month - 1])
    return date(year, month, day).strftime(USER_DATE_FORMAT)
