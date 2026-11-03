from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


ZERO = Decimal("0")
ONE_YUAN = Decimal("1")


def decimal_value(value: Decimal | int | str | float) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_yuan(value: Decimal) -> Decimal:
    return value.quantize(ONE_YUAN, rounding=ROUND_HALF_UP)


def require_nonnegative(name: str, value: Decimal) -> None:
    if value < ZERO:
        raise ValueError(f"{name}不能为负数")


@dataclass(frozen=True)
class CalculationLine:
    formula_code: str
    label: str
    expression: str
    operands: dict[str, str]
    raw_result: Decimal

    @property
    def rounded_result(self) -> Decimal:
        return round_yuan(self.raw_result)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["raw_result"] = str(self.raw_result)
        data["rounded_result"] = str(self.rounded_result)
        return data


@dataclass(frozen=True)
class CalculationResult:
    rule_code: str
    status: str
    raw_total: Decimal
    rounded_total: Decimal
    lines: tuple[CalculationLine, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_code": self.rule_code,
            "status": self.status,
            "raw_total": str(self.raw_total),
            "rounded_total": str(self.rounded_total),
            "lines": [line.to_dict() for line in self.lines],
            "warnings": list(self.warnings),
            "metadata": self.metadata,
        }


def _result(
    rule_code: str,
    lines: Iterable[CalculationLine],
    *,
    status: str = "CALCULATED",
    warnings: Iterable[str] = (),
    metadata: dict[str, str] | None = None,
) -> CalculationResult:
    frozen_lines = tuple(lines)
    raw_total = sum((line.raw_result for line in frozen_lines), ZERO)
    return CalculationResult(
        rule_code=rule_code,
        status=status,
        raw_total=raw_total,
        rounded_total=round_yuan(raw_total),
        lines=frozen_lines,
        warnings=tuple(warnings),
        metadata=metadata or {},
    )


def _progressive_profit_lines(gross_profit: Decimal) -> tuple[list[CalculationLine], list[str], str]:
    require_nonnegative("用于计算的毛利", max(gross_profit, ZERO))
    warnings: list[str] = []
    effective_profit = gross_profit
    if gross_profit <= ZERO:
        if gross_profit < ZERO:
            warnings.append("项目为负毛利，毛利奖励按0计算")
        effective_profit = ZERO

    tiers = (
        ("PROFIT_TIER_1", Decimal("0"), Decimal("50000"), Decimal("0.50"), "0—5万元"),
        ("PROFIT_TIER_2", Decimal("50000"), Decimal("100000"), Decimal("0.30"), "5—10万元"),
        ("PROFIT_TIER_3", Decimal("100000"), Decimal("300000"), Decimal("0.20"), "10—30万元"),
        ("PROFIT_TIER_4", Decimal("300000"), Decimal("500000"), Decimal("0.10"), "30—50万元"),
    )
    lines: list[CalculationLine] = []
    for code, lower, upper, rate, label in tiers:
        tier_base = min(max(effective_profit - lower, ZERO), upper - lower)
        if tier_base == ZERO and lines:
            continue
        result = tier_base * rate
        lines.append(
            CalculationLine(
                formula_code=code,
                label=f"毛利奖励（{label}）",
                expression=f"{tier_base} × {rate * 100}%",
                operands={"tier_base": str(tier_base), "rate": str(rate)},
                raw_result=result,
            )
        )

    status = "CALCULATED"
    if gross_profit > Decimal("500000"):
        status = "MANUAL_APPROVAL_REQUIRED"
        warnings.append("毛利超过50万元，超过部分不自动推断，需办公会议确定最终金额")
    return lines, warnings, status


class RuleEngine:
    """V0.2 已确认规则的确定性计算入口，全部使用 Decimal。"""

    @staticmethod
    def progressive_profit(gross_profit: Decimal | int | str | float) -> CalculationResult:
        profit = decimal_value(gross_profit)
        lines, warnings, status = _progressive_profit_lines(profit)
        return _result(
            "PROGRESSIVE_PROFIT",
            lines,
            status=status,
            warnings=warnings,
            metadata={"gross_profit": str(profit)},
        )

    @staticmethod
    def equipment_usage(equipment_fee: Decimal | int | str | float) -> CalculationResult:
        fee = decimal_value(equipment_fee)
        require_nonnegative("设备使用费", fee)
        return _result(
            "EQUIPMENT_USAGE",
            [CalculationLine("EQUIPMENT_15", "设备费奖励", f"{fee} × 15%", {"equipment_fee": str(fee), "rate": "0.15"}, fee * Decimal("0.15"))],
        )

    @staticmethod
    def project_external(contract_income: Decimal | int | str | float, total_expense: Decimal | int | str | float) -> CalculationResult:
        income = decimal_value(contract_income)
        expense = decimal_value(total_expense)
        require_nonnegative("合同收入", income)
        require_nonnegative("支出总计", expense)
        gross_profit = income - expense
        lines, warnings, status = _progressive_profit_lines(gross_profit)
        return _result(
            "PROJECT_EXTERNAL",
            lines,
            status=status,
            warnings=warnings,
            metadata={"contract_income": str(income), "total_expense": str(expense), "gross_profit": str(gross_profit)},
        )

    @staticmethod
    def meeting_audio(session_count: int) -> CalculationResult:
        if session_count < 0:
            raise ValueError("场次数不能为负数")
        fixed = Decimal("2500") * 3
        session_reward = Decimal(session_count) * Decimal("70")
        return _result(
            "GROUP_MEETING_AUDIO",
            [
                CalculationLine("FIXED_2500_X3", "固定额度", "2500 × 3", {"unit": "2500", "fixed_people": "3"}, fixed),
                CalculationLine("SESSION_70", "场次奖励", f"{session_count} × 70", {"session_count": str(session_count), "rate": "70"}, session_reward),
            ],
        )

    @staticmethod
    def audio_post(equipment_fee: Decimal | int | str | float, gross_profit: Decimal | int | str | float) -> CalculationResult:
        fee = decimal_value(equipment_fee)
        profit = decimal_value(gross_profit)
        require_nonnegative("设备费", fee)
        profit_lines, warnings, status = _progressive_profit_lines(profit)
        lines = [
            CalculationLine("FIXED_2500_X3", "固定额度", "2500 × 3", {"unit": "2500", "fixed_people": "3"}, Decimal("7500")),
            CalculationLine("EQUIPMENT_15", "设备费奖励", f"{fee} × 15%", {"equipment_fee": str(fee), "rate": "0.15"}, fee * Decimal("0.15")),
            *profit_lines,
        ]
        return _result("GROUP_AUDIO_POST", lines, status=status, warnings=warnings)

    @staticmethod
    def nonlinear(equipment_fee: Decimal | int | str | float, gross_profit: Decimal | int | str | float) -> CalculationResult:
        fee = decimal_value(equipment_fee)
        profit = decimal_value(gross_profit)
        require_nonnegative("设备费", fee)
        profit_lines, warnings, status = _progressive_profit_lines(profit)
        lines = [
            CalculationLine("FIXED_2500_X8", "固定额度", "2500 × 8", {"unit": "2500", "fixed_people": "8"}, Decimal("20000")),
            CalculationLine("EQUIPMENT_15", "设备费奖励", f"{fee} × 15%", {"equipment_fee": str(fee), "rate": "0.15"}, fee * Decimal("0.15")),
            *profit_lines,
        ]
        return _result("GROUP_NONLINEAR", lines, status=status, warnings=warnings)

    @staticmethod
    def efp(equipment_fee: Decimal | int | str | float, technical_service_fee: Decimal | int | str | float) -> CalculationResult:
        equipment = decimal_value(equipment_fee)
        service = decimal_value(technical_service_fee)
        require_nonnegative("设备使用费", equipment)
        require_nonnegative("技术服务费", service)
        return _result(
            "PROJECT_EFP",
            [
                CalculationLine("EQUIPMENT_15", "设备费奖励", f"{equipment} × 15%", {"equipment_fee": str(equipment), "rate": "0.15"}, equipment * Decimal("0.15")),
                CalculationLine("SERVICE_30", "技术服务奖励", f"{service} × 30%", {"service_fee": str(service), "rate": "0.30"}, service * Decimal("0.30")),
            ],
        )

    @staticmethod
    def screen(technical_service_fee: Decimal | int | str | float) -> CalculationResult:
        service = decimal_value(technical_service_fee)
        require_nonnegative("技术服务费", service)
        return _result(
            "PROJECT_SCREEN",
            [CalculationLine("SERVICE_30", "技术服务奖励", f"{service} × 30%", {"service_fee": str(service), "rate": "0.30"}, service * Decimal("0.30"))],
        )

    @staticmethod
    def sony_repair(
        internal_quote_total: Decimal | int | str | float,
        external_repair_total: Decimal | int | str | float,
        parts_cost: Decimal | int | str | float,
    ) -> CalculationResult:
        internal = decimal_value(internal_quote_total)
        external = decimal_value(external_repair_total)
        parts = decimal_value(parts_cost)
        for name, value in (("内部申报价", internal), ("对外维修总计", external), ("零件采购费", parts)):
            require_nonnegative(name, value)
        external_margin = max(external - parts, ZERO)
        warnings = ["对外维修零件成本高于维修收入，对外部分按0计算"] if external < parts else []
        return _result(
            "GROUP_SONY_REPAIR",
            [
                CalculationLine("INTERNAL_80", "内部维修奖励", f"{internal} × 80%", {"internal_quote_total": str(internal), "rate": "0.80"}, internal * Decimal("0.80")),
                CalculationLine("EXTERNAL_MARGIN", "对外维修毛利", f"max({external} − {parts}, 0)", {"external_repair_total": str(external), "parts_cost": str(parts)}, external_margin),
            ],
            warnings=warnings,
        )

    @staticmethod
    def driver(
        vehicle_type: str,
        monthly_km: Decimal | int | str | float,
        *,
        half_days: int = 0,
        full_days: int = 0,
    ) -> CalculationResult:
        km = decimal_value(monthly_km)
        require_nonnegative("当月累计里程", km)
        if half_days < 0 or full_days < 0:
            raise ValueError("半天和全天数量不能为负数")
        normalized_type = vehicle_type.strip().upper()
        aliases = {"小型": "SMALL", "中型": "MEDIUM", "大型": "LARGE", "半挂": "SEMITRAILER"}
        normalized_type = aliases.get(normalized_type, normalized_type)
        if normalized_type == "SMALL" and km <= Decimal("100"):
            raw = Decimal(half_days) * Decimal("50") + Decimal(full_days) * Decimal("100")
            expression = f"{half_days} × 50 + {full_days} × 100"
            code = "SMALL_DAY_RATE"
        else:
            rates = {"SMALL": Decimal("1"), "MEDIUM": Decimal("2"), "LARGE": Decimal("2.5"), "SEMITRAILER": Decimal("2.5")}
            if normalized_type not in rates:
                raise ValueError("不支持的车型")
            rate = rates[normalized_type]
            raw = km * rate
            expression = f"{km} × {rate}"
            code = f"{normalized_type}_KM_RATE"
        return _result(
            "GROUP_DRIVER",
            [CalculationLine(code, "驾驶班奖励", expression, {"vehicle_type": normalized_type, "monthly_km": str(km), "half_days": str(half_days), "full_days": str(full_days)}, raw)],
        )

    @staticmethod
    def introducer_share(project_bonus_pool: Decimal | int | str | float) -> CalculationResult:
        pool = decimal_value(project_bonus_pool)
        require_nonnegative("项目奖金池", pool)
        share = pool * Decimal("0.15")
        return _result(
            "PROJECT_INTRODUCER",
            [CalculationLine("INTRODUCER_15", "项目引进人份额", f"{pool} × 15%", {"project_bonus_pool": str(pool), "rate": "0.15"}, share)],
            metadata={"project_bonus_pool_unchanged": str(pool)},
        )
