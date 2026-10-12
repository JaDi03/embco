// Expand a diagram to the full screen. Every <figure class="diagram"> gets an Expand button. The
// overlay shows the version that matches the current color scheme, fitted to the screen or at its
// actual size (click the image or the size button to switch). Escape closes it.
(function () {
  let overlay, stage, image, sizeButton, closeButton, opener;

  function build() {
    overlay = document.createElement("div");
    overlay.className = "diagram-overlay";
    overlay.hidden = true;
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML =
      '<div class="diagram-overlay__bar">' +
      '<span class="diagram-overlay__title"></span>' +
      '<button type="button" class="diagram-button" data-action="size">Actual size</button>' +
      '<button type="button" class="diagram-button" data-action="close">Close</button>' +
      "</div>" +
      '<div class="diagram-overlay__stage"><img alt="" /></div>';
    document.body.appendChild(overlay);
    stage = overlay.querySelector(".diagram-overlay__stage");
    image = stage.querySelector("img");
    sizeButton = overlay.querySelector('[data-action="size"]');
    closeButton = overlay.querySelector('[data-action="close"]');

    sizeButton.addEventListener("click", toggleSize);
    closeButton.addEventListener("click", close);
    image.addEventListener("click", toggleSize);
    stage.addEventListener("click", (event) => {
      if (event.target === stage) close();
    });
    overlay.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
      if (event.key === "Tab") {
        // Keep the focus inside the dialog: it has only two buttons.
        event.preventDefault();
        (document.activeElement === sizeButton ? closeButton : sizeButton).focus();
      }
    });
  }

  function visibleImage(figure) {
    const images = Array.from(figure.querySelectorAll("img"));
    return images.find((img) => img.offsetParent !== null) || images[0];
  }

  function open(figure, button) {
    if (!overlay) build();
    const source = visibleImage(figure);
    opener = button;
    image.src = source.currentSrc || source.src;
    image.alt = source.alt;
    overlay.querySelector(".diagram-overlay__title").textContent = source.title || source.alt;
    overlay.setAttribute("aria-label", source.title || source.alt);
    setActual(false);
    overlay.hidden = false;
    document.documentElement.style.overflow = "hidden";
    closeButton.focus();
  }

  function close() {
    overlay.hidden = true;
    document.documentElement.style.overflow = "";
    if (opener) opener.focus();
  }

  function setActual(actual) {
    overlay.classList.toggle("diagram-overlay--actual", actual);
    sizeButton.textContent = actual ? "Fit to screen" : "Actual size";
  }

  function toggleSize() {
    setActual(!overlay.classList.contains("diagram-overlay--actual"));
  }

  function attach() {
    document.querySelectorAll("figure.diagram").forEach((figure) => {
      if (figure.querySelector(".diagram-expand")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "diagram-button diagram-expand";
      button.textContent = "Expand";
      const source = visibleImage(figure);
      button.setAttribute("aria-label", "Expand diagram: " + (source.title || source.alt));
      button.addEventListener("click", () => open(figure, button));
      figure.appendChild(button);
    });
  }

  if (window.document$) {
    window.document$.subscribe(attach);
  } else {
    document.addEventListener("DOMContentLoaded", attach);
  }
})();
