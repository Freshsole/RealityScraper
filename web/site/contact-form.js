/* Czech wording for the embedded InTouch form (labels and messages come from the
 * form definition in English; the component exposes an open shadow root). */
(function () {
  'use strict';
  var host = document.querySelector('intouch-form');
  if (!host) return;

  var LABELS = { name: 'Jméno a příjmení', email: 'E-mailová adresa', phone: 'Telefon (nepovinné)', message: 'Zpráva' };
  var PLACEHOLDERS = { name: 'Zadejte své jméno a příjmení', email: 'jmeno@domena.cz', phone: '+420 600 000 000', message: 'Jak vám můžeme pomoci?' };
  var MESSAGES = {
    'Please fill in the required fields.': 'Vyplňte prosím povinná pole.',
    'This form is not available.': 'Formulář je teď nedostupný. Napište nám prosím na podpora@realitify.cz.',
    'Something went wrong': 'Něco se nepovedlo, zkuste to prosím znovu.',
    'Thank you! We will be in touch.': 'Děkujeme, ozveme se vám co nejdříve.'
  };

  function translate(root) {
    root.querySelectorAll('[part~="field"]').forEach(function (box) {
      var input = box.querySelector('input, textarea');
      if (!input) return;
      var key = input.getAttribute('name');
      var label = box.querySelector('label');
      if (label && LABELS[key] && !label.getAttribute('data-cs')) {
        var mark = label.querySelector('[part~="required-mark"]');
        label.textContent = LABELS[key];
        if (mark) label.appendChild(mark);
        label.setAttribute('data-cs', '1');
      }
      if (PLACEHOLDERS[key]) input.setAttribute('placeholder', PLACEHOLDERS[key]);
    });
    root.querySelectorAll('[part~="message"]').forEach(function (msg) {
      var cs = MESSAGES[msg.textContent.trim()];
      if (cs && msg.textContent !== cs) msg.textContent = cs;
    });
  }

  function attach() {
    var root = host.shadowRoot;
    if (!root) return false;
    translate(root);
    new MutationObserver(function () { translate(root); }).observe(root, { childList: true, subtree: true, characterData: true });
    return true;
  }

  if (!attach()) {
    var tries = 0;
    var timer = setInterval(function () {
      if (attach() || ++tries > 50) clearInterval(timer);
    }, 100);
  }
})();
