#!/usr/bin/env python3
"""Проверить каждую клиентскую рекомендацию по обязательным критериям."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cabinet" / "data" / "profit_control_recommendations_v2.json"
OUTPUT_DIR = ROOT / "artifacts" / "profit_control_recommendation_audit_v2"

SCENARIO_LABELS = {
    "STOCK_ZERO": "остаток закончился",
    "STOCK_LT_3D": "остатка меньше чем на три дня",
    "STOCK_3_7D": "риск скорого дефицита",
    "STOCK_7_14D": "запас приближается к границе",
    "AVAILABILITY_LIMITED": "ограничение наличия в прошедшем периоде",
    "MONTH_DOWN_WEEK_RECOVERY": "месяц снизился, неделя восстанавливается",
    "LOGISTICS_UNIT_UP": "логистика на единицу выросла",
    "PRICE_DOWN_WITHOUT_VOLUME": "цена снизилась без роста продаж",
    "ADS_COST_UP": "рекламные расходы выросли",
    "DEMAND_VOLUME_DOWN": "продажи и результат снизились при стабильной цене",
    "UNIT_ECONOMICS_DOWN": "результат на единицу снизился без выделенной причины",
    "ONE_DAY_ALERT": "отклонение одного полного дня",
    "STABLE_GROWTH": "устойчивый рост",
    "NORMAL_VARIATION": "денежно значимого изменения нет",
    "DQ_BLOCKED": "данные не позволяют сделать вывод",
}


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    recommendations = payload.get("recommendations", [])
    scenario_counts = Counter(item["scenario_code"] for item in recommendations)
    decision_counts = Counter(item["decision"] for item in recommendations)
    rows = []
    failed = []
    for item in recommendations:
        checks = item.get("audit_checks", [])
        failed_checks = [check for check in checks if not check.get("passed")]
        row = {
            "product_key": item["product_key"],
            "product_name": item["product_name"],
            "scenario_code": item["scenario_code"],
            "scenario": SCENARIO_LABELS.get(item["scenario_code"], item["scenario_code"]),
            "decision": item["decision"],
            "problem_status": item.get("problem_status"),
            "cause_status": item.get("cause_status"),
            "action_type": item.get("action_type"),
            "impact_amount": item.get("impact_amount", 0),
            "passed_checks": sum(bool(check.get("passed")) for check in checks),
            "total_checks": len(checks),
            "failed_checks": failed_checks,
        }
        rows.append(row)
        if failed_checks:
            failed.append(row)

    report = {
        "generatedAt": payload.get("generatedAt"),
        "period": payload.get("primaryPeriod"),
        "firstPassBaseline": {
            "recommendations": 13,
            "client_actions": 1,
            "blocked_for_low_sales": 2,
            "observed_failures": [
                "Малый объём продаж скрывал уже подтверждённую денежную проблему.",
                "Проверка причины выдавалась клиенту как готовое бизнес-действие.",
                "Рекламные, складские и дневные поля частично не участвовали в выборе сценария.",
                "Условный балл приоритета был важнее денежного влияния.",
            ],
        },
        "secondPass": {
            "recommendations": len(recommendations),
            "decisions": dict(sorted(decision_counts.items())),
            "scenariosOnRealData": dict(sorted(scenario_counts.items())),
            "allChecksPassed": not failed,
            "failedRecommendations": len(failed),
        },
        "scenarioCatalog": [
            {
                "scenario_code": code,
                "meaning": label,
                "triggered_on_current_data": scenario_counts.get(code, 0),
            }
            for code, label in SCENARIO_LABELS.items()
        ],
        "recommendations": rows,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Проверка движка рекомендаций v2",
        "",
        f"Период: {payload['primaryPeriod']['currentStart']}–{payload['primaryPeriod']['currentEnd']} против "
        f"{payload['primaryPeriod']['previousStart']}–{payload['primaryPeriod']['previousEnd']}.",
        "",
        f"Проверено рекомендаций: {len(recommendations)}. Не прошли проверку: {len(failed)}.",
        "",
        "| Товар | Сценарий | Статус причины | Тип действия | Денежное влияние | Проверки |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['product_name'].replace('|', '/')} | {row['scenario']} | {row['cause_status']} | "
            f"{row['action_type']} | {row['impact_amount']:,.0f} ₽ | {row['passed_checks']}/{row['total_checks']} |"
        )
    lines.extend(["", "## Покрытие сценариев", ""])
    for code, label in SCENARIO_LABELS.items():
        lines.append(f"- {label}: {scenario_counts.get(code, 0)} на текущих данных.")
    if failed:
        lines.extend(["", "## Не пройденные проверки", ""])
        for row in failed:
            details = "; ".join(check["label"] for check in row["failed_checks"])
            lines.append(f"- {row['product_name']}: {details}.")
    (OUTPUT_DIR / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report["secondPass"], ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
