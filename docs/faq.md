# FAQ and troubleshooting

## Is my data private?

Yes, with one specific exception. auto-zcurve runs entirely on your own
computer — it's not a hosted website, and your projects are never uploaded
anywhere. The one exception: when you run an analysis, each PDF (or its
parsed text) is sent to the AI provider you've configured, because that's
how statistics get extracted from it. See
[Getting an API key](api-key.md#what-gets-sent-to-the-provider) for exactly
what that involves.

## Does this cost money?

auto-zcurve itself is free. Running an analysis uses your own AI provider
API key, which may incur small costs depending on the provider, model, and
number/length of articles — check current Gemini pricing if that matters
for your budget. Gemini's free tier is usually enough for trying the app out
on a handful of articles.

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

Try **Retry failed** after fixing the underlying PDF or schema. If several
unrelated articles fail with the same error, it's more likely a settings or
schema issue than a problem with any one PDF.

## "Unlock or add a Gemini API key to run"

You either haven't added a key yet, or you added one previously, saved it,
and need to unlock it again for this session (saved keys are never
automatically loaded — see [Getting an API key](api-key.md)). Go to
**Settings**, or use the shortcut button on the **Run analysis** tab.

## macOS asks for a Keychain password and shows "Python"

This is expected — auto-zcurve's bundled runtime is Python underneath, and
macOS sometimes surfaces that name (occasionally with a version number)
instead of "auto-zcurve" in the authorization prompt. Approving it lets
auto-zcurve read the key you've stored in your Keychain.

## I want to change which Gemini models are offered

The root `models.yml` file (in the folder where you installed auto-zcurve)
lists the Gemini models available in the app. Edit it and the change takes
effect immediately — no restart needed. See the
[README](https://github.com/shaheedazaad/auto-zcurve#model-allowlist) for
the exact format.

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
