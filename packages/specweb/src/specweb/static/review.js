// Review queue: the Approve button is enabled once every "fail" item is ticked,
// or an override reason is given (design: Document page details).
(function () {
  var button = document.getElementById("approve");
  if (!button) return;
  var override = document.getElementById("override");
  var reviewed = document.getElementById("fails-reviewed");
  var fails = Array.prototype.slice.call(
    document.querySelectorAll('input.review-item[data-kind="fail"]'));
  function update() {
    var allTicked = fails.every(function (box) { return box.checked; });
    reviewed.value = fails.length && allTicked ? String(fails.length) : "";
    button.disabled = !(allTicked || override.value.trim() !== "");
  }
  fails.forEach(function (box) { box.addEventListener("change", update); });
  override.addEventListener("input", update);
  update();
})();
