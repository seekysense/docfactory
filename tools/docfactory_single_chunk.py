from __future__ import annotations

from collections.abc import Generator
from typing import Any, Dict, Optional

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from docfactory_core import (
    KnowledgeBaseChunkCore,
    KnowledgeBaseClient,
    KnowledgeBaseError,
    RenderCore,
    extract_keywords,
)


class DocfactorySingleChunkTool(Tool):
    """Convert an indexed document into a single KB segment."""

    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        params: Dict[str, Any] = tool_parameters or {}

        dataset_id = self._resolve_dataset_id(params)
        document_id = self._safe_str(params.get("document_id"))
        result: Dict[str, Any] = {
            "dataset_id": dataset_id,
            "document_id": document_id,
            "converted_to_single_chunk": False,
            "error": None,
        }

        if not dataset_id:
            result["error"] = "dataset_id is required for the SingleChunk tool."
            yield from self._yield_messages(result)
            return

        if not document_id:
            result["error"] = "document_id is required for the SingleChunk tool."
            yield from self._yield_messages(result)
            return

        raw_text = params.get("rendered_text")
        if raw_text is None:
            result["error"] = "rendered_text parameter is required."
            yield from self._yield_messages(result)
            return

        content = raw_text if isinstance(raw_text, str) else str(raw_text)
        if not content.strip():
            result["error"] = "rendered_text cannot be empty when building a single chunk."
            yield from self._yield_messages(result)
            return

        try:
            data_context = RenderCore.coerce_json(
                params.get("data"),
                field_name="data",
                required=False,
            )
        except ValueError as exc:
            result["error"] = str(exc)
            yield from self._yield_messages(result)
            return

        keywords = extract_keywords(data_context) if data_context else []

        try:
            core = self._build_chunk_core()
        except KnowledgeBaseError as exc:
            result["error"] = str(exc)
            yield from self._yield_messages(result)
            return

        try:
            summary = core.replace_with_single_segment(
                dataset_id=dataset_id,
                document_id=document_id,
                content=content,
                keywords=keywords,
            )
        except KnowledgeBaseError as exc:
            result["error"] = str(exc)
        else:
            result.update(summary)

        yield from self._yield_messages(result)

    def _build_chunk_core(self) -> KnowledgeBaseChunkCore:
        credentials = self.runtime.credentials or {}
        base_url = (credentials.get("dify_api_base_url") or "").strip()
        api_key = (credentials.get("dify_api_key") or "").strip()
        if not base_url or not api_key:
            raise KnowledgeBaseError(
                "dify_api_base_url and dify_api_key must be configured for the SingleChunk tool."
            )
        client = KnowledgeBaseClient(base_url, api_key)
        return KnowledgeBaseChunkCore(client)

    def _resolve_dataset_id(self, params: Dict[str, Any]) -> Optional[str]:
        explicit = self._safe_str(params.get("dataset_id"))
        if explicit:
            return explicit
        credentials = self.runtime.credentials or {}
        fallback = self._safe_str(credentials.get("default_dataset_id"))
        if fallback:
            return fallback
        return None

    @staticmethod
    def _safe_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _yield_messages(
        self, result: Dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        if not result.get("dataset_id"):
            result["dataset_id"] = None

        yield self.create_json_message(result)

        error = result.get("error")
        if error:
            yield self.create_text_message(f"DocFactory SingleChunk error: {error}")
        else:
            dataset = result.get("dataset_id") or "unknown dataset"
            document = result.get("document_id") or "unknown document"
            yield self.create_text_message(
                f"Document {document} in dataset {dataset} converted to single chunk."
            )

        if result.get("dataset_id"):
            yield self.create_variable_message("dataset_id", result["dataset_id"])
        if result.get("document_id"):
            yield self.create_variable_message("document_id", result["document_id"])
        converted_flag = "True" if result.get("converted_to_single_chunk") else "False"
        yield self.create_variable_message("converted_to_single_chunk", converted_flag)
        yield self.create_variable_message("error", result.get("error") or "")
