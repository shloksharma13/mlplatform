# docs/alert-drill.md (P5.7)

End-to-end alert drill run 2026-10-02 via `scripts/chaos_alert.sh`
(RPS 10, latency 500ms, 4-minute traffic window).

## Observed timeline

- `[0s]` HPA `model-api` floor at `minReplicas=2`; latency 500ms enabled on
  all serving pods; traffic started.
- `[120s]` Remediator raised the HPA floor `2→4` (Alertmanager webhook fired
  on `HighP95Latency`; the script's poll query had a URL-encoding bug so it
  logged `query-failed`, but the HPA change proves the alert fired and the
  webhook executed — fixed in the script afterwards).
- Traffic summary: `n=467 errors=0 p50=0.514s p95=0.516s max=0.557s`.
- `HighP95Latency` confirmed `firing` in Prometheus (`ALERTS` metric).
- Grafana annotation created: id 1, text
  `HighP95Latency: HPA floor 2→4`, tags `[remediator, HighP95Latency]`.
- Cleanup: latency reset to 0 on all pods; HPA floor restored to 2.

## Result

Drill PASS: chaos → alert → automated HPA scale + Grafana annotation →
reset. Expected steady-state timeline is alert firing within ~3 min of
chaos+traffic, remediator action within ~1 min of firing.
