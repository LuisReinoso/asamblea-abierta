/**
 * Session cards shared by the home page and the sessions list.
 *
 * Two kinds of video live in the catalog and the cards must say which is
 * which: a "Sesión del Pleno" is the full recording of a plenary session (many
 * speakers, hours long); an "Intervención" is an official clip of one
 * asambleísta speaking in some session ("Asambleísta X - Sesión 133").
 */

const KIND = {
  full_session: {
    label: 'Sesión del Pleno',
    explain: 'Grabación completa de una sesión: todas las intervenciones en orden, con quién habla en cada momento.',
  },
  clip: {
    label: 'Intervención',
    explain: 'Clip oficial con la intervención de un asambleísta, recortado de una sesión del Pleno.',
  },
};

function kindOf(session) {
  return session.video_type === 'full_session' ? 'full_session' : 'clip';
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function videoIdOf(session) {
  const m = /[?&]v=([\w-]{6,})/.exec(session.url || '');
  return m ? m[1] : session.id;
}

function formatDuration(seconds) {
  const s = Math.round(seconds || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
}

function formatDate(iso) {
  if (!iso) return '';
  // A bare "YYYY-MM-DD" parses as UTC midnight, which is the previous day in
  // Ecuador (UTC-5); read the calendar date as local instead.
  const ymd = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  const d = ymd ? new Date(+ymd[1], +ymd[2] - 1, +ymd[3]) : new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('es-EC', { year: 'numeric', month: 'long', day: 'numeric' });
}

/** "Asambleísta Camila Cueva - Sesión 133" -> {speaker: "Camila Cueva", context: "Sesión 133"} */
function splitClipTitle(title) {
  const m = /^\s*Asamble[íi]sta\s+(.+?)\s+-\s+(Sesi[óo]n\s+.+)$/i.exec(title || '');
  return m ? { speaker: m[1], context: m[2] } : null;
}

function createSessionCard(session, index = 0) {
  const kind = kindOf(session);
  const card = document.createElement('a');
  card.className = `session-card session-card--${kind === 'full_session' ? 'full' : 'clip'}`;
  card.href = `sessions/${encodeURIComponent(session.id)}.html`;
  card.style.setProperty('--i', String(Math.min(index, 12)));

  const parts = kind === 'clip' ? splitClipTitle(session.title) : null;
  const title = parts ? parts.speaker : session.title;
  const context = parts ? parts.context : '';
  const speakers = session.speaker_count || 0;
  const meta = [
    context,
    formatDate(session.date),
    kind === 'full_session' ? `${speakers} ${speakers === 1 ? 'orador' : 'oradores'}` : '',
  ].filter(Boolean).join(' · ');
  const summary = session.summary ? `<p class="session-card-summary">${escapeHtml(session.summary)}</p>` : '';

  card.innerHTML = `
    <div class="session-card-thumb" style="--thumb:url('https://i.ytimg.com/vi/${escapeHtml(videoIdOf(session))}/mqdefault.jpg')">
      <span class="kind-badge kind-badge--${kind === 'full_session' ? 'full' : 'clip'}">${KIND[kind].label}</span>
      <span class="session-card-duration">${formatDuration(session.duration)}</span>
      <span class="session-card-play" aria-hidden="true"></span>
    </div>
    <div class="session-card-body">
      <h3 class="session-card-title">${escapeHtml(title)}</h3>
      <p class="session-card-meta">${escapeHtml(meta)}</p>
      ${summary}
    </div>
  `;
  return card;
}
