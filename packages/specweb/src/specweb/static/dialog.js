// Buttons with data-open-dialog="<id>" open that <dialog> as a modal
// (the job page's "Delete job" confirmation). Cancel closes it by itself.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-open-dialog]");
  if (button) document.getElementById(button.dataset.openDialog)?.showModal();
});
