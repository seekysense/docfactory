from typing import Any
from urllib.parse import urlparse

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError


class DocfactoryProvider(ToolProvider):
    """
    Tool provider for Docfactory integration.
    Validates credentials for Dify API access.
    1. Both 'dify_api_base_url' and 'dify_api_key' must be provided together to enable
       Knowledge Base saving.   
    """

    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        base_url = (credentials.get("dify_api_base_url") or "").strip()
        api_key = (credentials.get("dify_api_key") or "").strip()
        # default_dataset_id può essere sempre opzionale
        # default_dataset_id = (credentials.get("default_dataset_id") or "").strip()

        # Caso 1: tutto vuoto -> accettiamo (modalità "solo render")
        if not base_url and not api_key:
            return

        # Caso 2: uno dei due mancante -> errore chiaro
        if bool(base_url) ^ bool(api_key):
            raise ToolProviderCredentialValidationError(
                "Either leave both Dify API Base URL and API Key empty, "
                "or provide both values to enable Knowledge Base saving."
            )

        # Caso 3: entrambi presenti -> controllo sintattico veloce sull'URL
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ToolProviderCredentialValidationError(
                "Invalid Dify API Base URL."
            )

        # Nessuna chiamata HTTP qui: se formato ok, consideriamo le credenziali valide.
        return
