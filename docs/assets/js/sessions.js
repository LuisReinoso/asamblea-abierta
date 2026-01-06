/**
 * Sessions page JavaScript
 */

let allSessions = [];
let filteredSessions = [];

// Load sessions when page loads
document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    setupEventListeners();
});

function setupEventListeners() {
    document.getElementById('search-input').addEventListener('input', filterSessions);
    document.getElementById('filter-topic').addEventListener('change', filterSessions);
    document.getElementById('sort-by').addEventListener('change', sortAndDisplaySessions);
}

async function loadSessions() {
    try {
        const response = await fetch('./data/catalog.json');

        if (!response.ok) {
            displayNoSessions('El catálogo de sesiones no está disponible aún.');
            return;
        }

        const catalog = await response.json();
        allSessions = catalog.sessions || [];
        filteredSessions = [...allSessions];

        if (allSessions.length === 0) {
            displayNoSessions('No hay sesiones procesadas todavía.');
            return;
        }

        // Populate topic filter
        populateTopicFilter();

        // Display sessions
        sortAndDisplaySessions();

    } catch (error) {
        console.error('Error loading sessions:', error);
        displayNoSessions('Error al cargar las sesiones.');
    }
}

function populateTopicFilter() {
    const topics = new Set();
    allSessions.forEach(session => {
        session.topics.forEach(topic => topics.add(topic));
    });

    const select = document.getElementById('filter-topic');
    Array.from(topics).sort().forEach(topic => {
        const option = document.createElement('option');
        option.value = topic;
        option.textContent = topic;
        select.appendChild(option);
    });
}

function filterSessions() {
    const searchTerm = document.getElementById('search-input').value.toLowerCase();
    const selectedTopic = document.getElementById('filter-topic').value;

    filteredSessions = allSessions.filter(session => {
        // Filter by search term
        const matchesSearch = !searchTerm ||
            session.title.toLowerCase().includes(searchTerm) ||
            session.summary.toLowerCase().includes(searchTerm) ||
            session.topics.some(t => t.toLowerCase().includes(searchTerm)) ||
            session.keywords.some(k => k.toLowerCase().includes(searchTerm));

        // Filter by topic
        const matchesTopic = !selectedTopic || session.topics.includes(selectedTopic);

        return matchesSearch && matchesTopic;
    });

    sortAndDisplaySessions();
}

function sortAndDisplaySessions() {
    const sortBy = document.getElementById('sort-by').value;

    // Sort sessions
    filteredSessions.sort((a, b) => {
        switch (sortBy) {
            case 'date-desc':
                return new Date(b.date) - new Date(a.date);
            case 'date-asc':
                return new Date(a.date) - new Date(b.date);
            case 'duration-desc':
                return b.duration - a.duration;
            case 'duration-asc':
                return a.duration - b.duration;
            default:
                return 0;
        }
    });

    displaySessions();
}

function displaySessions() {
    const container = document.getElementById('sessions-list');
    const countElement = document.getElementById('sessions-count');
    const noResults = document.getElementById('no-results');

    if (filteredSessions.length === 0) {
        container.style.display = 'none';
        noResults.style.display = 'block';
        countElement.textContent = 'No se encontraron sesiones';
        return;
    }

    container.style.display = 'grid';
    noResults.style.display = 'none';
    countElement.textContent = `Mostrando ${filteredSessions.length} de ${allSessions.length} sesiones`;

    container.innerHTML = '';

    filteredSessions.forEach(session => {
        const card = createSessionCard(session);
        container.appendChild(card);
    });
}

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
    const topicsHTML = topics.map(topic =>
        `<span style="display: inline-block; background: var(--bg-light); padding: 0.25rem 0.5rem; margin: 0.25rem; border-radius: 4px; font-size: 0.875rem;">${topic}</span>`
    ).join(' ');

    card.innerHTML = `
        <h3 class="card-title">
            <a href="session-detail.html?id=${session.id}">${session.title}</a>
        </h3>
        <p class="card-meta">
            📅 ${formattedDate} • ⏱️ ${durationMinutes} min • 🎤 ${session.speaker_count} oradores
        </p>
        <div class="card-content">
            <p>${session.summary || 'Sesión de la Asamblea Nacional'}</p>
            ${topics.length > 0 ? `<div class="mt-2">${topicsHTML}</div>` : ''}
            ${session.bills_mentioned > 0 ? `<p class="mt-2" style="color: var(--primary-color);"><strong>📋 ${session.bills_mentioned} proyecto(s) de ley mencionado(s)</strong></p>` : ''}
            <p class="mt-2">
                <a href="${session.url}" target="_blank" style="font-size: 0.875rem;">📺 Ver video en YouTube</a>
            </p>
        </div>
    `;

    return card;
}

function displayNoSessions(message) {
    const container = document.getElementById('sessions-list');
    const countElement = document.getElementById('sessions-count');

    countElement.textContent = '';
    container.innerHTML = `
        <div class="card">
            <h3 class="card-title">Sin sesiones</h3>
            <div class="card-content">
                <p>${message}</p>
                <p class="mt-2">
                    <a href="https://github.com/tu-usuario/asamblea-nacional" target="_blank">
                        Ver documentación en GitHub
                    </a>
                </p>
            </div>
        </div>
    `;
}
