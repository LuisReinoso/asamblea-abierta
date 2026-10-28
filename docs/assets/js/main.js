/**
 * Home page: counts, and the newest plenary session + clips.
 * Cards and the clip/session vocabulary come from cards.js.
 */

document.addEventListener('DOMContentLoaded', () => {
    loadHome();
    const search = document.getElementById('search-input');
    if (search) {
        search.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' && search.value.trim()) {
                window.location.href = `sessions.html?q=${encodeURIComponent(search.value.trim())}`;
            }
        });
    }
});

async function loadHome() {
    let sessions = [];
    try {
        const response = await fetch('./data/catalog.json');
        if (response.ok) {
            const catalog = await response.json();
            sessions = (catalog.sessions || []).filter((s) => s.id);
        }
    } catch (error) {
        console.error('Error loading catalog:', error);
    }

    const full = sessions.filter((s) => kindOf(s) === 'full_session');
    const clips = sessions.filter((s) => kindOf(s) === 'clip');
    setText('count-full', full.length);
    setText('count-clip', clips.length);
    setText('count-full-label', full.length === 1 ? 'sesión' : 'sesiones');
    setText('count-clip-label', clips.length === 1 ? 'intervención' : 'intervenciones');
    setText('stat-sessions', full.length);
    setText('stat-clips', clips.length);
    setText('stat-hours', Math.round(sessions.reduce((sum, s) => sum + (s.duration || 0), 0) / 3600));
    loadSpeakerCount();

    const container = document.getElementById('recent-sessions');
    if (!sessions.length) {
        container.innerHTML = '<div class="card"><h3 class="card-title">Sin videos procesados todavía</h3></div>';
        return;
    }
    const byDate = (a, b) => new Date(b.date) - new Date(a.date);
    const recent = [...full.sort(byDate).slice(0, 1), ...clips.sort(byDate).slice(0, full.length ? 4 : 3)];
    container.innerHTML = '';
    recent.forEach((session, i) => container.appendChild(createSessionCard(session, i)));
}

async function loadSpeakerCount() {
    try {
        const response = await fetch('./data/stats/all-time.json');
        if (!response.ok) return;
        const stats = await response.json();
        setText('stat-speakers', stats.speaker_stats ? stats.speaker_stats.length : '-');
    } catch (error) {
        console.error('Error loading statistics:', error);
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}
