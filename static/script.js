/* global marked, L */
let map;
let markersLayer;
let currentReportMarker = null;
let currentHeatmapLayer = null;

async function initMap() {
    map = L.map('map').setView([46.21, -123.82], 13);
    
    // Add dark-themed tiles (CartoDB Dark Matter) for better contrast with the dashboard
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap contributors, &copy; CARTO',
        subdomains: 'abcd',
        maxZoom: 19
    }).addTo(map);
    
    markersLayer = L.layerGroup().addTo(map);

    try {
        const [zonesRes, hazardsRes] = await Promise.all([
            fetch('/api/v1/safe-zones?lat=46.21&lon=-123.82&radius=20'),
            fetch('/api/v1/hazards?lat=46.21&lon=-123.82&radius=20')
        ]);
        const zones = await zonesRes.json();
        const hazards = await hazardsRes.json();

        zones.forEach(z => {
            const color = z.status === 'operational' ? '#10b981' : '#f59e0b';
            L.circleMarker([z.latitude, z.longitude], {
                color: color, fillColor: color, fillOpacity: 0.5, radius: 8
            }).bindPopup(`<b>${z.name}</b><br>Type: ${z.type}<br>Capacity: ${z.current_occupancy}/${z.capacity}`).addTo(map);
        });

        hazards.forEach(h => {
            L.circle([h.latitude, h.longitude], {
                color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.4, radius: h.radius_m
            }).bindPopup(`<b>HAZARD: ${h.type}</b><br>Severity: ${h.severity.toUpperCase()}<br>${h.description}`).addTo(map);
        });
    } catch (e) {
        console.error("Failed to load map layers:", e);
    }
}

// Global state injected by Jinja in index.html will populate this if available
try {
    const eventsDataEl = document.getElementById('events-data');
    if (eventsDataEl && eventsDataEl.textContent.trim()) {
        window.reportsData = JSON.parse(eventsDataEl.textContent);
    } else {
        window.reportsData = [];
    }
} catch (e) {
    console.warn("Could not parse initial events data", e);
    window.reportsData = [];
}

function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws`;
    
    const ws = new WebSocket(wsUrl);
    
    ws.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);
            if (data.events && data.events.length !== window.reportsData.length) {
                window.reportsData = data.events;
                renderReportsList();
                if (window.reportsData.length > 0) {
                    selectReport(0); // Auto-select latest
                }
            }
        } catch (e) {
            console.error("WebSocket message parse error:", e);
        }
    };
    
    ws.onclose = function() {
        console.warn("WebSocket closed. Reconnecting in 3s...");
        setTimeout(connectWebSocket, 3000);
    };
}

// Initial render logic
document.addEventListener("DOMContentLoaded", () => {
    initMap();
    if (window.reportsData && window.reportsData.length > 0) {
        renderReportsList();
        selectReport(0);
    }
    connectWebSocket();
});

function renderReportsList() {
    const listEl = document.getElementById('reports-list');
    if (!window.reportsData || window.reportsData.length === 0) return;
    
    listEl.innerHTML = '';
    
    // Reverse so newest is on top
    [...window.reportsData].reverse().forEach((event, index) => {
        const r = event.report;
        const actualIndex = window.reportsData.length - 1 - index;
        
        const threatClass = (r.threat_level || 'unknown').toLowerCase();
        
        const card = document.createElement('div');
        card.className = `report-card ${threatClass}`;
        card.onclick = () => selectReport(actualIndex);
        
        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                <strong>${r.operator_id}</strong>
                <span style="font-size:0.8rem; color:var(--text-muted)">${new Date(r.timestamp).toLocaleTimeString()}</span>
            </div>
            <div>
                <span class="tag ${threatClass}">${(r.threat_level || 'UNKNOWN').toUpperCase()}</span>
                <span class="tag" style="background:#334155;">${(r.category || '').replace(/_/g, ' ')}</span>
            </div>
            <div style="margin-top:10px; font-size:0.9rem; color:var(--text-muted)">
                ${(r.audio_transcript || '').substring(0, 60)}...
            </div>
        `;
        listEl.appendChild(card);
    });
}

function selectReport(index) {
    const event = window.reportsData[index];
    if (!event) return;
    
    triggerArchitectureAnimation();
    
    const r = event.report;
    const res = event.result;
    const dispatchEl = document.getElementById('active-dispatch');
    
    if (r.location && r.location.latitude && map) {
        if (currentReportMarker) {
            map.removeLayer(currentReportMarker);
        }
        currentReportMarker = L.circleMarker([r.location.latitude, r.location.longitude], {
            color: '#38bdf8', fillColor: '#38bdf8', fillOpacity: 0.8, radius: 10
        }).bindPopup(`<b>Operator: ${r.operator_id}</b><br>${r.threat_level.toUpperCase()} Threat`)
        .addTo(map);
        map.setView([r.location.latitude, r.location.longitude], 14);
        generateHeatmap(r.location.latitude, r.location.longitude, r.category, r.threat_level);
    }
    
    let toolsHtml = '';
    if (res.tool_calls && res.tool_calls.length > 0) {
        toolsHtml = `<h3>🔧 Tool Invocations</h3>` + res.tool_calls.map(t => 
            `<div class="tool-call">
                <strong>${t.tool}</strong><br>
                <span style="color:#a7f3d0">${JSON.stringify(t.arguments)}</span>
                <div style="margin-top:5px; font-size:0.8rem; color:#94a3b8">Returned ${t.result_count} rows</div>
            </div>`
        ).join('');
    }

    // Extract <think> blocks
    let planText = res.dispatch_plan || '';
    const thinkRegex = /<think>([\s\S]*?)<\/think>/g;
    let thinkBlocks = '';
    let match;
    
    while ((match = thinkRegex.exec(planText)) !== null) {
        thinkBlocks += `<div class="thinking-block"><strong>Reasoning:</strong><br>${match[1].trim().replace(/\n/g, '<br>')}</div>`;
    }
    
    // Remove think blocks from main plan
    planText = planText.replace(/<think>[\s\S]*?<\/think>/g, '').trim();

    dispatchEl.innerHTML = `
        <div style="margin-bottom: 20px; padding: 15px; background: rgba(0,0,0,0.2); border-radius: 8px;">
            <h3 style="margin-top:0; color:var(--accent)">Raw Ingestion</h3>
            <p><strong>Transcript:</strong> ${r.audio_transcript}</p>
            <p><strong>Vision Analysis:</strong> ${r.image_analysis}</p>
        </div>
        
        ${thinkBlocks}
        ${toolsHtml}
        
        <h3 style="color:var(--success); border-top:1px solid var(--border); padding-top:20px;">Final Dispatch Plan</h3>
        <div class="markdown">
            ${marked.parse(planText)}
        </div>
    `;
}

function generateHeatmap(lat, lon, category, severity) {
    if (currentHeatmapLayer) {
        map.removeLayer(currentHeatmapLayer);
        currentHeatmapLayer = null;
    }
    
    // Only generate heatmap for dispersion hazards
    const dispersionHazards = ['chemical_spill', 'wildfire', 'gas_leak'];
    let effectiveCategory = category.toLowerCase();
    
    if (!dispersionHazards.includes(effectiveCategory)) {
        return;
    }
    
    const points = [];
    const intensity = (severity || '').toLowerCase() === 'critical' ? 1.0 : 0.6;
    const spreadKm = (severity || '').toLowerCase() === 'critical' ? 2.5 : 1.0;
    
    // Simulate wind blowing to the North-East
    for (let i = 0; i < 300; i++) {
        // Random distance skewed towards NE
        const dist = Math.random() * spreadKm;
        const angle = (Math.random() * Math.PI) - (Math.PI / 4); // NE direction focus
        
        const dLat = (dist * Math.cos(angle)) / 111.0;
        const dLon = (dist * Math.sin(angle)) / (111.0 * Math.cos(lat * Math.PI / 180));
        
        points.push([lat + dLat, lon + dLon, intensity * (1 - (dist / spreadKm))]);
    }
    
    if (typeof L.heatLayer !== 'undefined') {
        currentHeatmapLayer = L.heatLayer(points, {
            radius: 30,
            blur: 20,
            maxZoom: 14,
            gradient: {0.4: 'blue', 0.6: 'cyan', 0.8: 'yellow', 1.0: 'red'}
        }).addTo(map);
    }
}

function triggerArchitectureAnimation() {
    document.getElementById('node-a').classList.remove('active');
    document.getElementById('node-b').classList.remove('active');
    document.getElementById('node-db').classList.remove('active');
    
    const p1 = document.getElementById('packet-1');
    const p2 = document.getElementById('packet-2');
    
    p1.classList.remove('animating-packet');
    p2.classList.remove('animating-packet');
    
    // Force reflow
    void p1.offsetWidth;
    void p2.offsetWidth;
    
    // Sequence
    document.getElementById('node-a').classList.add('active');
    
    setTimeout(() => {
        p1.classList.add('animating-packet');
        document.getElementById('node-a').classList.remove('active');
    }, 500);

    setTimeout(() => {
        document.getElementById('node-b').classList.add('active');
    }, 2000);

    setTimeout(() => {
        p2.classList.add('animating-packet');
    }, 3000);

    setTimeout(() => {
        document.getElementById('node-db').classList.add('active');
    }, 4500);

    setTimeout(() => {
        document.getElementById('node-b').classList.remove('active');
        document.getElementById('node-db').classList.remove('active');
    }, 6000);
}
