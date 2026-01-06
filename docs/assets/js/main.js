/**
 * Main JavaScript for Asamblea Nacional Transparency Platform
 */

// Load statistics when page loads
document.addEventListener('DOMContentLoaded', () => {
    loadStatistics();
    loadRecentSessions();
});

/**
 * Load and display statistics
 */
async function loadStatistics() {
    try {
        const response = await fetch('./data/stats/all-time.json');

        if (!response.ok) {
            console.warn('Statistics not yet available');
            return;
        }

        const stats = await response.json();

        // Update stat counters
        document.getElementById('stat-sessions').textContent = stats.total_sessions || 0;

        // Calculate total hours
        let totalDuration = 0;
        if (stats.monthly_stats) {
            totalDuration = stats.monthly_stats.reduce((sum, month) => sum + (month.total_duration || 0), 0);
        }
        const totalHours = Math.round(totalDuration / 3600);
        document.getElementById('stat-hours').textContent = totalHours;

        // Count unique speakers
        const uniqueSpeakers = stats.speaker_stats ? stats.speaker_stats.length : 0;
        document.getElementById('stat-speakers').textContent = uniqueSpeakers;

        // Count unique topics
        const uniqueTopics = stats.topic_stats ? stats.topic_stats.length : 0;
        document.getElementById('stat-topics').textContent = uniqueTopics;

    } catch (error) {
        console.error('Error loading statistics:', error);
        // Keep default "-" values
    }
}

/**
 * Load and display recent sessions
 */
async function loadRecentSessions() {
    try {
        const response = await fetch('./data/catalog.json');

        if (!response.ok) {
            console.warn('Catalog not yet available');
            displayNoSessions();
            return;
        }

        const catalog = await response.json();

        if (!catalog.sessions || catalog.sessions.length === 0) {
            displayNoSessions();
            return;
        }

        // Get the 3 most recent sessions
        const recentSessions = catalog.sessions.slice(0, 3);

        // Display sessions
        const container = document.getElementById('recent-sessions');
        container.innerHTML = '';

        recentSessions.forEach(session => {
            const card = createSessionCard(session);
            container.appendChild(card);
        });

    } catch (error) {
        console.error('Error loading recent sessions:', error);
        displayNoSessions();
    }
}

/**
 * Create a session card element
 */
function createSessionCard(session) {
    const card = document.createElement('div');
    card.className = 'card';

    // Format date
    const date = new Date(session.date);
    const formattedDate = date.toLocaleDateString('es-EC', {
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    });

    // Format duration
    const durationMinutes = Math.round(session.duration / 60);

    // Get topics (limit to 3)
    const topics = session.topics.slice(0, 3);
    const topicsHTML = topics.map(topic => `<span class="topic-tag">${topic}</span>`).join(' ');

    card.innerHTML = `
        <h3 class="card-title">
            <a href="session-detail.html?id=${session.id}">${session.title}</a>
        </h3>
        <p class="card-meta">
            ${formattedDate} • ${durationMinutes} min • ${session.speaker_count} oradores
        </p>
        <div class="card-content">
            <p>${session.summary || 'Sesión de la Asamblea Nacional'}</p>
            ${topics.length > 0 ? `<p class="topics">${topicsHTML}</p>` : ''}
            <p style="margin-top: 0.5rem;">
                <a href="${session.url}" target="_blank" style="font-size: 0.875rem;">📺 Ver video en YouTube</a>
            </p>
        </div>
    `;

    return card;
}

/**
 * Display message when no sessions are available
 */
function displayNoSessions() {
    const container = document.getElementById('recent-sessions');
    container.innerHTML = `
        <div class="card">
            <h3 class="card-title">Sin sesiones procesadas</h3>
            <div class="card-content">
                <p>
                    Aún no hay sesiones procesadas. El sistema comenzará a procesar
                    sesiones automáticamente una vez configurado.
                </p>
                <p>
                    <a href="https://github.com/tu-usuario/asamblea-nacional" target="_blank">
                        Ver documentación en GitHub
                    </a>
                </p>
            </div>
        </div>
    `;
}
