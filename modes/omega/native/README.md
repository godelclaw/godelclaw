# Omega on CeTTa

This mode runs the pinned Omega loop on CeTTa with the PeTTa extended profile.
It loads the original NAL and PLN libraries. The runtime uses the checked-in
native adapters and owns state in a separate directory.

`loop.metta` retains Omega's control flow, budgets, message history, wake
timing and checkpoint order. `manifest.json` records the upstream source
hashes and host adaptations. The provider wire names and call IDs stay intact
when file calls select the native Omega implementation. Keep host helpers
scoped so imports cannot create a second effecting rule for an operation.

Supervised semantic memory uses the native memory service; standalone launches
keep a mode-private text store. The vendored upstream source remains available
for comparison. See the [mode guide](../../README.md) for launching, provider
settings and the tests.
