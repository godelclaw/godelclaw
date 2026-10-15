# Component APIs

An imported component owns the metadata for its commands and settings. The
attention component is the first migrated component; other commands retain their
existing implementation and help.

```metta
!(import! &self ./src/api_registry.metta)
(CommandSpec 1 "example" "example-command" 1 2
  "(example-command text [count])" "Describe what this command does." "")
```

The fields are version, component, command name, minimum and maximum argument
counts, usage, description and optional settings namespace. A nonempty settings
namespace includes that component's live settings in its command help.

`getSkills`, `help`, `check` and the native dispatcher's argument checks discover
these facts from the loaded space. No central command list needs editing. The
command's implementation remains a MeTTa equation; metadata does not execute it.
Invalid argument counts produce a result for that call, and subsequent calls run.
`(help "api")` describes registration to the agent.

Settings use the existing `SettingSpec` / `SettingsProvider` protocol, available
through `(help "settings")`. Defaults, units, numeric domains and persistence
stay with their component. `api:module-fields` projects command and setting
metadata from these same declarations; it does not duplicate them.

## Module layer pilot

`ComponentAPI.module` defines the envelope of the projected metadata.
`api_module_cetta.metta` is an optional consumer for engines with `lib_module`:

```metta
!(import! &self ./src/api_module_cetta.metta)
!(let $schema (api-module:schema "src/api/ComponentAPI.module")
   (api-module:attach $schema "attention"))
```

This attaches metadata to an empty executable core. Joining these layers installs
no equations. This consumer explicitly interprets lexical String and Integer
carriers as native string and integer values. Defaults and domains are serialized
as text in the envelope; their numeric meaning is checked by the settings
implementation, not proved by module attachment.

The ordinary catalogue works on the existing CeTTa engine. The optional consumer
requires the newer module library and is qualified separately with
`CETTA_MODULE_BIN`. This pilot does not replace the command parser, certify state
migrations or establish a correspondence between native effects and Lean models.
