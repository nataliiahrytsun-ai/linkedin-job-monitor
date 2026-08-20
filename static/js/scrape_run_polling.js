(() => {
  "use strict";

  const pollingRoot = document.getElementById("scrape-run-polling");
  const idsElement = document.getElementById("running-run-ids");
  const latestElement = document.getElementById("latest-run-state");
  if (!pollingRoot || !idsElement || !latestElement) {
    return;
  }

  const runIds = JSON.parse(idsElement.textContent);
  const initialLatestRun = JSON.parse(latestElement.textContent);
  if (!Array.isArray(runIds) || runIds.length === 0) {
    return;
  }

  const intervalMs = Number.parseInt(pollingRoot.dataset.intervalMs || "5000", 10);
  const statusUrl = new URL(pollingRoot.dataset.statusUrl, window.location.origin);
  statusUrl.searchParams.set("ids", runIds.join(","));
  const initialRunningIds = new Set(runIds);

  const latestSignature = (run) => {
    if (!run) {
      return "";
    }
    return `${run.id}:${run.status}:${run.finished_at || ""}`;
  };

  const runningIdsChanged = (runs) => {
    const currentRunningIds = new Set(
      runs.filter((run) => !run.is_terminal).map((run) => run.id),
    );
    return (
      currentRunningIds.size !== initialRunningIds.size ||
      [...currentRunningIds].some((runId) => !initialRunningIds.has(runId))
    );
  };

  const poll = async () => {
    if (document.hidden) {
      window.setTimeout(poll, intervalMs);
      return;
    }
    try {
      const response = await fetch(statusUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error(`Status request failed with ${response.status}`);
      }
      const payload = await response.json();
      const returnedIds = new Set(payload.runs.map((run) => run.id));
      const stateChanged =
        latestSignature(payload.latest_run) !== latestSignature(initialLatestRun) ||
        runningIdsChanged(payload.runs) ||
        payload.runs.some((run) => run.is_terminal) ||
        runIds.some((runId) => !returnedIds.has(runId));
      if (stateChanged) {
        window.location.reload();
        return;
      }
    } catch (_error) {
      // A transient status failure leaves the rendered DB state intact.
    }
    window.setTimeout(poll, intervalMs);
  };

  window.setTimeout(poll, intervalMs);
})();
