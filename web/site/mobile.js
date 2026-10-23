/* Pagination dots for the swipe carousels defined in mobile.css. */
(function () {
  var SELECTOR = '.how-section>.steps, .sold-cards, .quotes, .browser .results, #hry .game-hub, .loc-grid, .cmp, .pg-section .feature-grid, .pg-section .steps';
  var mq = window.matchMedia('(max-width:860px)');

  function setup(el) {
    if (el._mDots) return;
    var dots = document.createElement('div');
    dots.className = 'm-dots';
    dots.setAttribute('aria-hidden', 'true');
    el.insertAdjacentElement('afterend', dots);
    el._mDots = dots;
    var raf = 0;

    function pages() {
      var kids = Array.prototype.filter.call(el.children, function (c) { return !c.classList.contains('live-empty') && c.offsetWidth; });
      if (el.classList.contains('loc-grid')) kids = kids.filter(function (_, i) { return i % 4 === 0; });
      return kids;
    }

    function update() {
      raf = 0;
      var kids = pages();
      var scrollable = mq.matches && el.scrollWidth > el.clientWidth + 8 && kids.length > 1;
      dots.hidden = !scrollable;
      if (!scrollable) return;
      if (dots.children.length !== kids.length) {
        dots.innerHTML = kids.map(function () { return '<i></i>'; }).join('');
      }
      var left = el.scrollLeft, best = 0, bestD = Infinity;
      if (left >= el.scrollWidth - el.clientWidth - 4) best = kids.length - 1;
      else kids.forEach(function (k, i) {
        var d = Math.abs(k.offsetLeft - el.firstElementChild.offsetLeft - left);
        if (d < bestD) { bestD = d; best = i; }
      });
      Array.prototype.forEach.call(dots.children, function (d, i) { d.classList.toggle('on', i === best); });
    }

    function schedule() { if (!raf) raf = requestAnimationFrame(update); }
    el.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    new MutationObserver(schedule).observe(el, { childList: true });
    schedule();
  }

  function init() { document.querySelectorAll(SELECTOR).forEach(setup); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
