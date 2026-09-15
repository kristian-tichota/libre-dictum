# The response DSL

A response is a sequence of `+`-separated tokens, evaluated left to right. Everything is checked at
load, so an unknown key or verb names the command rather than failing silently.

```
enter                enter↓ enter↑
ctrl + c             ctrl↓ c↓ c↑ ctrl↑
3(down)              down three times; groups nest and resolve inside out
```

A modifier (`shift`, `ctrl`, `alt`, `meta` or `win`) stays down until a non-modifier completes the
chord. That is what makes `ctrl + c` a chord rather than two taps, and what makes a bare
`"hold control": "ctrl"` stay down until the next keystroke, possibly a whole utterance later.
Anything left down is shown on the tray and the chip. The key names are the alphabet, the digits,
`` . , ; ' - = / \ [ ] ` ``, the modifiers, `f1` to `f24`, the navigation keys, and `left_mouse` and
`right_mouse`. They are case-insensitive.

## Verbs

| Verb | Does |
|---|---|
| `hold(key)` | Presses the key and keeps it down. The hold outlives the command, and deliberately outlives a mode switch, because held state is a property of the keyboard. |
| `release(key)` | Releases the key. Releasing a key that is not held has no effect. |
| `toggle(key)` | |
| `mode(name)` | Moves every layer, whereas `mode(pedal:name)` moves one. See [Input layers](configuration.md#input-layers). |
| `save(value)` | Remembers a value for the **next** command. |
| `exec(program arg)` | Arguments split the way a shell would, so quotes work. |
| `python(code)` | Evaluates the code in a namespace of its own. |
| `script(name;;arg;;arg)` | Imports `scripts.name` from the configuration directory and calls its `script(*args)`. The directory is created and placed on `sys.path` at startup, so it needs no `__init__.py`. A script that raises is logged with a traceback, and the recognizer continues to run. [`examples/scripts/copy.py`](../../examples/scripts/copy.py) answers `script(copy;;some text)`. |
| `type(text)` | One key code per character. |
| `hud(target)` | Accepts `chip`, `sheet`, `open`, `close`, `modes`, `pedals`, `gestures`, `meters` and `group:<name>`. Checked at load, where a misspelling lists the alternatives, although `group:` produces only a warning, because deleting one command merges a two-command family into `misc`. With no display running the verb warns and the rest of the response still runs. |
| `sleep(layers)` / `wake(layers)` | An empty argument means all three mechanisms. Both require confirmation and are deterministic, so a repeated request has no effect. See [Sleep](configuration.md#sleep). |
| `recenter()` | Makes the current pose neutral. It takes no argument and is inert under the absolute pointer law. |

**`type()` deliberately does not use the clipboard.** No paste chord is portable: `ctrl + v` is
`quoted-insert` in a terminal and `scroll-up-command` in some editors, so a pasting response works
only in the application its author had open. `type()` works everywhere, including in a TTY and over
SSH, and leaves both clipboards untouched. There are three costs. **The key table is a US layout**,
because uinput emits *codes*, the compositor's keymap decides the glyph, and nothing in this process
can detect the mapping. **Typographic punctuation is folded to ASCII first**: smart quotes, the em
dash and the ellipsis are substituted, and accents are dropped by decomposition, so "café" becomes
"cafe". A character that still has no key causes a literal `type()` to be **rejected at load**, with
the character named, while dictated text is typed without it under a warning. Finally, a shift held
by `hold(shift)` is respected and restored.

## Placeholders

| Placeholder | Matches | Where |
|---|---|---|
| `{numeric}` | digits | both, restricted to single digits in VOSK modes |
| `{any}` | one word | both |
| `{rest}` | the remainder of the utterance | dictation only |

Placeholders are numbered from 1, and `{0}` is rejected. Number words from zero to ten become digits
before matching, so "two times press a" matches `{numeric} times press {any}`. A placeholder beyond
the available captures is not dropped; it renumbers onto the saved value:

```json
"number {numeric}": "save({1})",
"page down": "{1=1}(pagedown)",
"press {any}": "{2=1}({1})"
```

"number five" followed by "page down" scrolls five pages, whereas "page down" alone scrolls one. A
second `save()` **replaces** the first, because in a spoken interface a misheard count is corrected
by saying it again. Any keystroke consumes the value. The other verbs deliberately do not, so a mode
switch between the two utterances preserves the prefix. An unmatched utterance and a banned string
leave the value alone, while panic and reload discard it.

## Aliases, escaping, failure

Aliases are regular-expression replacements applied **before** anything else, so one may expand into
any syntax above, and a malformed alias is a load error. A literal plus is written `\+`, which is
`"\\+"` in JSON.

```json
"aliases": { "^say\\((.*)\\)$": "type(\\1) + enter" }
```

A response runs whole or not at all. If a token fails to parse, nothing is typed and a warning names
both the token and the command, so a partially typed chord can never reach the screen. While a
mechanism is [asleep](configuration.md#sleep), only `sleep`, `wake` and `hud` run for it, and a
chain mixing those with anything else is dropped whole.
