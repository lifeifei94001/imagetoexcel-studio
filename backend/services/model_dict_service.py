import re
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, desc, select

from models import ModelCodeMapping, utc_now_str
from services.ocr_service import (
    _detect_column_roles,
    _is_series_or_header_model,
    _norm_compare_token,
)

_DUAL_CANDIDATES_RE = re.compile(r"（[^：:]+[:：]\s*(.*?)\s*/\s*[^：:]+[:：]\s*(.*?)）")
_INVALID_CODE_TOKENS = {"", "-", "—", "无", "不详", "未知", "网络型号", "小型号", "具体型号", "型号"}


def normalize_dict_token(val: str) -> str:
    return _norm_compare_token(val)


def _is_valid_mapping_pair(model_name: str, network_code: str) -> bool:
    m = re.sub(r"\s+", " ", str(model_name or "")).strip()
    c = re.sub(r"\s+", " ", str(network_code or "")).strip()
    if not m or not c:
        return False
    if len(m) > 80 or len(c) > 80:
        return False
    if _is_series_or_header_model(m):
        return False
    if c.lower() in _INVALID_CODE_TOKENS or m.lower() in _INVALID_CODE_TOKENS:
        return False
    # 网络型号至少应包含一个字母或数字
    if not re.search(r"[A-Za-z0-9]", c):
        return False
    return True


def list_mappings(session: Session, query: str = "", limit: int = 500) -> List[ModelCodeMapping]:
    stmt = select(ModelCodeMapping).order_by(desc(ModelCodeMapping.hit_count), desc(ModelCodeMapping.id))
    rows = list(session.exec(stmt).all())
    q = (query or "").strip().lower()
    if q:
        q_norm = normalize_dict_token(q)
        rows = [
            r
            for r in rows
            if q in r.model_name.lower()
            or q in r.network_code.lower()
            or q in (r.brand_hint or "").lower()
            or (q_norm and (q_norm in r.norm_model or q_norm in r.norm_code))
        ]
    return rows[: max(1, min(limit, 2000))]


def count_mappings(session: Session) -> int:
    rows = session.exec(select(ModelCodeMapping.id)).all()
    return len(rows)


def upsert_mappings(
    session: Session,
    entries: List[Dict[str, Any]],
    default_source: str = "manual",
) -> Dict[str, Any]:
    """批量写入/更新「机型 ↔ 网络型号」映射（按 norm_model + norm_code 自动去重，并更新展示写法）。"""
    existing_all = list(session.exec(select(ModelCodeMapping)).all())
    by_pair: Dict[Tuple[str, str], ModelCodeMapping] = {
        (r.norm_model, r.norm_code): r for r in existing_all
    }
    now_str = utc_now_str()
    created = 0
    updated = 0
    skipped = 0

    for item in entries:
        raw_m = re.sub(r"\s+", " ", str(item.get("model_name") or "")).strip()
        raw_c = re.sub(r"\s+", " ", str(item.get("network_code") or "")).strip()
        brand = re.sub(r"\s+", " ", str(item.get("brand_hint") or "")).strip()[:32]
        src = str(item.get("source") or default_source or "manual").strip()[:24]

        if not _is_valid_mapping_pair(raw_m, raw_c):
            skipped += 1
            continue

        nm = normalize_dict_token(raw_m)
        nc = normalize_dict_token(raw_c)
        if not nm or not nc:
            skipped += 1
            continue

        pair_key = (nm, nc)
        existing = by_pair.get(pair_key)
        if existing:
            changed = False
            if existing.model_name != raw_m:
                existing.model_name = raw_m
                changed = True
            if existing.network_code != raw_c:
                existing.network_code = raw_c
                changed = True
            if brand and existing.brand_hint != brand:
                existing.brand_hint = brand
                changed = True
            if src == "manual" and existing.source != "manual":
                existing.source = "manual"
                changed = True
            if changed:
                existing.updated_at = now_str
                session.add(existing)
                updated += 1
            else:
                skipped += 1
        else:
            rec = ModelCodeMapping(
                model_name=raw_m,
                network_code=raw_c,
                norm_model=nm,
                norm_code=nc,
                brand_hint=brand,
                source=src,
                hit_count=0,
                updated_at=now_str,
            )
            session.add(rec)
            by_pair[pair_key] = rec
            created += 1

    if created > 0 or updated > 0:
        session.commit()

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total": len(by_pair),
    }


def delete_mapping(session: Session, mapping_id: int) -> bool:
    rec = session.get(ModelCodeMapping, mapping_id)
    if not rec:
        return False
    session.delete(rec)
    session.commit()
    return True


def clear_all_mappings(session: Session) -> int:
    rows = list(session.exec(select(ModelCodeMapping)).all())
    cnt = len(rows)
    for r in rows:
        session.delete(r)
    if cnt > 0:
        session.commit()
    return cnt


def build_lookup_index(session: Session) -> Dict[str, Any]:
    """单次轻量查询加载全部映射构建内存 O(1) 双向索引（毫秒级，零 AI Token）。"""
    rows = list(session.exec(select(ModelCodeMapping)).all())
    code_to_models: Dict[str, List[ModelCodeMapping]] = {}
    model_to_codes: Dict[str, List[ModelCodeMapping]] = {}
    for r in rows:
        if r.norm_code:
            code_to_models.setdefault(r.norm_code, []).append(r)
        if r.norm_model:
            model_to_codes.setdefault(r.norm_model, []).append(r)
    return {
        "count": len(rows),
        "code_to_models": code_to_models,
        "model_to_codes": model_to_codes,
    }


def _extract_dual_candidates_from_reason(reason: str) -> List[str]:
    """从双模型分歧告警原因中提取两个候选读数（过滤 '空'）。"""
    m = _DUAL_CANDIDATES_RE.search(str(reason or ""))
    if not m:
        return []
    out: List[str] = []
    for g in (m.group(1), m.group(2)):
        tok = (g or "").strip()
        if tok and tok != "空":
            out.append(tok)
    return out


def resolve_sheet_ambiguities_with_dict(
    sheet: Dict[str, Any],
    lookup_index: Dict[str, Any],
) -> Tuple[int, List[int]]:
    """
    仅在报价单出现「机型/网络型号」留空或双模型分歧标黄时，才查云端字典自动消歧：
    - 正常一致的单元格绝不触碰；
    - 字典唯一命中（1 对 1，或在多对多时恰好精准命中双模型候选之一）-> 自动回填标准值并清除该格黄底告警；
    - 字典无记录或存在一对多歧义且无法唯一裁定 -> 保持留空标黄，绝不瞎猜！
    返回 (resolved_cell_count, hit_mapping_ids)。
    """
    if not lookup_index or lookup_index.get("count", 0) <= 0:
        return 0, []
    if sheet.get("style_preset") == "general":
        return 0, []

    headers: List[str] = sheet.get("headers") or []
    rows: List[List[str]] = sheet.get("rows") or []
    warnings: List[Dict[str, Any]] = list(sheet.get("warnings") or [])
    if not headers or not rows:
        return 0, []

    roles = _detect_column_roles(headers)
    model_col: Optional[int] = roles.get("model_col")
    code_col: Optional[int] = roles.get("code_col")
    if model_col is None or code_col is None:
        return 0, []

    sec_set = set(int(x) for x in (sheet.get("section_header_rows") or []))
    code_to_models: Dict[str, List[ModelCodeMapping]] = lookup_index["code_to_models"]
    model_to_codes: Dict[str, List[ModelCodeMapping]] = lookup_index["model_to_codes"]

    warn_by_pos: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for w in warnings:
        try:
            warn_by_pos[(int(w["row"]), int(w["col"]))] = w
        except Exception:
            continue

    resolved_positions: set = set()
    hit_ids: List[int] = []
    resolved_count = 0

    for r_idx, row in enumerate(rows):
        if r_idx in sec_set or model_col >= len(row) or code_col >= len(row):
            continue

        cur_m = str(row[model_col] or "").strip()
        cur_c = str(row[code_col] or "").strip()
        w_model = warn_by_pos.get((r_idx, model_col))
        w_code = warn_by_pos.get((r_idx, code_col))

        # 方向 1：型号留空或有告警，且网络型号明确非空（如 ADA-AL10U -> Nova 12 Ultra，或 A2892 -> 14 PRO）
        if (not cur_m or w_model is not None) and cur_c and w_code is None:
            nc = normalize_dict_token(cur_c)
            cands = code_to_models.get(nc) or []
            if cands:
                unique_models = {c.norm_model: c for c in cands}
                chosen: Optional[ModelCodeMapping] = None
                if len(unique_models) == 1:
                    chosen = next(iter(unique_models.values()))
                elif w_model is not None:
                    # 一对多时，仅当双模型分歧的两个候选里恰好有 1 个命中字典，才唯一采信！
                    dual_cands = [normalize_dict_token(x) for x in _extract_dual_candidates_from_reason(w_model.get("reason", ""))]
                    matched = [unique_models[dc] for dc in dict.fromkeys(dual_cands) if dc in unique_models]
                    if len(matched) == 1:
                        chosen = matched[0]
                if chosen is not None:
                    row[model_col] = chosen.model_name
                    cur_m = chosen.model_name
                    resolved_positions.add((r_idx, model_col))
                    resolved_count += 1
                    if chosen.id is not None:
                        hit_ids.append(int(chosen.id))

        # 方向 2：网络型号留空或有告警，且型号明确非空（如 P50 E 4G -> ABR-AL60 ABR-AL90）
        if (not cur_c or w_code is not None) and cur_m and (w_model is None or (r_idx, model_col) in resolved_positions):
            nm = normalize_dict_token(cur_m)
            cands = model_to_codes.get(nm) or []
            if cands:
                unique_codes = {c.norm_code: c for c in cands}
                chosen_c: Optional[ModelCodeMapping] = None
                if len(unique_codes) == 1:
                    chosen_c = next(iter(unique_codes.values()))
                elif w_code is not None:
                    dual_cands = [normalize_dict_token(x) for x in _extract_dual_candidates_from_reason(w_code.get("reason", ""))]
                    matched_c = [unique_codes[dc] for dc in dict.fromkeys(dual_cands) if dc in unique_codes]
                    if len(matched_c) == 1:
                        chosen_c = matched_c[0]
                if chosen_c is not None:
                    row[code_col] = chosen_c.network_code
                    resolved_positions.add((r_idx, code_col))
                    resolved_count += 1
                    if chosen_c.id is not None:
                        hit_ids.append(int(chosen_c.id))

    if resolved_positions:
        sheet["warnings"] = [
            w
            for w in warnings
            if (int(w.get("row", -1)), int(w.get("col", -1))) not in resolved_positions
        ]

    return resolved_count, hit_ids


def apply_model_dict_to_result(session: Session, result: Dict[str, Any]) -> int:
    """对单张识别/拼表结果执行云端机型字典自动消歧，并累加命中次数（零 AI Token、毫秒级）。"""
    if not result or result.get("is_demo"):
        return 0
    try:
        idx = build_lookup_index(session)
    except Exception:
        return 0
    if idx.get("count", 0) <= 0:
        return 0

    total_resolved = 0
    all_hit_ids: List[int] = []

    merged = result.get("merged_sheet")
    if isinstance(merged, dict):
        cnt, hits = resolve_sheet_ambiguities_with_dict(merged, idx)
        total_resolved += cnt
        all_hit_ids.extend(hits)

    for rsh in (result.get("raw_sheets") or []):
        if isinstance(rsh, dict) and rsh is not merged:
            cnt, _ = resolve_sheet_ambiguities_with_dict(rsh, idx)
            # 仅统计主合并表消歧格数以免重复计数，若无 merged_sheet 则累加
            if not isinstance(merged, dict):
                total_resolved += cnt

    if all_hit_ids:
        try:
            hit_counts: Dict[int, int] = {}
            for mid in all_hit_ids:
                hit_counts[mid] = hit_counts.get(mid, 0) + 1
            for mid, delta in hit_counts.items():
                rec = session.get(ModelCodeMapping, mid)
                if rec:
                    rec.hit_count = int(rec.hit_count or 0) + delta
                    session.add(rec)
            session.commit()
        except Exception:
            pass

    if total_resolved > 0:
        prev_notice = str(result.get("notice") or "").strip()
        suffix = f"字典自动消歧 {total_resolved} 处"
        result["notice"] = f"{prev_notice} · {suffix}" if prev_notice else suffix

    return total_resolved
