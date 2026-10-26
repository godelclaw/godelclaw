# Upstream prompt baselines

Verbatim copies of the model-facing prompts of two open-source coding agents. They are baselines for GödelClaw's own coding prompts (`identity/coding-instructions.md`, `identity/coding-compact.md`, `identity/coding-tools.json`), which are revised and compared against them. Nothing here is loaded at run time.

- `codex/`: OpenAI Codex at `rust-v0.164.0`, Apache-2.0. Its `LICENSE` and `NOTICE` apply to every file in that folder.
- `opencode/`: OpenCode at `v1.18.18`, MIT. Its `LICENSE` applies to every file in that folder.

Each folder's `SOURCE` names the upstream commit and lists the SHA-256 of every copied file. Files keep their upstream paths and are never edited here; a revised prompt is a new file outside this folder.
