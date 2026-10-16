# Help translate Pikaraoke to more languages!

I'd love to have pikaraoke available in more languages. If you are able to translate it would be much appreciated! I have linked the translation files below. All you need to do is download the messages.po file for the locale you're translating. Then fill in all the translated text: Where `msgid` is in English, fill in the `msgstr` in the language requested.

When you've completed the file, open a pull request with the updated `messages.po` - you'll get credit as a contributor. The maintainer recompiles the catalogs when merging, so your translations ship in the next release.

## A couple of notes

If you see a `%s` character, that will be filled in with a variable. Leave it as is. Example:

```
#. Message shown after the download is completed and queued
#: karaoke.py:376
#, python-format
msgid "Downloaded and queued: %s"
msgstr "成功添加歌曲： %s"
```

If you see newlines and embedded html, try to keep them in the same format. Apologies for this, the html files are a bit messy. Example:

```
#. Link which will let the user log into admin mode.
#: templates/info.html:348
#, python-format
msgid ""
"\n"
"    Enable admin mode: <a href=\"%(login_url)s\">Log in</a>\n"
"  "
msgstr ""
"\n"
"    开启管理员模式: <a href=\"%(login_url)s\">登入</a>\n"
"  "
```

Do not translate embedded js function names or html attributes (or basically anything inside an html tag), just translate the surrounding text. Example (handleConfirmation):

```
#. Prompt for interaction in order to enable video autoplay.
#: templates/splash.html:629
msgid ""
"Due to limititations with browser permissions, you must interact\n"
"      with the page once before it allows autoplay of videos. Pikaraoke "
"will not\n"
"      play otherwise. Click the button below to\n"
"      <a onClick=\"handleConfirmation()\">confirm</a> ."
msgstr ""
"Devido a limitações nas permissões do navegador, você deve interagir com "
"a página uma vez antes que ela permita a reprodução automática de vídeos."
" Pikaraokê não jogue de outra forma. Clique no botão abaixo para       "
" <a onClick=\"handleConfirmation()\">confirmar</a> ."
```

### Updating translations

When pikaraoke releases new versions, very often there are new items to be translated or older translation strings that need to be reviewed.

To find new strings to be translated, click one of the links below and find the strings that have not yet been updated by looking for: `msgstr ""` (note that sometimes this search returns false positives, but should be a good start)

Also search for the string `#, fuzzy`. This will show a translation item that tried to auto-resolve, but the system is unsure if it's correct. Double-check the translation and correct it if necessary. Then remove the line `#, fuzzy` to accept the translation. If the line says: `#, fuzzy, python`, replace it with `#, python`

### Current translations

Each locale's `messages.po` is linked below. To see what still needs work in any of them, open the file and use the markers above - `msgstr ""` for untranslated strings and `#, fuzzy` for auto-resolved ones that need checking.

- [Chinese (Simplified)](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/zh_Hans_CN/LC_MESSAGES/messages.po)
- [Chinese (Traditional)](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/zh_Hant_TW/LC_MESSAGES/messages.po)
- [Dutch](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/nl_NL/LC_MESSAGES/messages.po)
- [Finnish](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/fi_FI/LC_MESSAGES/messages.po)
- [French](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/fr_FR/LC_MESSAGES/messages.po)
- [German](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/de_DE/LC_MESSAGES/messages.po)
- [Indonesian](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/id_ID/LC_MESSAGES/messages.po)
- [Italian](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/it_IT/LC_MESSAGES/messages.po)
- [Japanese](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/ja_JP/LC_MESSAGES/messages.po)
- [Korean](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/ko_KR/LC_MESSAGES/messages.po)
- [Norwegian (Bokmål)](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/nb_NO/LC_MESSAGES/messages.po)
- [Portuguese (Brazil)](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/pt_BR/LC_MESSAGES/messages.po)
- [Russian](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/ru_RU/LC_MESSAGES/messages.po)
- [Spanish](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/es_VE/LC_MESSAGES/messages.po)
- [Thai](https://github.com/vicwomg/pikaraoke/blob/master/pikaraoke/translations/th_TH/LC_MESSAGES/messages.po)
