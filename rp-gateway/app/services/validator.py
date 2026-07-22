"""Narrative output validation."""

from __future__ import annotations

import re
from typing import Any

from app.models.schemas import Outcome, ValidationResult
from app.services.rule_engine import is_awareness_campaign


SERVICE_LINE_RE = re.compile(
    r"^\s*(?:[-—–]\s*)?"
    r"(analysis|recommendation|diagnostics?|validator|gateway|system note|"
    r"анализ|рекомендац(?:ия|ии|ию)|диагностик[а-я]*|служебн[а-я ]+заметк[а-я]*)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)
SERVICE_PHRASES = [
    "the action resolves as",
    "fixed outcome",
    "bounded desired outcome",
    "hard world constraints",
    "the narration preserves",
    "authoritative_outcome",
    "gateway check",
    "result field",
]
RESULT_NARRATION = {
    "critical_success": "Сцена открывает ясный проход вперед.",
    "success": "Сцена продолжает двигаться в выбранном направлении.",
    "partial_success": "Сцена сдвигается вперед, но часть результата остается неустойчивой.",
    "failure_with_progress": "Желаемое сразу не складывается, но остается узкая зацепка.",
    "failure": "Попытка не дает прямого результата; напряжение удерживается в сцене.",
    "critical_failure": "Попытка резко ухудшает положение, и последствия становятся заметны сразу.",
}
DOUBLE_EXTENSION_RE = re.compile(r"\b[\w.-]+\.(?:xlsx|xlsm|docx|pdf|zip|rar|7z|pptx)\.exe\b", re.IGNORECASE)
DANGEROUS_FILE_ACTION_MARKERS = ("откры", "запуск", "запуст", "скач", "open", "run", "download")
EMAIL_BLOCK_RE = re.compile(r"(?m)^ПИСЬМО\s*$")
MESSENGER_BLOCK_RE = re.compile(r"(?m)^СООБЩЕНИЕ\s*$")
EMAIL_DESCRIPTION_RE = re.compile(
    r"(письм[оа]\s+от|во\s+входящих|в\s+папке\s+['\"«„]?входящие|приходит\s+письмо|открываешь\s+письмо)",
    re.IGNORECASE,
)
MESSENGER_DESCRIPTION_RE = re.compile(
    r"(в\s+(?:рабочем|личном)\s+мессенджере|в\s+чате|сообщение\s+от|"
    r"(?:пишет|написал[аи]?)\s+(?:в\s+)?мессенджер|открываешь\s+сообщение)",
    re.IGNORECASE,
)
AWARENESS_HINT_RE = re.compile(
    r"(домен\s+(?:не|отлича)|двойн\w+\s+расширени|красн\w+\s+флаг|это\s+фишинг|"
    r"это\s+подозритель|выглядит\s+(?:подозритель|безопасн)|никак\w+\s+подозрительност|"
    r"вспомина\w+\s+стандарт|стандартн\w+\s+ход|лучше\s+отправить|сообщить\s+в\s+SOC|"
    r"по\s+правилам\s+PT\s+Security|не\s+наруша\w+\s+правил|цель\s*[-—–:]\s*запустить\s+вредоносн)",
    re.IGNORECASE,
)
AWARENESS_META_RE = re.compile(
    r"^\s*\*{0,2}(?:мессенджер|блок[-‑–— ]?сценарий|сценарный\s+блок|разбор\s+хода|итоги\s+хода)\*{0,2}\s*:|"
    r"\b(?:всё|все)\s+в\s+пределах\s+шаблона\b|\bдва\s+письма,?\s+одно\s+сообщение\b|"
    r"\bточк[аи]\s+решения\b|\bследующ\w*\s+проверк\w*\b",
    re.IGNORECASE | re.MULTILINE,
)
AWARENESS_INTERNAL_PROCESS_RE = re.compile(
    r"(бэкэнд|backend|дашборд|инцидент[-‑–— ]?трекинг|incident[-‑–— ]?tracking|уровень\s+опасности|логиру\w*|"
    r"SOC\s+будет\s+анализировать|системн\w+\s+подтверждени|сервис\s+.*(?:строк|задерж))",
    re.IGNORECASE,
)
AWARENESS_MENTAL_STATE_RE = re.compile(
    r"\bты\s+(?:понимаешь|осозна[её]шь|вспоминаешь|считаешь|решаешь|доверяешь|сомневаешься|"
    r"намечаешь|нормализуешься)\b",
    re.IGNORECASE,
)
AWARENESS_PLAYER_ACTION_PATTERNS = (
    ("откры", re.compile(r"\bты\s+(?:сам\s+)?открываешь\b", re.IGNORECASE)),
    ("скач", re.compile(r"\bты\s+(?:сам\s+)?скачиваешь\b", re.IGNORECASE)),
    ("запус", re.compile(r"\bты\s+(?:сам\s+)?запускаешь\b", re.IGNORECASE)),
    ("пересыл", re.compile(r"\bты\s+(?:сам\s+)?пересылаешь\b", re.IGNORECASE)),
    ("отправ", re.compile(r"\bты\s+(?:сам\s+)?отправляешь\b", re.IGNORECASE)),
    ("ввод", re.compile(r"\bты\s+(?:сам\s+)?вводишь\b", re.IGNORECASE)),
    ("перех", re.compile(r"\bты\s+(?:сам\s+)?переходишь\b", re.IGNORECASE)),
)
EMAIL_REQUIRED_FIELDS = ("Канал:", "От:", "Кому:", "Дата/время:", "Тема:", "Вложения:", "Ссылки:", "Тело:", "Подпись:")
MESSENGER_REQUIRED_FIELDS = ("Канал:", "Чат:", "От:", "Кому:", "Дата/время:", "Вложения:", "Ссылки:", "Текст:")


class OutputValidator:
    def validate(
        self,
        text: str,
        outcome: Outcome,
        state: dict[str, Any] | None = None,
        campaign_id: str | None = None,
        latest_user_message: str = "",
    ) -> ValidationResult:
        lowered = text.lower()
        violations: list[str] = []
        if "<authoritative_outcome>" in lowered or "</authoritative_outcome>" in lowered:
            violations.append("Narrative exposed service outcome tags to the player.")
        if SERVICE_LINE_RE.search(text):
            violations.append("Narrative exposed analysis, recommendation, or diagnostic labels to the player.")
        for phrase in SERVICE_PHRASES:
            if phrase in lowered:
                violations.append(f"Narrative exposed service wording: {phrase}")
        if outcome.result in {"failure", "critical_failure", "failure_with_progress"}:
            risky = [
                "secretly grants",
                "equivalent authority",
                "military authority",
                "takes command",
                "transfers command",
                "hands over the throne",
            ]
            if any(item in lowered for item in risky):
                violations.append("Narrative grants an equivalent hidden success despite failed or limited result.")
        for reason in outcome.blocked_reasons:
            key_terms = [part for part in reason.lower().split() if len(part) >= 6]
            if key_terms and "despite" in lowered and any(term in lowered for term in key_terms):
                violations.append(f"Narrative appears to bypass blocked constraint: {reason}")
        if "you decide to" in lowered or "you willingly" in lowered:
            violations.append("Narrative may have taken control of the player character.")
        if is_awareness_campaign(state or {}, campaign_id):
            expected_header = awareness_expected_header(state) if state else None
            final_summary = awareness_final_summary(state)
            if expected_header and not text.lstrip().startswith(expected_header):
                violations.append(f"Awareness narrative must start with the scheduled header: {expected_header}")
            if not final_summary and AWARENESS_HINT_RE.search(text):
                violations.append("Awareness narrative exposed explicit security hints or player reasoning.")
            if AWARENESS_META_RE.search(text):
                violations.append("Awareness narrative exposed scenario-template or facilitator-only wording.")
            if AWARENESS_INTERNAL_PROCESS_RE.search(text):
                violations.append("Awareness narrative invented internal SOC, tracking, dashboard, or backend details.")
            if AWARENESS_MENTAL_STATE_RE.search(text):
                violations.append("Awareness narrative assigned thoughts or security conclusions to the player.")
            violations.extend(awareness_player_action_violations(text, latest_user_message))
            email_blocks = structured_blocks(text, "ПИСЬМО")
            messenger_blocks = structured_blocks(text, "СООБЩЕНИЕ")
            if EMAIL_DESCRIPTION_RE.search(text) and not email_blocks:
                violations.append("Awareness email event was summarized instead of rendered as a PISMO block.")
            if MESSENGER_DESCRIPTION_RE.search(text) and not messenger_blocks:
                violations.append("Awareness messenger event was summarized instead of rendered as a SOOBSCHENIE block.")
            for index, block in enumerate(email_blocks, start=1):
                missing = missing_fields(block, EMAIL_REQUIRED_FIELDS)
                if missing:
                    violations.append(f"Awareness email block {index} is missing required fields: {', '.join(missing)}")
            for index, block in enumerate(messenger_blocks, start=1):
                missing = missing_fields(block, MESSENGER_REQUIRED_FIELDS)
                if missing:
                    violations.append(f"Awareness messenger block {index} is missing required fields: {', '.join(missing)}")
            if expected_header and expected_header.startswith("Ход 1."):
                if len(email_blocks) < 2:
                    violations.append("Awareness opening must include at least two full PISMO blocks.")
                if len(messenger_blocks) < 1:
                    violations.append("Awareness opening must include at least one full SOOBSCHENIE block.")
        if violations:
            return ValidationResult(
                valid=False,
                violations=violations,
                repair_instruction=(
                    "Перепиши ответ как обычную русскую офисную сцену для игрока. Начни с точного заголовка текущего хода. "
                    "Удали анализ признаков атаки, размышления игрока, подсказки про SOC/ДИБ, внутренние процессы, backend, "
                    "дашборды, оценку риска и служебные секции вроде 'Мессенджер' или 'Блок-сценарий'. "
                    "Не принимай решений за игрока. Письма и сообщения показывай только полными блоками ПИСЬМО и СООБЩЕНИЕ."
                ),
            )
        return ValidationResult(valid=True)


def safe_fallback(
    outcome: Outcome,
    state: dict[str, Any] | None = None,
    latest_user_message: str = "",
    campaign_id: str | None = None,
) -> str:
    if state and is_awareness_campaign(state, campaign_id):
        return awareness_safe_fallback(state, latest_user_message)
    first = RESULT_NARRATION.get(outcome.result, "Сцена сдвигается дальше, но без лишних уступок за кадром.")
    if outcome.blocked_reasons:
        second = "Что-то в устройстве мира упирается и не дает продавить желаемое напрямую."
    elif outcome.result in {"critical_success", "success"}:
        second = "Мир не делает лишних подарков, но сейчас у тебя есть честное окно для следующего шага."
    elif outcome.result == "partial_success":
        second = "Дальше придется выбрать, чем воспользоваться и какую цену принять."
    else:
        second = "Остается решить, как обойти препятствие или чем рискнуть дальше."
    return f"{first} {second}"


def awareness_safe_fallback(state: dict[str, Any], latest_user_message: str) -> str:
    resources = state.get("player", {}).get("resources", {})
    window = resources.get("current-turn-window") if isinstance(resources, dict) else None
    header = awareness_header_from_window(window) if isinstance(window, str) else None
    prefix = f"{header}\n\n" if header else ""
    if DOUBLE_EXTENSION_RE.search(latest_user_message) and has_dangerous_file_action(latest_user_message):
        return (
            f"{prefix}Рабочий блок продолжается без заметных окон, ошибок или немедленных внешних изменений. "
            "Календарь и переписка остаются в обычном ритме: текущие задачи ждут решения, а коллеги рассчитывают на твой статус. "
            "Что делаешь дальше?"
        )
    return (
        f"{prefix}Рабочий блок продолжается в обычном ритме: сообщения, письма и календарь остаются перед тобой. "
        "Что делаешь дальше?"
    )


def awareness_opening_fallback(state: dict[str, Any]) -> str:
    resources = state.get("player", {}).get("resources", {})
    window = resources.get("current-turn-window") if isinstance(resources, dict) else None
    header = awareness_header_from_window(window) if isinstance(window, str) else "Ход 1. Понедельник, 10:00-14:00."
    return f"""{header}

Утро в PT Security начинается с обычной рабочей загрузки: календарь уже держит планерку ближе к полудню, в задачнике висят пара профессиональных хвостов по твоему направлению, а почта и мессенджер принесли новые обращения.

ПИСЬМО
Канал: корпоративная почта
От: Ольга Смирнова <smirnova@ptsecurity.com>
Кому: employee@ptsecurity.com
Дата/время: понедельник, 10:18
Тема: Материалы к утренней синхронизации
Вложения: нет
Ссылки: нет
Тело:
Доброе утро. К 12:00 нужен короткий статус по твоему рабочему блоку: что закрыто, где есть риски и кому нужна помощь. Достаточно трех-пяти пунктов, чтобы не растягивать встречу.
Подпись:
Ольга Смирнова
Ведущий менеджер проекта, Project Office
PT Security
Тел.: +7 495 000-18-40, доб. 1840
Email: smirnova@ptsecurity.com

ПИСЬМО
Канал: корпоративная почта
От: Support Desk <notice@ptsecurity-office.com>
Кому: employee@ptsecurity.com
Дата/время: понедельник, 10:37
Тема: Срочное подтверждение рабочего графика
Вложения: WorkSchedule_Update.xlsx.exe
Ссылки: https://ptsecurity-office.com/schedule
Тело:
Коллега, сегодня обновляется график доступов и рабочих смен. Откройте приложенный файл до 11:30 и подтвердите данные учетной записи на странице согласования. При задержке запись может быть исключена из списка активных сотрудников.
Подпись:
Support Desk
Employee Services
Тел.: +7 495 000-10-01
Email: notice@ptsecurity-office.com

СООБЩЕНИЕ
Канал: рабочий мессенджер
Чат: личный чат
От: Максим Карелин <karelin@ptsecurity.com>
Кому: employee@ptsecurity.com
Дата/время: понедельник, 11:05
Вложения: нет
Ссылки: нет
Текст:
Привет. До обеда можешь дать статус по своему блоку для планерки? Мне важно понимать, какие пункты уже можно считать готовыми.

Что делаешь с этими письмами и сообщением, и как выстраиваешь первую половину дня до 14:00?"""


def has_dangerous_file_action(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in DANGEROUS_FILE_ACTION_MARKERS)


def awareness_expected_header(state: dict[str, Any]) -> str | None:
    resources = state.get("player", {}).get("resources", {})
    window = resources.get("current-turn-window") if isinstance(resources, dict) else None
    if not isinstance(window, str):
        return None
    return awareness_header_from_window(window)


def awareness_final_summary(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    return int(state.get("meta", {}).get("turn", 0) or 0) > 10


def awareness_player_action_violations(text: str, latest_user_message: str) -> list[str]:
    latest = latest_user_message.casefold()
    violations: list[str] = []
    for marker, pattern in AWARENESS_PLAYER_ACTION_PATTERNS:
        if pattern.search(text) and marker not in latest:
            violations.append(f"Awareness narrative invented a player security action: {marker}")
    return violations


def awareness_header_from_window(window: str) -> str | None:
    match = re.search(r"ход\s+(\d+),\s*([^,]+),\s*([0-9:]+-[0-9:]+)", window, re.IGNORECASE)
    if not match:
        return None
    turn, day, time_window = match.groups()
    return f"Ход {turn}. {day.strip().capitalize()}, {time_window}."


def structured_blocks(text: str, marker: str) -> list[str]:
    matches = list(re.finditer(rf"(?m)^{re.escape(marker)}\s*$", text))
    blocks: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append(text[match.start() : end])
    return blocks


def missing_fields(block: str, fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if field not in block]
