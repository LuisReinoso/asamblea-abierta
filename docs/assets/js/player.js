/*
 * Session player: embeds the official YouTube video and keeps the page in step
 * with it. The transcript paragraph being spoken is highlighted (and followed),
 * the timeline playhead moves, and "Hablando ahora" shows the current speaker.
 *
 * The page works without this script (every timestamp links to YouTube); this
 * only enhances it. Times inside a long turn are interpolated by word position
 * when the page is generated, so paragraph highlighting is approximate.
 */
(function () {
  'use strict';

  const player = document.querySelector('.player[data-video]');
  if (!player || !player.dataset.video) return;

  const videoId = player.dataset.video;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const paragraphs = Array.from(document.querySelectorAll('.turn p[data-start]'));
  const starts = paragraphs.map((p) => parseFloat(p.dataset.start));
  const turns = Array.from(document.querySelectorAll('.turn'));
  const track = document.querySelector('.timeline-track');
  const playhead = document.querySelector('.tl-playhead');
  const now = document.querySelector('.now-speaking');
  const follow = document.querySelector('.follow-toggle');
  const followBox = follow && follow.querySelector('input');
  const chips = new Map(
    Array.from(document.querySelectorAll('.speaker-chip')).map((c) => [c.dataset.speaker, c])
  );
  const duration = (() => {
    const last = turns[turns.length - 1];
    return last ? parseFloat(last.dataset.end) : 0;
  })();

  let yt = null;
  let ready = false;
  let pendingSeek = null;
  let timer = null;
  let activeIndex = -1;
  let activeSpeaker = null;
  let lastUserScroll = 0;

  // ---- YouTube IFrame API -------------------------------------------------

  function loadApi() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    return new Promise((resolve) => {
      const previous = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = function () {
        if (typeof previous === 'function') previous();
        resolve();
      };
      const s = document.createElement('script');
      s.src = 'https://www.youtube.com/iframe_api';
      document.head.appendChild(s);
    });
  }

  function start(at) {
    if (yt) {
      seek(at);
      return;
    }
    pendingSeek = at || 0;
    player.classList.add('is-loading');
    loadApi().then(() => {
      const mount = document.createElement('div');
      player.replaceChildren(mount);
      yt = new window.YT.Player(mount, {
        host: 'https://www.youtube-nocookie.com',
        videoId,
        playerVars: { autoplay: 1, start: Math.floor(pendingSeek), rel: 0, playsinline: 1 },
        events: {
          onReady: () => {
            ready = true;
            player.classList.remove('is-loading');
            player.classList.add('is-live');
            document.body.classList.add('player-live');
            if (follow) follow.hidden = false;
            yt.playVideo();
          },
          onStateChange: (ev) => {
            if (ev.data === window.YT.PlayerState.PLAYING) startTicking();
            else stopTicking();
            tick();
          },
        },
      });
    });
  }

  function seek(at) {
    if (!ready) {
      pendingSeek = at;
      return;
    }
    yt.seekTo(at, true);
    yt.playVideo();
    lastUserScroll = 0; // a deliberate jump re-enables following
    tick();
  }

  // ---- Sync ----------------------------------------------------------------

  function startTicking() {
    if (!timer) timer = window.setInterval(tick, 250);
  }

  function stopTicking() {
    window.clearInterval(timer);
    timer = null;
  }

  function indexAt(t) {
    // last paragraph whose start <= t (binary search)
    let lo = 0;
    let hi = starts.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] <= t + 0.25) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  function tick() {
    if (!ready) return;
    const t = yt.getCurrentTime() || 0;
    if (playhead && duration) {
      playhead.style.setProperty('--x', `${Math.min(100, (100 * t) / duration)}%`);
      playhead.classList.add('is-visible');
    }
    const i = indexAt(t);
    if (i === activeIndex) return;
    if (activeIndex >= 0) paragraphs[activeIndex].classList.remove('is-active');
    activeIndex = i;
    if (i < 0) return;

    const p = paragraphs[i];
    p.classList.add('is-active');
    const turn = p.closest('.turn');
    turns.forEach((el) => el.classList.toggle('is-current', el === turn));
    setSpeaker(turn.dataset.speaker);

    const following = followBox ? followBox.checked : true;
    if (following && Date.now() - lastUserScroll > 4000) {
      p.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
    }
  }

  function setSpeaker(key) {
    if (key === activeSpeaker) return;
    activeSpeaker = key;
    if (track) {
      track.querySelectorAll('.tl-block').forEach((b) =>
        b.classList.toggle('is-current', b.dataset.speaker === key)
      );
    }
    chips.forEach((c, k) => c.classList.toggle('is-current', k === key));

    const chip = chips.get(key);
    if (!now || !chip) return;
    now.hidden = false;
    now.classList.toggle('now-speaking--unk', key === 'unk');
    now.querySelector('.avatar').textContent = chip.dataset.initials;
    now.querySelector('.now-name').textContent = chip.dataset.name;
    now.querySelector('.now-meta').textContent = chip.dataset.meta || '';
    // restart the entrance animation on every speaker change
    now.classList.remove('is-changing');
    void now.offsetWidth;
    now.classList.add('is-changing');
  }

  // ---- Interaction ---------------------------------------------------------

  player.querySelector('.player-facade').addEventListener('click', (ev) => {
    ev.preventDefault();
    start(0);
  });

  document.addEventListener('click', (ev) => {
    const link = ev.target.closest('[data-seek]');
    if (!link) return;
    ev.preventDefault();
    const at = parseFloat(link.dataset.seek);
    start(at);
    if (window.innerWidth < 900) player.scrollIntoView({ block: 'start', behavior: reduceMotion ? 'auto' : 'smooth' });
  });

  // Clicking a paragraph jumps the video there too.
  document.querySelector('.transcript').addEventListener('click', (ev) => {
    const p = ev.target.closest('.turn p[data-start]');
    if (!p || window.getSelection().toString()) return; // let people select text
    start(parseFloat(p.dataset.start));
  });

  // A speaker chip highlights that person's blocks and jumps to their next turn.
  chips.forEach((chip, key) => {
    chip.addEventListener('click', () => {
      const focused = chip.classList.contains('is-focused');
      if (track) {
        track.classList.toggle('has-focus', !focused);
        track.querySelectorAll('.tl-block').forEach((blk) =>
          blk.classList.toggle('is-focused', !focused && blk.dataset.speaker === key)
        );
      }
      chips.forEach((c, k) => c.classList.toggle('is-focused', !focused && k === key));
      if (focused) return;
      const t = ready ? yt.getCurrentTime() : -1;
      const next = turns.find((el) => el.dataset.speaker === key && parseFloat(el.dataset.start) > t + 1)
        || turns.find((el) => el.dataset.speaker === key);
      if (next) start(parseFloat(next.dataset.start));
    });
  });

  // Manual scrolling pauses following for a few seconds.
  ['wheel', 'touchmove', 'keydown'].forEach((type) =>
    window.addEventListener(type, (ev) => {
      if (type === 'keydown' && !['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(ev.key)) return;
      lastUserScroll = Date.now();
    }, { passive: true })
  );
})();
