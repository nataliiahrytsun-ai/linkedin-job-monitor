(() => {
  "use strict";

  const dialogs = new Map(
    [...document.querySelectorAll("[data-source-dialog]")].map((dialog) => [
      dialog.id,
      dialog,
    ]),
  );

  const showDialog = (dialog) => {
    if (!(dialog instanceof HTMLDialogElement) || dialog.open) {
      return;
    }
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  };

  document.querySelectorAll("[data-dialog-target]").forEach((button) => {
    button.addEventListener("click", () => {
      showDialog(dialogs.get(button.dataset.dialogTarget));
    });
  });

  dialogs.forEach((dialog) => {
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
