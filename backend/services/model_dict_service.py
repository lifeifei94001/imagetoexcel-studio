import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlmodel import Session, desc, select

from models import ModelCodeMapping, utc_now_str
from services.ocr_service import (
    _detect_column_roles,
    _is_series_or_header_model,
    _norm_compare_token,
)

_DUAL_CANDIDATES_RE = re.compile(r"（[^：:]+[:：]\s*(.*?)\s*/\s*[^：:]+[:：]\s*(.*?)）")
_INVALID_CODE_TOKENS = {"", "-", "—", "无", "不详", "未知", "网络型号", "小型号", "具体型号", "型号"}

# 标准品牌映射表：(canonical_key, display_name, strong_title_or_model_patterns)
# 顺序刻意把更具体的子品牌放在前面（如红米/一加/真我/iQOO/荣耀/红魔）
_BRAND_SPECS: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("apple", "苹果", ("苹果", "apple", "iphone", "ipad", "macbook", "airpods", "watch")),
    ("huawei", "华为", ("华为", "huawei", "pura", "畅享", "麦芒", "matepad", "matebook", "mate ", "mate", "nova")),
    ("honor", "荣耀", ("荣耀", "honor", "magic")),
    ("redmi", "红米", ("红米", "redmi")),
    ("xiaomi", "小米", ("小米", "xiaomi", "civi", "mix fold", "mix flip")),
    ("oneplus", "一加", ("一加", "oneplus")),
    ("realme", "真我/Realme", ("真我", "realme")),
    ("oppo", "OPPO", ("oppo", "reno", "find x", "find n")),
    ("iqoo", "iQOO", ("iqoo",)),
    ("vivo", "VIVO", ("vivo",)),
    ("samsung", "三星", ("三星", "samsung", "galaxy")),
    ("asus", "华硕/ROG", ("华硕", "asus", "rog", "zenfone")),
    ("redmagic", "红魔", ("红魔", "redmagic")),
    ("nubia", "努比亚", ("努比亚", "nubia")),
    ("zte", "中兴", ("中兴", "zte", "axon")),
    ("motorola", "摩托罗拉", ("摩托罗拉", "motorola", "moto", "razr")),
    ("lenovo", "联想", ("联想", "lenovo", "拯救者", "小新")),
    ("meizu", "魅族", ("魅族", "meizu")),
    ("blackshark", "黑鲨", ("黑鲨", "blackshark")),
    ("sony", "索尼", ("索尼", "sony", "xperia")),
    ("google", "谷歌", ("谷歌", "google", "pixel")),
]

_BRAND_KEY_TO_DISPLAY: Dict[str, str] = {k: disp for k, disp, _ in _BRAND_SPECS}

# 使用纯英文字母边界 (?<![A-Za-z])...(?![A-Za-z])，避免 Python \b 把中文当词字符导致 OPPO行货 / VIVO行货 / Realme行货 失配
_TITLE_BRAND_REGEXES: List[Tuple[str, re.Pattern]] = [
    ("apple", re.compile(r"(苹果|(?<![A-Za-z])(?:apple|iphone|ipad)(?![A-Za-z]))", re.I)),
    ("huawei", re.compile(r"(华为|(?<![A-Za-z])huawei(?![A-Za-z]))", re.I)),
    ("honor", re.compile(r"(荣耀|(?<![A-Za-z])honor(?![A-Za-z]))", re.I)),
    ("redmi", re.compile(r"(红米|(?<![A-Za-z])redmi(?![A-Za-z]))", re.I)),
    ("xiaomi", re.compile(r"(小米|(?<![A-Za-z])xiaomi(?![A-Za-z]))", re.I)),
    ("oneplus", re.compile(r"(一加|(?<![A-Za-z])oneplus(?![A-Za-z]))", re.I)),
    ("realme", re.compile(r"(真我|(?<![A-Za-z])realme(?![A-Za-z]))", re.I)),
    ("oppo", re.compile(r"((?<![A-Za-z])oppo(?![A-Za-z])|欧珀)", re.I)),
    ("iqoo", re.compile(r"(?<![A-Za-z])iqoo(?![A-Za-z])", re.I)),
    ("vivo", re.compile(r"((?<![A-Za-z])vivo(?![A-Za-z])|维沃)", re.I)),
    ("samsung", re.compile(r"(三星|(?<![A-Za-z])(?:samsung|galaxy)(?![A-Za-z]))", re.I)),
    ("asus", re.compile(r"(华硕|(?<![A-Za-z])(?:asus|rog)(?![A-Za-z]))", re.I)),
    ("redmagic", re.compile(r"(红魔|(?<![A-Za-z])redmagic(?![A-Za-z]))", re.I)),
    ("nubia", re.compile(r"(努比亚|(?<![A-Za-z])nubia(?![A-Za-z]))", re.I)),
    ("zte", re.compile(r"(中兴|(?<![A-Za-z])zte(?![A-Za-z]))", re.I)),
    ("motorola", re.compile(r"(摩托罗拉|(?<![A-Za-z])(?:motorola|moto|razr)(?![A-Za-z]))", re.I)),
    ("lenovo", re.compile(r"(联想|(?<![A-Za-z])lenovo(?![A-Za-z])|拯救者)", re.I)),
    ("meizu", re.compile(r"(魅族|(?<![A-Za-z])meizu(?![A-Za-z]))", re.I)),
    ("blackshark", re.compile(r"(黑鲨|(?<![A-Za-z])blackshark(?![A-Za-z]))", re.I)),
    ("sony", re.compile(r"(索尼|(?<![A-Za-z])(?:sony|xperia)(?![A-Za-z]))", re.I)),
    ("google", re.compile(r"(谷歌|(?<![A-Za-z])(?:google|pixel)(?![A-Za-z]))", re.I)),
]

# 车型系列专属强特征（仅保留跨品牌绝不撞车的专属系列词，杜绝如 S20 在三星/VIVO 间误判）
_MODEL_SIGNATURE_REGEXES: List[Tuple[str, re.Pattern]] = [
    ("apple", re.compile(r"(?<![A-Za-z])(?:iphone|ipad|macbook)(?![A-Za-z])", re.I)),
    ("huawei", re.compile(r"(华为|(?<![A-Za-z])(?:pura\s*\d|mate\s*(?:x|xt|rs|pad|book|\d{2})|nova\s*\d)|畅享|麦芒)", re.I)),
    ("honor", re.compile(r"(荣耀|(?<![A-Za-z])(?:honor(?![A-Za-z])|magic\s*(?:v|vs|v2|v3|flip|\d)))", re.I)),
    ("redmi", re.compile(r"(红米|(?<![A-Za-z])redmi(?![A-Za-z]))", re.I)),
    ("xiaomi", re.compile(r"(小米|(?<![A-Za-z])(?:xiaomi(?![A-Za-z])|mix\s*(?:fold|flip|\d)|civi\s*\d))", re.I)),
    ("oneplus", re.compile(r"(一加|(?<![A-Za-z])oneplus(?![A-Za-z]))", re.I)),
    ("realme", re.compile(r"(真我|(?<![A-Za-z])realme(?![A-Za-z]))", re.I)),
    ("oppo", re.compile(r"(?<![A-Za-z])(?:oppo(?![A-Za-z])|find\s*[xn]\d|reno\s*\d)", re.I)),
    ("iqoo", re.compile(r"(?<![A-Za-z])iqoo(?![A-Za-z])", re.I)),
    ("vivo", re.compile(r"(?<![A-Za-z])vivo(?![A-Za-z])", re.I)),
    ("samsung", re.compile(r"(三星|(?<![A-Za-z])(?:samsung|galaxy)(?![A-Za-z])|(?<![A-Za-z])z\s*(?:fold|flip)\s*\d)", re.I)),
    ("asus", re.compile(r"(华硕|(?<![A-Za-z])(?:asus|rog|zenfone)(?![A-Za-z]))", re.I)),
    ("redmagic", re.compile(r"(红魔|(?<![A-Za-z])redmagic(?![A-Za-z]))", re.I)),
    ("nubia", re.compile(r"(努比亚|(?<![A-Za-z])nubia(?![A-Za-z]))", re.I)),
    ("zte", re.compile(r"(中兴|(?<![A-Za-z])(?:zte|axon)(?![A-Za-z]))", re.I)),
    ("motorola", re.compile(r"(摩托罗拉|(?<![A-Za-z])(?:motorola|moto|razr)(?![A-Za-z]))", re.I)),
    ("lenovo", re.compile(r"(联想|(?<![A-Za-z])lenovo(?![A-Za-z])|拯救者)", re.I)),
    ("meizu", re.compile(r"(魅族|(?<![A-Za-z])meizu(?![A-Za-z]))", re.I)),
    ("blackshark", re.compile(r"(黑鲨|(?<![A-Za-z])blackshark(?![A-Za-z]))", re.I)),
    ("sony", re.compile(r"(索尼|(?<![A-Za-z])(?:sony|xperia)(?![A-Za-z]))", re.I)),
    ("google", re.compile(r"(谷歌|(?<![A-Za-z])(?:google|pixel)(?![A-Za-z]))", re.I)),
]

# 仅高置信、跨品牌极少撞车的入网号编码规则（用于无品牌标注时的安全辅助判定）
_CODE_SIGNATURE_RULES: List[Tuple[Tuple[str, ...], re.Pattern]] = [
    (("apple",), re.compile(r"^A[1-3]\d{3}$", re.I)),
    (("samsung",), re.compile(r"^SM-[A-Z]\d{3,4}[A-Z0-9]*$", re.I)),
    (("realme",), re.compile(r"^RMX\d{4}$", re.I)),
    (("asus",), re.compile(r"^(?:I00\d[A-Z]{2}|AI2\d{3}|ASUS_[A-Z0-9]+)$", re.I)),
    (("motorola",), re.compile(r"^XT\d{4}(?:-\d+)?$", re.I)),
    (("redmagic", "nubia"), re.compile(r"^NX\d{3}[A-Z]?$", re.I)),
    (("xiaomi", "redmi"), re.compile(r"^(?:2[1-9]\d{4,6}[A-Z0-9]{2,4}C|M2\d{3}[A-Z0-9]{3,5}C)$", re.I)),
    (("vivo", "iqoo"), re.compile(r"^V2\d{3}[A-Z]?$", re.I)),
]


def normalize_dict_token(val: str) -> str:
    return _norm_compare_token(val)


def extract_brands_from_text(text: str, include_model_signatures: bool = False) -> Set[str]:
    """从标题、文件名、表头或品牌备注文字中提取标准品牌 key 集合（支持多品牌合表，如 红魔/努比亚/中兴）。"""
    s = str(text or "").strip()
    if not s:
        return set()
    found: Set[str] = set()
    for bkey, pat in _TITLE_BRAND_REGEXES:
        if pat.search(s):
            found.add(bkey)
    if include_model_signatures:
        for bkey, pat in _MODEL_SIGNATURE_REGEXES:
            if pat.search(s):
                found.add(bkey)
    return found


def extract_brands_from_code(network_code: str) -> Set[str]:
    """从高置信的入网号编码格式中反推所属品牌（可返回多选族，如 xiaomi/redmi 或 vivo/iqoo）。"""
    tokens = [t.strip() for t in re.split(r"[\s/,;|]+", str(network_code or "").strip()) if t.strip()]
    found: Set[str] = set()
    for tok in tokens:
        for keys, pat in _CODE_SIGNATURE_RULES:
            if pat.match(tok):
                found.update(keys)
    return found


def infer_entry_brands(brand_hint: str, model_name: str = "", network_code: str = "") -> Set[str]:
    """
    为单条字典映射推导标准品牌 key 集合：
    1. 若 model_name 自身带有明确品牌（如合表中的「红魔 9 Pro」或「红米 K70」），优先锁定该具体品牌；
    2. 其次解析 brand_hint（兼容历史已存的「华为行货」「苹果行货 过保机」等写法）；
    3. 最后若仍为空，尝试从高置信机型前缀或入网号格式推导。
    """
    model_explicit = extract_brands_from_text(model_name, include_model_signatures=False)
    hint_brands = extract_brands_from_text(brand_hint, include_model_signatures=False)

    if model_explicit:
        # 若 brand_hint 是多品牌合表（如 红魔/努比亚/中兴），用行内具体品牌精准收敛到单品牌
        overlap = model_explicit & hint_brands
        return overlap if overlap else model_explicit
    if hint_brands:
        return hint_brands

    sig_brands = extract_brands_from_text(model_name, include_model_signatures=True)
    if sig_brands:
        return sig_brands
    return extract_brands_from_code(network_code)


def canonicalize_brand_label(brand_hint: str, model_name: str = "", network_code: str = "") -> str:
    """将原始品牌/表名提示归一化为干净的标准展示名称（如「华为行货」->「华为」，无法识别则保留原备注）。"""
    keys = infer_entry_brands(brand_hint, model_name=model_name, network_code=network_code)
    if keys:
        ordered = [_BRAND_KEY_TO_DISPLAY[k] for k, _, _ in _BRAND_SPECS if k in keys]
        return "/".join(ordered)[:32]
    return re.sub(r"\s+", " ", str(brand_hint or "")).strip()[:32]


def detect_sheet_brands(
    sheet: Dict[str, Any],
    quote_title: str = "",
    source_filename: str = "",
) -> Set[str]:
    """
    纯本地零 Token 识别当前报价单所属标准品牌集合：
    1. 优先从 quote_title / source_filename / sheet.name 识别（支持「红魔/努比亚/中兴」多品牌合表）；
    2. 若标题未含品牌（例如临时粘贴的无名截图），继续扫描表头、区块小表头与行内机型/入网号投票。
    """
    # 第 1 层：报价单标题 + 原文件名 + 表名
    for txt in (quote_title, source_filename, sheet.get("name") or ""):
        b = extract_brands_from_text(str(txt or ""), include_model_signatures=False)
        if b:
            return b

    # 第 2 层：表头与区块小表头（如「荣耀系列」「华为系列」「iPhone系列」「三星系列」）
    headers = sheet.get("headers") or []
    rows = sheet.get("rows") or []
    sec_rows = [int(x) for x in (sheet.get("section_header_rows") or []) if 0 <= int(x) < len(rows)]
    header_blob = " ".join(str(h or "") for h in headers)
    sec_blob = " ".join(" ".join(str(c or "") for c in rows[r]) for r in sec_rows)
    b_hdr = extract_brands_from_text(f"{header_blob} {sec_blob}", include_model_signatures=True)
    if b_hdr:
        return b_hdr

    # 第 3 层：按整表已知机型名 & 高置信入网号多数投票
    roles = _detect_column_roles(headers)
    model_col = roles.get("model_col")
    code_col = roles.get("code_col")
    votes: Dict[str, int] = {}
    for idx, row in enumerate(rows[:120]):
        m_val = str(row[model_col] or "") if (model_col is not None and model_col < len(row)) else ""
        c_val = str(row[code_col] or "") if (code_col is not None and code_col < len(row)) else ""
        rb = extract_brands_from_text(m_val, include_model_signatures=True)
        if not rb and c_val:
            rb = extract_brands_from_code(c_val)
        for k in rb:
            votes[k] = votes.get(k, 0) + 1
    if not votes:
        return set()
    max_v = max(votes.values())
    if max_v < 2 and len(rows) > 3:
        return set()
    return {k for k, v in votes.items() if v >= max(2, int(max_v * 0.5))}


def format_brand_keys(keys: Set[str]) -> str:
    if not keys:
        return ""
    return "/".join(_BRAND_KEY_TO_DISPLAY[k] for k, _, _ in _BRAND_SPECS if k in keys)


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
    if not re.search(r"[A-Za-z0-9]", c):
        return False
    return True


def list_mappings(
    session: Session,
    query: str = "",
    brand: str = "",
    limit: int = 500,
) -> List[ModelCodeMapping]:
    stmt = select(ModelCodeMapping).order_by(desc(ModelCodeMapping.hit_count), desc(ModelCodeMapping.id))
    rows = list(session.exec(stmt).all())

    b_filter = (brand or "").strip()
    if b_filter:
        filter_keys = extract_brands_from_text(b_filter, include_model_signatures=False)
        filtered_by_brand: List[ModelCodeMapping] = []
        for r in rows:
            r_keys = infer_entry_brands(r.brand_hint, r.model_name, r.network_code)
            if filter_keys:
                if r_keys & filter_keys:
                    filtered_by_brand.append(r)
            elif b_filter == "__untagged__":
                if not r_keys and not (r.brand_hint or "").strip():
                    filtered_by_brand.append(r)
            elif b_filter.lower() in (r.brand_hint or "").lower():
                filtered_by_brand.append(r)
        rows = filtered_by_brand

    q = (query or "").strip().lower()
    if q:
        q_norm = normalize_dict_token(q)
        rows = [
            r
            for r in rows
            if q in r.model_name.lower()
            or q in r.network_code.lower()
            or q in (r.brand_hint or "").lower()
            or q in format_brand_keys(infer_entry_brands(r.brand_hint, r.model_name, r.network_code)).lower()
            or (q_norm and (q_norm in r.norm_model or q_norm in r.norm_code))
        ]
    return rows[: max(1, min(limit, 2000))]


def list_brand_summary(session: Session) -> List[Dict[str, Any]]:
    """统计云端字典中各标准品牌词条数量，供前端分品牌快速筛选展示。"""
    rows = list(session.exec(select(ModelCodeMapping)).all())
    counts: Dict[str, int] = {}
    for r in rows:
        label = canonicalize_brand_label(r.brand_hint, r.model_name, r.network_code) or "未分类"
        counts[label] = counts.get(label, 0) + 1
    return [
        {"brand": k, "count": v}
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def count_mappings(session: Session) -> int:
    rows = session.exec(select(ModelCodeMapping.id)).all()
    return len(rows)


def upsert_mappings(
    session: Session,
    entries: List[Dict[str, Any]],
    default_source: str = "manual",
) -> Dict[str, Any]:
    """批量写入/更新「机型 ↔ 网络型号」映射（按 norm_model + norm_code 去重，并自动归一化标准品牌）。"""
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
        raw_brand = re.sub(r"\s+", " ", str(item.get("brand_hint") or "")).strip()
        brand = canonicalize_brand_label(raw_brand, model_name=raw_m, network_code=raw_c)
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
    """单次轻量查询加载全部映射，构建带标准品牌集合的内存 O(1) 双向索引（毫秒级，零 AI Token）。"""
    rows = list(session.exec(select(ModelCodeMapping)).all())
    code_to_models: Dict[str, List[Tuple[ModelCodeMapping, Set[str]]]] = {}
    model_to_codes: Dict[str, List[Tuple[ModelCodeMapping, Set[str]]]] = {}
    for r in rows:
        bkeys = infer_entry_brands(r.brand_hint, r.model_name, r.network_code)
        entry = (r, bkeys)
        if r.norm_code:
            code_to_models.setdefault(r.norm_code, []).append(entry)
        if r.norm_model:
            model_to_codes.setdefault(r.norm_model, []).append(entry)
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


def _filter_candidates_by_brand(
    candidates: List[Tuple[ModelCodeMapping, Set[str]]],
    target_brands: Set[str],
    allow_untagged_fallback: bool,
) -> List[ModelCodeMapping]:
    """
    分品牌严格隔离过滤：
    - 当识别出目标品牌（如苹果）时，凡是属于其他品牌（如小米）的词条一律强拦截，绝不跨品牌误填；
    - 若存在同品牌词条，仅保留同品牌词条（如苹果 14 Pro 与小米 14 Pro 各自唯一命中）；
    - 无品牌标签词条：仅在 allow_untagged_fallback=True（如全球唯一入网号查机型）且无同品牌命中时作为兜底；
      对于「机型名 -> 查入网号」，为防止未标品牌的通用机型名跨品牌串码，严格要求同品牌命中。
    """
    if not candidates:
        return []

    if target_brands:
        same_brand = [r for r, bkeys in candidates if bkeys and (bkeys & target_brands)]
        if same_brand:
            return same_brand
        if allow_untagged_fallback:
            return [r for r, bkeys in candidates if not bkeys]
        return []

    # 整张表完全未识别出品牌时的保守模式：仅当所有候选属于单一品牌（或均无品牌冲突）才允许继续
    all_known_brands: Set[str] = set()
    for _, bkeys in candidates:
        all_known_brands.update(bkeys)
    if len(all_known_brands) > 1:
        return []
    return [r for r, _ in candidates]


def resolve_sheet_ambiguities_with_dict(
    sheet: Dict[str, Any],
    lookup_index: Dict[str, Any],
    quote_title: str = "",
    source_filename: str = "",
) -> Tuple[int, List[int], Set[str]]:
    """
    仅在报价单出现「机型/网络型号」留空或双模型分歧标黄时，才查云端字典自动消歧（按品牌严格隔离）：
    - 正常一致的单元格绝不触碰；
    - 先识别当前表所属标准品牌（如苹果/小米/华为/红魔等），严格禁止跨品牌误填；
    - 仅在同品牌下唯一命中（1 对 1，或一对多中恰好精准命中双模型候选之一）才自动回填并清除黄底告警；
    - 字典无同品牌记录或仍存在一对多歧义 -> 保持留空标黄，宁愿留空绝不瞎填！
    返回 (resolved_cell_count, hit_mapping_ids, detected_sheet_brands)。
    """
    if not lookup_index or lookup_index.get("count", 0) <= 0:
        return 0, [], set()
    if sheet.get("style_preset") == "general":
        return 0, [], set()

    headers: List[str] = sheet.get("headers") or []
    rows: List[List[str]] = sheet.get("rows") or []
    warnings: List[Dict[str, Any]] = list(sheet.get("warnings") or [])
    if not headers or not rows:
        return 0, [], set()

    roles = _detect_column_roles(headers)
    model_col: Optional[int] = roles.get("model_col")
    code_col: Optional[int] = roles.get("code_col")
    if model_col is None or code_col is None:
        return 0, [], set()

    sheet_brands = detect_sheet_brands(
        sheet,
        quote_title=quote_title,
        source_filename=source_filename or str(sheet.get("source_filename") or ""),
    )

    sec_set = set(int(x) for x in (sheet.get("section_header_rows") or []))
    code_to_models: Dict[str, List[Tuple[ModelCodeMapping, Set[str]]]] = lookup_index["code_to_models"]
    model_to_codes: Dict[str, List[Tuple[ModelCodeMapping, Set[str]]]] = lookup_index["model_to_codes"]

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

        # 方向 1：型号留空或有告警，且网络型号明确非空（如 ADA-AL10U -> Nova 12 Ultra）
        if (not cur_m or w_model is not None) and cur_c and w_code is None:
            nc = normalize_dict_token(cur_c)
            raw_cands = code_to_models.get(nc) or []
            row_code_brands = extract_brands_from_code(cur_c)
            eff_brands = (row_code_brands & sheet_brands) or sheet_brands or row_code_brands
            cands = _filter_candidates_by_brand(raw_cands, eff_brands, allow_untagged_fallback=True)
            if cands:
                unique_models = {c.norm_model: c for c in cands}
                chosen: Optional[ModelCodeMapping] = None
                if len(unique_models) == 1:
                    chosen = next(iter(unique_models.values()))
                elif w_model is not None:
                    dual_cands = [
                        normalize_dict_token(x)
                        for x in _extract_dual_candidates_from_reason(w_model.get("reason", ""))
                    ]
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

        # 方向 2：网络型号留空或有告警，且型号明确非空（如 14 Pro -> 严格按当前品牌隔离，绝不跨品牌乱填）
        if (not cur_c or w_code is not None) and cur_m and (w_model is None or (r_idx, model_col) in resolved_positions):
            nm = normalize_dict_token(cur_m)
            raw_cands_c = model_to_codes.get(nm) or []
            row_explicit_brands = extract_brands_from_text(cur_m, include_model_signatures=False)
            row_sig_brands = extract_brands_from_text(cur_m, include_model_signatures=True)
            eff_brands_c = (
                (row_explicit_brands & sheet_brands)
                or row_explicit_brands
                or sheet_brands
                or row_sig_brands
            )
            # 机型 -> 入网号严格隔离品牌（allow_untagged_fallback=False），彻底杜绝小米 14 Pro 误填进苹果表！
            cands_c = _filter_candidates_by_brand(raw_cands_c, eff_brands_c, allow_untagged_fallback=False)
            if cands_c:
                unique_codes = {c.norm_code: c for c in cands_c}
                chosen_c: Optional[ModelCodeMapping] = None
                if len(unique_codes) == 1:
                    chosen_c = next(iter(unique_codes.values()))
                elif w_code is not None:
                    dual_cands = [
                        normalize_dict_token(x)
                        for x in _extract_dual_candidates_from_reason(w_code.get("reason", ""))
                    ]
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

    return resolved_count, hit_ids, sheet_brands


def apply_model_dict_to_result(
    session: Session,
    result: Dict[str, Any],
    source_filename: str = "",
) -> int:
    """对单张识别/拼表结果执行分品牌隔离的云端机型字典自动消歧，并累加命中次数（零 AI Token、毫秒级）。"""
    if not result or result.get("is_demo"):
        return 0
    try:
        idx = build_lookup_index(session)
    except Exception:
        return 0
    if idx.get("count", 0) <= 0:
        return 0

    quote_title = str(result.get("quote_title") or "")
    fname = source_filename or str(result.get("source_filename") or "")

    total_resolved = 0
    all_hit_ids: List[int] = []
    detected_brands: Set[str] = set()

    merged = result.get("merged_sheet")
    if isinstance(merged, dict):
        cnt, hits, bkeys = resolve_sheet_ambiguities_with_dict(
            merged,
            idx,
            quote_title=quote_title,
            source_filename=fname,
        )
        total_resolved += cnt
        all_hit_ids.extend(hits)
        detected_brands.update(bkeys)

    for rsh in (result.get("raw_sheets") or []):
        if isinstance(rsh, dict) and rsh is not merged:
            cnt, _, bkeys = resolve_sheet_ambiguities_with_dict(
                rsh,
                idx,
                quote_title=quote_title,
                source_filename=fname,
            )
            detected_brands.update(bkeys)
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
        brand_tag = format_brand_keys(detected_brands)
        suffix = (
            f"字典自动消歧 {total_resolved} 处（{brand_tag}）"
            if brand_tag
            else f"字典自动消歧 {total_resolved} 处"
        )
        result["notice"] = f"{prev_notice} · {suffix}" if prev_notice else suffix

    return total_resolved
