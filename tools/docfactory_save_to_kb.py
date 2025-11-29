from __future__ import annotations

from collections.abc import Generator
from typing import Any, Dict

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from docfactory_core import (
    KnowledgeBaseClient,
    KnowledgeBaseDocumentCore,
    KnowledgeBaseError,
    RenderCore,
)


class DocfactorySaveToKBTool(Tool):
    """Persist rendered text into the Dify Knowledge Base via text-based APIs."""

    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        params: Dict[str, Any] = tool_parameters or {}

        result: Dict[str, Any] = {
            "saved_to_kb": False,
            "dataset_id": params.get("dataset_id"),
            "document_id": params.get("document_id"),
            "metadata_applied": None,
            "error": None,
        }

        raw_text = params.get("rendered_text")
        if raw_text is None:
            result["error"] = "rendered_text parameter is required."
            yield from self._yield_messages(result)
            return

        rendered_text = raw_text if isinstance(raw_text, str) else str(raw_text)
        if not rendered_text.strip():
            result["error"] = "rendered_text cannot be empty."
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

        try:
            core = self._build_document_core()
        except KnowledgeBaseError as exc:
            result["error"] = str(exc)
            yield from self._yield_messages(result)
            return

        try:
            summary = core.save_text_document(
                rendered_text=rendered_text,
                parameters=params,
                data_context=data_context,
            )
        except KnowledgeBaseError as exc:
            result["error"] = str(exc)
        except ValueError as exc:
            result["error"] = str(exc)
        else:
            result.update(summary)

        yield from self._yield_messages(result)

    def _build_document_core(self) -> KnowledgeBaseDocumentCore:
        credentials = self.runtime.credentials or {}
        base_url = (credentials.get("dify_api_base_url") or "").strip()
        api_key = (credentials.get("dify_api_key") or "").strip()
        if not base_url or not api_key:
            raise KnowledgeBaseError(
                "dify_api_base_url and dify_api_key must be configured for SaveToKB tool."
            )
        client = KnowledgeBaseClient(base_url, api_key)
        return KnowledgeBaseDocumentCore(
            client,
            default_dataset_id=credentials.get("default_dataset_id"),
        )

    def _yield_messages(
        self, result: Dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        yield self.create_json_message(result)

        error = result.get("error")
        if error:
            yield self.create_text_message(f"DocFactory KB save error: {error}")
        else:
            dataset = result.get("dataset_id") or "unknown dataset"
            document = result.get("document_id") or "unknown document"
            yield self.create_text_message(
                f"DocFactory KB save completed for document {document} in dataset {dataset}."
            )

        if result.get("document_id"):
            yield self.create_variable_message("document_id", result["document_id"])
        if result.get("dataset_id"):
            yield self.create_variable_message("dataset_id", result["dataset_id"])
        saved_flag = "True" if result.get("saved_to_kb") else "False"
        yield self.create_variable_message("saved_to_kb", saved_flag)
        yield self.create_variable_message("error", result.get("error") or "")
