/* Editorial motion ported from the approved prototype: typed tagline, typed CTA
   labels, and the hand-drawn outline that follows them. */
(function () {
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var OUTLINE =
    '<svg class="drawn-outline" aria-hidden="true" viewBox="0 0 340 100" preserveAspectRatio="none">' +
    '<path pathLength="1" d="M 82 12 C 135 3 244 4 298 24 C 324 34 332 58 307 76 C 264 100 110 96 44 83 ' +
    'C 9 76 9 53 25 36 C 40 20 90 10 146 10 C 216 8 272 14 299 25"/></svg>';

  function letters(el, start, step) {
    if (!el) return start;
    var full = el.textContent;
    el.setAttribute('aria-label', full);
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    var n = 0;
    nodes.forEach(function (node) {
      if (node.parentElement.closest('svg')) return;
      var frag = document.createDocumentFragment();
      Array.prototype.forEach.call(node.textContent, function (c) {
        var span = document.createElement('span');
        span.className = 'typed-character';
        span.setAttribute('aria-hidden', 'true');
        span.textContent = c;
        span.style.setProperty('--appear', start + n * step + 'ms');
        frag.append(span);
        n++;
      });
      node.replaceWith(frag);
    });
    return start + n * step;
  }

  function editorial() {
    var tagline = document.querySelector('[data-typed-tagline]');
    var taglineEnd = tagline ? letters(tagline, 180, 52) : 0;
    document.querySelectorAll('.button').forEach(function (button, i) {
      var start = tagline ? taglineEnd + 300 : 180 + i * 100;
      var label = button.textContent;
      button.innerHTML = '<span class="button-copy">' + button.innerHTML + '</span>';
      var end = letters(button.querySelector('.button-copy'), start, 36);
      button.setAttribute('aria-label', label.trim());
      button.insertAdjacentHTML('beforeend', OUTLINE);
      button.style.setProperty('--draw-start', end + 100 + 'ms');
      button.classList.add('animated-button');
    });
    if (reduced) document.body.classList.add('motion-reduced');
  }

  function timezone() {
    var field = document.getElementById('timezone_name');
    if (!field) return;
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (!tz) return;
      field.value = tz;
      var label = document.getElementById('tz-label');
      if (label) label.textContent = tz;
    } catch (err) {
      /* UTC stays */
    }
  }

  function chips() {
    var all = document.querySelectorAll('[data-chip]');
    var sync = function () {
      all.forEach(function (chip) {
        var input = chip.querySelector('input');
        if (!input) return;
        chip.classList.toggle('selected', input.checked);
        var mark = chip.querySelector('b');
        if (mark) mark.textContent = input.checked ? '✓' : '+';
      });
    };
    /* Radios deselect their siblings without firing an event on them, so every
       chip is resynced on any change. */
    document.addEventListener('change', sync);
    sync();
  }

  function guardDoubleSubmit() {
    document.querySelectorAll('form[data-once]').forEach(function (form) {
      form.addEventListener('submit', function () {
        if (form.dataset.submitted) return;
        form.dataset.submitted = '1';
        window.setTimeout(function () {
          form.querySelectorAll('button[type=submit]').forEach(function (b) {
            b.disabled = true;
            b.classList.add('is-sending');
          });
        }, 0);
      });
    });
  }

  editorial();
  timezone();
  chips();
  guardDoubleSubmit();
})();
