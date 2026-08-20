# Getting an API key

auto-zcurve doesn't run its own AI model — it sends each PDF to an AI
provider's servers and asks for the statistics back. To do that, it needs an
**API key**: a private password-like string that identifies your account with
that provider and lets them bill you for what you use.

The default and recommended provider is **Google Gemini**.

## Create a Gemini API key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey) and sign
   in with a Google account.
2. Create a new API key.
3. Copy it — it's a long string of letters and numbers.

Gemini's free tier is usually enough to try auto-zcurve out on a handful of
articles. Larger projects or heavier models may incur small costs on Google's
side; check current Gemini pricing if that matters for your budget.

## Add the key to auto-zcurve

Open **Settings** in the app and paste the key into the **Gemini** row (see
[Your first project](quickstart.md) for a screenshot of this screen). Tick
**Store securely on this computer** if you want auto-zcurve to remember it
between sessions.

## What "store securely" actually means

When you check that box, auto-zcurve saves the key using your operating
system's built-in credential store (macOS Keychain, Windows Credential
Manager, or the Linux secret service, when one is available). It is **never**
written to a plain text file, and it is **never** included in a project's
files, logs, downloads, or reports.

A saved key is not automatically loaded the next time you open the app — you
choose to unlock it each session, so a key never sits "on" without your
knowledge.

!!! note "macOS Keychain prompt"
    macOS may ask you to authorize access and identify the app as "Python,"
    sometimes with a version number, rather than "auto-zcurve." This is
    expected — auto-zcurve runs on Python underneath.

## What gets sent to the provider

During a run, each PDF (or its parsed text, depending on your parser setting)
is sent to the provider along with your extraction instructions and schema.
That's it — no other project files, filenames, or personal information leave
your computer as part of that request.

## An alternative provider: OpenRouter

OpenRouter is available as an experimental backup if you want to try a
different model. It requires you to type in an exact OpenRouter model ID
yourself (there's no built-in list, and quality/reliability varies by model),
so it's best treated as a fallback rather than a first choice.

## Next step

Continue to [Your first project](quickstart.md).
