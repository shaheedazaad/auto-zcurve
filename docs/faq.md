# FAQ and troubleshooting

## Is my data private?

Yes, with one specific exception. auto-zcurve runs entirely on your own
computer — it's not a hosted website, and your projects are never uploaded
anywhere. The one exception: when you run an analysis, the project's
instructions and schema, together with each article (the PDF, its parsed text,
or an XML file's text), are sent to the AI provider you've configured, because
that's how statistics get extracted from it. See
[Getting an API key](api-key.md#what-gets-sent-to-the-provider) for exactly
what that involves.

## Does this cost money?

auto-zcurve itself is free. Hosted providers may charge for requests; check
your account's pricing and quota. Local endpoint costs and hardware needs
depend on the server you run. auto-zcurve does not supply a model service.

## The browser link stopped working

Every time you launch auto-zcurve (`auto-zcurve` in your terminal), it
generates a brand-new, random web address for security. A link from a
previous session will always show an error — just switch back to the
terminal window and look for the current address, or run `auto-zcurve`
again if you'd closed it.

## The app closed when I closed my terminal

That's expected. auto-zcurve runs as long as its terminal window stays
open. Closing the terminal (or pressing <kbd>Ctrl</kbd>+<kbd>C</kbd> inside
it) stops the app. Your projects and results are saved to disk regardless,
and reopening the app with `auto-zcurve` picks up where you left off.

## An article keeps failing

Check the error message next to it on the **Articles** tab first. Common
causes:

- The PDF is a scanned image with no extractable text or very poor OCR
  quality.
- The PDF is password-protected or corrupted.
- The extraction schema doesn't match what's actually in the article (for
  example, `required: true` on a field that isn't always reported).
- An XML article is longer than the model's context window (the message
  gives both sizes). Choose a model with a larger context window; see
  [XML articles](quickstart.md#2-add-your-articles).

Temporary provider errors, such as rate limits (HTTP 429) and server errors
(HTTP 5xx), are retried automatically with increasing waits, up to three
attempts per request, before an article is marked as failed. Errors that
won't go away on their own, such as an invalid key or a rejected request, fail
straight away. The exception is an OpenRouter model that reads PDFs through
OpenRouter's parser (one without native PDF input): if the first attempt
fails for any reason, including an invalid key or a rejected request (for
example HTTP 400 or 401), auto-zcurve tries once more with a different PDF
parser (Mistral OCR) before marking the article as failed.

Try **Retry** after fixing the underlying article, schema or model choice.
If several unrelated articles fail with the same error, it's more likely a
settings or schema issue than a problem with any one article.

## The app asks for an API key

The project's extractor has no key. Open **Settings**, click **Edit** on the
extractor, and paste a key into **API key**. See
[API keys and privacy](api-key.md).

## I set GEMINI_API_KEY but the app doesn't use it

API keys are no longer read from environment variables or `.env` files. Add
the key to an [extractor](settings.md#extractors) instead.

## My OpenAI-compatible endpoint will not run

Check the [endpoint settings](settings.md#openai-compatible):

- Use the API base URL, including `/v1` if required, without `/chat/completions`.
- Make sure the server is running and the extractor's model ID matches a model it serves.
- Add or replace the extractor's key if the server requires authentication.
- If it rejects `response_format`, edit the extractor and choose a supported JSON output mode.
- If a PDF has no extractable text, run OCR first. This provider does not send figures.

A successful connection does not guarantee valid extraction output. All JSON
modes still undergo schema validation. Check the article's failure message
before retrying.

## How do I uninstall?

Follow [Uninstalling](installation.md#uninstalling). Projects and saved
credentials are preserved; the scripts remove the app and launcher.

## macOS asks for a Keychain password and shows "Python"

This is expected — auto-zcurve's bundled runtime is Python underneath, and
macOS sometimes surfaces that name (occasionally with a version number)
instead of "auto-zcurve" in the authorization prompt. Approving it lets
auto-zcurve read the keys you've stored in your Keychain. All extractors'
keys share one Keychain item, so you are asked at most once per session.

## How do I use a different model?

Edit the project's extractor in **Settings** and enter any model ID the
provider offers, or create a new extractor and choose it on the project's
**Run analysis** tab. **Show available models** lists the provider's current
models, using the extractor's saved key or the key typed into the form
(OpenRouter's list needs no key).

## Where are my projects actually stored?

- **macOS:** `~/Library/Application Support/Auto Z-Curve/projects`
- **Windows:** `%LOCALAPPDATA%\Auto Z-Curve\projects`
- **Linux:** `${XDG_DATA_HOME:-~/.local/share}/auto-zcurve/projects`

Each project has its own folder there, including its `output/` results —
see [Understanding your results](results.md).

## I'm still stuck

Open an issue on the project's GitHub repository with a description of what
you tried and what happened; it helps to include the exact error message
shown in the app.
