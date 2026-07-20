# Golden replay hold point

PR CI intentionally remains on hold until `latest/results.jsonl` and
`latest/manifest.json` are produced by `scripts/run_golden_matrix.py` from a
clean candidate checkout. Synthetic fixtures and partial `--case-id` runs are
not accepted as release evidence.

The two files are bound to the exact matrix, taxonomy, Git commit, model and
embedding configuration, data artifacts, and result bytes. After generation,
run both commands before promoting the candidate:

```bash
python scripts/verify_golden_replay.py --replay-dir tests/replay/latest
python scripts/evaluate_golden_matrix.py \
  --results tests/replay/latest/results.jsonl \
  --manifest tests/replay/latest/manifest.json \
  --output-dir tests/replay/latest/report
```
