# A configuration split over several files

`config.json` holds the global settings and an `include` list, and everything else is one file per
subject. Templates in `groups/` are shared by the modes in `modes/`, which is how two modes obtain
the same navigation commands without either file repeating them.

```
config.json          globals + include
groups/models.json   the model settings both kinds of mode need
groups/*.json        one template per area of commands
modes/*.json         one runnable mode each
```

Nothing merges across files, and a mode defined twice is a load error naming both files. `imports`
is what crosses a file boundary, and a command records which template and which file it came from,
so an error about `step up` in `command mode` states that it was imported from `::navigation` in
`groups/navigation.json`.

The `path` in `groups/models.json` is relative to this directory, as it is for a single-file
configuration.
