Translation requires you have Babel installed, and the `pybabel` command available to run.

## Contributing to translations

Translation help is much appreciated

Look in `pikaraoke/translations` directory and identify the `messages.po` file for a locale/language you can help translate. Fill in all the translations in the file and submit a PR or email the maintainer of pikaraoke.

## Marking strings for translation

In Python files, you can mark a string for translation on the web frontend using `flask_babel`.

```python
# At the top of the file
import flask_babel

_ = flask_babel.gettext

# At use site, include a tagged comment which will be included for the
# translator, as well as call the `_` function to translate the string.
# MSG: Message shown after logging in as admin successfully
flash(_("Admin mode granted!"), "is-success")
```

In html Jinja templates, you can either use the `_` function, or the `{% trans %}` tag.
You still want to tag the comment with the same MSG: tag so that the translators have context
when translating.

```jinja
{# MSG: Header showing the currently playing song. #}
{% trans %}Now Playing{% endtrans %}


<script>
    // Note the Jinja comment is included in a javascript comment just to stop the syntax
    // highlighter from becoming confused.  All that's required is the jinja comment on the line
    // before the translated string.
    // {# MSG: Confirmation message when clicking a button to skip a track. #}
    `{{ _("Are you sure you want to skip this track? If you didn't add this song, ask permission first!") }}`
</script>
```

## Rebuilding translations

After modifying the templates or code and marking new strings for translation,
from the ./pikaraoke subdirectory, run

```shell
$ pybabel extract -F babel.cfg -o messages.pot --add-comments="MSG:" --strip-comment-tags  --sort-by-file .
$ pybabel update -i messages.pot -d translations
# Update any translations/**/messages.po files
$ pybabel compile -d translations/
```

This will extract the strings out of the .py and .html files, and place them into the master strings file `messages.pot`.
The update command will update each languages `translations/<lang>/LC_MESSAGES/messages.po`
file, which is what a translator for a particular language will see. The python app consumes `messages.mo` files,
which are binary files created by the compile step.

## Updating an existing translation

After updating the translation files, 'Fuzzy' messages are marked with a `#, fuzzy` line above the msgid line, and are the result of a merge where a message is deemed slightly changed from the previous version.

_These will be ignored by the translation until they are addressed!_ A message marked as fuzzy is supposed to be looked at by a human to make sure the translation doesn't need updating, after which the human translator removes that flag.

Be sure to review the updated translation and recompile as necessary.

Often line break changes will trigger these, if you want to force compilation, run:

```shell
$ pybabel compile -f -d translations
```

## Add a new language

In order to start translating a new language, use

```shell
$ pybabel init -i messages.pot -d translations -l $NEW_LOCALE
```

to create a new empty .po file.
As well as editing the `constants.py` `LANGUAGES` mapping to make that language available.

## How are translations detected

Currently I have it set based on the Accept-Language header sent with each request,
[which can be modified using this guide][accept-language-chrome].

They can also be changed in the web UI's settings menu under "Preferred language" a restart of pikaraoke is required.

## Testing a language

You can force a language locale on a given webpage by adding the lang query to the end of the URL. Example: `http://localhost:5555/?lang=pt_BR`

This will work for HTML endpoints, but for translations within python code (flashed messages, splash screen notifications), the host's locale is used.

[accept-language-chrome]: https://support.google.com/pixelslate/answer/173424?hl=en&co=GENIE.Platform%3DDesktop
