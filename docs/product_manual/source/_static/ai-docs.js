(() => {
  "use strict";

  const toolbar = document.querySelector("[data-ai-docs-toolbar]");
  if (!toolbar) return;

  const trigger = toolbar.querySelector(".ai-docs-trigger");
  const primaryCopy = toolbar.querySelector(".ai-docs-copy-primary");
  const triggerLabel = toolbar.querySelector("[data-ai-docs-trigger-label]");
  const menu = toolbar.querySelector(".ai-docs-popover");
  const copyButtons = Array.from(toolbar.querySelectorAll("[data-ai-docs-copy]"));
  const copyHelp = toolbar.querySelector("[data-ai-docs-copy-help]");
  const status = toolbar.querySelector(".ai-docs-status");
  const markdownUrl = toolbar.dataset.markdownUrl;
  let markdownText = null;
  let resetTimer;

  const menuItems = () =>
    Array.from(menu.querySelectorAll(".ai-docs-menu-item")).filter(
      (item) => !item.disabled,
    );

  const setOpen = (open, focusFirst = false) => {
    trigger.setAttribute("aria-expanded", String(open));
    menu.hidden = !open;
    if (open && focusFirst) menuItems()[0]?.focus();
  };

  const announce = (message) => {
    window.clearTimeout(resetTimer);
    status.textContent = message;
    triggerLabel.textContent = message;
    resetTimer = window.setTimeout(() => {
      status.textContent = "";
      triggerLabel.textContent = "复制页面";
    }, 1800);
  };

  const fallbackCopy = (text) => {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.inset = "0 auto auto -9999px";
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("copy command was rejected");
  };

  fetch(markdownUrl, {
    headers: { Accept: "text/markdown, text/plain;q=0.9" },
    cache: "no-cache",
  }).then((response) => {
    if (!response.ok) throw new Error(`Markdown request failed: ${response.status}`);
    return response.text();
  }).then((markdown) => {
    markdownText = markdown;
    copyButtons.forEach((button) => {
      button.disabled = false;
      button.setAttribute("aria-disabled", "false");
    });
    copyHelp.textContent = "复制本页 Markdown，供 AI 使用";
  }).catch((error) => {
    console.error("Unable to prepare page Markdown", error);
    copyHelp.textContent = "本页 Markdown 暂时不可用";
  });

  trigger.addEventListener("click", () => {
    setOpen(trigger.getAttribute("aria-expanded") !== "true");
  });

  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true, true);
    }
  });

  const copyMarkdown = async () => {
    try {
      if (markdownText === null) throw new Error("Markdown is not ready");
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(markdownText);
        } catch (error) {
          console.warn("Clipboard API rejected the write; using fallback", error);
          fallbackCopy(markdownText);
        }
      } else {
        fallbackCopy(markdownText);
      }
      setOpen(false);
      primaryCopy.focus();
      announce("已复制");
    } catch (error) {
      console.error("Unable to copy page Markdown", error);
      setOpen(false);
      primaryCopy.focus();
      announce("复制失败");
    }
  };

  copyButtons.forEach((button) => button.addEventListener("click", copyMarkdown));

  menu.addEventListener("keydown", (event) => {
    const items = menuItems();
    if (items.length === 0) return;
    const current = items.indexOf(document.activeElement);
    let next = current;
    if (event.key === "ArrowDown") next = (current + 1) % items.length;
    if (event.key === "ArrowUp") next = (current - 1 + items.length) % items.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    if (next !== current) {
      event.preventDefault();
      items[next]?.focus();
    }
  });

  document.addEventListener("click", (event) => {
    if (!toolbar.contains(event.target)) setOpen(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      setOpen(false);
      trigger.focus();
    }
  });
})();
