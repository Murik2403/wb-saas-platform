"""Pure pinned-route expansion with a shared, dated intermediate stock ledger."""
from __future__ import annotations
from datetime import date
import math

class RouteError(ValueError):
    def __init__(self, code, message):
        super().__init__(message); self.code = code

class NestedDemandResolver:
    def __init__(self, snapshot):
        self.boms = {int(b["id"]): b for b in snapshot.get("boms", [])}
        self.catalog = {int(c["nm_id"]): c for c in snapshot.get("catalog_identities", [])}
        self.layers = [dict(l, remaining=int(l.get("ready_units") or 0)) for l in snapshot.get("intermediate_layers", []) if l.get("status") != "reversed"]
        self.allocations = []
        self.planned = []

    def identity(self, nm_id, chrt_id):
        card = self.catalog.get(nm_id)
        if not card or not card.get("valid") or chrt_id not in card.get("chrt_ids", []):
            raise RouteError("invalid_nested_variant", "Полуфабрикат или его вариант отсутствует в проверенном каталоге.")

    def child(self, component):
        try:
            nm, chrt = int(component["nm_id"]), int(component.get("chrt_id") or 0)
            self.identity(nm, chrt)
            pinned = int(component["component_bom_version_id"])
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, RouteError): raise
            raise RouteError("missing_nested_version", "Для полуфабриката не закреплена версия спецификации.") from exc
        bom = self.boms.get(pinned)
        if not bom or int(bom["nm_id"]) != nm or int(bom.get("chrt_id") or 0) not in {0, chrt}:
            raise RouteError("missing_nested_bom", "Закреплённая спецификация полуфабриката не найдена или относится к другому варианту.")
        if component.get("unit") != "шт":
            raise RouteError("invalid_nested_unit", "Полуфабрикат должен учитываться в штуках.")
        return nm, chrt, bom

    def validate(self, nm, chrt, bom, units, path=()):
        identity = (nm, chrt)
        if identity in path: raise RouteError("nested_cycle", "В спецификациях найден цикл полуфабрикатов.")
        if len(path) > 8: raise RouteError("nested_depth", "Маршрут длиннее восьми вложенных этапов.")
        if not bom.get("materials"): raise RouteError("empty_bom", "У этапа нет компонентов спецификации.")
        height = 0
        for component in bom["materials"]:
            try: required = units * float(component["quantity_per_unit"])
            except (KeyError, TypeError, ValueError): required = float("nan")
            if not math.isfinite(required) or required <= 0:
                raise RouteError("invalid_bom_component", "Расход компонента должен быть конечным положительным числом.")
            kind = component.get("component_type") or "raw_material"
            if kind == "semi_finished":
                self.identity(nm, chrt)
                child_nm, child_chrt, child_bom = self.child(component)
                if not required.is_integer(): raise RouteError("fractional_nested_units", "Для этапа требуется дробное число полуфабрикатов.")
                height = max(height, 1 + self.validate(child_nm, child_chrt, child_bom, required, (*path, identity)))
            elif kind != "raw_material" or not component.get("material_key") or not component.get("material_name") or not component.get("unit"):
                raise RouteError("invalid_bom_component", "У сырья не указаны имя, единица или корректный тип.")
        return height

    def reserve(self, nm, chrt, required, due, task_key, pinned):
        remaining = required
        used = []
        candidates = [l for l in self.layers + self.planned if int(l["nm_id"]) == nm and int(l.get("chrt_id") or 0) == chrt and l["remaining"] > 0]
        for layer in sorted(candidates, key=lambda l: (str(l.get("source_date") or "1900-01-01"), int(l.get("id") or 0), str(l.get("planned_task_key") or ""))):
            source_day = str(layer.get("source_date") or "")[:10]
            try:
                if source_day: date.fromisoformat(source_day)
            except ValueError: raise RouteError("invalid_intermediate_date", "У партии полуфабриката некорректная дата.")
            if source_day and source_day > due: continue
            if layer.get("source_type") == "reconstruction_cost_pending" or layer.get("producer_status") == "reversed":
                raise RouteError("invalid_intermediate_provenance", "У доступного полуфабриката не подтверждено происхождение FIFO.")
            take = min(remaining, layer["remaining"])
            if take <= 0: break
            layer["remaining"] -= take; remaining -= take
            used.append({"layer_id": layer.get("id"), "planned_task_key": layer.get("planned_task_key"), "units": take,
                         "source_date": source_day or None, "producer_release_id": layer.get("producer_release_id"),
                         "source_bom_version_id": layer.get("producer_bom_version_id"), "required_bom_version_id": pinned})
            if remaining == 0: break
        self.allocations.append({"task_key": task_key, "nm_id": nm, "chrt_id": chrt, "due_date": due,
                                 "required_units": required, "covered_units": required - remaining,
                                 "produce_units": remaining, "bom_version_id": pinned, "allocations": used})
        return remaining

    def expand(self, nm, chrt, bom, units, due, task_key, chain=()):
        route = [*chain, {"nm_id": nm, "chrt_id": chrt, "bom_version_id": int(bom["id"]), "bom_version": int(bom.get("version") or 0)}]
        for component in bom["materials"]:
            required = units * float(component["quantity_per_unit"])
            if not math.isfinite(required) or required <= 0:
                raise RouteError("invalid_bom_component", "Расход компонента должен быть конечным положительным числом.")
            if component.get("component_type") == "semi_finished":
                if not required.is_integer():
                    raise RouteError("fractional_nested_units", "После покрытия остатком требуется дробное число полуфабрикатов.")
                child_nm, child_chrt, child_bom = self.child(component)
                missing = self.reserve(child_nm, child_chrt, int(required), due, task_key, int(child_bom["id"]))
                if missing: yield from self.expand(child_nm, child_chrt, child_bom, missing, due, task_key, route)
            else:
                yield {**component, "required_qty": required, "stage_units": units, "stage_nm_id": nm,
                       "stage_chrt_id": chrt, "stage_bom": bom, "route": route}

    def register_planned(self, nm, chrt, bom, units, due, task_key):
        self.planned.append({"nm_id": nm, "chrt_id": chrt, "remaining": int(units), "ready_units": int(units),
                             "source_date": due, "planned_task_key": task_key, "producer_bom_version_id": int(bom["id"])})
