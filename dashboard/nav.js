// The console's pages: one sidebar link per page, the page in the address (#bills, #treasury),
// so a reload or the back button lands where the owner was.

export const PAGES = ["bills", "payments", "suppliers", "treasury", "activity", "settings"];
const FIRST = PAGES[0];

/** The page named in a hash like "#treasury", or the first page. */
export function pageOf(hash) {
  const name = String(hash ?? "").replace(/^#/, "");
  return PAGES.includes(name) ? name : FIRST;
}

let listeners = [];

/** Show one page and mark its link; tells whoever listens which page is now open. */
export function show(page) {
  const name = PAGES.includes(page) ? page : FIRST;
  for (const section of document.querySelectorAll(".page[data-page]")) section.hidden = section.dataset.page !== name;
  for (const link of document.querySelectorAll(".side-link[data-page]")) {
    if (link.dataset.page === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  for (const listener of listeners) listener(name);
}

/** Open a page and keep it in the address. */
export function go(page) {
  if (window.location.hash !== `#${page}`) window.location.hash = page;
  else show(page);
}

export function onPage(listener) {
  listeners = [...listeners, listener];
}

/** Tools that open over the page (Earn, cross-chain funds, connections): any element with
 * data-modal="<dialog id>" opens it; data-close or a click outside closes it. */
function wireModals() {
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-modal]");
    if (opener) {
      document.getElementById(opener.dataset.modal)?.showModal();
      return;
    }
    if (event.target.closest("[data-close]")) event.target.closest("dialog")?.close();
    else if (event.target instanceof HTMLDialogElement) {  // the backdrop, not the dialog's own padding
      const box = event.target.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside) event.target.close();
    }
  });
}

export function start() {
  window.addEventListener("hashchange", () => show(pageOf(window.location.hash)));
  wireModals();
  show(pageOf(window.location.hash));
}
