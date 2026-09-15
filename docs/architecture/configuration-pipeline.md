# The configuration pipeline

Two properties make a live reload safe, and both follow from the shape of the pipeline rather than
from care at the call sites: **parsing is pure**, and **a candidate is validated whole before
anything is touched**.

`read_documents` (I/O) → `merge_documents` → `resolve_imports` → build → validate →
`resolve_model_paths` (I/O). The middle four are `parse_settings`: a candidate can be built and
checked in full without opening a file, loading a model or touching the session. All the reading
happens first, so a reload never half-applies a config whose fourth file was unreadable.

**Nothing deep-merges across files.** A mode or setting defined in two files is an error naming
both, because the alternative is a silent change to what a command does, discoverable only by
speaking it. What the split provides instead is provenance: `Origin` records which template declared
each command and which file that template is in, so an error can point at the text to edit. `Origin`
is deliberately **not** compared when settings are diffed, so moving a command between files
rebuilds nothing.

Imports resolve depth-first with cycle detection. Objects merge recursively, lists extend, and the
importing mode wins for any scalar it defines. `icon` is seeded but never merged, because merging a
list extends it, and `type` and `imports` are never copied. A `vosk` or `transformer` block inside a
template is hoisted only into a mode of that type, which is how one template can carry both.

## Reload scope

Rebuilding is keyed on `_stream_signature`. A VOSK grammar is frozen at construction, so the test
is always whether the recognizer would have been constructed differently.

```python
VOSK:        (kind, vosk, commands, mode_vocabulary(mode, settings))
transformer: (kind, transformer)
```

A VOSK mode is rebuilt by a changed command pattern, and also by a changed response, because
`Command` conservatively compares both. It is rebuilt by **any** mode rename, reserved phrase, or
`overlay` edit that alters a phrase, because those appear in *every* grammar. It is kept across
changes to aliases, `icon`, `input_delay`, `banned_strings`, `gestures` and the pointer settings,
and across a move between files. Rebuilding reloads the model, which is the slow part.

Beyond the streams, `apply` releases every held key and clears the saved value. A reload may delete
the very command that would have released a modifier, so held state MUST NOT outlive the
configuration that created it. `Application.reload` then regroups every mode for the displays,
**unconditionally**, because any edit can move a command between groups and the revision number is
what lets a display distinguish one configuration's groups from another's. It also rebuilds a
tracker that gave up, retries a socket that would not bind, and **wakes every mechanism**.

## Run-time checks

Four checks happen at run time. A token still holding `{1}` cannot be resolved earlier, because
what it resolves to depends on the capture, and a placeholder used as a repeat count is probed as
`1`. Whether a model loads is decision 10. Whether a `script()` target exists, and the JSON *type*
of a value, are both known gaps rather than choices.

Everything else is checked at load, which is what keeps the execution path total: `executor.py`
never has to reject a key name, so nothing on a recognizer thread raises over a typographical error.
The full list is
[configuration.md](../reference/configuration.md#load-time-validation).

## Adding a key

A new key MUST be read through a literal `data.get("key")`, `raw.get("key")`, `knob("…")` or
`_require(...)`, because `tests/documentation/` parses the source for exactly those forms. Put the
key on the dataclass with the same default, document it in a table in
`reference/configuration.md`, decide whether it belongs in `_stream_signature`, which anything a
recognizer is *constructed* from does, and add the scenario.
