document.querySelectorAll(".faq-item").forEach((item, index) => {
  if (index === 0) item.classList.add("open");
  const trigger = item.querySelector(".faq-q");
  trigger.addEventListener("click", () => {
    const open = item.classList.contains("open");
    document.querySelectorAll(".faq-item").forEach((other) => other.classList.remove("open"));
    if (!open) item.classList.add("open");
  });
});
