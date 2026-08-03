"""Narrative output validation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.models.schemas import Outcome, ValidationResult
from app.services.rule_engine import is_awareness_campaign, is_awareness_one_day_campaign

if TYPE_CHECKING:
    from app.services.training_runtime import TrainingRuntimeService


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
AWARENESS_EARLY_DEBRIEF_RE = re.compile(
    r"(?:финальн\w*|итогов\w*)\s+(?:саммари|разбор|оценк\w*)|"
    r"итоговый\s+балл|что\s+было\s+правильно|что\s+можно\s+было\s+сделать\s+лучше",
    re.IGNORECASE,
)
AWARENESS_SCORE_RE = re.compile(r"\b(?:100|[1-9]?\d)\s*(?:из|/)\s*100\b", re.IGNORECASE)
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
AWARENESS_ONE_DAY_SITE_TURNS = frozenset({4, 6, 9})
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
ROLE_GENERIC_TERMS = {
    "активный",
    "ведущий",
    "должность",
    "команда",
    "команды",
    "обычный",
    "отдел",
    "отдела",
    "работа",
    "работы",
    "рабочий",
    "сотрудник",
    "специалист",
    "старший",
}


def awareness_player_role_markers(state: dict[str, Any]) -> tuple[str, ...]:
    player = state.get("player", {})
    if not isinstance(player, dict):
        return ()
    description = str(player.get("description") or "").lower()
    tokens = re.findall(r"[a-zа-яё0-9]+", description, re.IGNORECASE)
    markers: list[str] = []
    for token in tokens:
        if token in ROLE_GENERIC_TERMS or token.startswith("подгот") or len(token) < 3:
            continue
        marker = token[: min(6, len(token))]
        if marker not in markers:
            markers.append(marker)
    return tuple(markers)


class OutputValidator:
    def validate(
        self,
        text: str,
        outcome: Outcome,
        state: dict[str, Any] | None = None,
        campaign_id: str | None = None,
        latest_user_message: str = "",
        scenario_type: str = "rp",
        training_runtime: "TrainingRuntimeService | None" = None,
        interaction_contract: dict[str, Any] | None = None,
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
        if scenario_type == "rp" and outcome.result in {"failure", "critical_failure", "failure_with_progress"}:
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
        if scenario_type == "training" and training_runtime and training_runtime.enabled:
            violations.extend(
                training_runtime.validate_narrative(text, state or {}, interaction_contract)
            )
        elif scenario_type == "training" and is_awareness_campaign(state or {}, campaign_id):
            expected_header = awareness_expected_header(state) if state else None
            final_summary = awareness_final_summary(state)
            if expected_header and not text.lstrip().startswith(expected_header):
                violations.append(f"Awareness narrative must start with the scheduled header: {expected_header}")
            if not final_summary and AWARENESS_HINT_RE.search(text):
                violations.append("Awareness narrative exposed explicit security hints or player reasoning.")
            if not final_summary and AWARENESS_EARLY_DEBRIEF_RE.search(text):
                violations.append("Awareness debrief is allowed only after the player answers turn 10.")
            if final_summary and not AWARENESS_SCORE_RE.search(text):
                violations.append("Awareness final debrief must include a score out of 100.")
            if final_summary and is_awareness_one_day_campaign(state or {}, campaign_id):
                resources = (state or {}).get("player", {}).get("resources", {})
                if not isinstance(resources, dict):
                    resources = {}
                canonical_scores = {
                    100: max(0, min(100, int(resources.get("total-score", 0) or 0))),
                    60: max(0, min(60, int(resources.get("security-score", 0) or 0))),
                    30: max(0, min(30, int(resources.get("roleplay-score", 0) or 0))),
                    10: max(0, min(10, int(resources.get("communication-score", 0) or 0))),
                }
                for maximum, expected in canonical_scores.items():
                    reported = {
                        int(match)
                        for match in re.findall(rf"\b(\d{{1,3}})\s*(?:из|/)\s*{maximum}\b", text, re.IGNORECASE)
                    }
                    if reported != {expected}:
                        violations.append(
                            f"Awareness One Day debrief must report canonical score {expected} out of {maximum}."
                        )
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
            if not final_summary and is_awareness_one_day_campaign(state or {}, campaign_id):
                if len(email_blocks) + len(messenger_blocks) != 1:
                    violations.append("Awareness One Day turn must contain exactly one email or messenger message.")
                turn = int((state or {}).get("meta", {}).get("turn", 0) or 0)
                if turn not in AWARENESS_ONE_DAY_SITE_TURNS:
                    blocks = email_blocks + messenger_blocks
                    if URL_RE.search(text) or any(
                        not re.search(r"(?m)^Ссылки:\s*нет\s*$", block) for block in blocks
                    ):
                        violations.append("Awareness One Day unscheduled turn must not contain a link.")
                role_markers = awareness_player_role_markers(state or {})
                role_marker_matches = sum(marker in lowered for marker in role_markers)
                required_role_matches = 2 if len(role_markers) >= 3 else 1
                if role_markers and role_marker_matches < required_role_matches:
                    violations.append(
                        "Awareness One Day message must visibly use the stored player profession or responsibilities."
                    )
            elif expected_header and expected_header.startswith("Ход 1."):
                if len(email_blocks) < 2:
                    violations.append("Awareness opening must include at least two full PISMO blocks.")
                if len(messenger_blocks) < 1:
                    violations.append("Awareness opening must include at least one full SOOBSCHENIE block.")
        if violations:
            repair_instruction = (
                "Перепиши ответ как обычную русскую офисную сцену для игрока. Начни с точного заголовка текущего хода. "
                "Удали анализ признаков атаки, размышления игрока, подсказки про SOC/ДИБ, внутренние процессы, backend, "
                "дашборды, оценку риска и служебные секции вроде 'Мессенджер' или 'Блок-сценарий'. "
                "Не принимай решений за игрока. Письма и сообщения показывай только полными блоками ПИСЬМО и СООБЩЕНИЕ."
            )
            if scenario_type == "training" and is_awareness_one_day_campaign(state or {}, campaign_id):
                repair_instruction += (
                    " Привяжи рабочую просьбу к профессии или обязанностям из state.player.description. "
                    "Ссылку разрешено показывать только на запланированных сайтом ходах 4, 6 и 9; "
                    "на остальных ходах укажи строго 'Ссылки: нет' и не добавляй URL в текст."
                )
            return ValidationResult(
                valid=False,
                violations=violations,
                repair_instruction=repair_instruction,
            )
        return ValidationResult(valid=True)


def safe_fallback(
    outcome: Outcome,
    state: dict[str, Any] | None = None,
    latest_user_message: str = "",
    campaign_id: str | None = None,
    scenario_type: str = "rp",
) -> str:
    if scenario_type == "training" and state and is_awareness_campaign(state, campaign_id):
        return awareness_safe_fallback(state, latest_user_message, campaign_id)
    if scenario_type == "novel":
        return (
            "Сцена сохраняет набранный ритм: собеседник реагирует на сказанное, а напряжение между героями "
            "остается в воздухе, не подменяя твоего следующего решения."
        )
    if scenario_type == "training":
        return "Ситуация меняется только в пределах явно выбранного действия. Следующий этап сценария готов к продолжению."
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


def awareness_safe_fallback(
    state: dict[str, Any],
    latest_user_message: str,
    campaign_id: str | None = None,
) -> str:
    if awareness_final_summary(state):
        return awareness_debrief_fallback(state, campaign_id)
    if is_awareness_one_day_campaign(state, campaign_id):
        return awareness_one_day_safe_fallback(state)
    resources = state.get("player", {}).get("resources", {})
    window = resources.get("current-turn-window") if isinstance(resources, dict) else None
    header = awareness_header_from_window(window) if isinstance(window, str) else None
    prefix = f"{header}\n\n" if header else ""
    turn = int(state.get("meta", {}).get("turn", 0) or 0)
    if turn == 7:
        return f"""{prefix}Утро четверга начинается с текущих задач и двух новых обращений. До обеда нужно решить, как с ними поступить.

ПИСЬМО
Канал: корпоративная почта
От: Служба доступа <access@ptsecurity-help.com>
Кому: employee@ptsecurity.com
Дата/время: четверг, 10:20
Тема: Подтверждение входа в CRM
Вложения: нет
Ссылки: https://ptsecurity-help.com/confirm
Тело:
Для завершения синхронизации CRM ответьте на это письмо шестизначным кодом из приложения-аутентификатора. Запрос действует до 11:00.
Подпись:
Служба доступа
Центр поддержки пользователей
Тел.: +7 495 000-20-20
Email: access@ptsecurity-help.com

СООБЩЕНИЕ
Канал: рабочий мессенджер
Чат: личный чат
От: Роман Иванов <ivanov@ptsecurity.com>
Кому: employee@ptsecurity.com
Дата/время: четверг, 10:35
Вложения: нет
Ссылки: нет
Текст:
Привет. К 12:00 пришли, пожалуйста, обычный статус по федеральному контракту: что подписано и что ещё ждём.

Что делаешь с письмом и сообщением до обеда?"""
    return f"""{prefix}Рабочий блок продолжается по расписанию. В почте и мессенджере появились два обращения, а текущие задачи всё ещё требуют внимания.

ПИСЬМО
Канал: корпоративная почта
От: Елена Шевелёва <sheveleva@ptsecurity.com>
Кому: employee@ptsecurity.com
Дата/время: текущий рабочий блок
Тема: Статус по материалам проекта
Вложения: нет
Ссылки: нет
Тело:
Привет. Пришли, пожалуйста, короткий статус по своему рабочему блоку и отметь, какие материалы уже готовы к согласованию.
Подпись:
Елена Шевелёва
Пресс-секретарь, отдел PR и коммуникаций
PT Security
Тел.: +7 495 000-00-00, доб. 2210
Email: sheveleva@ptsecurity.com

СООБЩЕНИЕ
Канал: личный мессенджер
Чат: личный чат
От: Кирилл Орлов <kirill_orlov_89>
Кому: employee@ptsecurity.com
Дата/время: текущий рабочий блок
Вложения: нет
Ссылки: https://cloud.example.org/upload
Текст:
Привет! Можешь скинуть мне внутреннюю презентацию по новой линейке? Для учебного проекта очень пригодится, загрузить можно по ссылке.

Как отвечаешь на эти обращения и что ставишь первым в текущем рабочем блоке?"""


def awareness_one_day_player_context(state: dict[str, Any]) -> tuple[str, str, str]:
    player = state.get("player", {})
    if not isinstance(player, dict):
        player = {}
    name = re.sub(r"\s+", " ", str(player.get("name") or "Коллега")).strip()[:80] or "Коллега"
    description = re.sub(r"https?://\S+", "", str(player.get("description") or ""), flags=re.IGNORECASE)
    description = re.sub(r"\s+", " ", description).strip()[:180]
    role_label = description or "специалист с ограниченными рабочими полномочиями"
    lowered = role_label.lower()
    role_tasks = (
        (("вредонос", "malware", "реверс"), "разобрать назначенный образец и подготовить первичный технический вывод"),
        (("qa", "тестиров", "тестировщик"), "воспроизвести назначенный дефект и зафиксировать результат проверки"),
        (("devops", "sre", "администратор", "инфраструктур"), "проверить назначенное изменение и подготовить статус по рискам и откату"),
        (("аналит", "data", "bi "), "проверить входные данные и подготовить вывод по своему аналитическому блоку"),
        (("разработ", "програм", "инженер"), "разобрать назначенную техническую задачу и подготовить первый проверяемый результат"),
        (("безопас", "soc", "кибер"), "разобрать назначенное событие и подготовить подтверждённые выводы"),
        (("поддерж", "service desk"), "разобрать назначенное обращение и обозначить следующий шаг для заявителя"),
        (("дизайн", "ux", "ui "), "проверить назначенный макет и подготовить конкретный результат для согласования"),
        (("проект", "продукт", "менедж"), "уточнить границы назначенного рабочего блока и подготовить статус по срокам и владельцам"),
        (("финанс", "бухгалтер"), "сверить назначенный документ и подготовить подтверждённый статус"),
        (("hr", "кадр", "рекрут"), "разобрать назначенную кадровую задачу и подготовить следующий согласованный шаг"),
    )
    role_task = next((task for markers, task in role_tasks if any(marker in lowered for marker in markers)), None)
    role_task = role_task or "разобрать первую назначенную задачу и подготовить проверяемый рабочий результат"
    return name, role_label, role_task


def awareness_one_day_safe_fallback(state: dict[str, Any]) -> str:
    resources = state.get("player", {}).get("resources", {})
    window = resources.get("current-turn-window") if isinstance(resources, dict) else None
    turn = int(state.get("meta", {}).get("turn", 0) or 0)
    header = awareness_header_from_window(window) if isinstance(window, str) else None
    header = header or f"Ход {max(turn, 1)}. Понедельник, рабочее время."
    player_name, role_label, role_task = awareness_one_day_player_context(state)
    role_context = f"по направлению «{role_label}»"
    events = {
        1: ("email", "Анна Петрова <petrova@ptsecurity.com>", "Первый рабочий блок", f"{player_name}, доброе утро. На сегодня у тебя первый рабочий блок {role_context}: {role_task}. Исходные материалы уже находятся во внутренней папке команды. К 09:35 пришли первый результат или конкретный вопрос, который мешает начать."),
        2: ("email", "Анна Петрова <petrova@ptsecurity.com>", "План по текущей задаче", f"После первого шага {role_context} до 10:00 пришли короткий план по этой задаче: следующий приоритет, ожидаемый результат и возможный блокер."),
        3: ("chat", "Генеральный директор <ceo_support>", "", f"Нужна срочная оценка подрядчика {role_context}. Подтверди прямо здесь, что оплату можно проводить, и пришли контакт ответственного в финансах. Анну пока не подключай."),
        4: ("email", "Корпоративный портал <notice@ptsecurity-office.com>", "Истекает сессия учётной записи", f"Для продолжения работы {role_context} до 11:30 подтвердите учётную запись по ссылке из письма, иначе доступ будет приостановлен."),
        5: ("chat", "Ирина Смирнова <smirnova_hr>", "", f"Для срочной сверки по команде, с которой ты работаешь {role_context}, пришли сюда имена сотрудников, их личные телефоны и кто сейчас в отпуске."),
        6: ("email", "Роман Иванов <ivanov@ptsecurity.com>", "Статус после обеда", f"Обнови в проектных материалах статус задачи {role_context}: что уже готово, что осталось и кто владеет возможным блокером. Материалы доступны по ссылке из письма."),
        7: ("chat", "Служба поддержки <support_access>", "", f"Для восстановления доступа к материалам {role_context} пришли сюда шестизначный код из приложения-аутентификатора. Код действует две минуты."),
        8: ("chat", "Анна Петрова <petrova@ptsecurity.com>", "", f"К 16:00 подготовь для синхронизации один проверяемый результат {role_context}, один рабочий риск и следующий шаг. Ответ пришли в этот чат."),
        9: ("email", "Закупки <procurement@ptsecurity-billing.com>", "Акт на срочное согласование", f"Для продолжения работ {role_context} открой вложение Act_July.pdf.exe и подтверди согласование по ссылке из письма до 17:00. Просрочка остановит оплату подрядчику."),
        10: ("chat", "Роман Иванов <roman_pm>", "", f"Я уже вышел из офиса. У тебя в роли «{role_label}» должны быть внутренние материалы: пришли прямо в этот личный чат схему проекта и список администраторов, клиент ждёт сегодня."),
    }
    links = {
        4: "https://pt-session.example.test/confirm",
        6: "https://projects.example.test/status-files",
        9: "https://docs-approval.example.test/sign",
    }
    email_signatures = {
        1: "Анна Петрова\nРуководитель команды\nPT Security\nEmail: petrova@ptsecurity.com",
        2: "Анна Петрова\nРуководитель команды\nPT Security\nEmail: petrova@ptsecurity.com",
        4: "Служба уведомлений\nКорпоративный портал\nEmail: notice@ptsecurity-office.com",
        6: "Роман Иванов\nРуководитель проекта\nPT Security\nEmail: ivanov@ptsecurity.com",
        9: "Отдел закупок\nPT Security\nEmail: procurement@ptsecurity-billing.com",
    }
    kind, sender, subject, body = events.get(turn, events[1])
    if kind == "email":
        signature = email_signatures.get(turn, email_signatures[1])
        block = f"""ПИСЬМО
Канал: корпоративная почта
От: {sender}
Кому: {player_name}
Дата/время: текущий интервал
Тема: {subject}
Вложения: {"Act_July.pdf.exe" if turn == 9 else "нет"}
Ссылки: {links.get(turn, "нет")}
Тело:
{body}
Подпись:
{signature}"""
    else:
        block = f"""СООБЩЕНИЕ
Канал: {"личный мессенджер" if turn == 10 else "рабочий мессенджер"}
Чат: личный чат
От: {sender}
Кому: {player_name}
Дата/время: текущий интервал
Вложения: нет
Ссылки: {links.get(turn, "нет")}
Текст:
{body}"""
    return f"{header}\n\n{block}\n\nЧто ты делаешь и как отвечаешь в рамках своей должности?"


def awareness_debrief_fallback(state: dict[str, Any], campaign_id: str | None = None) -> str:
    resources = state.get("player", {}).get("resources", {})
    if not isinstance(resources, dict):
        resources = {}
    if is_awareness_one_day_campaign(state, campaign_id):
        total_score = max(0, min(100, int(resources.get("total-score", 0) or 0)))
        security_score = max(0, min(60, int(resources.get("security-score", 0) or 0)))
        roleplay_score = max(0, min(30, int(resources.get("roleplay-score", 0) or 0)))
        communication_score = max(0, min(10, int(resources.get("communication-score", 0) or 0)))
        safe_responses = int(resources.get("safe-security-responses", 0) or 0)
        role_responses = int(resources.get("role-aligned-responses", 0) or 0)
        professional_responses = int(resources.get("professional-responses", 0) or 0)
        unsafe_actions = int(resources.get("unsafe-actions", 0) or 0)
        security_evidence = str(resources.get("security-score-evidence") or "нет засчитанных безопасных реакций")
        roleplay_evidence = str(resources.get("roleplay-score-evidence") or "нет засчитанных ролевых реакций")
        communication_evidence = str(
            resources.get("communication-score-evidence") or "нет засчитанных профессиональных ответов"
        )
        return f"""Итоговый разбор.

Десятый игровой ход завершён. Рабочий день окончен, новых писем и сообщений сценарий больше не открывает.

Итоговый балл: {total_score} из 100.

Компоненты оценки:
- безопасность: {security_score} из 60;
- соответствие рабочей роли и границам полномочий: {roleplay_score} из 30;
- профессиональная коммуникация: {communication_score} из 10.

Наблюдаемые результаты:
- безопасных реакций на проверочные события: {safe_responses};
- ролевых профессиональных реакций: {role_responses};
- явно сформулированных деловых ответов: {professional_responses};
- небезопасных действий: {unsafe_actions}.

Основания начисления:
- безопасность: {security_evidence};
- соответствие должности: {roleplay_evidence};
- коммуникация: {communication_evidence}.

Рекомендации для повторного прохождения:
- формулируй решение как явное действие: что именно не делаешь, как независимо проверяешь запрос и куда сообщаешь;
- связывай рабочий ответ со своей должностью, границами полномочий, владельцем задачи и следующим шагом;
- отвечай адресату в деловом формате, даже если основное решение состоит в отказе или эскалации.

Для повторного прохождения создай новую партию или отдельную ветку."""
    score = max(0, min(100, int(resources.get("awareness-score", 0) or 0)))
    safe_escalations = int(resources.get("safe-escalations", 0) or 0)
    unsafe_actions = int(resources.get("unsafe-actions", 0) or 0)
    credential_exposure = int(resources.get("credential-exposure", 0) or 0)
    confidential_disclosures = int(resources.get("confidential-disclosures", 0) or 0)
    return f"""Итоговый разбор.

Десятый игровой ход завершён, новых событий и решений сценарий больше не открывает.

Итоговый балл: {score} из 100.

Зафиксированные результаты:
- корректных эскалаций: {safe_escalations};
- небезопасных действий: {unsafe_actions};
- случаев раскрытия учётных данных: {credential_exposure};
- случаев раскрытия конфиденциальной информации: {confidential_disclosures}.

Сильные стороны и ошибки следует разбирать только по явно заявленным действиям игрока. Для повторного прохождения создай новую партию или отдельную ветку."""


def awareness_opening_fallback(state: dict[str, Any]) -> str:
    if is_awareness_one_day_campaign(state):
        return awareness_one_day_safe_fallback(state)
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
    if awareness_final_summary(state):
        return "Итоговый разбор."
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
