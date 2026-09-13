from pathlib import Path

import yaml

DEFINITION_VERSION = "chan-period-center-hierarchy-cache-fingerprint-v13"
PERIOD_DEFINITION_VERSION = DEFINITION_VERSION
REQUIRED_RUNTIME_RULES = {
    "RAW-5M-001", "INC-001", "FX-001", "PEN-NEW-001", "PEN-NEW-002",
    "PEN-SPECIAL-001", "PEN-SPECIAL-002", "PEN-SPECIAL-003", "PEN-SPECIAL-004",
    "PEN-SPECIAL-005", "CENTER-DIRECTIONAL-ENTRY-001", "CENTER-DIRECTIONAL-CORE-001",
    "CENTER-L1-EXTENSION-001", "CENTER-L1-REENTRY-001", "CONFIRM-001",
    "MOVEMENT-BASE-001", "MOVEMENT-TREND-001", "MOVEMENT-BOUNDARY-001",
    "MOVEMENT-DECOMP-001", "MOVEMENT-STATUS-001", "CENTER-RELATION-HIERARCHY-001",
    "CENTER-UPGRADE-EXTENSION-001", "CENTER-UPGRADE-EXPANSION-001",
    "CENTER-PARENT-CHILD-001", "SAME-LEVEL-CENTER-THREE-001",
    "SAME-LEVEL-DECOMPOSITION-001", "MOVEMENT-SHARED-ENDPOINT-001",
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
