from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row


HEURISTIC_METHODS = {
    "paid_storage_share_day",
    "posting_share_day",
    "revenue_share_day",
    "revenue_share_window",
}

PROMOTION_MARKERS = ("wb продвижение",)
JAM_MARKERS = ("джем", "подписк")
REVIEW_MARKERS = ("списание за отзыв",)


@dataclass(frozen=True)
class ExpenseSubtypeModel:
    code: str
    label: str
    default_group: str
    source_layer: str
    source_field: str
    default_method: str
    default_confidence: str
    reason: str
    store_visible: bool = True
    product_visible: bool = True
    cabinet_visible: bool = True


EXPENSE_SUBTYPES: list[ExpenseSubtypeModel] = [
    ExpenseSubtypeModel(
        code="ads",
        label="Реклама",
        default_group="product-level",
        source_layer="stg_wb_ads -> fact_product_money_attribution",
        source_field="spend",
        default_method="nm_id_match",
        default_confidence="high",
        reason="Основной рекламный расход из рекламного слоя.",
    ),
    ExpenseSubtypeModel(
        code="cogs",
        label="Себестоимость",
        default_group="product-level",
        source_layer="cogs_engine",
        source_field="estimated_cogs",
        default_method="category_default_cogs",
        default_confidence="medium",
        reason="Себестоимость товара из слоя COGS.",
    ),
    ExpenseSubtypeModel(
        code="commission_core",
        label="Комиссия маркетплейса",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="commission_amount",
        default_method="order_id_match",
        default_confidence="high",
        reason="Комиссия WB, привязанная к товару по order_id/nm_id.",
    ),
    ExpenseSubtypeModel(
        code="payment_processing",
        label="Организация платежа",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="payment_processing",
        default_method="payment_processing",
        default_confidence="high",
        reason="Отдельная платежная подстатья, если приходит как самостоятельный денежный слой.",
    ),
    ExpenseSubtypeModel(
        code="logistics",
        label="Логистика",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="logistics_amount, rebill_logistic_cost, delivery_rub",
        default_method="order_id_match",
        default_confidence="high",
        reason="Логистические удержания WB, привязанные к товару.",
    ),
    ExpenseSubtypeModel(
        code="storage",
        label="Хранение",
        default_group="mixed / heuristic",
        source_layer="stg_wb_finance + stg_wb_paid_storage",
        source_field="storage_fee, warehouse_price",
        default_method="paid_storage_share_day",
        default_confidence="high",
        reason="Хранение распределяется через долю платного хранения, а не прямой строкой finance -> SKU.",
    ),
    ExpenseSubtypeModel(
        code="returns",
        label="Прочие возвратные удержания",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="deduction_amount",
        default_method="order_id_match",
        default_confidence="high",
        reason="Остаточные возвратные удержания после выделения корректировки вознаграждения Wildberries.",
    ),
    ExpenseSubtypeModel(
        code="wb_reward_return_adjustment",
        label="Корректировка вознаграждения Wildberries",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="amount / raw_payload->ppvz_reward",
        default_method="order_id_match",
        default_confidence="high",
        reason="Возвратная часть ppvz_reward; в UI показывается отдельно от общего bucket возвратов.",
    ),
    ExpenseSubtypeModel(
        code="penalties",
        label="Прочие штрафы",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="penalty_amount",
        default_method="nm_id_match",
        default_confidence="high",
        reason="Fallback для штрафов, которые не удалось выделить в более точную подстатью.",
    ),
    ExpenseSubtypeModel(
        code="pvz_related_penalty",
        label="ПВЗ-связанные удержания",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="penalty_amount / raw_payload->bonus_type_name",
        default_method="nm_id_match",
        default_confidence="high",
        reason="Отдельная ПВЗ-связанная штрафовая статья, когда raw явно указывает на ПВЗ.",
    ),
    ExpenseSubtypeModel(
        code="other_sale_adjustment",
        label="Вознаграждение Wildberries",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="amount / raw_payload->ppvz_reward",
        default_method="order_id_match",
        default_confidence="high",
        reason="Отдельный денежный компонент из raw поля ppvz_reward; в UI показывается нейтрально как вознаграждение Wildberries без лишней трактовки.",
    ),
    ExpenseSubtypeModel(
        code="other_logistics_rebill",
        label="Возмещение издержек по перевозке/складским операциям",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="rebill_logistic_cost",
        default_method="barcode_match",
        default_confidence="high",
        reason="Отдельно выделенное возмещение логистических и складских издержек WB.",
    ),
    ExpenseSubtypeModel(
        code="other_pvz_reimbursement",
        label="Возмещение за выдачу и возврат на ПВЗ",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="amount",
        default_method="barcode_match",
        default_confidence="high",
        reason="Компенсационные строки WB за выдачу и возврат товаров на ПВЗ.",
    ),
    ExpenseSubtypeModel(
        code="other_product_expense_tail",
        label="Прочие товарные расходы",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="amount",
        default_method="order_id_match",
        default_confidence="high",
        reason="Мелкий остаток товарных расходов, который пока не выделен в отдельную подстатью.",
    ),
    ExpenseSubtypeModel(
        code="sale_linked_service",
        label="Товарные сервисы, связанные с продажей",
        default_group="product-level",
        source_layer="stg_wb_finance.service_amount",
        source_field="service_amount",
        default_method="order_id_match",
        default_confidence="high",
        reason="Услуги, пришедшие в service_amount и связанные с продажей.",
    ),
    ExpenseSubtypeModel(
        code="review_service",
        label="Отзывы",
        default_group="product-level",
        source_layer="stg_wb_finance.amount",
        source_field="amount / raw_payload->bonus_type_name",
        default_method="nm_id_match",
        default_confidence="high",
        reason="Расходы за отзывы, привязанные к товару через nm_id в bonus_type_name.",
    ),
    ExpenseSubtypeModel(
        code="other_service",
        label="Прочие товарные услуги",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="amount, service_amount",
        default_method="nm_id_match",
        default_confidence="high",
        reason="Прочие сервисы, которые честно попали на товар.",
    ),
    ExpenseSubtypeModel(
        code="acquiring",
        label="Эквайринг",
        default_group="product-level",
        source_layer="stg_wb_finance",
        source_field="acquiring_fee / service_amount",
        default_method="order_id_match",
        default_confidence="high",
        reason="Эквайринг выделяется отдельно, когда service_amount фактически совпадает с acquiring_fee.",
    ),
    ExpenseSubtypeModel(
        code="jam_subscription",
        label="Подписка Джем",
        default_group="store-level",
        source_layer="stg_wb_finance.amount",
        source_field="amount / raw_payload->bonus_type_name",
        default_method="store_level",
        default_confidence="high",
        reason="Подписка кабинета WB без честной SKU-привязки.",
        product_visible=False,
    ),
    ExpenseSubtypeModel(
        code="other_store_service",
        label="Прочие магазинные сервисы",
        default_group="store-level",
        source_layer="stg_wb_finance",
        source_field="amount, service_amount",
        default_method="store_level",
        default_confidence="high",
        reason="Store-only сервисы без честной товарной привязки.",
        product_visible=False,
    ),
    ExpenseSubtypeModel(
        code="wb_promotion_overlap_excluded",
        label="WB Продвижение",
        default_group="store-level",
        source_layer="stg_wb_finance.amount",
        source_field="amount / raw_payload->bonus_type_name",
        default_method="excluded_before_attribution",
        default_confidence="high",
        reason="Исключено из расходов как финансовое отражение уже учтенной рекламы.",
        product_visible=False,
        cabinet_visible=False,
    ),
]


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _lower(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _match_any(text_value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text_value for marker in markers)


def _same_money(left: Any, right: Any) -> bool:
    return _money(left) == _money(right)


def _choose_group(model: ExpenseSubtypeModel, total: Decimal, mixed_amount: Decimal) -> str:
    if total == Decimal("0.00"):
        return model.default_group
    if mixed_amount > Decimal("0.00"):
        return "mixed / heuristic"
    return model.default_group


def _choose_confidence(values: set[str], default: str) -> str:
    cleaned = [value for value in values if value]
    if not cleaned:
        return default
    ordered = []
    for candidate in ("high", "medium", "low"):
        if candidate in cleaned:
            ordered.append(candidate)
    for value in sorted(cleaned):
        if value not in ordered:
            ordered.append(value)
    return ", ".join(ordered)


def _classify_fact_subtype(row: dict[str, Any]) -> str:
    money_bucket = _lower(row.get("money_bucket"))
    if money_bucket == "payment_processing":
        return "payment_processing"
    if money_bucket == "commission":
        return "commission_core"
    if money_bucket == "returns":
        finance_amount = _money(row.get("finance_amount"))
        finance_ppvz_reward = _money(row.get("finance_ppvz_reward"))
        if (
            finance_amount > Decimal("0.00")
            and finance_ppvz_reward > Decimal("0.00")
            and _same_money(finance_amount, finance_ppvz_reward)
        ):
            return "wb_reward_return_adjustment"
        return "returns"
    if money_bucket == "penalties":
        operation_type = _lower(row.get("finance_operation_type"))
        supplier_oper_name = _lower(row.get("finance_supplier_oper_name"))
        doc_type_name = _lower(row.get("finance_doc_type_name"))
        bonus_type_name = _lower(row.get("finance_bonus_type_name"))
        combined_text = " | ".join(
            value for value in (operation_type, supplier_oper_name, doc_type_name, bonus_type_name) if value
        )
        if "пвз" in combined_text:
            return "pvz_related_penalty"
        return "penalties"
    if money_bucket != "services":
        if money_bucket == "other":
            operation_type = _lower(row.get("finance_operation_type"))
            supplier_oper_name = _lower(row.get("finance_supplier_oper_name"))
            doc_type_name = _lower(row.get("finance_doc_type_name"))
            rebill_logistic_cost = _money(row.get("finance_rebill_logistic_cost"))
            combined_text = " | ".join(
                value for value in (operation_type, supplier_oper_name, doc_type_name) if value
            )
            if rebill_logistic_cost > Decimal("0.00") or "возмещение издержек по перевозке" in combined_text:
                return "other_logistics_rebill"
            if "возмещение за выдачу и возврат товаров на пвз" in combined_text:
                return "other_pvz_reimbursement"
            if operation_type == "продажа" or supplier_oper_name == "продажа" or doc_type_name == "продажа":
                return "other_sale_adjustment"
            return "other_product_expense_tail"
        return money_bucket

    bonus_type_name = _lower(row.get("finance_bonus_type_name"))
    if _match_any(bonus_type_name, REVIEW_MARKERS):
        return "review_service"
    if _match_any(bonus_type_name, JAM_MARKERS):
        return "jam_subscription"

    service_amount = _money(row.get("finance_service_amount"))
    acquiring_fee = _money(row.get("finance_acquiring_fee"))
    if service_amount > Decimal("0.00") and acquiring_fee > Decimal("0.00") and _same_money(service_amount, acquiring_fee):
        return "acquiring"
    if service_amount > Decimal("0.00"):
        return "sale_linked_service"

    if row.get("canonical_product_key") == "~":
        return "other_store_service"
    return "other_service"


def _expense_model_map() -> dict[str, ExpenseSubtypeModel]:
    return {item.code: item for item in EXPENSE_SUBTYPES}


def latest_wb_period(conn: Connection[Any], schema: str) -> tuple[str, str]:
    sql = f"""
    SELECT period_start::text AS period_start, period_end::text AS period_end
    FROM {schema}.mart_store_summary_30d
    WHERE marketplace = 'wb'
    ORDER BY period_end DESC, run_date DESC
    LIMIT 1
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        raise RuntimeError("WB period not found in mart_store_summary_30d")
    return str(row["period_start"]), str(row["period_end"])


def fetch_wb_expense_fact_rows(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    sql = f"""
    WITH bridge_pick AS (
      SELECT canonical_product_key, offer_id
      FROM (
        SELECT
          canonical_product_key,
          offer_id,
          ROW_NUMBER() OVER (
            PARTITION BY canonical_product_key
            ORDER BY match_confidence DESC, bridge_id
          ) AS rn
        FROM {schema}.dim_product_bridge
        WHERE marketplace = 'wb'
          AND NULLIF(offer_id, '~') IS NOT NULL
      ) ranked
      WHERE rn = 1
    )
    SELECT
      f.event_date::text AS event_date,
      f.canonical_product_key,
      COALESCE(bp.offer_id, '~') AS offer_id,
      f.money_bucket,
      f.amount,
      f.source_table,
      f.source_record_id,
      f.attribution_method,
      f.attribution_confidence,
      wf.operation_type AS finance_operation_type,
      wf.supplier_oper_name AS finance_supplier_oper_name,
      wf.doc_type_name AS finance_doc_type_name,
      wf.raw_payload->>'bonus_type_name' AS finance_bonus_type_name,
      wf.amount AS finance_amount,
      wf.service_amount AS finance_service_amount,
      wf.acquiring_fee AS finance_acquiring_fee,
      wf.rebill_logistic_cost AS finance_rebill_logistic_cost,
      wf.commission_amount AS finance_commission_amount,
      wf.raw_payload->>'ppvz_reward' AS finance_ppvz_reward,
      wf.raw_payload->>'acquiring_percent' AS finance_acquiring_percent,
      wf.raw_payload->>'seller_promo_discount' AS finance_seller_promo_discount,
      wf.raw_payload->>'loyalty_discount' AS finance_loyalty_discount
    FROM {schema}.fact_product_money_attribution f
    LEFT JOIN bridge_pick bp
      ON bp.canonical_product_key = f.canonical_product_key
    LEFT JOIN {schema}.stg_wb_finance wf
      ON f.source_table = 'stg_wb_finance'
     AND wf.finance_id = split_part(f.source_record_id, ':', 1)
    WHERE f.marketplace = 'wb'
      AND f.money_component_type = 'expense'
      AND f.event_date BETWEEN %s AND %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (period_start, period_end))
        return list(cur.fetchall())


def fetch_raw_wb_promotion_amount(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> Decimal:
    sql = f"""
    SELECT COALESCE(SUM(amount), 0)::numeric(18,2) AS amount
    FROM {schema}.stg_wb_finance
    WHERE COALESCE(marketplace, 'wb') = 'wb'
      AND operation_date::date BETWEEN %s AND %s
      AND LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%wb продвижение%%'
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (period_start, period_end))
        row = cur.fetchone()
    return _money(row["amount"] if row else 0)


def build_wb_expense_granularity_matrix(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    models = _expense_model_map()
    fact_rows = fetch_wb_expense_fact_rows(conn, schema, period_start, period_end)

    grouped: dict[str, dict[str, Any]] = {
        model.code: {
            "amount": Decimal("0.00"),
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": Decimal("0.00"),
            "source_layers": set(),
            "source_fields": set(),
            "attribution_methods": set(),
            "confidences": set(),
        }
        for model in EXPENSE_SUBTYPES
    }

    for row in fact_rows:
        subtype = _classify_fact_subtype(row)
        if subtype not in grouped:
            continue
        amount = _money(row.get("amount"))
        state = grouped[subtype]
        state["amount"] += amount
        if row.get("canonical_product_key") == "~":
            state["store_level_amount"] += amount
        else:
            state["product_level_amount"] += amount
        if _lower(row.get("attribution_method")) in HEURISTIC_METHODS:
            state["mixed_amount"] += amount
        state["source_layers"].add(_lower(row.get("source_table")))
        state["source_fields"].add(models[subtype].source_field)
        state["attribution_methods"].add(_lower(row.get("attribution_method")))
        state["confidences"].add(_lower(row.get("attribution_confidence")))

    rows: list[dict[str, Any]] = []
    for model in EXPENSE_SUBTYPES:
        state = grouped[model.code]
        amount = state["amount"]
        if model.code == "wb_promotion_overlap_excluded":
            amount = Decimal("0.00")
        rows.append(
            {
                "bucket_group": _choose_group(model, amount, state["mixed_amount"]),
                "bucket_subtype": model.code,
                "label": model.label,
                "amount": amount,
                "product_level_amount": state["product_level_amount"],
                "store_level_amount": state["store_level_amount"],
                "mixed_amount": state["mixed_amount"],
                "source_field": ", ".join(sorted(field for field in state["source_fields"] if field)) or model.source_field,
                "source_table": ", ".join(sorted(layer for layer in state["source_layers"] if layer)) or model.source_layer,
                "source_layer": ", ".join(sorted(layer for layer in state["source_layers"] if layer)) or model.source_layer,
                "attribution_method": ", ".join(sorted(method for method in state["attribution_methods"] if method)) or model.default_method,
                "confidence": _choose_confidence(state["confidences"], model.default_confidence),
                "reason": model.reason,
                "should_be_visible_in_cabinet_yes_no": "yes" if model.cabinet_visible else "no",
            }
        )
    return rows


def build_wb_product_expense_breakdown(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> dict[str, list[dict[str, Any]]]:
    models = _expense_model_map()
    fact_rows = fetch_wb_expense_fact_rows(conn, schema, period_start, period_end)
    by_offer: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: {
            model.code: {
                "amount": Decimal("0.00"),
                "mixed_amount": Decimal("0.00"),
                "source_layers": set(),
                "source_fields": set(),
                "attribution_methods": set(),
                "confidences": set(),
            }
            for model in EXPENSE_SUBTYPES
            if model.product_visible
        }
    )

    for row in fact_rows:
        if row.get("canonical_product_key") == "~":
            continue
        offer_id = str(row.get("offer_id") or "~")
        subtype = _classify_fact_subtype(row)
        model = models.get(subtype)
        if not model or not model.product_visible:
            continue
        amount = _money(row.get("amount"))
        state = by_offer[offer_id][subtype]
        state["amount"] += amount
        if _lower(row.get("attribution_method")) in HEURISTIC_METHODS:
            state["mixed_amount"] += amount
        state["source_layers"].add(_lower(row.get("source_table")))
        state["source_fields"].add(model.source_field)
        state["attribution_methods"].add(_lower(row.get("attribution_method")))
        state["confidences"].add(_lower(row.get("attribution_confidence")))

    result: dict[str, list[dict[str, Any]]] = {}
    for offer_id, subtype_state in by_offer.items():
        rows: list[dict[str, Any]] = []
        for model in EXPENSE_SUBTYPES:
            if not model.product_visible:
                continue
            state = subtype_state[model.code]
            rows.append(
                {
                    "bucket_group": _choose_group(model, state["amount"], state["mixed_amount"]),
                    "bucket_subtype": model.code,
                    "label": model.label,
                    "amount": state["amount"],
                    "product_level_amount": state["amount"],
                    "store_level_amount": Decimal("0.00"),
                    "mixed_amount": state["mixed_amount"],
                    "source_field": ", ".join(sorted(field for field in state["source_fields"] if field)) or model.source_field,
                    "source_table": ", ".join(sorted(layer for layer in state["source_layers"] if layer)) or model.source_layer,
                    "source_layer": ", ".join(sorted(layer for layer in state["source_layers"] if layer)) or model.source_layer,
                    "attribution_method": ", ".join(sorted(method for method in state["attribution_methods"] if method)) or model.default_method,
                    "confidence": _choose_confidence(state["confidences"], model.default_confidence),
                    "reason": model.reason,
                    "should_be_visible_in_cabinet_yes_no": "yes" if model.cabinet_visible else "no",
                }
            )
        result[offer_id] = rows
    return result


def build_wb_services_split(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    matrix = {row["bucket_subtype"]: row for row in build_wb_expense_granularity_matrix(conn, schema, period_start, period_end)}
    promo_raw = fetch_raw_wb_promotion_amount(conn, schema, period_start, period_end)

    rows = [
        {
            "service_subtype": "acquiring",
            "raw_source_amount": matrix["acquiring"]["amount"],
            "final_model_amount": matrix["acquiring"]["amount"],
            "product_level_amount": matrix["acquiring"]["product_level_amount"],
            "store_level_amount": matrix["acquiring"]["store_level_amount"],
            "status": "extracted_from_sale_linked_service",
            "reason": "Эквайринг выделен отдельно, потому что service_amount полностью совпадает с acquiring_fee.",
        },
        {
            "service_subtype": "sale_linked_service",
            "raw_source_amount": matrix["sale_linked_service"]["amount"],
            "final_model_amount": matrix["sale_linked_service"]["amount"],
            "product_level_amount": matrix["sale_linked_service"]["product_level_amount"],
            "store_level_amount": matrix["sale_linked_service"]["store_level_amount"],
            "status": "kept",
            "reason": "Продажно-связанные service_amount строки оставлены в SKU-экономике.",
        },
        {
            "service_subtype": "review_service",
            "raw_source_amount": matrix["review_service"]["amount"],
            "final_model_amount": matrix["review_service"]["amount"],
            "product_level_amount": matrix["review_service"]["product_level_amount"],
            "store_level_amount": matrix["review_service"]["store_level_amount"],
            "status": "kept",
            "reason": "Списания за отзывы остаются товарной услугой.",
        },
        {
            "service_subtype": "other_service",
            "raw_source_amount": matrix["other_service"]["amount"],
            "final_model_amount": matrix["other_service"]["amount"],
            "product_level_amount": matrix["other_service"]["product_level_amount"],
            "store_level_amount": matrix["other_service"]["store_level_amount"],
            "status": "kept_if_present",
            "reason": "Фолбэк для других товарных сервисов.",
        },
        {
            "service_subtype": "wb_promotion",
            "raw_source_amount": promo_raw,
            "final_model_amount": matrix["wb_promotion_overlap_excluded"]["amount"],
            "product_level_amount": matrix["wb_promotion_overlap_excluded"]["product_level_amount"],
            "store_level_amount": matrix["wb_promotion_overlap_excluded"]["store_level_amount"],
            "status": "excluded_as_ads_overlap",
            "reason": "Финансовое отражение рекламы исключено как дубль ads-layer.",
        },
        {
            "service_subtype": "jam_subscription",
            "raw_source_amount": matrix["jam_subscription"]["amount"],
            "final_model_amount": matrix["jam_subscription"]["amount"],
            "product_level_amount": matrix["jam_subscription"]["product_level_amount"],
            "store_level_amount": matrix["jam_subscription"]["store_level_amount"],
            "status": "kept_store_only",
            "reason": "Джем остается только на магазине.",
        },
        {
            "service_subtype": "other_store_service",
            "raw_source_amount": matrix["other_store_service"]["amount"],
            "final_model_amount": matrix["other_store_service"]["amount"],
            "product_level_amount": matrix["other_store_service"]["product_level_amount"],
            "store_level_amount": matrix["other_store_service"]["store_level_amount"],
            "status": "kept_if_present",
            "reason": "Прочие store-only сервисы сохраняются как магазинные расходы.",
        },
    ]
    return rows


def build_wb_acquiring_check(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      COUNT(*) FILTER (WHERE COALESCE(acquiring_fee, 0) <> 0)::int AS acquiring_rows,
      COALESCE(SUM(acquiring_fee), 0)::numeric(18,2) AS raw_acquiring_fee_total,
      COALESCE(SUM(service_amount), 0)::numeric(18,2) AS raw_service_amount_total,
      COUNT(*) FILTER (
        WHERE COALESCE(acquiring_fee, 0) <> 0
          AND COALESCE(service_amount, 0) <> 0
      )::int AS overlap_rows,
      COUNT(*) FILTER (
        WHERE COALESCE(acquiring_fee, 0) <> 0
          AND COALESCE(service_amount, 0) = COALESCE(acquiring_fee, 0)
      )::int AS equal_rows,
      COALESCE(SUM(
        CASE
          WHEN COALESCE(acquiring_fee, 0) <> 0
           AND COALESCE(service_amount, 0) = COALESCE(acquiring_fee, 0)
          THEN acquiring_fee
          ELSE 0
        END
      ), 0)::numeric(18,2) AS exact_overlap_total,
      COALESCE(SUM(
        CASE
          WHEN COALESCE(service_amount, 0) <> COALESCE(acquiring_fee, 0)
          THEN service_amount
          ELSE 0
        END
      ), 0)::numeric(18,2) AS service_non_acquiring_total
    FROM {schema}.stg_wb_finance
    WHERE COALESCE(marketplace, 'wb') = 'wb'
      AND operation_date::date BETWEEN %s AND %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (period_start, period_end))
        raw_totals = cur.fetchone() or {}

    matrix = {row["bucket_subtype"]: row for row in build_wb_expense_granularity_matrix(conn, schema, period_start, period_end)}
    acquiring_total = matrix["acquiring"]["amount"]
    sale_linked_total = matrix["sale_linked_service"]["amount"]

    rows: list[dict[str, Any]] = [
        {
            "layer": "stg_wb_finance_raw",
            "check_name": "raw_acquiring_fee_total",
            "fields_checked": "acquiring_fee",
            "rows_total": int(raw_totals.get("acquiring_rows") or 0),
            "amount_total": _money(raw_totals.get("raw_acquiring_fee_total")),
            "why": "Сумма raw acquiring_fee в finance.",
        },
        {
            "layer": "stg_wb_finance_service",
            "check_name": "raw_service_amount_total",
            "fields_checked": "service_amount",
            "rows_total": int(raw_totals.get("acquiring_rows") or 0),
            "amount_total": _money(raw_totals.get("raw_service_amount_total")),
            "why": "Сумма service_amount на том же слое raw finance.",
        },
        {
            "layer": "stg_wb_finance_overlap",
            "check_name": "exact_overlap_acquiring_vs_service_amount",
            "fields_checked": "acquiring_fee = service_amount",
            "rows_total": int(raw_totals.get("equal_rows") or 0),
            "amount_total": _money(raw_totals.get("exact_overlap_total")),
            "why": "Показывает, какая часть service_amount фактически является эквайрингом.",
        },
        {
            "layer": "fact_product_money_attribution",
            "check_name": "acquiring_in_final_model",
            "fields_checked": "bucket_subtype = acquiring",
            "rows_total": 0 if acquiring_total == Decimal("0.00") else 1,
            "amount_total": acquiring_total,
            "why": "Финальная подстатья эквайринга после выделения из services.",
        },
        {
            "layer": "model_structure",
            "check_name": "sale_linked_service_residual",
            "fields_checked": "bucket_subtype = sale_linked_service",
            "rows_total": 0 if sale_linked_total == Decimal("0.00") else 1,
            "amount_total": sale_linked_total,
            "why": "Остаток sale_linked_service после выделения эквайринга.",
        },
    ]
    return rows


def build_wb_other_breakdown(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    matrix = {row["bucket_subtype"]: row for row in build_wb_expense_granularity_matrix(conn, schema, period_start, period_end)}
    fact_rows = fetch_wb_expense_fact_rows(conn, schema, period_start, period_end)
    counts: dict[str, int] = defaultdict(int)
    for row in fact_rows:
        subtype = _classify_fact_subtype(row)
        if subtype.startswith("other_"):
            counts[subtype] += 1
    mapping = [
        (
            "other_sale_adjustment",
            "Вознаграждение Wildberries",
            "yes",
        ),
        (
            "other_logistics_rebill",
            "Возмещение издержек по перевозке/складским операциям",
            "yes",
        ),
        (
            "other_pvz_reimbursement",
            "Возмещение за выдачу и возврат на ПВЗ",
            "yes",
        ),
        (
            "other_product_expense_tail",
            "Прочий хвост other",
            "no",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for subtype, raw_name, can_be_separate in mapping:
        row = matrix[subtype]
        product_level_amount = _money(row["product_level_amount"])
        store_level_amount = _money(row["store_level_amount"])
        level = "mixed" if _money(row["mixed_amount"]) > Decimal("0.00") else ("store" if store_level_amount > Decimal("0.00") else "product")
        rows.append(
            {
                "subtype_name": raw_name,
                "bucket_subtype": subtype,
                "rows": counts.get(subtype, 0),
                "amount": row["amount"],
                "product_store_level": level,
                "can_be_separate_bucket_yes_no": can_be_separate,
                "source_field": row["source_field"],
                "source_table": row["source_table"],
                "confidence": row["confidence"],
            }
        )
    return rows


def build_wb_commission_granularity(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    matrix = {row["bucket_subtype"]: row for row in build_wb_expense_granularity_matrix(conn, schema, period_start, period_end)}
    sql = f"""
    SELECT
      COUNT(*) FILTER (WHERE COALESCE(commission_amount, 0) <> 0)::int AS commission_rows,
      COALESCE(SUM(ABS(COALESCE(commission_amount, 0))), 0)::numeric(18,2) AS commission_abs_total,
      COUNT(*) FILTER (WHERE COALESCE(acquiring_fee, 0) <> 0)::int AS acquiring_rows,
      COALESCE(SUM(acquiring_fee), 0)::numeric(18,2) AS acquiring_total,
      COUNT(*) FILTER (
        WHERE NULLIF(COALESCE(raw_payload->>'seller_promo_discount', ''), '') IS NOT NULL
          AND COALESCE(raw_payload->>'seller_promo_discount', '0') <> '0'
      )::int AS seller_discount_rows,
      COALESCE(SUM(
        CASE
          WHEN NULLIF(COALESCE(raw_payload->>'seller_promo_discount', ''), '') IS NOT NULL
           AND COALESCE(raw_payload->>'seller_promo_discount', '0') <> '0'
          THEN (raw_payload->>'seller_promo_discount')::numeric
          ELSE 0
        END
      ), 0)::numeric(18,2) AS seller_discount_total,
      COUNT(*) FILTER (
        WHERE NULLIF(COALESCE(raw_payload->>'loyalty_discount', ''), '') IS NOT NULL
          AND COALESCE(raw_payload->>'loyalty_discount', '0') <> '0'
      )::int AS loyalty_discount_rows,
      COALESCE(SUM(
        CASE
          WHEN NULLIF(COALESCE(raw_payload->>'loyalty_discount', ''), '') IS NOT NULL
           AND COALESCE(raw_payload->>'loyalty_discount', '0') <> '0'
          THEN (raw_payload->>'loyalty_discount')::numeric
          ELSE 0
        END
      ), 0)::numeric(18,2) AS loyalty_discount_total
    FROM {schema}.stg_wb_finance
    WHERE COALESCE(marketplace, 'wb') = 'wb'
      AND operation_date::date BETWEEN %s AND %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (period_start, period_end))
        raw = cur.fetchone() or {}

    return [
        {
            "bucket_subtype": "commission_core",
            "label": "Комиссия маркетплейса",
            "amount": matrix["commission_core"]["amount"],
            "rows": int(raw.get("commission_rows") or 0),
            "product_store_level": "product",
            "source_field": "commission_amount",
            "confidence": matrix["commission_core"]["confidence"],
        },
        {
            "bucket_subtype": "acquiring",
            "label": "Эквайринг",
            "amount": matrix["acquiring"]["amount"],
            "rows": int(raw.get("acquiring_rows") or 0),
            "product_store_level": "product",
            "source_field": "acquiring_fee / service_amount",
            "confidence": matrix["acquiring"]["confidence"],
        },
        {
            "bucket_subtype": "payment_processing",
            "label": "Организация платежа",
            "amount": matrix["payment_processing"]["amount"],
            "rows": 0 if matrix["payment_processing"]["amount"] == Decimal("0.00") else 1,
            "product_store_level": "product",
            "source_field": "payment_processing",
            "confidence": matrix["payment_processing"]["confidence"],
        },
        {
            "bucket_subtype": "seller_promo_discount",
            "label": "Скидочные корректировки продавца",
            "amount": _money(raw.get("seller_discount_total")),
            "rows": int(raw.get("seller_discount_rows") or 0),
            "product_store_level": "raw_only",
            "source_field": "raw_payload->seller_promo_discount",
            "confidence": "low" if _money(raw.get("seller_discount_total")) == Decimal("0.00") else "medium",
        },
        {
            "bucket_subtype": "loyalty_discount",
            "label": "Бонусные корректировки лояльности",
            "amount": _money(raw.get("loyalty_discount_total")),
            "rows": int(raw.get("loyalty_discount_rows") or 0),
            "product_store_level": "raw_only",
            "source_field": "raw_payload->loyalty_discount",
            "confidence": "low" if _money(raw.get("loyalty_discount_total")) == Decimal("0.00") else "medium",
        },
    ]


def build_cabinet_expense_structure_before_after(
    conn: Connection[Any],
    schema: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    matrix = {row["bucket_subtype"]: row for row in build_wb_expense_granularity_matrix(conn, schema, period_start, period_end)}
    withholding_total = (
        matrix["jam_subscription"]["amount"]
        + matrix["other_store_service"]["amount"]
        + matrix["sale_linked_service"]["amount"]
        + matrix["review_service"]["amount"]
        + matrix["other_service"]["amount"]
        + matrix["other_sale_adjustment"]["amount"]
        + matrix["other_logistics_rebill"]["amount"]
        + matrix["other_pvz_reimbursement"]["amount"]
        + matrix["other_product_expense_tail"]["amount"]
        + matrix["penalties"]["amount"]
    )
    rows = [
        {
            "stage": "before",
            "scope": "store",
            "article": "ads",
            "amount": matrix["ads"]["amount"],
            "note": "Старая структура кабинета показывала рекламу отдельной укрупненной статьей.",
        },
        {
            "stage": "before",
            "scope": "store",
            "article": "logistics",
            "amount": matrix["logistics"]["amount"],
            "note": "Старая структура кабинета показывала логистику одной строкой.",
        },
        {
            "stage": "before",
            "scope": "store",
            "article": "commission",
            "amount": matrix["commission_core"]["amount"],
            "note": "Старая структура кабинета показывала комиссию одной строкой.",
        },
        {
            "stage": "before",
            "scope": "store",
            "article": "acquiring",
            "amount": matrix["acquiring"]["amount"],
            "note": "Старая структура кабинета показывала эквайринг одной строкой.",
        },
        {
            "stage": "before",
            "scope": "store",
            "article": "storage",
            "amount": matrix["storage"]["amount"],
            "note": "Старая структура кабинета показывала хранение одной строкой.",
        },
        {
            "stage": "before",
            "scope": "store",
            "article": "withholding",
            "amount": withholding_total,
            "note": "Старая структура кабинета смешивала services + other + penalties в один withholding.",
        },
    ]
    for row in build_wb_expense_granularity_matrix(conn, schema, period_start, period_end):
        scope = "product"
        if row["bucket_group"] == "store-level":
            scope = "store"
        elif row["bucket_group"] == "mixed / heuristic":
            scope = "mixed"
        rows.append(
            {
                "stage": "after",
                "scope": scope,
                "article": row["bucket_subtype"],
                "amount": row["amount"],
                "note": row["reason"],
            }
        )
    return rows
