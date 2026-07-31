#!/usr/bin/env python3
"""Apply explicit reviewed rules to object measurements without inventing biological gates."""
from __future__ import annotations

import argparse
import json
import operator
from pathlib import Path


OPERATORS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
             "==": operator.eq, "!=": operator.ne}


def value_for(obj: dict, condition: dict):
    metric = condition["metric"]
    if condition.get("channel_role"):
        return obj.get("channels", {}).get(condition["channel_role"], {}).get(metric)
    return obj.get(metric)


def condition_matches(obj: dict, condition: dict) -> bool:
    value = value_for(obj, condition)
    return value is not None and OPERATORS[condition["operator"]](value, condition["threshold"])


def classify(payload: dict, rules: list[dict], default: str) -> dict:
    rows, counts = [], {}
    for obj in payload.get("objects", []):
        label = default
        for rule in rules:
            conditions = rule.get("conditions", [])
            mode = rule.get("match", "all")
            matches = [condition_matches(obj, condition) for condition in conditions]
            if conditions and (all(matches) if mode == "all" else any(matches)):
                label = rule["label"]
                break
        counts[label] = counts.get(label, 0) + 1
        rows.append({"object_id": obj["object_id"], "class": label})
    return {"schema_version": 1, "default": default, "counts": counts, "objects": rows}


def validate_rules(rules: list[dict]) -> None:
    labels = set()
    for index, rule in enumerate(rules):
        if not rule.get("label") or rule["label"] in labels:
            raise ValueError(f"classification rule {index} requires a unique label")
        labels.add(rule["label"])
        if rule.get("match", "all") not in {"all", "any"} or not rule.get("conditions"):
            raise ValueError(f"classification rule {index} requires conditions and all/any matching")
        for condition in rule["conditions"]:
            if condition.get("operator") not in OPERATORS or not isinstance(condition.get("threshold"), (int, float)):
                raise ValueError(f"classification rule {index} has an invalid operator or threshold")
            if not condition.get("metric"):
                raise ValueError(f"classification rule {index} condition requires a metric")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        rule_payload = json.loads(args.rules.read_text(encoding="utf-8"))
        rules = rule_payload.get("rules", [])
        validate_rules(rules)
        payload = classify(json.loads(args.measurements.read_text(encoding="utf-8")), rules,
                           rule_payload.get("default", "unclassified"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    payload.update({"measurements": str(args.measurements), "rules": str(args.rules)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": payload["counts"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
