from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from app.domain.rules import RuleEngine


CALCULATION_PROFILES: dict[str, tuple[str, dict[str, str], dict[str, Any]]] = {
    "PROJECT_SETTLEMENT": (
        "PROJECT_EXTERNAL",
        {"contract_income": "project.contract_income", "total_expense": "project.total_expense"},
        {},
    ),
    "PROJECT_BUDGET": (
        "PROJECT_EXTERNAL",
        {"contract_income": "project.contract_income", "total_expense": "project.total_expense"},
        {},
    ),
    "EQUIPMENT_USAGE": ("EQUIPMENT_USAGE", {"equipment_fee": "equipment.fee"}, {}),
    "EFP_SERVICE": (
        "PROJECT_EFP",
        {"equipment_fee": "equipment.fee", "technical_service_fee": "technical_service.fee"},
        {},
    ),
    "SCREEN_SERVICE": ("PROJECT_SCREEN", {"technical_service_fee": "technical_service.fee"}, {}),
    "MEETING_AUDIO": ("GROUP_MEETING_AUDIO", {"session_count": "meeting.session_count"}, {}),
    "AUDIO_POST": (
        "GROUP_AUDIO_POST",
        {"equipment_fee": "equipment.fee", "gross_profit": "project.gross_profit"},
        {},
    ),
    "NONLINEAR": (
        "GROUP_NONLINEAR",
        {"equipment_fee": "equipment.fee", "gross_profit": "project.gross_profit"},
        {},
    ),
    "DRIVER": (
        "GROUP_DRIVER",
        {"vehicle_type": "vehicle.type", "monthly_km": "vehicle.monthly_km"},
        {"half_days": "vehicle.half_days", "full_days": "vehicle.full_days"},
    ),
    "SONY_REPAIR": (
        "GROUP_SONY_REPAIR",
        {
            "internal_quote_total": "repair.internal_quote_total",
            "external_repair_total": "repair.external_repair_total",
            "parts_cost": "repair.parts_cost",
        },
        {},
    ),
}

PROJECT_BUDGET_COMPONENT_PATHS = (
    "project.group_equipment_labor_fee",
    "project.labor_fee",
    "project.equipment_rental_service_fee",
    "project.stage_production_fee",
)
PROJECT_BUDGET_MISC_PATHS = (
    "project.misc_handling_fee",
    "project.misc_consumables_fee",
    "project.misc_courier_fee",
    "project.misc_venue_fee",
    "project.misc_other_fee",
)
PROJECT_SETTLEMENT_LEAF_PATHS = (
    "project.internal_group_equipment_fee",
    "project.internal_labor_fee",
    "project.external_labor_fee",
    "project.travel_meal_fee",
    "project.travel_lodging_fee",
    "project.travel_transportation_fee",
    "project.equipment_rental_service_fee",
    "project.stage_production_fee",
    "project.equipment_purchase_fee",
    *PROJECT_BUDGET_MISC_PATHS,
)


def confirmed_calculation_inputs(
    form_type: str,
    fields: list[dict[str, Any]],
    requested_rule_code: str,
) -> tuple[str, dict[str, Any]]:
    if form_type not in CALCULATION_PROFILES:
        raise ValueError(f"表格类型尚未发布核算规则：{form_type}")
    rule_code, required, optional = CALCULATION_PROFILES[form_type]
    if requested_rule_code != rule_code:
        raise ValueError(f"当前表格类型只能使用规则：{rule_code}")
    by_path = {item["path"]: item for item in fields}
    if form_type == "PROJECT_BUDGET" or (
        form_type == "PROJECT_SETTLEMENT" and any(path in by_path for path in PROJECT_SETTLEMENT_LEAF_PATHS)
    ):
        source_paths = (
            "project.contract_income",
            *(
                (*PROJECT_BUDGET_COMPONENT_PATHS, *PROJECT_BUDGET_MISC_PATHS)
                if form_type == "PROJECT_BUDGET"
                else PROJECT_SETTLEMENT_LEAF_PATHS
            ),
        )
        values: dict[str, Decimal] = {}
        for field_path in source_paths:
            field = by_path.get(field_path)
            if not field or not field.get("confirmed") or field.get("value_normalized") in (None, ""):
                raise ValueError(f"缺少已确认的计算字段：{field_path}")
            try:
                values[field_path] = Decimal(str(field["value_normalized"]))
            except InvalidOperation as exc:
                raise ValueError(f"计算字段不是有效数字：{field_path}") from exc
            if values[field_path] < 0:
                raise ValueError(f"计算字段不能为负数：{field_path}")
        if form_type == "PROJECT_BUDGET":
            misc_expense = sum((values[path] for path in PROJECT_BUDGET_MISC_PATHS), Decimal("0"))
            total_expense = sum((values[path] for path in PROJECT_BUDGET_COMPONENT_PATHS), Decimal("0")) + misc_expense
        else:
            total_expense = sum((values[path] for path in PROJECT_SETTLEMENT_LEAF_PATHS), Decimal("0"))
        return rule_code, {
            "contract_income": str(values["project.contract_income"]),
            "total_expense": str(total_expense),
        }
    inputs: dict[str, Any] = {}
    for input_name, field_path in required.items():
        field = by_path.get(field_path)
        if not field or not field.get("confirmed") or field.get("value_normalized") in (None, ""):
            raise ValueError(f"缺少已确认的计算字段：{field_path}")
        inputs[input_name] = field["value_normalized"]
    for input_name, field_path in optional.items():
        field = by_path.get(field_path)
        inputs[input_name] = field["value_normalized"] if field and field.get("confirmed") else 0
    return rule_code, inputs


def calculate_rule(rule_code: str, inputs: dict[str, Any]):
    dispatch: dict[str, Callable[[], object]] = {
        "PROJECT_EXTERNAL": lambda: RuleEngine.project_external(inputs["contract_income"], inputs["total_expense"]),
        "EQUIPMENT_USAGE": lambda: RuleEngine.equipment_usage(inputs["equipment_fee"]),
        "PROJECT_EFP": lambda: RuleEngine.efp(inputs["equipment_fee"], inputs["technical_service_fee"]),
        "PROJECT_SCREEN": lambda: RuleEngine.screen(inputs["technical_service_fee"]),
        "GROUP_MEETING_AUDIO": lambda: RuleEngine.meeting_audio(int(inputs["session_count"])),
        "GROUP_AUDIO_POST": lambda: RuleEngine.audio_post(inputs["equipment_fee"], inputs["gross_profit"]),
        "GROUP_NONLINEAR": lambda: RuleEngine.nonlinear(inputs["equipment_fee"], inputs["gross_profit"]),
        "GROUP_SONY_REPAIR": lambda: RuleEngine.sony_repair(inputs["internal_quote_total"], inputs["external_repair_total"], inputs["parts_cost"]),
        "GROUP_DRIVER": lambda: RuleEngine.driver(inputs["vehicle_type"], inputs["monthly_km"], half_days=int(inputs.get("half_days", 0)), full_days=int(inputs.get("full_days", 0))),
        "PROJECT_INTRODUCER": lambda: RuleEngine.introducer_share(inputs["project_bonus_pool"]),
    }
    if rule_code not in dispatch:
        raise ValueError(f"未知或尚未发布的规则：{rule_code}")
    return dispatch[rule_code]()
