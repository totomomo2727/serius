/* Editorial motion ported from the approved prototype: typed tagline, typed CTA
   labels, and the hand-drawn outline that follows them. */
(function () {
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var root = document.documentElement;
  /* Tells the head script that the motion code is alive and will reveal the
     page itself; without this the head script drops the hiding rules. */
  root.dataset.motion = 'ready';

  function showEverything() {
    root.classList.remove('js');
  }

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

  /* Every moment the intro makes a sound, in ms from load, so the audio layer
     can be switched on later and replayed in step with the typing. */
  var beats = [];

  function beat(at, kind) {
    beats.push({ at: at, kind: kind });
  }

  function editorial() {
    var tagline = document.querySelector('[data-typed-tagline]');
    var taglineEnd = 0;
    if (tagline) {
      taglineEnd = letters(tagline, 180, 52);
      for (var t = 180; t < taglineEnd; t += 52) beat(t, 'key');
      beat(taglineEnd, 'return');
    }
    var last = taglineEnd;
    document.querySelectorAll('.button').forEach(function (button, i) {
      var start = tagline ? taglineEnd + 300 : 180 + i * 100;
      var label = button.textContent;
      button.innerHTML = '<span class="button-copy">' + button.innerHTML + '</span>';
      var end = letters(button.querySelector('.button-copy'), start, 36);
      button.setAttribute('aria-label', label.trim());
      button.insertAdjacentHTML('beforeend', OUTLINE);
      button.style.setProperty('--draw-start', end + 100 + 'ms');
      button.classList.add('animated-button');
      if (i === 0) {
        for (var k = start; k < end; k += 36) beat(k, 'key');
        beat(end + 100, 'draw');
        last = end + 950;
      }
    });
    if (reduced) document.body.classList.add('motion-reduced');
    return last;
  }

  /* The hero settles after the copy has finished typing: the newspaper first,
     then Serius, then his notes — 90ms apart, which reads as one movement. */
  function heroReveal(after) {
    var order = { paper: 0, bird: 140, note: 280 };
    var items = document.querySelectorAll('[data-reveal]');
    if (!items.length) return;
    var base = reduced ? 0 : after;
    items.forEach(function (el) {
      var offset = order[el.dataset.reveal] || 0;
      el.style.setProperty('--reveal-delay', base + offset + 'ms');
      if (offset === 0) beat(base, 'paper');
    });
    requestAnimationFrame(function () {
      items.forEach(function (el) {
        el.classList.add('is-in');
      });
    });
  }

  /* Sections arrive as they are scrolled to, their children one after another. */
  function scrollReveal() {
    var groups = document.querySelectorAll('[data-stagger]');
    if (!groups.length) return;
    var show = function (group) {
      Array.prototype.forEach.call(group.children, function (child, i) {
        var delay = reduced ? 0 : i * 110;
        child.style.setProperty('--reveal-delay', delay + 'ms');
        child.classList.add('is-in');
        window.setTimeout(function () {
          audio.play('tick');
        }, delay);
      });
    };
    if (reduced || !('IntersectionObserver' in window)) {
      groups.forEach(show);
      return;
    }
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          show(entry.target);
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.25, rootMargin: '0px 0px -8% 0px' }
    );
    groups.forEach(function (group) {
      observer.observe(group);
    });
  }

  /* A small synthesised foley kit — keystrokes, the carriage return, paper and
     the pen drawing the oval. Synthesised rather than sampled so the page
     stays a single request, and silent until asked for: browsers block
     unprompted audio and so should we. */
  var audio = (function () {
    var LEVEL = 0.16;
    var ctx = null;
    var master = null;
    var on = false;
    try {
      on = window.localStorage.getItem('fp-sound') === 'on';
    } catch (err) {
      on = false;
    }

    function ready() {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!ctx) {
        ctx = new Ctx();
        master = ctx.createGain();
        master.gain.value = on ? LEVEL : 0;
        master.connect(ctx.destination);
      }
      return ctx;
    }

    function noise(duration) {
      var frames = Math.max(1, Math.floor(ctx.sampleRate * duration));
      var buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
      var data = buffer.getChannelData(0);
      for (var i = 0; i < frames; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
      var src = ctx.createBufferSource();
      src.buffer = buffer;
      return src;
    }

    function hit(opts) {
      var src = noise(opts.length);
      var filter = ctx.createBiquadFilter();
      filter.type = opts.type || 'bandpass';
      filter.frequency.value = opts.frequency;
      filter.Q.value = opts.q || 1;
      var gain = ctx.createGain();
      var now = ctx.currentTime;
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(opts.level, now + 0.004);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + opts.length);
      src.connect(filter).connect(gain).connect(master);
      src.start(now);
      src.stop(now + opts.length + 0.02);
    }

    function tone(frequency, length, level) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      var now = ctx.currentTime;
      osc.type = 'triangle';
      osc.frequency.value = frequency;
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(level, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + length);
      osc.connect(gain).connect(master);
      osc.start(now);
      osc.stop(now + length + 0.02);
    }

    var voices = {
      key: function () {
        hit({ length: 0.035, frequency: 1500 + Math.random() * 700, q: 1.4, level: 0.5 });
      },
      return: function () {
        hit({ length: 0.12, frequency: 900, q: 0.8, level: 0.35 });
        tone(320, 0.09, 0.05);
      },
      draw: function () {
        hit({ length: 0.5, frequency: 2600, q: 0.5, level: 0.14, type: 'highpass' });
      },
      paper: function () {
        hit({ length: 0.34, frequency: 1800, q: 0.4, level: 0.2, type: 'highpass' });
      },
      tick: function () {
        tone(660, 0.07, 0.04);
      },
      toggle: function () {
        tone(520, 0.08, 0.06);
      },
    };

    return {
      enabled: function () {
        return on;
      },
      /* Switching off silences what is already ringing, not just what is
         scheduled, so "off" is immediate. */
      set: function (value) {
        on = value;
        try {
          window.localStorage.setItem('fp-sound', value ? 'on' : 'off');
        } catch (err) {
          /* private browsing keeps the choice for this page only */
        }
        if (!ready()) return;
        var now = ctx.currentTime;
        master.gain.cancelScheduledValues(now);
        master.gain.setValueAtTime(master.gain.value, now);
        master.gain.linearRampToValueAtTime(value ? LEVEL : 0, now + 0.02);
      },
      /* Resolves once the context is running, which a browser only allows
         from a gesture; scheduled sounds wait for this rather than each
         queueing their own resume and arriving together late. */
      unlock: function () {
        if (!ready()) return Promise.resolve(false);
        if (ctx.state === 'running') return Promise.resolve(true);
        return Promise.resolve(ctx.resume()).then(function () {
          return ctx.state === 'running';
        });
      },
      /* A sound whose moment has passed is dropped, never replayed late. */
      play: function (kind) {
        if (!on || !voices[kind]) return;
        if (!ready() || ctx.state !== 'running') return;
        voices[kind]();
      },
    };
  })();

  /* Toggling sound on replays the intro so the typing is actually heard, and
     the click itself is the gesture that unlocks audio playback. */
  function soundControl(replay) {
    var button = document.querySelector('[data-sound-toggle]');
    if (!button || !(window.AudioContext || window.webkitAudioContext)) return;
    var state = button.querySelector('[data-sound-state]');
    var paint = function () {
      button.setAttribute('aria-pressed', audio.enabled() ? 'true' : 'false');
      if (state) state.textContent = audio.enabled() ? 'on' : 'off';
    };
    button.hidden = false;
    paint();
    /* A returning visitor kept sound on, but audio stays blocked until they
       interact with the page, so the first interaction unlocks it quietly. */
    if (audio.enabled()) {
      var once = function () {
        audio.unlock();
        document.removeEventListener('pointerdown', once);
        document.removeEventListener('keydown', once);
      };
      document.addEventListener('pointerdown', once);
      document.addEventListener('keydown', once);
    }
    button.addEventListener('click', function () {
      audio.set(!audio.enabled());
      paint();
      if (!audio.enabled()) {
        playBeats(0);
        return;
      }
      audio.unlock().then(function () {
        if (!audio.enabled()) return;
        audio.play('toggle');
        if (replay) replay();
      });
    });
  }

  /* One intro is audible at a time: a second replay cancels the first rather
     than playing both sets of keystrokes over one animation. */
  var pending = [];

  function playBeats(offset) {
    pending.forEach(window.clearTimeout);
    pending = [];
    if (!audio.enabled()) return;
    beats.forEach(function (item) {
      pending.push(
        window.setTimeout(function () {
          audio.play(item.kind);
        }, Math.max(0, item.at - offset))
      );
    });
  }

  function scheduleBeats() {
    playBeats(window.performance && performance.now ? performance.now() : 0);
  }

  /* Restarting the CSS animations from their first frame. */
  function replayIntro() {
    var nodes = document.querySelectorAll('.typed-character, .drawn-outline path');
    nodes.forEach(function (node) {
      node.style.animation = 'none';
    });
    void document.body.offsetWidth;
    nodes.forEach(function (node) {
      node.style.animation = '';
    });
    playBeats(0);
  }

  /* Only fills fields that ask for detection; a saved preference is never
     overwritten by the device it happens to be opened on. */
  function timezone() {
    var field = document.querySelector('[data-detect-timezone]');
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
    /* Coming back through history restores the disabled button from the page
       cache, so the form is released whenever the page is shown again. */
    window.addEventListener('pageshow', function (event) {
      if (!event.persisted) return;
      document.querySelectorAll('form[data-once]').forEach(function (form) {
        delete form.dataset.submitted;
        form.querySelectorAll('button[type=submit]').forEach(function (b) {
          b.disabled = false;
          b.classList.remove('is-sending');
        });
      });
    });
  }

  /* A short pulse on the controls people actually press, where the device
     supports it; ignored everywhere else. */
  function touchFeedback() {
    var buzz = function (pattern) {
      if (audio.enabled() && navigator.vibrate) navigator.vibrate(pattern);
    };
    document.addEventListener('change', function (event) {
      if (!event.target.closest('[data-chip]')) return;
      audio.play('tick');
      buzz(8);
    });
    document.querySelectorAll('.button, .nav-cta').forEach(function (button) {
      button.addEventListener('click', function () {
        audio.play('paper');
        buzz(12);
      });
    });
  }

  try {
    var introEnd = editorial();
    heroReveal(introEnd + 120);
    scrollReveal();
    soundControl(replayIntro);
    scheduleBeats();
    touchFeedback();
  } catch (err) {
    showEverything();
  }
  /* Last resort: whatever went wrong, nothing stays hidden. */
  window.setTimeout(function () {
    if (document.querySelector('[data-reveal]:not(.is-in)')) showEverything();
  }, 6000);
  timezone();
  chips();
  guardDoubleSubmit();
})();
