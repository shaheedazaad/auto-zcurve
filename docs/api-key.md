# API keys and privacy

Gemini and OpenRouter require an **API key**, a private password-like string
that identifies your account with the provider. An OpenAI-compatible server
may require a key or allow keyless requests. You supply the provider account
or server; auto-zcurve does not supply an AI service.

## Create a Gemini API key

Go to [Google AI Studio](https://aistudio.google.com/app/apikey), sign in,
and create and copy a key. Check your provider account for current pricing,
quotas, and available models before running a large project.

## Add the key to auto-zcurve

Keys belong to [extractors](settings.md#extractors), so you can keep several
keys for the same provider.

1. Open **Settings** from the top bar.
2. Click **Add extractor**, or **Edit** on an existing one.
3. Fill in the name, provider, and model, paste the key into **API key**, and
   click **Save extractor**.

The extractor shows **Key saved** once its key is stored. To change a key,
edit the extractor and paste a new one into **Replace API key**. **Remove
key** deletes it. For a keyless OpenAI-compatible server, leave the key
empty.

API keys are not read from environment variables or `.env` files.

## Where keys are kept

All extractors' keys are kept together in **one** item in your operating
system's credential store, such as macOS Keychain, Windows Credential Manager,
or an available Linux secret service. The app never writes keys to its
settings files, project folders, logs, or results downloads, and never sends
them to the browser page.

That item is first opened when a key is needed: when you run or retry a
project, save or remove an extractor's key, or ask for its model list. Once it
has been read, it isn't opened again that app session; if it couldn't be read,
for example because you denied access, the app asks again the next time a key
is needed. Starting the app, opening Settings, or opening a project never
reads it. Your computer may ask for permission when it is opened.

If no usable credential store is available, keys are kept in memory for the
current app session only, and Settings says so.

If saved keys can't be read, for example because access was denied, actions
that require a saved key to proceed stop with a message. Editing a model
still saves, and the model is checked when it runs. The app never replaces
saved keys it couldn't read, so other extractors' keys are not lost.

!!! note "macOS Keychain prompt"
    The prompt may identify the bundled runtime as “Python” rather than
    “auto-zcurve.” Approve it when you intend to use your saved keys.

## What gets sent to the provider

Extraction sends the project instructions and schema, together with the
article content:

- **Gemini:** the original PDF.
- **OpenRouter:** the PDF, including its filename, for processing through its
  file-parser service and selected model.
- **OpenAI-compatible:** locally extracted text with page numbers, sent to
  the API base URL you configured. A local server can keep that request on
  your computer; a remote server receives the text.

**XML articles** are sent the same way to every provider: the file's full
text, decoded as UTF-8 (a leading byte-order mark is dropped) and otherwise
unchanged, inside the request. No file is uploaded and no parser service is
used. If the article is clearly longer than the model's known context window,
the article fails with an error and nothing is sent; it is never shortened.

Article content may itself contain author names or other personal information.
Provider credentials are sent for authentication. Projects and reports are
stored on your computer; the app does not upload the entire project folder.

## An alternative provider: OpenRouter

Create a key through your OpenRouter account, then add an extractor with the
**OpenRouter (Experimental)** provider, the exact model ID, and that key. See
[OpenRouter settings](settings.md#openrouter-experimental) for validation and
PDF handling.

## OpenAI-compatible endpoints

For a hosted or local Chat Completions server, follow the
[endpoint setup instructions](settings.md#openai-compatible). A key is optional
only when the server permits unauthenticated requests.

## Next step

Continue to [Your first project](quickstart.md).
