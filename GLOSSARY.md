# auto-zcurve

Extracts reported statistical tests from research articles with an LLM, lets a researcher review them, and produces z-curve reports.

## Language

**Source**:
An article file uploaded to a project, from which statistics are extracted.
_Avoid_: Document, input, PDF (when the format is not specifically PDF)

**Source format**:
The file format of a source: PDF or XML.
_Avoid_: File type, input type

**Article text**:
The text of a source as given to the model when the source is not sent as a PDF.
_Avoid_: Parsed document, extracted text

**Extractor**:
A named, saved configuration (provider, model, endpoint and request parameters) that a run uses to send sources to a model; its API key, if any, is stored separately in the key vault. A user can have several, including several for the same provider.
_Avoid_: Model config, profile, connection, provider (when the whole configuration is meant)

**Provider**:
The API family an extractor talks to: Gemini, OpenRouter or OpenAI-compatible.
_Avoid_: Backend, vendor

**Key vault**:
The single credential-store item that holds every extractor's API key, read at most once per app session after a successful unlock.
_Avoid_: Keychain (it may be another operating system's store), credentials file
