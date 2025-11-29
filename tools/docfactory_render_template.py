from __future__ import annotations

from collections.abc import Generator
from typing import Any, Dict

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from docfactory_core import RenderCore


class DocfactoryRenderTool(Tool):
    """Tool responsible only for rendering Jinja2 templates."""

    MAX_TEXT_MESSAGE_LENGTH = 4000
    MAX_VARIABLE_LENGTH = 20000

    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        params: Dict[str, Any] = tool_parameters or {}
        result: Dict[str, Any] = {"rendered_text": "", "error": None}

        try:
            data_context = RenderCore.coerce_json(
                params.get("data"),
                field_name="data",
                required=True,
            )
        except ValueError as exc:
            result["error"] = str(exc)
            yield from self._yield_error_messages(result)
            return

        template_string = params.get("template")
        if not isinstance(template_string, str) or not template_string.strip():
            result["error"] = "template is required and must be a non-empty string."
            yield from self._yield_error_messages(result)
            return

        try:
            engine_options = RenderCore.coerce_json(
                params.get("template_engine_options"),
                field_name="template_engine_options",
                required=False,
            )
        except ValueError as exc:
            result["error"] = str(exc)
            yield from self._yield_error_messages(result)
            return

        try:
            renderer = RenderCore()
            rendered_text = renderer.render(
                template_string,
                data_context,
                engine_options or {},
            )
        except Exception as exc:  
            result["error"] = f"Template rendering failed: {exc}"
            yield from self._yield_error_messages(result)
            return

        result["rendered_text"] = rendered_text

        yield self.create_json_message(result)

        snippet = rendered_text[: self.MAX_TEXT_MESSAGE_LENGTH]
        if snippet:
            yield self.create_text_message(snippet)
        else:
            yield self.create_text_message("Template rendered successfully but produced empty text.")

        if len(rendered_text) <= self.MAX_VARIABLE_LENGTH:
            yield self.create_variable_message("rendered_text", rendered_text)
        yield self.create_variable_message("error", "")

    def _yield_error_messages(
        self, result: Dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        yield self.create_json_message(result)
        error_message = result.get("error") or "Unknown DocFactory render error."
        yield self.create_text_message(f"DocFactory render error: {error_message}")
        yield self.create_variable_message("error", error_message)
