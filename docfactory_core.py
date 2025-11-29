from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from uuid import uuid4
import time

import requests
from jinja2 import BaseLoader, Environment, StrictUndefined, Undefined


class KnowledgeBaseError(RuntimeError):
    """Raised when Knowledge Base interactions fail."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class KnowledgeBaseClient:
    """Thin wrapper around the Dify Knowledge Base HTTP API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        if not self.base_url:
            raise KnowledgeBaseError("dify_api_base_url must be configured.")
        if not self.api_key:
            raise KnowledgeBaseError("dify_api_key must be configured.")

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.setdefault("Authorization", f"Bearer {self.api_key}")
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
        params = kwargs.pop("params", None)
        timeout = kwargs.pop("timeout", 30)
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise KnowledgeBaseError(f"Request to {url} failed: {exc}") from exc

        if response.status_code >= 400:
            payload: Optional[Dict[str, Any]] = None
            detail: str
            try:
                payload = response.json()
                detail = json.dumps(payload)
            except ValueError:
                detail = response.text or ""
            raise KnowledgeBaseError(
                f"Dify API error {response.status_code} for {method} {path}: {detail}",
                status_code=response.status_code,
                payload=payload if isinstance(payload, dict) else None,
            )

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}


class RenderCore:
    """Pure Jinja2 rendering utilities."""

    def __init__(self, engine_options: Optional[Dict[str, Any]] = None) -> None:
        self.engine_options = engine_options or {}

    @staticmethod
    def coerce_json(
        value: Any,
        *,
        field_name: str,
        required: bool,
        default: Any = None,
    ) -> Optional[Any]:
        if value is None:
            if required:
                raise ValueError(f"{field_name} is required.")
            return default

        if isinstance(value, (dict, list)):
            return value

        if isinstance(value, str):
            value = value.strip()
            if not value:
                if required:
                    raise ValueError(f"{field_name} cannot be empty.")
                return default
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field_name} is not valid JSON: {exc}") from exc

        raise ValueError(f"{field_name} must be a JSON string or structured object.")

    def render(
        self,
        template_string: str,
        data_context: Any,
        engine_options: Optional[Dict[str, Any]] = None,
    ) -> str:
        env = self._build_environment(engine_options or {})
        template = env.from_string(template_string)
        return template.render(data_context)

    def _build_environment(self, overrides: Dict[str, Any]) -> Environment:
        options = {
            "autoescape": False,
            "strict_variables": False,
            "trim_blocks": True,
            "lstrip_blocks": True,
        }
        options.update(self.engine_options)
        options.update(overrides)

        undefined_cls = StrictUndefined if options.get("strict_variables") else Undefined
        env = Environment(
            loader=BaseLoader(),
            autoescape=bool(options.get("autoescape", False)),
            undefined=undefined_cls,
            trim_blocks=options.get("trim_blocks", True),
            lstrip_blocks=options.get("lstrip_blocks", True),
        )
        env.filters["format_currency"] = self._format_currency
        env.filters["format_date"] = self._format_date
        return env

    @staticmethod
    def _format_currency(value: Any, currency: str = "EUR", decimals: int = 2) -> str:
        if value is None or value == "":
            return ""
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return str(value)

        quantized = amount.quantize(Decimal(10) ** -decimals)
        formatted = f"{quantized:,.{decimals}f}"
        formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{formatted} {currency}".strip()

    @staticmethod
    def _format_date(value: Any, fmt: str = "%d/%m/%Y") -> str:
        if value is None:
            return ""

        dt: Optional[datetime] = None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, date):
            dt = datetime.combine(value, datetime.min.time())
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value)
        elif isinstance(value, str):
            value = value.strip()
            if not value:
                return ""
            for date_format in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
                try:
                    dt = datetime.strptime(value, date_format)
                    break
                except ValueError:
                    continue
            if dt is None:
                try:
                    dt = datetime.fromisoformat(value)
                except ValueError:
                    return value
        else:
            return str(value)

        return dt.strftime(fmt) if dt else ""


def normalize_upsert_mode(mode: Optional[str]) -> str:
    allowed = {"create_or_update", "create_only", "update_only"}
    if not isinstance(mode, str):
        return "create_or_update"
    normalized = mode.strip().lower()
    return normalized if normalized in allowed else "create_or_update"


def parse_metadata(metadata_value: Any) -> Optional[Dict[str, Any]]:
    parsed = RenderCore.coerce_json(
        metadata_value,
        field_name="metadata_json",
        required=False,
    )
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise ValueError("metadata_json must be a JSON object.")
    sanitized: Dict[str, Any] = {}
    for key, value in parsed.items():
        sanitized[str(key)] = value
    return sanitized


def assemble_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    base = {"generated_by": "DocFactory"}
    if metadata:
        base.update(metadata)
    return base or None


def generate_document_name(data_context: Any) -> str:
    slug_source = "document"
    if isinstance(data_context, dict):
        for key in ("name", "title", "document_name", "customer_code"):
            value = data_context.get(key)
            if isinstance(value, str) and value.strip():
                slug_source = value.strip()
                break
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", slug_source).strip("_") or "document"
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"docfactory_{slug}_{timestamp}_{uuid4().hex[:6]}"


def normalize_document_response(response: Dict[str, Any]) -> Dict[str, Any]:
    candidate = response or {}
    if "data" in candidate and isinstance(candidate["data"], dict):
        candidate = candidate["data"]
    if "document" in candidate and isinstance(candidate["document"], dict):
        candidate = candidate["document"]
    if "document_id" in candidate and "id" not in candidate:
        candidate["id"] = candidate["document_id"]
    return candidate


def extract_metadata_from_response(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = response or {}
    if "data" in payload and isinstance(payload["data"], dict):
        payload = payload["data"]
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else None


def extract_keywords(data_context: Any) -> List[str]:
    keywords: List[str] = []
    if isinstance(data_context, dict):
        for key in ("customer_code", "document_type", "year", "name", "title"):
            value = data_context.get(key)
            if isinstance(value, (str, int, float)):
                keywords.append(str(value))
    return sorted(set(keywords))


class KnowledgeBaseDocumentCore:
    """Handles KB document creation/updating exclusively via text endpoints."""

    def __init__(
        self,
        client: KnowledgeBaseClient,
        *,
        default_dataset_id: Optional[str] = None,
    ) -> None:
        self.client = client
        self.default_dataset_id = (default_dataset_id or "").strip() or None

    def save_text_document(
        self,
        *,
        rendered_text: Any,
        parameters: Dict[str, Any],
        data_context: Any = None,
    ) -> Dict[str, Any]:
        dataset_id = self._resolve_dataset_id(parameters)
        metadata_input = parse_metadata(parameters.get("metadata_json"))
        metadata_payload = assemble_metadata(metadata_input)
        upsert_mode = normalize_upsert_mode(parameters.get("upsert_mode"))
        document_name = self._safe_str(parameters.get("document_name"))
        document_id = self._safe_str(parameters.get("document_id"))
        text = self._normalize_text(rendered_text)

        resolved_id = self._ensure_document_for_upsert(
            dataset_id=dataset_id,
            document_id=document_id,
            document_name=document_name,
            upsert_mode=upsert_mode,
            rendered_text=text,
            data_context=data_context,
        )

        summary = {
            "saved_to_kb": True,
            "dataset_id": dataset_id,
            "document_id": resolved_id,
            "metadata_applied": None,
        }

        if metadata_payload:
            applied_metadata = self._apply_metadata(
                dataset_id=dataset_id,
                document_id=resolved_id,
                metadata=metadata_payload,
            )
            summary["metadata_applied"] = applied_metadata

        return summary

    def _resolve_dataset_id(self, parameters: Dict[str, Any]) -> str:
        dataset_candidate = self._safe_str(parameters.get("dataset_id")) or self.default_dataset_id
        if not dataset_candidate:
            raise KnowledgeBaseError("dataset_id is required to save documents into the Knowledge Base.")
        return dataset_candidate

    def _ensure_document_for_upsert(
        self,
        *,
        dataset_id: str,
        document_id: Optional[str],
        document_name: Optional[str],
        upsert_mode: str,
        rendered_text: str,
        data_context: Any,
    ) -> str:
        if document_id:
            self._assert_document_exists(dataset_id, document_id)
            self._update_document_by_text(
                dataset_id=dataset_id,
                document_id=document_id,
                rendered_text=rendered_text,
                document_name=document_name,
            )
            return document_id

        if document_name:
            existing = self._find_document_by_name(dataset_id, document_name)
            if existing:
                if upsert_mode == "create_only":
                    raise KnowledgeBaseError(
                        "Document already exists but upsert_mode is create_only."
                    )
                target_id = self._extract_document_id(existing)
                self._update_document_by_text(
                    dataset_id=dataset_id,
                    document_id=target_id,
                    rendered_text=rendered_text,
                    document_name=document_name,
                )
                return target_id

            if upsert_mode == "update_only":
                raise KnowledgeBaseError(
                    "Document not found and upsert_mode is update_only."
                )

            created = self._create_document_by_text(
                dataset_id=dataset_id,
                document_name=document_name,
                rendered_text=rendered_text,
            )
            return self._extract_document_id(created)

        if upsert_mode == "update_only":
            raise KnowledgeBaseError(
                "document_name or document_id is required when upsert_mode is update_only."
            )

        generated_name = generate_document_name(data_context)
        created = self._create_document_by_text(
            dataset_id=dataset_id,
            document_name=generated_name,
            rendered_text=rendered_text,
        )
        return self._extract_document_id(created)

    def _create_document_by_text(
        self,
        *,
        dataset_id: str,
        document_name: str,
        rendered_text: str,
    ) -> Dict[str, Any]:
        payload = {
            "name": document_name,
            "text": rendered_text,
            "indexing_technique": "high_quality",
            "process_rule": {"mode": "automatic"},
        }
        response = self.client.request(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-text",
            json=payload,
        )
        document = normalize_document_response(response)
        if "id" not in document and "document_id" not in document:
            raise KnowledgeBaseError(
                "Document creation succeeded but no identifier was returned."
            )
        return document

    def _update_document_by_text(
        self,
        *,
        dataset_id: str,
        document_id: str,
        rendered_text: str,
        document_name: Optional[str],
    ) -> None:
        payload: Dict[str, Any] = {"text": rendered_text}
        if document_name:
            payload["name"] = document_name
        self.client.request(
            "POST",
            f"/datasets/{dataset_id}/documents/{document_id}/update_by_text",
            json=payload,
        )

    def _apply_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not metadata:
            return None

        meta_def_response = self.client.request(
            "GET",
            f"/datasets/{dataset_id}/metadata",
        )

        def _extract_items(payload: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
            """Compat helper: usa prima 'doc_metadata', poi 'data' se presente."""
            if not isinstance(payload, dict):
                return None
            if isinstance(payload.get("doc_metadata"), list):
                return payload["doc_metadata"]
            if isinstance(payload.get("data"), list):
                return payload["data"]
            return None

        items = _extract_items(meta_def_response)
        if not isinstance(items, list):
            return metadata

        name_to_id: Dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            meta_id = item.get("id")
            if name and meta_id:
                name_to_id[name] = str(meta_id)
        for key in metadata.keys():
            name = str(key).strip()
            if not name or name in name_to_id:
                continue
            try:
                create_resp = self.client.request(
                    "POST",
                    f"/datasets/{dataset_id}/metadata",
                    json={
                        "type": "string",
                        "name": name,
                    },
                )
            except KnowledgeBaseError as exc:
                if exc.status_code is None or exc.status_code >= 500:
                    raise
                continue

            if isinstance(create_resp, dict):
                new_id = create_resp.get("id")
                if new_id:
                    name_to_id[name] = str(new_id)

        meta_def_response = self.client.request(
            "GET",
            f"/datasets/{dataset_id}/metadata",
        )
        items = _extract_items(meta_def_response)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                meta_id = item.get("id")
                if name and meta_id:
                    name_to_id[name] = str(meta_id)

        metadata_list: List[Dict[str, Any]] = []
        for key, value in metadata.items():
            name = str(key).strip()
            if not name:
                continue
            meta_id = name_to_id.get(name)
            if not meta_id:
                continue
            metadata_list.append(
                {
                    "id": meta_id,
                    "name": name,
                    "value": value,
                }
            )

        if not metadata_list:
            return metadata

        payload = {
            "operation_data": [
                {
                    "document_id": document_id,
                    "metadata_list": metadata_list,
                }
            ]
        }
        self.client.request(
            "POST",
            f"/datasets/{dataset_id}/documents/metadata",
            json=payload,
        )
        return metadata

    def _find_document_by_name(
        self,
        dataset_id: str,
        document_name: str,
    ) -> Optional[Dict[str, Any]]:
        response = self.client.request(
            "GET",
            f"/datasets/{dataset_id}/documents",
            params={"page": 1, "limit": 200, "keyword": document_name},
        )
        documents: List[Dict[str, Any]] = []
        data = response.get("data")
        if isinstance(data, dict) and "documents" in data:
            documents = data["documents"] or []
        elif isinstance(data, list):
            documents = data
        elif "documents" in response:
            documents = response["documents"] or []

        for document in documents:
            name = document.get("name") or document.get("document_name")
            if name and name.lower() == document_name.lower():
                return document
        return None

    def _assert_document_exists(self, dataset_id: str, document_id: str) -> Dict[str, Any]:
        response = self.client.request(
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}",
        )
        if not isinstance(response, dict):
            raise KnowledgeBaseError(
                f"Document {document_id} not found inside dataset {dataset_id}."
            )
        return response

    @staticmethod
    def _extract_document_id(document: Dict[str, Any]) -> str:
        doc_id = document.get("id") or document.get("document_id")
        if not doc_id:
            raise KnowledgeBaseError("Unable to resolve document_id from Knowledge Base response.")
        return str(doc_id)

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _safe_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class KnowledgeBaseChunkCore:
    """Handles conversion of an indexed document into a single segment."""

    def __init__(self, client: KnowledgeBaseClient) -> None:
        self.client = client


    def _wait_for_completed(
        self,
        dataset_id: str,
        document_id: str,
        *,
        timeout_seconds: int = 60,
        poll_interval_seconds: int = 3,
    ) -> Dict[str, Any]:
        """
        Polls the document status until indexing_status == 'completed'
        or timeout expires. Raises KnowledgeBaseError on timeout or error statuses.
        """
        deadline = time.time() + timeout_seconds

        last_status: str | None = None
        while True:
            document = self._get_document(dataset_id, document_id)
            status = (
                document.get("indexing_status")
                or document.get("data", {}).get("indexing_status")
                or document.get("document", {}).get("indexing_status")
            )

            last_status = str(status) if status is not None else None

            if last_status == "completed":
                return document

            if last_status in {"error", "failed"}:
                raise KnowledgeBaseError(
                    f"Document {document_id} indexing failed with status {last_status!r}."
                )

            if time.time() >= deadline:
                raise KnowledgeBaseError(
                    f"Timed out waiting for document {document_id} to complete; "
                    f"last indexing_status was {last_status!r}."
                )

            time.sleep(poll_interval_seconds)

    def replace_with_single_segment(
        self,
        *,
        dataset_id: str,
        document_id: str,
        content: Any,
        keywords: Optional[List[str]] = None,
        timeout_seconds: int = 60,
        poll_interval_seconds: int = 3,
    ) -> Dict[str, Any]:
        self._wait_for_completed(
            dataset_id=dataset_id,
            document_id=document_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

        normalized_content = content if isinstance(content, str) else str(content)

        self._update_document_text(
            dataset_id=dataset_id,
            document_id=document_id,
            rendered_text=normalized_content,
        )

        self._wait_for_completed(
            dataset_id=dataset_id,
            document_id=document_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

        segment_ids = self._list_segments(dataset_id, document_id)
        for segment_id in segment_ids:
            self._delete_segment(dataset_id, document_id, segment_id)

        payload = {
            "segments": [
                {
                    "content": normalized_content,
                    "keywords": keywords or [],
                }
            ]
        }
        self.client.request(
            "POST",
            f"/datasets/{dataset_id}/documents/{document_id}/segments",
            json=payload,
        )

        return {
            "dataset_id": dataset_id,
            "document_id": document_id,
            "converted_to_single_chunk": True,
        }



    def _list_segments(self, dataset_id: str, document_id: str) -> List[str]:
        response = self.client.request(
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}/segments",
        )
        segments: List[Dict[str, Any]] = []
        data = response.get("data")
        if isinstance(data, dict) and "segments" in data:
            segments = data["segments"] or []
        elif isinstance(data, list):
            segments = data
        elif "segments" in response:
            segments = response["segments"] or []

        segment_ids: List[str] = []
        for segment in segments:
            segment_id = segment.get("id") or segment.get("segment_id")
            if segment_id:
                segment_ids.append(str(segment_id))
        return segment_ids

    def _delete_segment(self, dataset_id: str, document_id: str, segment_id: str) -> None:
        try:
            self.client.request(
                "DELETE",
                f"/datasets/{dataset_id}/documents/{document_id}/segments/{segment_id}",
            )
        except KnowledgeBaseError as exc:
            # Se il segmento è già stato eliminato (404 not_found),
            # non è un errore per noi: lo consideriamo "già cancellato".
            if exc.status_code == 404:
                payload = exc.payload or {}
                error_code = str(payload.get("code") or "").lower()
                if error_code == "not_found":
                    return

            # Per qualsiasi altro errore, rilanciamo
            raise


    def _get_document(self, dataset_id: str, document_id: str) -> Dict[str, Any]:
        return self.client.request(
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}",
        )

    def _update_document_text(
        self,
        *,
        dataset_id: str,
        document_id: str,
        rendered_text: str,
    ) -> None:
        text_value = rendered_text if isinstance(rendered_text, str) else str(rendered_text)

        name_value: str = document_id  # fallback sicuro
        try:
            doc = self._get_document(dataset_id, document_id)
            raw = None
            if isinstance(doc, dict):
                if isinstance(doc.get("data"), dict):
                    raw = doc["data"]
                else:
                    raw = doc

            if isinstance(raw, dict):
                candidate = raw.get("name")
                if isinstance(candidate, str) and candidate.strip():
                    name_value = candidate
        except KnowledgeBaseError:
            pass

        payload = {
            "text": text_value,
            "name": name_value,
        }

        self.client.request(
            "POST",
            f"/datasets/{dataset_id}/documents/{document_id}/update_by_text",
            json=payload,
        )

