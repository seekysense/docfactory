# DocFactory for Dify

DocFactory is a Dify plugin that turns **raw JSON data into expressive text documents**, and (optionally) **stores them in the Dify Knowledge Base (KB)**.

The goal is to make JSON easy to use in two directions:

- **For LLMs** – generate rich, context-aware text from JSON **without** having to explain every field to the model in a long prompt.
- **For humans** – produce readable documents (reports, summaries, “cards”) from the same JSON, and save them as searchable KB documents with precise metadata.

At its core, DocFactory uses **Jinja2 templates** plus a few helpers (custom filters, metadata handling, upsert logic, single-chunk management) so you can keep JSON as your single source of truth, and let templates decide *how* that JSON is presented and stored.

---

## Why template JSON instead of dumping it to the LLM?

Rendering JSON into a text template before sending it to an LLM is a winning strategy because:

1. **Maximum expressivity of information**  
   With Jinja2 templates you can:
   - show/hide fields with `if/else`
   - loop over collections
   - change the narrative depending on values (e.g. overdue invoices vs. paid ones)
   This gives you fine-grained control over *how* the same JSON appears in different contexts.

2. **No unnecessary filtering steps**  
   Instead of pre-filtering JSON in tools and workflows, you simply pass the **full JSON** to the template and let the template itself decide what is relevant for that specific document.

3. **Less prompt noise**  
   You don’t need huge prompts like _“field X means this, field Y means that, please ignore these 20 technical flags…”_.  
   The template already transforms JSON into a human/LLM-friendly narrative, so the final prompt can stay short and focused.

4. **Context-aware semantics with `if/then` logic**  
   Thanks to templating logic, you can express “business meaning” directly in the template instead of encoding it in long, fragile prompts.  
   Example: if `balance < 0` then write “customer in credit”; otherwise “customer has an outstanding debt”.

In short: **JSON = data model**, **templates = presentation & meaning**, **LLM = reasoning**.

---

## High-level architecture

DocFactory ships as a single plugin with three tools:

1. **DocFactory – Render template**  
   `docfactory_render_template`  
   Render a Jinja2 template using JSON data and return only the text.

2. **DocFactory – Save to KB**  
   `docfactory_save_to_kb`  
   Take rendered text (typically from step 1), attach metadata, and upsert a document into the Dify Knowledge Base.

3. **DocFactory – Single chunk**  
   `docfactory_single_chunk`  
   Take an **already indexed** KB document and replace all its segments with a **single segment** containing your final text.  
   This lets you use the KB as a “pure” document store where each document is one continuous text block.

---

## Plugin credentials

At provider level (`docfactory.yaml`) you can configure:

- `dify_api_base_url` (optional, **required for KB operations**)  
  Base URL of your Dify API, e.g. `http://api:5001/v1`.  
  If left empty, DocFactory can still render templates but **cannot** save or modify KB documents.

- `dify_api_key` (optional, **required for KB operations**)  
  A Dify API key with permission to access the Knowledge Base.  
  If empty, KB operations will fail with a clear error.

- `default_dataset_id` (optional)  
  Dataset ID used as a fallback when tools are called without an explicit `dataset_id`.

If you only need to **render text for LLMs**, you can leave all credentials empty.  
If you want to **save to KB** or use **Single chunk**, you must configure `dify_api_base_url` and `dify_api_key`.

---

## Tool 1 – DocFactory: Render template

**Tool ID:** `docfactory_render_template`  
**Purpose:** Turn JSON into text using a Jinja2 template. No persistence, just rendering.

### Inputs

- `data` (string, required)  
  JSON object or JSON string used as the template context.  
  Internally parsed/validated; if invalid JSON, the tool returns a structured error.

- `template` (string, required)  
  Jinja2 template body.  
  You can use normal Jinja features plus two custom filters:
  - `format_currency(value, currency="EUR", decimals=2)`  
    Renders numbers like `1.234,50 EUR` (European style).
  - `format_date(value, fmt="%d/%m/%Y")`  
    Accepts many date formats / timestamps and normalizes them to your format.

- `template_engine_options` (string, optional, JSON)  
  JSON object to tweak the Jinja2 environment.  
  Supported keys (all optional), for example:
  ```json
  {
    "strict_variables": true,
    "autoescape": false,
    "trim_blocks": true,
    "lstrip_blocks": true
  }
``

### Outputs

* `rendered_text` (string)
  The full rendered document string.
  Also exposed as a variable (up to 20,000 characters) so you can pass it directly into an LLM step.

* `error` (string, optional)
  Empty on success; contains a message if something went wrong (e.g. invalid JSON, invalid template).

### Typical usage

1. Fetch JSON via an HTTP tool or another plugin.
2. Call **DocFactory – Render template** with:

   * `data`: the fetched JSON
   * `template`: your Jinja2 template for that domain (invoice, customer card, etc.)
3. Feed `rendered_text` into your LLM as part of the system or user message.

You now give the LLM a **clean narrative** instead of raw JSON.

---

## Tool 2 – DocFactory: Save to KB

**Tool ID:** `docfactory_save_to_kb`
**Purpose:** Persist rendered text into the Dify Knowledge Base, with flexible upsert behavior and metadata.

### Inputs

* `rendered_text` (string, required)
  The text you want to store (often the output of `docfactory_render_template`, but can be anything).

* `data` (string, optional, JSON)
  Original JSON context.
  Used for things like:

  * auto-generating a **document name** if none is provided (e.g. using `name`, `title`, `document_name`, `customer_code`)
  * extracting **keywords** or extra metadata (via helper functions in the core).

* `dataset_id` (string, optional)
  Target dataset.
  If omitted, falls back to provider-level `default_dataset_id`.
  If neither is present, the tool raises a clear error.

* `document_id` (string, optional)
  If provided, the tool **updates this existing document**.

* `document_name` (string, optional)
  “Friendly” document name to **search or create** a document when `document_id` is not set.
  If neither `document_id` nor `document_name` are provided, DocFactory will auto-generate a name from `data` (e.g. using fields like `customer_code`).

* `metadata_json` (string, optional, JSON)
  Additional metadata to store on the document.
  This is merged with a default base:

  ```json
  {
    "generated_by": "DocFactory",
    "...your fields..."
  }
  ```

  Example:

  ```json
  {
    "customer_code": "C12345",
    "year": 2024,
    "document_type": "accounting_sheet"
  }
  ```

* `upsert_mode` (string, optional)
  Controls how the tool behaves when the document exists or not.
  Allowed values:

  * `create_or_update` (default) – if document exists, update it; otherwise create.
  * `create_only` – create a new document, fail if it already exists.
  * `update_only` – update an existing document, fail if it does not exist.

### Outputs

* `saved_to_kb` (boolean)
  `true` if the document was successfully saved/updated.

* `dataset_id` (string)
  The dataset actually used.

* `document_id` (string)
  The final document ID in the KB.

* `metadata_applied` (object, optional)
  The metadata that ended up on the document (merged base + your `metadata_json`).

* `error` (string, optional)
  Empty on success; contains error description on failure.

### Example – accounting card for a customer

Imagine you fetch an **accounting sheet JSON** from an external API:

1. HTTP tool: `GET /customers/{code}/accounting-sheet` → JSON
2. DocFactory – Render template:

   * `data`: the JSON from the API
   * `template`: a Jinja2 template that generates a human-readable accounting report
3. DocFactory – Save to KB:

   * `rendered_text`: the report text
   * `data`: the original JSON
   * `dataset_id`: e.g. `customer_docs`
   * `metadata_json`:

     ```json
     {
       "customer_code": "C12345",
       "document_type": "accounting_sheet",
       "year": 2024
     }
     ```

Later, you can filter in the KB by `customer_code` to retrieve **exactly that document**.

---

## Tool 3 – DocFactory: Single chunk

**Tool ID:** `docfactory_single_chunk`
**Purpose:** Convert an indexed KB document into a **single segment** containing your final text.

By default, Dify splits documents into multiple chunks.
Sometimes you want the Knowledge Base to behave more like a **document archive**:

* one document = one continuous text
* no splitting into semantic chunks
* retrieval or direct access always returns the **full** document.

That’s exactly what **Single chunk** gives you.

### Inputs

* `dataset_id` (string, optional)
  Dataset containing the document. Falls back to provider `default_dataset_id` if omitted.

* `document_id` (string, required)
  ID of the existing KB document you want to transform.

* `rendered_text` (string, required)
  The **full text** that should remain in the document as its only segment.
  You typically pass the same `rendered_text` you stored earlier, or a new, updated version.

* `data` (string, optional, JSON)
  Optional context used only to extract **keywords** for the new single segment, using fields like:

  * `customer_code`
  * `document_type`
  * `year`
  * `name`
  * `title`

### Behavior

Internally, DocFactory:

1. Waits until the document’s indexing status is `completed`.
2. Updates the document text via the KB text endpoints.
3. Lists all existing segments for that document.
4. Deletes those segments.
5. Creates **one** new segment with:

   * `content`: `rendered_text`
   * `keywords`: extracted from `data` (if provided)

### Outputs

* `dataset_id` (string)
  Dataset used.

* `document_id` (string)
  Document processed.

* `converted_to_single_chunk` (boolean)
  `true` if the document has been successfully converted.

* `error` (string, optional)
  Error message, if something failed.

### Why this matters

This tool lets you use Dify KB as a **pure document system**:

* You save your rendered documents (e.g. reports) via `docfactory_save_to_kb`.
* Then you run `docfactory_single_chunk` so that each document is **exactly one chunk**.
* Later, when you retrieve that document (by ID or via filters/keywords), you always get the entire text as a single block—perfect for:

  * full-document summarization
  * classification
  * legal/contract checks
  * any scenario where splitting into multiple chunks would hurt the context.

---

## Typical workflow patterns

### 1. JSON → Template → LLM (no KB)

1. HTTP tool / other plugin → JSON.
2. `docfactory_render_template` → `rendered_text`.
3. LLM step:

   * System message: your instructions.
   * User message: includes `rendered_text`.

No storage, pure runtime transformation.

---

### 2. JSON → Template → KB document

1. HTTP tool / DB tool → JSON.
2. `docfactory_render_template` → `rendered_text`.
3. `docfactory_save_to_kb`:

   * `rendered_text`: from step 2
   * `data`: original JSON
   * `dataset_id`: your dataset
   * `metadata_json`: identifiers like `customer_code`, `year`, `document_type`.

Optionally:

4. `docfactory_single_chunk`:

   * `dataset_id` / `document_id`: from step 3 outputs
   * `rendered_text`: same text as stored
   * `data`: same JSON, to keep useful keywords.

Now the KB holds a human-readable document, retrievable both via semantic search and exact metadata filters, and stored as a **single continuous segment**.

---

## Notes & limitations

* **KB operations require credentials**
  `dify_api_base_url` and `dify_api_key` must be set for `docfactory_save_to_kb` and `docfactory_single_chunk` to work.

* **Rendering is pure in-memory**
  `docfactory_render_template` never calls external HTTP APIs. It only runs Jinja2 locally inside your Dify instance.

* **Error handling**
  All tools return:

  * a JSON message with detailed `error` field
  * a short text message describing the outcome
  * a variable `error` you can branch on in workflows.

* **Source of truth stays JSON**
  Templates don’t mutate the JSON; they only decide how to represent it.
  You can change templates without touching your underlying data pipelines.

---

DocFactory is meant to be the “document engine” for your JSON:
**you keep the data, it manufactures the text.**
