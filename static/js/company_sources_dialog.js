(() => {
  "use strict";

  const dialogs = new Map(
    [...document.querySelectorAll("[data-source-dialog]")].map((dialog) => [
      dialog.id,
      dialog,
    ]),
  );
  const refreshTimers = new WeakMap();

  const scheduleDiscoveryRefresh = (dialog) => {
    if (dialog?.dataset.discoveryRunning !== "true" || refreshTimers.has(dialog)) {
      return;
    }
    const timer = window.setTimeout(() => {
      window.location.assign(dialog.dataset.discoveryRefreshUrl);
    }, 3000);
    refreshTimers.set(dialog, timer);
  };

  const showDialog = (dialog) => {
    if (!(dialog instanceof HTMLDialogElement) || dialog.open) {
      return;
    }
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    scheduleDiscoveryRefresh(dialog);
  };

  const selectTab = (dialog, tab) => {
    const tabs = [...dialog.querySelectorAll("[data-source-tab]")];
    tabs.forEach((candidate) => {
      const selected = candidate === tab;
      candidate.setAttribute("aria-selected", selected ? "true" : "false");
      candidate.tabIndex = selected ? 0 : -1;
      const panel = dialog.querySelector(
        `[data-source-panel="${candidate.dataset.sourceTab}"]`,
      );
      if (panel) {
        panel.hidden = !selected;
      }
    });
    dialog.querySelectorAll("[data-source-tab-action]").forEach((action) => {
      action.hidden = action.dataset.sourceTabAction !== tab.dataset.sourceTab;
    });
  };

  document.querySelectorAll("[data-dialog-target]").forEach((button) => {
    button.addEventListener("click", () => {
      showDialog(dialogs.get(button.dataset.dialogTarget));
    });
  });

  dialogs.forEach((dialog) => {
    const opener = document.querySelector(`[data-dialog-target="${dialog.id}"]`);
    dialog.querySelectorAll("[data-source-tab]").forEach((tab, index, tabs) => {
      tab.addEventListener("click", () => selectTab(dialog, tab));
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
          return;
        }
        event.preventDefault();
        let targetIndex = index;
        if (event.key === "ArrowRight") targetIndex = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") targetIndex = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") targetIndex = 0;
        if (event.key === "End") targetIndex = tabs.length - 1;
        selectTab(dialog, tabs[targetIndex]);
        tabs[targetIndex].focus();
      });
    });

    dialog.querySelectorAll("[data-source-form-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const form = dialog.querySelector(`#${button.dataset.sourceFormToggle}`);
        if (form) {
          form.hidden = !form.hidden;
          if (!form.hidden) form.querySelector("input, select, button")?.focus();
        }
      });
    });

    dialog.querySelectorAll("[data-copy-adapter-task]").forEach((button) => {
      button.addEventListener("click", async () => {
        const text = button.parentElement?.querySelector("[data-adapter-task-text]")?.value;
        if (text && navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(text);
          button.textContent = "Copied";
        }
      });
    });

    dialog.querySelectorAll("[data-connected-source-link]").forEach((link) => {
      link.addEventListener("click", (event) => {
        const target = dialog.querySelector(link.getAttribute("href"));
        const connectedTab = dialog.querySelector('[data-source-tab="connected"]');
        if (!target || !connectedTab) return;
        event.preventDefault();
        selectTab(dialog, connectedTab);
        target.classList.add("source-row-highlight");
        target.scrollIntoView({ block: "nearest" });
      });
    });

    dialog.addEventListener("close", () => {
      const refreshTimer = refreshTimers.get(dialog);
      if (refreshTimer) {
        window.clearTimeout(refreshTimer);
        refreshTimers.delete(dialog);
      }
      if (opener instanceof HTMLElement) opener.focus();
    });
    dialog.querySelectorAll("[data-source-dialog-close]").forEach((button) => {
      button.addEventListener("click", () => {
        if (dialog.open && typeof dialog.close === "function") {
          dialog.close();
        }
      });
    });

    dialog.addEventListener("click", (event) => {
      if (event.target === dialog && typeof dialog.close === "function") {
        dialog.close();
      }
    });

    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && dialog.open && typeof dialog.close === "function") {
        event.preventDefault();
        dialog.close();
      }
    });
  });

  showDialog(document.querySelector("[data-source-dialog][data-auto-open]"));
})();
