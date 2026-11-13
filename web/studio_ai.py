"""Optional local-only intent extraction. This module has no mutation tools."""
import json
import os
import re
import urllib.error
import urllib.request

from . import catalog, studio_service
from setlist.planning import MAX_PHASES, MAX_TRACKS, PHASES, validate_policy

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["search", "build"]},
        "name": {"type": "string"},
        "rule": {"type": "object", "additionalProperties": False, "properties": {
            "q": {"type": "string"}, "artist": {"type": "string"}, "genre": {"type": "string"},
            "bpm_min": {"type": "number"}, "bpm_max": {"type": "number"},
            "energy_min": {"type": "number"}, "energy_max": {"type": "number"},
            "clean_only": {"type": "boolean"}, "never_played": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "sort": {"type": "string", "enum": sorted(studio_service.SORTS)},
        }},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "name", "rule", "unsupported"],
}
PLAN_FIELDS = {
    "target_minutes": {"type": "number", "exclusiveMinimum": 0, "maximum": 1440},
    "tolerance_minutes": {"type": "number", "minimum": 0, "maximum": 1440},
    "bpm_min": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
    "bpm_max": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
    "clean_only": {"type": "boolean"},
    "energy_target": {"type": "number", "minimum": 0, "maximum": 1},
}
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        **SCHEMA["properties"], "action": {"type": "string", "enum": ["plan"]},
        "policy": {
            "type": "object", "additionalProperties": False,
            "properties": {
                **PLAN_FIELDS, "use_feedback": {"type": "boolean"},
                "max_tracks": {"type": "integer", "minimum": 1, "maximum": MAX_TRACKS},
                "phases": {"type": "array", "minItems": 1, "maxItems": MAX_PHASES, "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {**PLAN_FIELDS, "phase": {"type": "string", "enum": sorted(PHASES)},
                                   "label": {"type": "string", "minLength": 1, "maxLength": 80}},
                    "required": ["phase", "target_minutes", "tolerance_minutes"],
                }},
            },
        },
    },
    "required": ["action", "name", "rule", "policy", "unsupported"],
}


def sparse(value):
    if isinstance(value, dict):
        return {key: sparse(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [sparse(item) for item in value]
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Local AI returned duplicate JSON keys; no action was taken.")
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError("Local AI returned a nonfinite JSON number; no action was taken.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("The local AI runtime redirected the request; no redirected destination was contacted.")


def status(model=None):
    model = os.environ.get("AUTOCUE_STUDIO_MODEL", "").strip() if model is None else model
    return {"configured": bool(model), "model": model, "endpoint": "http://127.0.0.1:11434/api/chat",
            "note": "Experimental, review-only local Ollama model; not production-quality certified. "
                    "No model is downloaded and this app contacts loopback only. "
                    "Use local weights with runtime cloud routing disabled; third-party runtime configuration is outside this app. "
                    "One-shot translation sends your prompt and extraction instructions only; "
                    "initial timed plans use two separate calls without catalog data. "
                    "Other conversations may send prior planning policy and up to three explicitly opted-in "
                    "metadata examples on initial/tool-change search/build/organize drafts. "
                    "Known explicit release-year/date phrases are blocked before inference; this is not a general language capability guarantee. "
                    "AI output always needs filter/planning-policy review and explicit save approval."}


def _validate_common(value, actions, extra=()):
    studio_service.exact_fields(value, {"action", "name", "rule", "unsupported"} | set(extra))
    if value["action"] not in actions:
        raise ValueError("AI action is not supported in this workflow.")
    catalog.text(value["name"], "AI crate name", required=True)
    if not isinstance(value["unsupported"], list) or any(not isinstance(item, str) for item in value["unsupported"]):
        raise ValueError("AI must report unsupported requirements explicitly.")
    if value["unsupported"]:
        raise ValueError("The request includes requirements this assistant cannot enforce: " +
                         "; ".join(value["unsupported"])[:2000])
    if not isinstance(value["rule"], dict) or set(value["rule"]) - set(SCHEMA["properties"]["rule"]["properties"]):
        raise ValueError("AI may only use documented metadata filters, not invent source file IDs.")
    return {**value, "rule": studio_service.validate_rule(sparse(value["rule"]))}


def validate_intent(value):
    return _validate_common(value, ("search", "build"))


def validate_plan_intent(value):
    value = _validate_common(value, ("plan",), ("policy",))
    policy = value["policy"]
    allowed = set(PLAN_SCHEMA["properties"]["policy"]["properties"])
    if not isinstance(policy, dict) or set(policy) - allowed:
        raise ValueError("AI planning cannot invent file IDs, pins, exclusions or event requests.")
    policy = sparse(policy)
    if "phases" in policy:
        phases = policy["phases"]
        allowed_phase = set(PLAN_FIELDS) | {"phase", "label"}
        if not isinstance(phases, list) or any(
                not isinstance(phase, dict) or set(phase) - allowed_phase for phase in phases):
            raise ValueError("AI phases may only describe timing, energy and metadata bounds, not file IDs.")
    return {**value, "policy": validate_policy({"tolerance_minutes": 3, "max_tracks": 20, **policy})}


class UnsupportedRequest(ValueError):
    """A known unsupported request rejected before inference."""


def check_request(message):
    catalog.text(message, "prompt", required=True)
    if len(message) > 2000:
        raise ValueError("The Studio AI prompt limit is 2,000 characters.")
    release_filter = re.search(
        r"\b(?:released?\s+(?:in|from|before|after|between|since|during)\s+(?:the\s+)?\d{4}s?\b"
        r"|release[\s-]+(?:year|date)\s*(?:(?:is|of|in|before|after|between)\s*|[=:]\s*)?\d{4}\b"
        r"|(?:songs|tracks|music)\s+from\s+(?:the\s+)?\d{3}0s\b)",
        message, re.IGNORECASE)
    if release_filter:
        raise UnsupportedRequest("The request includes requirements this assistant cannot enforce: "
                                 f"release-year/date filtering ({release_filter.group()}). "
                                 "No AI request was sent. Remove the release-date requirement or use supported metadata filters.")


def _interpret(message, schema, validator, instruction, *, model=None, include_schema=True):
    check_request(message)
    configured = status(model)
    if not configured["configured"]:
        raise ValueError("Local AI is not configured. Save an already installed Ollama model tag in Studio settings "
                         "(or set AUTOCUE_STUDIO_MODEL) and run the local runtime, or use the filter builder. No request was sent.")
    if configured["model"].casefold().endswith((":cloud", "-cloud")):
        raise ValueError("Cloud-tagged models are not enabled in the local Studio adapter. Select locally installed weights.")
    payload = {
        "model": configured["model"], "stream": False, "format": "json",
        "options": {"temperature": 0, "num_predict": 2000, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": instruction +
                "\nReturn only one JSON object. Fields use native JSON numbers/booleans, not strings. "
                "Omit optional fields that the NEW message does not request. " +
                ("Output schema: " + json.dumps(schema, separators=(",", ":")) if include_schema else "")},
            {"role": "user", "content": message},
        ],
    }
    if not include_schema:
        payload["messages"][0]["content"] = instruction
        payload["options"].update({"num_ctx": 4096, "repeat_penalty": 1.1})
    if set(schema.get("properties", {})) == {"action"}:
        payload["format"] = schema
        payload["messages"][0]["content"] = instruction
    if configured["model"].split(":", 1)[0] == "qwen3":
        payload["think"] = False
    request = urllib.request.Request(configured["endpoint"], data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=90) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("Local AI response exceeds 64 KiB.")
        envelope = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_number)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("message"), dict):
            raise ValueError("Local AI returned an invalid response envelope; no action was taken.")
        if envelope.get("done") is not True:
            raise ValueError("Local AI returned an incomplete response.")
        intent = validator(json.loads(envelope["message"]["content"], object_pairs_hook=_unique_object,
                                      parse_constant=_invalid_number))
    except urllib.error.HTTPError as error:
        with error:
            detail = error.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError("Local AI is unavailable; your library was not changed. Use the filter builder. "
                           f"HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError("Local AI is unavailable; your library was not changed. Use the filter builder. " +
                           str(error)) from error
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Local AI returned an invalid intent; no action was taken.") from error
    return {"intent": intent, "provider": "local-ollama",
            "note": "Review every translated filter and planning constraint against your request. "
                    "No search, generation, save or export was executed."}


def interpret(message, *, model=None):
    return _interpret(message, SCHEMA, validate_intent,
        "Translate only; no execution. New crate: action build. Otherwise action search. "
        "Include each requested numeric bound separately: between X and Y BPM means bpm_min X and bpm_max Y; "
        "energy uses energy_min/energy_max in 0..1. Clean means clean_only true. "
        "Never-played means never_played true. Unrequested fields are omitted from rule. "
        "unsupported is [] for supported filters. Years, exact edits, deletion, exports and purchases "
        "are unsupported: choose search, rule {}, and list only the user's unsupported requirement. "
        'Example unsupported request "Permanently delete duplicate audio files": '
        '{"action":"search","name":"Unsupported deletion","rule":{},'
        '"unsupported":["permanent audio deletion"]}. '
        'Example supported request "Build 20 clean House songs 118-124 BPM": '
        '{"action":"build","name":"Clean House","rule":{"genre":"House","bpm_min":118,'
        '"bpm_max":124,"clean_only":true,"limit":20},"unsupported":[]}.', model=model)


def interpret_plan(message, *, model=None):
    timing = _interpret(message, TIMING_SCHEMA, lambda value: _validate_timing(value, message),
        'Extract time requirements only. Output {"target_minutes":number or null,'
        '"tolerance_minutes":number or null,"max_tracks":integer or null,"phases":array}. '
        "Null means NOT specified. NEVER invent a duration. "
        "Convert explicitly stated hours to minutes. "
        "Single dinner set means phases []. Multi-stage means ordered phase objects containing "
        "phase (snake_case stage name), target_minutes and quote (exact user text specifying that stage budget). "
        "Never add phases just because the set is named dinner. Only multiple EXPLICIT stage budgets create phases. "
        "Optional phase tolerance_minutes must be explicitly requested. "
        "Add stage durations for total if user omitted total. Preserve explicit zero tolerance. "
        "No genre, BPM, energy or filters.", model=model, include_schema=False)
    constraints = _interpret(message, CONSTRAINT_SCHEMA,
        lambda value: _validate_constraints(value, message, timing["intent"]),
        'Extract ALL literal music filters explicitly stated, not just one. Output '
        '{"constraints":[{"field":"clean_only","value":true,"quote":"clean"},'
        '{"field":"genre","value":"House","quote":"House"}],"unsupported":[]}. '
        "Each constraint needs an exact quote from the USER supporting that value. "
        "Use native numbers/booleans. Fields: " + ", ".join(CONSTRAINT_FIELDS) + ". "
        "clean/tagged-clean means clean_only true. Include both clean and genre if both requested. "
        "BPM uses bpm_min/bpm_max; energy bounds use energy_min/energy_max. "
        "No inferred genre, BPM, energy or text query. Dinner/cocktail/dancing are stage names, never genre or q. "
        "Timing, tolerances and maximum song counts are handled separately: omit them, not unsupported. "
        "For per-stage constraints add phase_index (zero-based ordered stage); omit for global constraints. "
        "For global policy BPM/clean bounds add scope policy; otherwise omit scope. "
        "energy_target/use_feedback are policy fields. "
        "No constraints means constraints []. Unsupported years, deletion, exports, exact edits, "
        "cue/mixed timing, purchases and named-song obligations go in unsupported. "
        'Example "cocktail followed by open dancing": {"constraints":[],"unsupported":[]}. '
        'Example "tagged-clean House set 19 minutes": {"constraints":['
        '{"field":"clean_only","value":true,"quote":"tagged-clean"},'
        '{"field":"genre","value":"House","quote":"House"}],"unsupported":[]}.',
        model=model, include_schema=False)
    draft = constraints["intent"]
    result = {**constraints, "intent": {key: item for key, item in draft.items() if key != "constraint_evidence"},
              "constraint_evidence": draft["constraint_evidence"], "note": constraints["note"] +
              " Timing and source-quoted constraints were extracted separately; quotes do not prove completeness."}
    return result


TIMING_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    **{key: {"anyOf": [PLAN_SCHEMA["properties"]["policy"]["properties"][key], {"type": "null"}]}
       for key in ("target_minutes", "tolerance_minutes", "max_tracks")},
    "phases": {"type": "array", "maxItems": MAX_PHASES, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {key: value for key, value in {
            **PLAN_FIELDS, "phase": {"type": "string", "enum": sorted(PHASES)},
            "quote": {"type": "string"}, "label": {"type": "string"}}.items()
            if key in ("phase", "target_minutes", "quote", "label", "tolerance_minutes")},
        "required": ["phase", "target_minutes", "quote"]}}},
    "required": ["target_minutes"]}
CONSTRAINT_FIELDS = tuple(SCHEMA["properties"]["rule"]["properties"]) + ("energy_target", "use_feedback")
CONSTRAINT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "constraints": {"type": "array", "maxItems": 100, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"field": {"type": "string", "enum": list(CONSTRAINT_FIELDS)},
                       "value": {"type": ["number", "boolean", "string"]},
                       "quote": {"type": "string"},
                       "scope": {"type": "string", "enum": ["rule", "policy"]},
                       "phase_index": {"type": "integer", "minimum": 0}},
        "required": ["field", "value", "quote"]}},
    "unsupported": SCHEMA["properties"]["unsupported"]}, "required": ["constraints", "unsupported"]}


def _numbers(quote):
    return [float(number) for number in re.findall(r"(?<![\w.])-?(?:\d+(?:\.\d+)?|\.\d+)(?![\w.])", quote)]


def _duration_numbers(quote):
    return _numbers(quote) + [float(number) * 60 for number in re.findall(
        r"(?<![\w.])(-?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:hours?|hrs?|h)\b", quote, re.IGNORECASE)]


def _validate_timing(value, message):
    studio_service.exact_fields(value, {"target_minutes"}, set(TIMING_SCHEMA["properties"]) - {"target_minutes"})
    policy = sparse(value)
    phases = value.get("phases", [])
    if not isinstance(phases, list) or len(phases) > MAX_PHASES:
        raise ValueError("AI timing phases must be a bounded list.")
    if not phases:
        policy.pop("phases", None)
    else:
        normalized = []
        cursor = 0
        for phase in phases:
            studio_service.exact_fields(phase, {"phase", "target_minutes", "quote"}, {"label", "tolerance_minutes"})
            quote = phase["quote"]
            catalog.text(quote, "AI phase source quote", required=True)
            position = message.find(quote, cursor)
            if position < 0 or phase["target_minutes"] not in _duration_numbers(quote):
                raise ValueError("AI phase budget is not grounded in the user's request.")
            cursor = position + len(quote)
            if phase.get("tolerance_minutes") is not None and phase["tolerance_minutes"] not in _duration_numbers(message):
                raise ValueError("AI phase tolerance is not grounded in the user's request.")
            normalized.append({"tolerance_minutes": 1, **{
                key: item for key, item in sparse(phase).items() if key != "quote"}})
        policy["phases"] = normalized
        if "target_minutes" not in policy:
            targets = [phase["target_minutes"] for phase in normalized]
            if any(type(target) not in (int, float) for target in targets):
                raise ValueError("AI phase durations must be numbers.")
            policy["target_minutes"] = sum(targets)
    if "target_minutes" not in policy:
        raise ValueError("The request includes requirements this assistant cannot enforce: missing intended duration")
    for field in ("target_minutes", "tolerance_minutes", "max_tracks"):
        source_numbers = _duration_numbers(message) if field != "max_tracks" else _numbers(message)
        if field in policy and policy[field] not in source_numbers and not (
                field == "target_minutes" and phases and
                policy[field] == sum(phase["target_minutes"] for phase in policy["phases"])):
            raise ValueError("AI timing constraint is not grounded in the user's request.")
    return validate_plan_intent({"action": "plan", "name": "AI set draft", "rule": {},
                                 "policy": policy, "unsupported": []})["policy"]


def _validate_constraints(value, message, timing):
    studio_service.exact_fields(value, {"constraints", "unsupported"})
    if not isinstance(value["constraints"], list) or len(value["constraints"]) > 100:
        raise ValueError("AI constraints must be a bounded list.")
    rule, policy, evidence, seen = {}, {key: item for key, item in timing.items()
                                      if key in PLAN_SCHEMA["properties"]["policy"]["properties"]}, [], set()
    if "phases" in policy:
        policy["phases"] = [{key: item for key, item in phase.items()
                             if key in PLAN_FIELDS or key in ("phase", "label")} for phase in policy["phases"]]
    for item in value["constraints"]:
        studio_service.exact_fields(item, {"field", "value", "quote"}, {"scope", "phase_index"})
        field, proposed, quote = item["field"], item["value"], item["quote"]
        if proposed is None:
            raise ValueError("AI constraint values cannot be null.")
        if not isinstance(field, str) or field not in CONSTRAINT_FIELDS:
            raise ValueError("AI constraint field is not supported.")
        catalog.text(quote, "AI source quote", required=True)
        if quote not in message:
            raise ValueError("AI constraint quote is not present in the user's request.")
        if field in ("clean_only", "never_played", "use_feedback"):
            marker = {"clean_only": r"\bclean(?:_only)?\b",
                      "never_played": r"\b(?:never[\s_-]+played|unplayed)\b",
                      "use_feedback": r"\b(?:feedback|preferences?|ratings?|use_feedback)\b"}[field]
            if type(proposed) is not bool or not re.search(marker, quote, re.IGNORECASE):
                raise ValueError("AI boolean constraint needs a typed value and a relevant source quote.")
        if isinstance(proposed, str) and proposed.casefold() not in quote.casefold():
            raise ValueError("AI text constraint is not grounded in its source quote.")
        if type(proposed) in (int, float) and proposed not in _numbers(quote):
            raise ValueError("AI numeric constraint is not grounded in its source quote.")
        scope = item.get("scope", "policy" if field in ("energy_target", "use_feedback") else "rule")
        phase_index = item.get("phase_index")
        if scope not in ("rule", "policy"):
            raise ValueError("AI constraint scope is unsupported.")
        if "phase_index" in item:
            if type(phase_index) is not int or not 0 <= phase_index < len(policy.get("phases", [])):
                raise ValueError("AI constraint references an unavailable phase.")
            if field not in ("bpm_min", "bpm_max", "clean_only", "energy_target"):
                raise ValueError("AI constraint field is not available per phase.")
            target = policy["phases"][phase_index]
            scope = "phase"
        elif scope == "policy":
            if field not in ("bpm_min", "bpm_max", "clean_only", "energy_target", "use_feedback"):
                raise ValueError("AI constraint field is not available in global policy.")
            target = policy
        else:
            if field not in SCHEMA["properties"]["rule"]["properties"]:
                raise ValueError("AI constraint field is not available in a rule.")
            target = rule
        identity = (scope, phase_index, field)
        if identity in seen:
            raise ValueError("AI returned duplicate constraint fields.")
        seen.add(identity)
        target[field] = proposed
        evidence.append(item)
    intent = validate_plan_intent({"action": "plan", "name": "AI set draft", "rule": rule,
                                  "policy": policy, "unsupported": value["unsupported"]})
    return {**intent, "constraint_evidence": evidence}
