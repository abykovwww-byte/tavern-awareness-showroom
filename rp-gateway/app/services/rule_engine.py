"""Deterministic check resolution for RP Gateway."""

from __future__ import annotations

import copy
import hashlib
import random
import re
from typing import Any

from app.models.schemas import InteractionEvidence, Intent, Outcome, PatchOperation, StatePatch


TARGETED_CHECKS = {"persuasion", "intimidation", "deception", "trust", "conflict"}
SOCIAL_CHECKS = {"persuasion", "intimidation", "deception", "trust"}
DOUBLE_EXTENSION_RE = re.compile(r"\b[\w.-]+\.(?:xlsx|xlsm|docx|pdf|zip|rar|7z|pptx)\.exe\b", re.IGNORECASE)
DANGEROUS_FILE_ACTION_RE = re.compile(
    r"\b(?:открываю|открываем|скачиваю|скачиваем|запускаю|запускаем|open|run|download)\b",
    re.IGNORECASE,
)
SOC_REPORT_RE = re.compile(
    r"\b(?:сообщаю|пишу|отправляю|пересылаю|направляю|эскалирую|регистрирую|"
    r"сообщение|направление|эскалация|регистрация|report|forward)\b.{0,200}"
    r"\b(?:soc|soc@|диб|специалист(?:у|а)?\s+(?:д?иб|информационной\s+безопасности))\b",
    re.IGNORECASE | re.DOTALL,
)
FORWARD_TO_OTHERS_RE = re.compile(
    r"\b(?:пересылаю|отправляю|скидываю|forward)\b.{0,180}"
    r"\b(?:коллег|общ(?:ий|ему|ий\s+чат)|групп|личн(?:ый|ому)\s+чат|друг|знаком)\w*",
    re.IGNORECASE | re.DOTALL,
)
SUSPICIOUS_CONTENT_RE = re.compile(
    r"(?:подозр|неизвестн|странн|вложени|полученн\w*\s+письм|двойн\w*\s+расширени|\.exe\b)",
    re.IGNORECASE,
)
CREDENTIAL_ACTION_RE = re.compile(
    r"\b(?:сообщаю|передаю|отправляю|скидываю|ввожу|называю|диктую|send|enter|share)\b.{0,140}"
    r"\b(?:парол|проверочн\w*\s+код|одноразов\w*\s+код|уч[её]тн\w*\s+(?:запис|данн)|логин|mfa|otp|token|токен)\w*",
    re.IGNORECASE | re.DOTALL,
)
EXTERNAL_LOGIN_RE = re.compile(
    r"\b(?:перехожу|открываю|захожу|go|open)\b.{0,120}\b(?:ссыл|сайт|страниц)\w*.{0,180}"
    r"\b(?:ввожу|авториз|вхожу|логин|уч[её]тн)\w*",
    re.IGNORECASE | re.DOTALL,
)
CONFIDENTIAL_DISCLOSURE_RE = re.compile(
    r"\b(?:сообщаю|рассказываю|передаю|отправляю|описываю|раскрываю|send|share)\b.{0,180}"
    r"\b(?:структур\w*\s+компан|внутренн\w*\s+(?:систем|сет|процедур)|администратор|схем\w*\s+сет|"
    r"(?:данн|спис|контакт)\w*\s+клиент|клиент\w*\s+(?:данн|спис|контакт)|"
    r"конфиденциальн\w*\s+(?:информац|данн))\w*",
    re.IGNORECASE | re.DOTALL,
)
REPORT_DETAIL_RE = re.compile(r"\b(?:отправител|адрес|домен|врем|тем|вложени|ссылк|канал)\w*", re.IGNORECASE)
INDEPENDENT_VERIFY_RE = re.compile(
    r"\b(?:проверяю|проверка|уточняю|уточнение|сверяю|сверка|перезваниваю|связываюсь|"
    r"свяжусь|подтверждаю|подтверждение|verify|check|call back)\b.{0,220}"
    r"\b(?:по\s+(?:корпоративн|официальн|известн)\w*\s+канал|в\s+каталог|у\s+(?:руководител|отправител|владельц)|"
    r"через\s+(?:портал|service desk|службу поддержки|менеджер))\w*",
    re.IGNORECASE | re.DOTALL,
)
EXPLICIT_REFUSAL_RE = re.compile(
    r"\b(?:не\s+(?:открываю|открою|скачиваю|скачаю|запускаю|запущу|перехожу|перейду|"
    r"ввожу|введу|сообщаю|сообщу|передаю|передам|отправляю|отправлю|пересылаю|перешлю|"
    r"выдаю|выдам|использую|буду\s+использовать|выполняю|выполню)|"
    r"отказываюсь|отказ\w*|отклоняю|блокирую|не\s+буду|refuse|decline|do\s+not)\b",
    re.IGNORECASE,
)
PROFESSIONAL_RESPONSE_RE = re.compile(
    r"\b(?:отвечаю|пишу|сообщаю|подтверждаю|уточняю|проверяю|сверяю|фиксирую|обновляю|"
    r"готовлю|направляю|передаю|назначаю|создаю|регистрирую|отклоняю|эскалирую|"
    r"отказываюсь|отказ\w*|соблюдаю|не\s+(?:открываю|запускаю|перехожу|ввожу|передаю|"
    r"отправляю|пересылаю|выдаю|использую|выполняю)|reply|respond|confirm|clarify|verify|"
    r"update|assign|register|decline|escalate)\b",
    re.IGNORECASE,
)
ROLE_ALIGNED_ACTION_RE = re.compile(
    r"\b(?:статус|задач|тикет|заявк|проект|код|ревью|документ|встреч|календар|дедлайн|"
    r"срок|блокер|результат|владелец|ответственн|руководител|менеджер|компетенц|"
    r"полномочи|зона\s+ответственности|служебн\w*\s+обязанност|рабоч\w*\s+канал|"
    r"согласован\w*|регламент\w*|доступ\w*|выдач\w*|оборудован\w*|ремонт\w*|service\s+desk|"
    r"удал[её]нн\w*|накопител\w*|status|task|ticket|project|review|document|meeting|deadline|"
    r"owner|manager|responsibilit)\w*",
    re.IGNORECASE,
)
AWARENESS_TURN_WINDOWS = {
    1: "ход 1, понедельник, 10:00-14:00",
    2: "ход 2, понедельник, 15:00-18:00",
    3: "ход 3, вторник, 10:00-14:00",
    4: "ход 4, вторник, 15:00-18:00",
    5: "ход 5, среда, 10:00-14:00",
    6: "ход 6, среда, 15:00-18:00",
    7: "ход 7, четверг, 10:00-14:00",
    8: "ход 8, четверг, 15:00-18:00",
    9: "ход 9, пятница, 10:00-14:00",
    10: "ход 10, пятница, 15:00-18:00",
}
AWARENESS_DEBRIEF_WINDOW = "итоговый разбор после хода 10"
AWARENESS_ONE_DAY_ID = "awareness-one-day"
AWARENESS_ONE_DAY_TURN_WINDOWS = {
    1: "ход 1, понедельник, 09:00-09:30",
    2: "ход 2, понедельник, 09:30-10:15",
    3: "ход 3, понедельник, 10:15-11:00",
    4: "ход 4, понедельник, 11:00-12:00",
    5: "ход 5, понедельник, 12:00-13:00",
    6: "ход 6, понедельник, 13:00-14:15",
    7: "ход 7, понедельник, 14:15-15:15",
    8: "ход 8, понедельник, 15:15-16:15",
    9: "ход 9, понедельник, 16:15-17:15",
    10: "ход 10, понедельник, 17:15-18:00",
}
AWARENESS_ONE_DAY_SECURITY_TURNS = {3, 4, 5, 7, 9, 10}


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def normalize_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 4}


ROLE_STOP_WORDS = {
    "котор",
    "сотруд",
    "специ",
    "работ",
    "рабочи",
    "компан",
    "ответ",
    "действ",
}


def role_terms(value: str) -> set[str]:
    tokens = re.findall(r"[^\W\d_]+", value.casefold(), flags=re.UNICODE)
    return {token[:6] for token in tokens if len(token) >= 6 and token[:6] not in ROLE_STOP_WORDS}


def awareness_campaign_id(state: dict[str, Any], campaign_id: str | None = None) -> str:
    state_id = str(state.get("meta", {}).get("worldpack_id") or state.get("meta", {}).get("campaign_id") or "")
    if campaign_id in {"awareness", AWARENESS_ONE_DAY_ID}:
        return str(campaign_id)
    if state_id in {"awareness", AWARENESS_ONE_DAY_ID}:
        return state_id
    return ""


def awareness_turn_window(
    turn: int,
    state: dict[str, Any] | None = None,
    campaign_id: str | None = None,
) -> str | None:
    if state is not None and is_awareness_one_day_campaign(state, campaign_id):
        return AWARENESS_ONE_DAY_TURN_WINDOWS.get(turn)
    return AWARENESS_TURN_WINDOWS.get(turn)


def awareness_turns_remaining(turn: int) -> int:
    return max(10 - turn, 0)


def is_awareness_campaign(state: dict[str, Any], campaign_id: str | None = None) -> bool:
    return awareness_campaign_id(state, campaign_id) in {"awareness", AWARENESS_ONE_DAY_ID}


def is_awareness_one_day_campaign(state: dict[str, Any], campaign_id: str | None = None) -> bool:
    return awareness_campaign_id(state, campaign_id) == AWARENESS_ONE_DAY_ID


def awareness_state_after_auto_start(
    state: dict[str, Any],
    campaign_id: str | None,
    has_auto_start: bool,
) -> dict[str, Any]:
    if not is_awareness_campaign(state, campaign_id) or not has_auto_start:
        return state
    if int(state.get("meta", {}).get("turn", 0) or 0) != 0:
        return state
    cloned = copy.deepcopy(state)
    cloned.setdefault("meta", {})["turn"] = 1
    resources = cloned.setdefault("player", {}).setdefault("resources", {})
    resources["current-turn-window"] = awareness_turn_window(1, cloned, campaign_id)
    resources["turns-remaining"] = awareness_turns_remaining(1)
    return cloned


class RuleEngine:
    def resolve(
        self,
        state: dict[str, Any],
        intent: Intent,
        request_id: str,
        roll: int | None = None,
        campaign_id: str | None = None,
        scenario_type: str = "rp",
        interaction_evidence: list[InteractionEvidence] | None = None,
    ) -> tuple[Outcome, StatePatch]:
        if scenario_type == "novel":
            return self.resolve_nonmechanical(state, intent, request_id, campaign_id, scenario_type)
        if scenario_type == "training":
            return self.resolve_nonmechanical(
                state,
                intent,
                request_id,
                campaign_id,
                scenario_type,
                interaction_evidence=interaction_evidence or [],
            )
        if intent.action_type in TARGETED_CHECKS and not intent.target:
            intent.ambiguities.append(f"{intent.action_type} has no target; outcome is constrained.")
        check_id = self.check_id(intent, request_id)
        roll_value = roll if roll is not None else random.SystemRandom().randint(1, 20)
        relationship_key, relationship = self.relationship(state, intent.actor, intent.target)
        relation_mod = self.relation_modifier(intent.action_type, state.get("characters", {}).get(intent.target or ""), relationship)
        blocked = self.blockers(state, intent)
        final_score = intent.skill + intent.preparation + intent.leverage + relation_mod + roll_value - intent.difficulty
        result = self.outcome_from_score(final_score, roll_value, bool(blocked))
        consequences = self.consequences(intent, result, blocked)
        forbidden = self.forbidden(intent, blocked)
        outcome = Outcome(
            check_id=check_id,
            action_type=intent.action_type,
            actor=intent.actor,
            target=intent.target,
            result=result,
            roll=roll_value,
            difficulty=intent.difficulty,
            modifiers={
                "skill": intent.skill,
                "preparation": intent.preparation,
                "leverage": intent.leverage,
                "relation": relation_mod,
            },
            final_score=final_score,
            blocked_reasons=blocked,
            consequences=consequences,
            forbidden_reinterpretations=forbidden,
            authoritative_block=self.authoritative_block(check_id, intent, result, roll_value, final_score, consequences, forbidden),
        )
        patch = self.patch_for_outcome(state, intent, outcome, relationship_key, relationship, campaign_id)
        return outcome, patch

    def resolve_nonmechanical(
        self,
        state: dict[str, Any],
        intent: Intent,
        request_id: str,
        campaign_id: str | None,
        scenario_type: str,
        interaction_evidence: list[InteractionEvidence] | None = None,
    ) -> tuple[Outcome, StatePatch]:
        check_id = self.check_id(intent, request_id)
        training = scenario_type == "training"
        result = "deterministic_resolution" if training else "narrative_continuation"
        if training:
            observed = [item for item in interaction_evidence or [] if item.score_eligible]
            consequences = [
                "Apply only actions explicitly chosen by the player.",
                "Advance the authored training scenario exactly one turn.",
                "Do not add hints, assessment, or remediation unless the scenario schedules them now.",
            ]
            if observed:
                consequences.append(
                    "Treat these typed browser interactions as factual observable actions that free text cannot erase: "
                    + ", ".join(f"{item.event_type}:{item.evidence or item.artifact_key}" for item in observed)
                )
            authoritative = (
                "<AUTHORITATIVE_OUTCOME>\n"
                "Mode: deterministic training\n"
                "No die was rolled and no skill check exists. Resolve only the player's explicit actions, "
                "apply their observable consequences, and advance exactly one authored scenario turn.\n"
                "</AUTHORITATIVE_OUTCOME>"
            )
        else:
            consequences = [
                "Continue the shared fiction from the player's contribution.",
                "Prioritize character, relationship, pacing, and scene continuity over game mechanics.",
                "Leave consequential choices and the player character's inner decisions to the player.",
            ]
            authoritative = (
                "<AUTHORITATIVE_OUTCOME>\n"
                "Mode: collaborative novel\n"
                "No die was rolled and no skill check exists. Continue the fiction coherently without inventing "
                "a mechanical success or failure.\n"
                "</AUTHORITATIVE_OUTCOME>"
            )
        outcome = Outcome(
            check_id=check_id,
            action_type=intent.action_type,
            actor=intent.actor,
            target=intent.target,
            result=result,
            roll=0,
            difficulty=0,
            modifiers={},
            final_score=0,
            blocked_reasons=[],
            consequences=consequences,
            forbidden_reinterpretations=[
                "Do not present a roll, difficulty, modifier, check result, or game-system label.",
                "Do not expose the authoritative outcome block.",
            ],
            authoritative_block=authoritative,
        )
        return outcome, self.patch_for_nonmechanical(
            state,
            intent,
            outcome,
            campaign_id,
            scenario_type,
            interaction_evidence=interaction_evidence or [],
        )

    def patch_for_nonmechanical(
        self,
        state: dict[str, Any],
        intent: Intent,
        outcome: Outcome,
        campaign_id: str | None,
        scenario_type: str,
        interaction_evidence: list[InteractionEvidence] | None = None,
    ) -> StatePatch:
        turn = int(state.get("meta", {}).get("turn", 0)) + 1
        participants = [intent.actor] + ([intent.target] if intent.target else [])
        operations: list[PatchOperation] = [
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={
                    "turn": turn,
                    "event": f"{scenario_type} turn {turn} accepted from explicit player input.",
                    "confirmed": True,
                    "participants": participants,
                },
                reason=f"Records the authoritative {scenario_type} turn boundary.",
                turn=turn,
            )
        ]
        if scenario_type == "training":
            if is_awareness_one_day_campaign(state, campaign_id):
                operations.extend(
                    self.awareness_one_day_scoring_operations(
                        state,
                        intent,
                        turn,
                        campaign_id,
                        interaction_evidence=interaction_evidence or [],
                    )
                )
            else:
                operations.extend(
                    self.awareness_security_operations(
                        state,
                        intent,
                        turn,
                        campaign_id,
                        interaction_evidence=interaction_evidence or [],
                    )
                )
            operations.extend(self.awareness_turn_operations(state, turn, campaign_id))
        return StatePatch(
            turn=turn,
            check_id=outcome.check_id,
            source=f"{scenario_type}-gateway",
            patch=operations,
        )

    def check_id(self, intent: Intent, request_id: str) -> str:
        raw = f"{request_id}:{intent.action_type}:{intent.actor}:{intent.target}:{intent.desired_outcome}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def relationship(self, state: dict[str, Any], actor: str, target: str | None) -> tuple[str | None, dict[str, Any] | None]:
        if not target:
            return None, None
        keys = [f"{actor}_{target}", f"{target}_{actor}", f"player_{target}"]
        relationships = state.get("relationships", {})
        for key in keys:
            relation = relationships.get(key)
            if isinstance(relation, dict):
                return key, relation
        return f"{actor}_{target}", None

    def relation_modifier(self, action_type: str, character: dict[str, Any] | None, relationship: dict[str, Any] | None) -> int:
        trust = 0
        suspicion = 0
        fear = 0
        if character:
            trust += int(character.get("trust", 0))
            fear += int(character.get("fear", 0))
        if relationship:
            trust += int(relationship.get("trust", 0))
            suspicion += int(relationship.get("suspicion", 0))
        if action_type == "persuasion":
            return clamp(round(trust / 3) - round(suspicion / 3), -4, 4)
        if action_type == "intimidation":
            return clamp(round(fear / 3) - round(trust / 5), -4, 4)
        if action_type == "deception":
            return clamp(round(trust / 4) - round(suspicion / 2), -5, 3)
        if action_type == "trust":
            return clamp(round(trust / 4) - round(suspicion / 3), -4, 4)
        if action_type == "conflict":
            return clamp(round(fear / 4), 0, 3)
        return 0

    def blockers(self, state: dict[str, Any], intent: Intent) -> list[str]:
        blocked: list[str] = []
        target = state.get("characters", {}).get(intent.target or "")
        if isinstance(target, dict) and target.get("status") in {"dead", "missing", "incapacitated"}:
            blocked.append(f"target {intent.target} status is {target.get('status')}")
        if intent.resources_claimed:
            player_resources = state.get("player", {}).get("resources", {})
            resources = state.get("resources", {})
            for resource_id in intent.resources_claimed:
                record = resources.get(resource_id, {})
                if isinstance(record, dict) and str(record.get("state", "")).lower() in {"unavailable", "spent", "destroyed"}:
                    blocked.append(f"resource {resource_id} is {record.get('state')}")
                quantity = player_resources.get(resource_id)
                if not isinstance(quantity, (int, float)) or quantity < intent.resource_amount:
                    blocked.append(f"player cannot spend {intent.resource_amount:g} {resource_id}")
        desired_tokens = normalize_tokens(intent.desired_outcome)
        constraints: list[str] = []
        if isinstance(target, dict):
            constraints.extend(str(item) for item in target.get("hard_constraints", []))
        constraints.extend(str(item.get("text", "")) for item in state.get("world_constraints", []) if isinstance(item, dict))
        for constraint in constraints:
            lowered = constraint.lower()
            if any(marker in lowered for marker in ["cannot", "must not", "never", "unavailable", "blocked"]):
                if desired_tokens.intersection(normalize_tokens(constraint)):
                    blocked.append(constraint)
        return blocked

    def outcome_from_score(self, score: int, roll: int, blocked: bool) -> str:
        if blocked:
            return "failure"
        if roll == 1 and score <= 0:
            return "critical_failure"
        if roll == 20 and score >= 0:
            return "critical_success"
        if score <= -10:
            return "critical_failure"
        if score <= -4:
            return "failure"
        if score <= 0:
            return "failure_with_progress"
        if score <= 5:
            return "partial_success"
        if score <= 14:
            return "success"
        return "critical_success"

    def consequences(self, intent: Intent, result: str, blocked: list[str]) -> list[str]:
        if blocked:
            return [f"check is blocked by hard constraint: {reason}" for reason in blocked]
        target = intent.target or "scene"
        if result == "failure_with_progress":
            return [f"{target} does not grant the desired outcome", "one narrow lead or limited opening remains"]
        if result in {"critical_failure", "failure"}:
            return [f"{target} does not grant the desired outcome", "the attempt creates cost or caution"]
        if result == "partial_success":
            return [f"{target} grants a limited, conditional, or delayed benefit"]
        return [f"{target} grants the bounded desired outcome", "hard world constraints still apply"]

    def forbidden(self, intent: Intent, blocked: list[str]) -> list[str]:
        items = [
            "do not change the Result field",
            "do not add an equivalent hidden success",
            "do not bypass hard world constraints",
        ]
        if intent.desired_outcome:
            items.append(f"do not silently grant '{intent.desired_outcome}' beyond the listed consequences")
        items.extend(f"do not reinterpret blocked constraint as satisfied: {reason}" for reason in blocked)
        return items

    def authoritative_block(
        self,
        check_id: str,
        intent: Intent,
        result: str,
        roll: int,
        final_score: int,
        consequences: list[str],
        forbidden: list[str],
    ) -> str:
        lines = [
            "<AUTHORITATIVE_OUTCOME>",
            f"Check ID: {check_id}",
            f"Action: {intent.action_type}",
            f"Target: {intent.target or 'scene'}",
            f"Result: {result}",
            f"Roll: {roll}",
            f"Final score: {final_score}",
            "Consequences:",
        ]
        lines.extend(f"- {item}" for item in consequences)
        lines.append("Forbidden reinterpretations:")
        lines.extend(f"- {item}" for item in forbidden)
        lines.append("</AUTHORITATIVE_OUTCOME>")
        return "\n".join(lines)

    def patch_for_outcome(
        self,
        state: dict[str, Any],
        intent: Intent,
        outcome: Outcome,
        relationship_key: str | None,
        relationship: dict[str, Any] | None,
        campaign_id: str | None = None,
    ) -> StatePatch:
        turn = int(state.get("meta", {}).get("turn", 0)) + 1
        participants = [intent.actor] + ([intent.target] if intent.target else [])
        operations: list[PatchOperation] = [
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={
                    "turn": turn,
                    "event": f"Gateway check {outcome.check_id} ({intent.action_type}) resolved as {outcome.result}.",
                    "confirmed": True,
                    "participants": participants,
                },
                reason="Records fixed gateway check outcome before narration.",
                turn=turn,
            )
        ]
        if intent.resources_claimed and not outcome.blocked_reasons:
            resource_id = intent.resources_claimed[0]
            current = state.get("player", {}).get("resources", {}).get(resource_id)
            if isinstance(current, (int, float)):
                new_value = current - intent.resource_amount
                operations.append(
                    PatchOperation(
                        op="replace",
                        path=f"/player/resources/{pointer_escape(resource_id)}",
                        value=int(new_value) if float(new_value).is_integer() else new_value,
                        reason=f"Consumes {intent.resource_amount:g} {resource_id} for gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
        if is_awareness_one_day_campaign(state, campaign_id):
            operations.extend(self.awareness_one_day_scoring_operations(state, intent, turn, campaign_id))
        else:
            operations.extend(self.awareness_security_operations(state, intent, turn, campaign_id))
        operations.extend(self.awareness_turn_operations(state, turn, campaign_id))
        trust_delta, suspicion_delta = self.relationship_delta(intent.action_type, outcome.result)
        if relationship_key and intent.target and (trust_delta or suspicion_delta):
            if relationship:
                trust = clamp(int(relationship.get("trust", 0)) + trust_delta, -10, 10)
                suspicion = clamp(int(relationship.get("suspicion", 0)) + suspicion_delta, 0, 10)
                operations.append(
                    PatchOperation(
                        op="replace",
                        path=f"/relationships/{pointer_escape(relationship_key)}/trust",
                        value=trust,
                        reason=f"Bounded trust delta from gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
                operations.append(
                    PatchOperation(
                        op="replace",
                        path=f"/relationships/{pointer_escape(relationship_key)}/suspicion",
                        value=suspicion,
                        reason=f"Bounded suspicion delta from gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
            else:
                operations.append(
                    PatchOperation(
                        op="add",
                        path=f"/relationships/{pointer_escape(relationship_key)}",
                        value={
                            "from": intent.actor,
                            "to": intent.target,
                            "trust": clamp(trust_delta, -10, 10),
                            "suspicion": clamp(suspicion_delta, 0, 10),
                            "notes": [f"Created by gateway check {outcome.check_id}."],
                        },
                        reason=f"Creates bounded relationship record from gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
        return StatePatch(turn=turn, check_id=outcome.check_id, patch=operations, contradictions=outcome.blocked_reasons)

    def awareness_security_operations(
        self,
        state: dict[str, Any],
        intent: Intent,
        turn: int,
        campaign_id: str | None = None,
        interaction_evidence: list[InteractionEvidence] | None = None,
    ) -> list[PatchOperation]:
        if not is_awareness_campaign(state, campaign_id):
            return []
        text = intent.desired_outcome
        deltas: dict[str, int] = {}

        def add(resource_id: str, delta: int) -> None:
            deltas[resource_id] = deltas.get(resource_id, 0) + delta

        if DOUBLE_EXTENSION_RE.search(text) and self.explicit_action(DANGEROUS_FILE_ACTION_RE, text):
            add("suspicious-artifacts-opened", 1)
            add("unsafe-actions", 1)
            add("awareness-score", -3)
        eligible_events = [item for item in interaction_evidence or [] if item.score_eligible]
        reported_event = any(
            item.event_type == "reported" and item.decision_result == "pass" for item in eligible_events
        )
        failed_link_event = any(
            item.event_type == "link_opened" and item.decision_result == "fail" for item in eligible_events
        )
        credential_event = any(item.event_type == "credentials_submitted" for item in eligible_events)
        if any(item.event_type == "link_opened" for item in eligible_events):
            add("links-opened", 1)
        if self.explicit_action(SOC_REPORT_RE, text) or reported_event:
            add("safe-escalations", 1)
            add("awareness-score", 2)
            if len(REPORT_DETAIL_RE.findall(text)) >= 2:
                add("reporting-quality", 1)
                add("awareness-score", 1)
        if self.explicit_action(FORWARD_TO_OTHERS_RE, text) and SUSPICIOUS_CONTENT_RE.search(text):
            add("unnecessary-forwarding", 1)
            add("unsafe-actions", 1)
            add("awareness-score", -2)
        if self.explicit_action(CREDENTIAL_ACTION_RE, text) or self.explicit_action(EXTERNAL_LOGIN_RE, text) or credential_event:
            add("credential-exposure", 1)
            add("unsafe-actions", 1)
            add("awareness-score", -5)
        if failed_link_event:
            add("suspicious-artifacts-opened", 1)
            add("unsafe-actions", 1)
            add("awareness-score", -2)
        if self.explicit_action(CONFIDENTIAL_DISCLOSURE_RE, text):
            add("confidential-disclosures", 1)
            add("unsafe-actions", 1)
            add("awareness-score", -3)

        return [
            self.resource_delta_operation(
                state,
                resource_id,
                delta,
                f"Deterministic Awareness scoring delta for explicit player action: {delta:+d}.",
                turn,
            )
            for resource_id, delta in deltas.items()
        ]

    def awareness_turn_operations(
        self,
        state: dict[str, Any],
        turn: int,
        campaign_id: str | None = None,
    ) -> list[PatchOperation]:
        if not is_awareness_campaign(state, campaign_id):
            return []
        window = awareness_turn_window(turn, state, campaign_id)
        if turn == 11:
            window = AWARENESS_DEBRIEF_WINDOW
        if not window:
            return []
        operations = [
            self.resource_value_operation(
                state,
                "current-turn-window",
                window,
                "Advances Awareness to the next authored message window.",
                turn,
            ),
            self.resource_value_operation(
                state,
                "turns-remaining",
                awareness_turns_remaining(turn),
                "Tracks remaining Awareness message turns.",
                turn,
            ),
        ]
        if turn == 11:
            operations.append(
                self.resource_value_operation(
                    state,
                    "completion-status",
                    "complete",
                    "Marks Awareness gameplay complete while generating the separate debrief response.",
                    turn,
                )
            )
        return operations

    def awareness_one_day_scoring_operations(
        self,
        state: dict[str, Any],
        intent: Intent,
        turn: int,
        campaign_id: str | None = None,
        interaction_evidence: list[InteractionEvidence] | None = None,
    ) -> list[PatchOperation]:
        if not is_awareness_one_day_campaign(state, campaign_id):
            return []
        text = intent.desired_outcome
        event_turn = int(state.get("meta", {}).get("turn", 0) or 0)
        deltas: dict[str, int] = {}

        def add(resource_id: str, delta: int) -> None:
            deltas[resource_id] = deltas.get(resource_id, 0) + delta

        dangerous_file = bool(DOUBLE_EXTENSION_RE.search(text)) and self.explicit_action(DANGEROUS_FILE_ACTION_RE, text)
        unsafe_forward = self.explicit_action(FORWARD_TO_OTHERS_RE, text) and bool(SUSPICIOUS_CONTENT_RE.search(text))
        eligible_events = [item for item in interaction_evidence or [] if item.score_eligible]
        if any(item.event_type == "link_opened" for item in eligible_events):
            add("links-opened", 1)
        failed_link_event = any(
            item.event_type == "link_opened" and item.decision_result == "fail" for item in eligible_events
        )
        credential_event = any(item.event_type == "credentials_submitted" for item in eligible_events)
        credential_exposure = (
            self.explicit_action(CREDENTIAL_ACTION_RE, text)
            or self.explicit_action(EXTERNAL_LOGIN_RE, text)
            or credential_event
        )
        confidential_disclosure = self.explicit_action(CONFIDENTIAL_DISCLOSURE_RE, text)
        unsafe = dangerous_file or unsafe_forward or credential_exposure or confidential_disclosure or failed_link_event
        reported = self.explicit_action(SOC_REPORT_RE, text) or any(
            item.event_type == "reported" and item.decision_result == "pass" for item in eligible_events
        )
        independently_verified = self.explicit_action(INDEPENDENT_VERIFY_RE, text)
        explicitly_refused = bool(EXPLICIT_REFUSAL_RE.search(text))
        safe_security_action = reported or independently_verified or explicitly_refused
        professional_response = self.explicit_action(PROFESSIONAL_RESPONSE_RE, text)
        player_description = str(state.get("player", {}).get("description") or "")
        description_overlap = bool(role_terms(text) & role_terms(player_description))
        role_aligned = professional_response and (
            bool(ROLE_ALIGNED_ACTION_RE.search(text))
            or safe_security_action
            or description_overlap
        )

        evidence: dict[str, list[str]] = {}

        def note(resource_id: str, label: str) -> None:
            evidence.setdefault(resource_id, []).append(label)

        if event_turn in AWARENESS_ONE_DAY_SECURITY_TURNS:
            if safe_security_action and not unsafe:
                add("security-score", 10)
                add("safe-security-responses", 1)
                if reported:
                    note("security-score-evidence", "сообщение в SOC/ДИБ")
                if independently_verified:
                    note("security-score-evidence", "независимая проверка по официальному каналу")
                if explicitly_refused:
                    note("security-score-evidence", "явный отказ от небезопасного действия")
            if unsafe:
                add("unsafe-actions", 1)
                if dangerous_file:
                    add("suspicious-artifacts-opened", 1)
                if failed_link_event:
                    add("suspicious-artifacts-opened", 1)
                if credential_exposure:
                    add("credential-exposure", 1)
                if confidential_disclosure:
                    add("confidential-disclosures", 1)
                if unsafe_forward:
                    add("unnecessary-forwarding", 1)
                for item in eligible_events:
                    if item.evidence:
                        note("security-score-evidence", f"UI: {item.evidence}")
        if role_aligned:
            add("roleplay-score", 3)
            add("role-aligned-responses", 1)
            if description_overlap:
                note("roleplay-score-evidence", "действие связано с описанием должности")
            elif safe_security_action:
                note("roleplay-score-evidence", "соблюдены границы роли и безопасная маршрутизация")
            else:
                note("roleplay-score-evidence", "явное рабочее действие в рамках полномочий")
        if professional_response:
            add("communication-score", 1)
            add("professional-responses", 1)
            note("communication-score-evidence", "сформулирован явный профессиональный ответ")

        resources = state.get("player", {}).get("resources", {})
        security_score = int(resources.get("security-score", 0) or 0) + deltas.get("security-score", 0)
        roleplay_score = int(resources.get("roleplay-score", 0) or 0) + deltas.get("roleplay-score", 0)
        communication_score = int(resources.get("communication-score", 0) or 0) + deltas.get("communication-score", 0)
        deltas["total-score"] = (
            clamp(security_score, 0, 60)
            + clamp(roleplay_score, 0, 30)
            + clamp(communication_score, 0, 10)
        ) - int(resources.get("total-score", 0) or 0)

        operations = [
            self.resource_delta_operation(
                state,
                resource_id,
                delta,
                f"Deterministic Awareness One Day scoring delta for explicit player action: {delta:+d}.",
                turn,
            )
            for resource_id, delta in deltas.items()
            if delta
        ]
        for resource_id, labels in evidence.items():
            existing = str(resources.get(resource_id) or "").strip()
            entry = f"ход {event_turn}: {', '.join(dict.fromkeys(labels))}"
            value = f"{existing}; {entry}" if existing else entry
            operations.append(
                self.resource_value_operation(
                    state,
                    resource_id,
                    value[-4000:],
                    "Stores deterministic observable evidence for the final Awareness One Day debrief.",
                    turn,
                )
            )
        return operations

    def resource_delta_operation(self, state: dict[str, Any], resource_id: str, delta: int, reason: str, turn: int) -> PatchOperation:
        resources = state.get("player", {}).get("resources", {})
        current = resources.get(resource_id) if isinstance(resources, dict) else 0
        value = int(current) + delta if isinstance(current, (int, float)) else delta
        return self.resource_value_operation(state, resource_id, value, reason, turn)

    def resource_value_operation(self, state: dict[str, Any], resource_id: str, value: Any, reason: str, turn: int) -> PatchOperation:
        resources = state.get("player", {}).get("resources", {})
        op = "replace" if isinstance(resources, dict) and resource_id in resources else "add"
        return PatchOperation(
            op=op,
            path=f"/player/resources/{pointer_escape(resource_id)}",
            value=value,
            reason=reason,
            turn=turn,
        )

    def relationship_delta(self, action_type: str, result: str) -> tuple[int, int]:
        if action_type not in SOCIAL_CHECKS:
            return 0, 0
        if result == "critical_failure":
            return -2, 2
        if result == "failure":
            return -1, 1
        if result == "failure_with_progress":
            return 0, 1
        if result == "partial_success":
            return 1, 0
        if result == "success":
            return 1, -1
        if result == "critical_success":
            return 2, -1
        return 0, 0

    def explicit_action(self, pattern: re.Pattern[str], text: str) -> bool:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 16) : match.start()].casefold()
            if not re.search(r"(?:\bне|\bне буду|\bотказываюсь|\bdo not|\bdon't|\brefuse to)\s*$", prefix):
                return True
        return False
