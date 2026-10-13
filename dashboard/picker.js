// Picking a wallet the EIP-6963 way: one button per wallet that announced itself, with the icon and
// name it announced. With a single wallet there is nothing to pick.

/** Resolves with the wallet the person picks in `container`, or the only one there is. */
export function chooseWallet(container, wallets) {
  if (wallets.length === 1) return Promise.resolve(wallets[0]);
  return new Promise((resolve) => {
    container.replaceChildren(...wallets.map((detail) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-ghost wallet-choice";
      if (detail.info.icon) {
        const icon = document.createElement("img");  // an <img>, as EIP-6963 asks, so an SVG icon cannot run scripts
        icon.src = detail.info.icon;
        icon.alt = "";
        icon.width = icon.height = 20;
        button.append(icon);
      }
      button.append(detail.info.name);
      button.addEventListener("click", () => {
        container.hidden = true;
        resolve(detail);
      });
      return button;
    }));
    container.hidden = false;
  });
}
