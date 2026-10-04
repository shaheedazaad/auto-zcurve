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

1. Open **Settings** from the top bar.
2. Expand the provider under **Provider connections**, or select it in the sidebar.
3. Click **Add key**, paste the key, and click **Save key**.
4. Tick **Store securely on this computer** to save it between sessions.

The provider shows **Ready** when its key is available for the current
session. Default model settings live in the same provider section but have
their own save button. See [Settings and providers](settings.md).

## What "store securely" actually means

Saved keys use the operating system's credential store, such as macOS
Keychain, Windows Credential Manager, or an available Linux secret service.
The app does not save them in its settings file or include them in project
downloads. If no usable credential store is available, keys can be used in
memory for the current app session.

Starting the app or opening Settings does not read saved keys. **Unlock**
loads a key explicitly. **Run analysis** and **Retry failed** also load the
selected provider's saved key when needed; OpenRouter model validation can
load its key too. The operating system may ask for authorisation.

**Replace** changes the key. **Remove** deletes the saved key and forgets the
session copy. For a keyless endpoint, leave its key unset.

!!! note "macOS Keychain prompt"
    The prompt may identify the bundled runtime as “Python” rather than
    “auto-zcurve.” Approve it when you intend to use the saved key.

## What gets sent to the provider

Extraction sends the project instructions and schema, together with the
article content:

- **Gemini:** the original PDF.
- **OpenRouter:** the PDF, including its filename, for processing through its
  file-parser service and selected model.
- **OpenAI-compatible:** locally extracted text with page numbers, sent to
  the API base URL you configured. A local server can keep that request on
  your computer; a remote server receives the text.

Article content may itself contain author names or other personal information.
Provider credentials are sent for authentication. Projects and reports are
stored on your computer; the app does not upload the entire project folder.

## An alternative provider: OpenRouter

Create a key through your OpenRouter account, then add it to the
**OpenRouter (Experimental)** section. Enter the exact model ID in the
project. See [OpenRouter settings](settings.md#openrouter) for validation and
PDF handling.

## OpenAI-compatible endpoints

For a hosted or local Chat Completions server, follow the
[endpoint setup instructions](settings.md#openai-compatible). A key is optional
only when the server permits unauthenticated requests.

## Next step

Continue to [Your first project](quickstart.md).
