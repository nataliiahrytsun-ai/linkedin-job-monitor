document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".jobs-table-filter-form");
  if (!form) {
    return;
  }

  const filters = [...form.querySelectorAll(".column-filter")];
  const safeMargin = 12;
  const triggerGap = 6;
  let openFilter = null;
  let scrollFrame = null;

  const closeOpenFilter = (restoreFocus = false) => {
    if (!openFilter) {
      return;
    }

    const filter = openFilter;
    openFilter = null;
    filter.open = false;

    if (restoreFocus) {
      filter.querySelector("summary")?.focus();
    }
  };

  const positionPopover = (filter) => {
    if (!filter.open) {
      return;
    }

    const trigger = filter.querySelector("summary");
    const popover = filter.querySelector(".column-filter-popover");
    if (!trigger || !popover) {
      return;
    }

    popover.style.visibility = "hidden";
    popover.style.maxHeight = "";
    popover.style.top = `${safeMargin}px`;
    popover.style.left = `${safeMargin}px`;

    const triggerRect = trigger.getBoundingClientRect();
    const popoverRect = popover.getBoundingClientRect();
    const headerRect =
      filter.closest("thead")?.getBoundingClientRect() ?? triggerRect;
    const maximumLeft = Math.max(
      safeMargin,
      window.innerWidth - safeMargin - popoverRect.width,
    );
    const left = Math.min(Math.max(triggerRect.left, safeMargin), maximumLeft);
    let top;

    if (window.matchMedia("(max-width: 40rem)").matches) {
      top = triggerRect.bottom + triggerGap;
      const availableHeight = Math.max(
        0,
        window.innerHeight - safeMargin - top,
      );
      popover.style.maxHeight = `${availableHeight}px`;
    } else {
      const spaceBelow = window.innerHeight - headerRect.bottom - triggerGap;
      const spaceAbove = headerRect.top - triggerGap;
      const preferredTop =
        popoverRect.height > spaceBelow && spaceAbove > spaceBelow
          ? headerRect.top - triggerGap - popoverRect.height
          : headerRect.bottom + triggerGap;
      const maximumTop = Math.max(
        safeMargin,
        window.innerHeight - safeMargin - popoverRect.height,
      );
      top = Math.min(Math.max(preferredTop, safeMargin), maximumTop);
    }

    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    popover.style.visibility = "visible";
  };

  filters.forEach((filter) => {
    filter.addEventListener("toggle", () => {
      if (!filter.open) {
        if (openFilter === filter) {
          openFilter = null;
        }
        return;
      }

      filters.forEach((otherFilter) => {
        if (otherFilter !== filter) {
          otherFilter.open = false;
        }
      });
      openFilter = filter;
      positionPopover(filter);
    });
  });

  document.addEventListener("pointerdown", (event) => {
    if (openFilter && !openFilter.contains(event.target)) {
      closeOpenFilter();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && openFilter) {
      event.preventDefault();
      closeOpenFilter(true);
    }
  });

  const closeForViewportChange = () => closeOpenFilter();
  window.addEventListener("resize", closeForViewportChange);
  window.addEventListener("orientationchange", closeForViewportChange);
  window.addEventListener(
    "scroll",
    () => {
      if (!openFilter || scrollFrame !== null) {
        return;
      }

      scrollFrame = window.requestAnimationFrame(() => {
        scrollFrame = null;
        if (openFilter) {
          positionPopover(openFilter);
        }
      });
    },
    {passive: true},
  );

  form.addEventListener("submit", () => closeOpenFilter());

  form.querySelectorAll("select, input[type='date']").forEach((control) => {
    control.addEventListener("change", () => {
      closeOpenFilter();
      form.requestSubmit();
    });
  });

  form.querySelectorAll("input[type='text']").forEach((control) => {
    control.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        closeOpenFilter();
        form.requestSubmit();
      }
    });
  });
});
