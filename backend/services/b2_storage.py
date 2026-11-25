import base64
import hashlib
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from database import STORAGE_DIR, _get_clean_env

B2_AUTH_URL = "https://api.backblazeb2.com/b2api/v2/b2_authorize_account"

_b2_auth_cache: Dict[str, Any] = {
    "token": "",
    "api_url": "",
    "download_url": "",
    "bucket_id": "",
    "bucket_name": "",
    "expires_at": 0.0,
}


def _get_b2_credentials() -> tuple[str, str, str]:
    key_id = _get_clean_env("B2_KEY_ID") or _get_clean_env("B2_APPLICATION_KEY_ID")
    app_key = _get_clean_env("B2_APPLICATION_KEY")
    bucket_name = _get_clean_env("B2_BUCKET_NAME")
    return key_id, app_key, bucket_name


def b2_is_enabled() -> bool:
    key_id, app_key, _ = _get_b2_credentials()
    return bool(key_id and app_key)


def get_b2_bucket_name() -> str:
    _, _, bucket_name = _get_b2_credentials()
    return bucket_name or _b2_auth_cache.get("bucket_name") or ""


def _authorize_b2(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force_refresh and _b2_auth_cache["token"] and now < _b2_auth_cache["expires_at"]:
        return _b2_auth_cache

    key_id, app_key, configured_bucket = _get_b2_credentials()
    if not key_id or not app_key:
        raise RuntimeError("未配置 Backblaze B2 密钥（B2_KEY_ID / B2_APPLICATION_KEY）")

    basic = base64.b64encode(f"{key_id}:{app_key}".encode("utf-8")).decode("ascii")
    resp = requests.get(B2_AUTH_URL, headers={"Authorization": f"Basic {basic}"}, timeout=20)
    if resp.status_code != 200:
        safe_msg = resp.text[:200].replace(app_key, "***").replace(key_id, "***")
        raise RuntimeError(f"Backblaze B2 鉴权失败 (HTTP {resp.status_code})：{safe_msg}")

    data = resp.json()
    allowed = data.get("allowed") or {}
    bucket_id = allowed.get("bucketId") or ""
    bucket_name = configured_bucket or allowed.get("bucketName") or ""

    # 若密钥未绑定单一桶且未在环境里写 bucket_id，则按 bucket_name 查询一次
    if not bucket_id and bucket_name:
        list_resp = requests.post(
            f"{data['apiUrl']}/b2api/v2/b2_list_buckets",
            headers={"Authorization": data["authorizationToken"]},
            json={"accountId": data["accountId"], "bucketName": bucket_name},
            timeout=20,
        )
        if list_resp.status_code == 200:
            buckets = list_resp.json().get("buckets") or []
            if buckets:
                bucket_id = buckets[0].get("bucketId") or ""

    if not bucket_id or not bucket_name:
        raise RuntimeError("未能解析 Backblaze B2 存储桶 ID 或名称，请检查 B2_BUCKET_NAME 配置")

    _b2_auth_cache.update(
        {
            "token": data["authorizationToken"],
            "api_url": data["apiUrl"],
            "download_url": data["downloadUrl"],
            "bucket_id": bucket_id,
            "bucket_name": bucket_name,
            "expires_at": now + 12 * 3600,
        }
    )
    return _b2_auth_cache


def save_object(object_key: str, raw_bytes: bytes, content_type: str = "application/octet-stream") -> str:
    """
    将文件持久化至 Backblaze B2（已配置 B2 密钥时）或本地/tmp storage/（未配置时平稳降级）。
    返回存储引用路径：'b2://<bucket>/<object_key>' 或本地绝对路径。
    """
    clean_key = object_key.lstrip("/")
    if not b2_is_enabled():
        local_path = STORAGE_DIR / clean_key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(raw_bytes)
        return clean_key

    auth = _authorize_b2()
    up_url_resp = requests.post(
        f"{auth['api_url']}/b2api/v2/b2_get_upload_url",
        headers={"Authorization": auth["token"]},
        json={"bucketId": auth["bucket_id"]},
        timeout=20,
    )
    if up_url_resp.status_code == 401:
        auth = _authorize_b2(force_refresh=True)
        up_url_resp = requests.post(
            f"{auth['api_url']}/b2api/v2/b2_get_upload_url",
            headers={"Authorization": auth["token"]},
            json={"bucketId": auth["bucket_id"]},
            timeout=20,
        )
    if up_url_resp.status_code != 200:
        raise RuntimeError(f"获取 Backblaze 上传地址失败 (HTTP {up_url_resp.status_code})")

    up_info = up_url_resp.json()
    sha1_hex = hashlib.sha1(raw_bytes).hexdigest()
    upload_headers = {
        "Authorization": up_info["authorizationToken"],
        "X-Bz-File-Name": quote(clean_key, safe="/"),
        "Content-Type": content_type,
        "X-Bz-Content-Sha1": sha1_hex,
    }
    put_resp = requests.post(
        up_info["uploadUrl"],
        headers=upload_headers,
        data=raw_bytes,
        timeout=45,
    )
    if put_resp.status_code != 200:
        raise RuntimeError(f"上传至 Backblaze B2 失败 (HTTP {put_resp.status_code})：{put_resp.text[:200]}")

    return f"b2://{auth['bucket_name']}/{clean_key}"


def load_object(storage_ref: str) -> Optional[bytes]:
    """从 Backblaze B2 或本地降级目录读取对象字节。"""
    if not storage_ref:
        return None
    if storage_ref.startswith("b2://"):
        rest = storage_ref[len("b2://") :]
        bucket_name, _, object_key = rest.partition("/")
        if not object_key:
            return None
        auth = _authorize_b2()
        url = f"{auth['download_url']}/file/{bucket_name}/{quote(object_key, safe='/')}"
        resp = requests.get(url, headers={"Authorization": auth["token"]}, timeout=45)
        if resp.status_code == 401:
            auth = _authorize_b2(force_refresh=True)
            resp = requests.get(url, headers={"Authorization": auth["token"]}, timeout=45)
        if resp.status_code == 200:
            return resp.content
        return None

    local_path = STORAGE_DIR / storage_ref.lstrip("/")
    if not local_path.exists():
        alt = Path(storage_ref)
        if alt.exists():
            return alt.read_bytes()
        return None
    return local_path.read_bytes()


def _parse_b2_object_key(storage_ref: str) -> str:
    if not storage_ref:
        return ""
    if storage_ref.startswith("b2://"):
        rest = storage_ref[len("b2://") :]
        _, _, object_key = rest.partition("/")
        return object_key.lstrip("/")
    return storage_ref.lstrip("/")


def _b2_post_with_retry(endpoint_path: str, payload: Dict[str, Any], timeout: int = 25) -> Dict[str, Any]:
    auth = _authorize_b2()
    url = f"{auth['api_url']}{endpoint_path}"
    resp = requests.post(url, headers={"Authorization": auth["token"]}, json=payload, timeout=timeout)
    if resp.status_code == 401:
        auth = _authorize_b2(force_refresh=True)
        url = f"{auth['api_url']}{endpoint_path}"
        resp = requests.post(url, headers={"Authorization": auth["token"]}, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Backblaze B2 接口 {endpoint_path} 调用失败 (HTTP {resp.status_code})：{resp.text[:180]}")
    return resp.json()


def _list_b2_file_versions(prefix: str = "", max_pages: int = 20) -> list[Dict[str, Any]]:
    auth = _authorize_b2()
    files: list[Dict[str, Any]] = []
    start_name: Optional[str] = None
    start_id: Optional[str] = None
    for _ in range(max_pages):
        body: Dict[str, Any] = {
            "bucketId": auth["bucket_id"],
            "maxFileCount": 1000,
        }
        if prefix:
            body["prefix"] = prefix
        if start_name:
            body["startFileName"] = start_name
        if start_id:
            body["startFileId"] = start_id
        data = _b2_post_with_retry("/b2api/v2/b2_list_file_versions", body)
        batch = data.get("files") or []
        files.extend(batch)
        start_name = data.get("nextFileName")
        start_id = data.get("nextFileId")
        if not start_name:
            break
    return files


def delete_object_versions(storage_ref: str) -> Dict[str, int]:
    """物理删除单个对象在 Backblaze B2（含全部历史隐藏版本）或本地降级目录中的文件。"""
    if not storage_ref:
        return {"deleted_files": 0, "freed_bytes": 0}

    deleted_files = 0
    freed_bytes = 0

    if storage_ref.startswith("b2://") and b2_is_enabled():
        object_key = _parse_b2_object_key(storage_ref)
        if not object_key:
            return {"deleted_files": 0, "freed_bytes": 0}
        versions = _list_b2_file_versions(prefix=object_key, max_pages=5)
        for f in versions:
            if f.get("fileName") != object_key:
                continue
            _b2_post_with_retry(
                "/b2api/v2/b2_delete_file_version",
                {"fileName": f["fileName"], "fileId": f["fileId"]},
            )
            deleted_files += 1
            freed_bytes += int(f.get("contentLength") or 0)
        return {"deleted_files": deleted_files, "freed_bytes": freed_bytes}

    local_path = STORAGE_DIR / storage_ref.lstrip("/")
    if local_path.exists() and local_path.is_file():
        freed_bytes += local_path.stat().st_size
        local_path.unlink(missing_ok=True)
        deleted_files += 1
    return {"deleted_files": deleted_files, "freed_bytes": freed_bytes}


def delete_prefix_objects(prefix: str) -> Dict[str, int]:
    """按前缀（例如 jobs/<job_uid>/）物理清空 B2 中所有文件及历史版本，或清理本地对应子目录。"""
    clean_prefix = prefix.lstrip("/")
    deleted_files = 0
    freed_bytes = 0

    if b2_is_enabled():
        versions = _list_b2_file_versions(prefix=clean_prefix, max_pages=20)
        for f in versions:
            fname = f.get("fileName") or ""
            if clean_prefix and not fname.startswith(clean_prefix):
                continue
            _b2_post_with_retry(
                "/b2api/v2/b2_delete_file_version",
                {"fileName": fname, "fileId": f["fileId"]},
            )
            deleted_files += 1
            freed_bytes += int(f.get("contentLength") or 0)
        return {"deleted_files": deleted_files, "freed_bytes": freed_bytes}

    target_dir = STORAGE_DIR / clean_prefix if clean_prefix else STORAGE_DIR
    if target_dir.exists():
        for p in list(target_dir.rglob("*")):
            if p.is_file():
                freed_bytes += p.stat().st_size
                p.unlink(missing_ok=True)
                deleted_files += 1
    return {"deleted_files": deleted_files, "freed_bytes": freed_bytes}


def purge_bucket_files() -> Dict[str, int]:
    """彻底清零 Backblaze B2 存储桶（或本地存储目录）内的全部文件及历史隐藏版本。"""
    return delete_prefix_objects("")


def get_storage_stats() -> Dict[str, Any]:
    """查询当前 Backblaze B2 存储桶（或本地缓存）占用文件数与总字节数。"""
    if b2_is_enabled():
        versions = _list_b2_file_versions(prefix="", max_pages=20)
        total_bytes = sum(int(f.get("contentLength") or 0) for f in versions)
        return {
            "backend": "b2",
            "bucket": get_b2_bucket_name(),
            "file_count": len(versions),
            "total_bytes": total_bytes,
        }
    total_files = 0
    total_bytes = 0
    if STORAGE_DIR.exists():
        for p in STORAGE_DIR.rglob("*"):
            if p.is_file():
                total_files += 1
                total_bytes += p.stat().st_size
    return {
        "backend": "local",
        "bucket": "",
        "file_count": total_files,
        "total_bytes": total_bytes,
    }
