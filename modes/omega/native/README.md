# Omega on CeTTa

These are the explicit adapters for the pinned Omega source in `../upstream`.
The launcher copies these files; it does not rewrite the loop or generate
replacement equations from upstream text at startup.

`loop.metta` preserves Omega's control flow, budgets, message history, tool
dispatch, wake timing, and checkpoint order. Its two adaptations select the
separate Omega host helper and preserve native text received from a channel.
`manifest.json` records the upstream source hashes and those substitutions.
The launcher tests require the loop to equal that precise adaptation.

The utility, memory, and skill files expose the existing host boundaries and
current PeTTa annotations. Supervised semantic memory uses the CeTTa service;
standalone modes retain their private text store. Provider, channel, and
operating-system adapters still have host components. The vendored upstream
source remains available for comparison.
