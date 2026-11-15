const timer = document.querySelector("[data-countdown]");
const endForm = document.querySelector("[data-end]");
const chatForm = document.querySelector("[data-chat]");

if (timer && endForm && chatForm) {
  const deadline = performance.now() + Number(timer.dataset.countdown) * 1000;
  let sending = false;
  chatForm.addEventListener("submit", () => {
    sending = true;
    const button = chatForm.querySelector("button");
    button.disabled = true;
    button.textContent = "Waiting for reply…";
  });

  function updateTimer() {
    const seconds = Math.max(0, Math.ceil((deadline - performance.now()) / 1000));
    timer.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    if (seconds === 0) {
      if (!sending) endForm.requestSubmit();
      return;
    }
    window.setTimeout(updateTimer, 200);
  }
  updateTimer();
}

const ratingForm = document.querySelector("[data-rating]");
if (ratingForm) {
  ratingForm.addEventListener("submit", () => {
    ratingForm.querySelector("[data-saving]").hidden = false;
  });
}
