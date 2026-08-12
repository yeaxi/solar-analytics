# reports/

This directory is the local, on-disk output of the read-only soak checkpoint
validator (`tools/pv_soak_checkpoint.py`).

Only one subdirectory is tracked in git:

- `soak_checkpoints/` — immutable content-addressed JSON snapshots written by
  `python tools/pv_soak_checkpoint.py snapshot --input <collector.json>
  --output-dir reports/soak_checkpoints`. The generated files are ignored via
  `.gitignore` (kept only by a `.gitkeep` sentinel) so that per-run scratch
  never ends up in the source repository.

Everything else this directory used to hold — dated `candidate_*`,
`compat_fix_*`, `storage_fix_*` audit directories from prior one-off
deployments — has been removed. Solar Analytics is a reusable custom
integration; per-installation evidence trails do not belong in the source
tree.
