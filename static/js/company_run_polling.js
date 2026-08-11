(() => {
  "use strict";

  const pollingRoot = document.getElementById("company-run-polling");
  if (!pollingRoot) {
    return;
  }

  const companyId = Number.parseInt(pollingRoot.dataset.companyId, 10);
  const baselineRunId = Number.parseInt(pollingRoot.dataset.baselineRunId, 10);
  const mode = pollingRoot.dataset.mode;
  const intervalMs = Number.parseInt(pollingRoot.dataset.intervalMs || "5000", 10);
  const maxNewRunChecks = 24;
  let remainingNewRunChecks = maxNewRunChecks;
  const statusUrl = new URL(pollingRoot.dataset.statusUrl, window.location.origin);
  statusUrl.searchParams.set("company_id", companyId.toString());
  if (baselineRunId > 0) {
    statusUrl.searchParams.set("ids", baselineRunId.toString());
  }

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
      const latestRun = (await response.json()).company_latest_run;
      const foundNewRun = latestRun && latestRun.id !== baselineRunId;
      const watchedRunFinished =
        mode === "running" && latestRun && latestRun.id === baselineRunId && latestRun.is_terminal;
      if (foundNewRun || watchedRunFinished) {
        window.location.reload();
        return;
      }
    } catch (_error) {
      // Keep the rendered company state when a status check is transiently unavailable.
    }
    if (mode === "new") {
      remainingNewRunChecks -= 1;
      if (remainingNewRunChecks <= 0) {
        return;
      }
    }
    window.setTimeout(poll, intervalMs);
  };

  window.setTimeout(poll, intervalMs);
})();
