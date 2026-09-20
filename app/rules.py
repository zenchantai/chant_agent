from pathlib import Path

import yaml

DEFINITION_VERSION = "chan-period-directional-z-center-v28"
PERIOD_DEFINITION_VERSION = DEFINITION_VERSION
REQUIRED_RUNTIME_RULES = {
    "RAW-5M-001", "INC-001", "FX-001", "PEN-NEW-001", "PEN-NEW-002",
    "PEN-SPECIAL-001", "PEN-SPECIAL-002", "PEN-SPECIAL-003", "PEN-SPECIAL-004",
    "PEN-SPECIAL-005", "COMPONENT-CENTER-FREE-V25", "CENTER-WORKSPACE-V25",
    "CENTER-STRICT-CORE-V25", "CENTER-STATE-MACHINE-V25",
    "CENTER-NEWBORN-EXPANSION-V26", "CENTER-Z-WAVE-V26", "CENTER-FAMILY-REVISION-V25",
    "POINT-STRENGTH-VECTOR-V25", "POINT-FIRST-V25", "POINT-SECOND-V25",
    "POINT-THIRD-V25", "MOVEMENT-POINT-BOUNDARY-V25",
    "MOVEMENT-CLASSIFICATION-V25", "RECURSION-V25", "CONFIRM-V25",
    "PERIOD-PROFILE-V27", "DAILY-L2-PROJECTION-V27", "DAILY-CONFIRMED-SOURCE-V27",
}


def load_rulebook(path: Path | None = None) -> dict:
    target = path or Path(__file__).resolve().parents[1] / "knowledge" / "chan_rules.yaml"
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if data.get("version") != DEFINITION_VERSION:
        raise RuntimeError(f"规则版本不匹配: {data.get('version')}")
    runtime_ids = {rule["id"] for rule in data.get("rules", []) if rule.get("runtime", True)}
    missing = REQUIRED_RUNTIME_RULES - runtime_ids
    if missing:
        raise RuntimeError(f"规则知识库缺少强制规则: {', '.join(sorted(missing))}")
    return data


RULEBOOK = load_rulebook()
