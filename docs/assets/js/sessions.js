/**
 * Sessions page JavaScript
 */

let allSessions = [];
let filteredSessions = [];
let currentType = '';

// Load sessions when page loads
document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    setupEventListeners();
});

function setupEventListeners() {
    document.getElementById('search-input').addEventListener('input', filterSessions);
    document.querySelectorAll('.kind-tab').forEach((tab) => {
        tab.addEventListener('click', () => {
            currentType = tab.dataset.type;
            document.querySelectorAll('.kind-tab').forEach((t) =>
                t.setAttribute('aria-selected', String(t === tab)));
            document.getElementById('kind-explain').innerHTML = currentType
                ? escapeHtml(KIND[currentType].explain)
                : 'Las <strong>sesiones del Pleno</strong> son la grabación completa, con todos los oradores. Las <strong>intervenciones</strong> son clips oficiales de un solo asambleísta.';
            filterSessions();
        });
    });
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
        // entries without an id cannot link anywhere
        allSessions = (catalog.sessions || []).filter((s) => s.id);
        document.querySelectorAll('.kind-tab-count').forEach((el) => {
            const type = el.dataset.count;
            el.textContent = allSessions.filter((s) => !type || kindOf(s) === type).length;
        });
        filteredSessions = [...allSessions];

        if (allSessions.length === 0) {
            displayNoSessions('No hay sesiones procesadas todavía.');
            return;
        }

        // Populate topic filter
        populateTopicFilter();

        // Search typed on the home page
        const q = new URLSearchParams(window.location.search).get('q');
        if (q) document.getElementById('search-input').value = q;

        // Deep links from the home page: sessions.html#pleno / #intervenciones
        const fromHash = { '#pleno': 'full_session', '#intervenciones': 'clip' }[window.location.hash];
        if (fromHash) {
            document.querySelector(`.kind-tab[data-type="${fromHash}"]`).click();
            return;
        }

        // Display sessions
        filterSessions();

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
            (session.summary || '').toLowerCase().includes(searchTerm) ||
            session.topics.some(t => t.toLowerCase().includes(searchTerm)) ||
            session.keywords.some(k => k.toLowerCase().includes(searchTerm));

        // Filter by topic
        const matchesTopic = !selectedTopic || session.topics.includes(selectedTopic);

        // Filter by video type (full plenary session vs. per-speaker clip)
        const matchesType = !currentType || kindOf(session) === currentType;

        return matchesSearch && matchesTopic && matchesType;
    });

    sortAndDisplaySessions();
}

function sortAndDisplaySessions() {
    const sortBy = document.getElementById('sort-by').value;

    // Sort sessions
    filteredSessions.sort((a, b) => {
        // Plenary sessions are the main product: pinned above the clips.
        const pin = (kindOf(b) === 'full_session') - (kindOf(a) === 'full_session');
        if (pin) return pin;
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
    countElement.textContent = `Mostrando ${filteredSessions.length} de ${allSessions.length} videos`;

    container.innerHTML = '';

    filteredSessions.forEach((session, i) => {
        container.appendChild(createSessionCard(session, i));
    });
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
                    <a href="https://github.com/LuisReinoso/asamblea-abierta" target="_blank">
                        Ver documentación en GitHub
                    </a>
                </p>
            </div>
        </div>
    `;
}
