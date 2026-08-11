(() => {
  "use strict";

  const pollingRoot = document.getElementById("company-run-polling");
  if (!pollingRoot) {
    return;
  }

  const parseIds = (value) =>
    (value || "")
      .split(",")
      .map((token) => Number.parseInt(token, 10))
      .filter((value) => Number.isInteger(value) && value > 0);

  const companyId = Number.parseInt(pollingRoot.dataset.companyId, 10);
  const baselineRunId = Number.parseInt(pollingRoot.dataset.baselineRunId || "0", 10);
  const expectedSourceIds = parseIds(pollingRoot.dataset.expectedSourceIds);
  const expectedRunIds = parseIds(pollingRoot.dataset.expectedRunIds);
  const mode = pollingRoot.dataset.mode;
  const intervalMs = Number.parseInt(pollingRoot.dataset.intervalMs || "5000", 10);
  const maxSubmissionChecks = 24;
  let remainingSubmissionChecks = maxSubmissionChecks;
  const statusUrl = new URL(pollingRoot.dataset.statusUrl, window.location.origin);
  statusUrl.searchParams.set("company_id", companyId.toString());
  if (expectedRunIds.length) {
    statusUrl.searchParams.set("ids", expectedRunIds.join(","));
  }
  if (expectedSourceIds.length) {
    statusUrl.searchParams.set("company_source_ids", expectedSourceIds.join(","));
    statusUrl.searchParams.set("after_id", baselineRunId.toString());
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
      const payload = await response.json();
      const runsById = new Map(payload.runs.map((run) => [run.id, run]));
      const runningWatchComplete =
        mode === "running" &&
        expectedRunIds.length > 0 &&
        expectedRunIds.every((runId) => runsById.get(runId)?.is_terminal === true);
      const submissionComplete =
        mode === "submission" && payload.submission_complete === true;
      if (runningWatchComplete || submissionComplete) {
        window.location.reload();
        return;
      }
    } catch (_error) {
      // Keep the rendered Company state when a status check is transiently unavailable.
    }
    if (mode === "submission") {
      remainingSubmissionChecks -= 1;
      if (remainingSubmissionChecks <= 0) {
        return;
      }
    }
    window.setTimeout(poll, intervalMs);
  };

  window.setTimeout(poll, intervalMs);
})();
