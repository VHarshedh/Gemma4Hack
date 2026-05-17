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
            
            if (data.msg_type === "sensor") {
                renderSensor(data);
                return;
            }
            
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
        
        // Dispatch autonomous drone for recon
        dispatchDrone(r.location.latitude, r.location.longitude);
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

// Global drone registry
let drones = [];

function dispatchDrone(targetLat, targetLon) {
    if (!map) return;
    
    const startLat = 46.215; // Mock Command Center coordinates
    const startLon = -123.810;
    
    const droneIcon = L.divIcon({
        html: '<div style="font-size: 24px; text-shadow: 0 0 10px cyan;">🚁</div>',
        className: 'drone-icon',
        iconSize: [24, 24],
        iconAnchor: [12, 12]
    });

    const droneMarker = L.marker([startLat, startLon], {icon: droneIcon}).addTo(map);
    drones.push(droneMarker);
    
    const steps = 100;
    const duration = 3000; 
    const stepTime = duration / steps;
    let currentStep = 0;
    
    const latStep = (targetLat - startLat) / steps;
    const lonStep = (targetLon - startLon) / steps;
    
    const flyInterval = setInterval(() => {
        currentStep++;
        const newLat = startLat + (latStep * currentStep);
        const newLon = startLon + (lonStep * currentStep);
        droneMarker.setLatLng([newLat, newLon]);
        
        if (currentStep >= steps) {
            clearInterval(flyInterval);
            droneMarker.bindPopup("<b>Recon Drone 01</b><br>On station streaming video.").openPopup();
            
            // Radar ping effect
            L.circle([targetLat, targetLon], {
                color: '#22d3ee', fillColor: '#22d3ee', fillOpacity: 0.2, radius: 150
            }).addTo(map);
        }
    }, stepTime);
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

// Global sensor markers and latest readings
let sensorMarkers = {};
let sensorReadings = {};

const SENSOR_THRESHOLDS = {
    air_quality: { warning: 100, critical: 200 }, // AQI: 0-50 Good, 51-100 Moderate, 101-200 Unhealthy, 201+ Very Unhealthy/Hazardous
    seismic:     { warning: 3.5, critical: 5.0 }, // Mw: 3.5 felt/minor damage, 5.0+ significant damage
    flood:       { warning: 0.8, critical: 1.5 }, // metres above normal: 0.8 watch, 1.5 danger
    fire:        { warning: 250, critical: 400 }, // °C: 250 active fire, 400 extreme/structure threat
};

const SENSOR_ICONS = { air_quality: '💨', seismic: '🌍', flood: '🌊', fire: '🔥' };

function sensorStatus(type, value) {
    const t = SENSOR_THRESHOLDS[type];
    if (!t) return 'normal';
    if (value >= t.critical) return 'critical';
    if (value >= t.warning)  return 'warning';
    return 'normal';
}

function renderSensor(data) {
    // ── Map dot ──────────────────────────────────────────────
    if (map) {
        if (sensorMarkers[data.sensor_id]) map.removeLayer(sensorMarkers[data.sensor_id]);
        const status = sensorStatus(data.type, data.value);
        const color = status === 'critical' ? '#ef4444' : status === 'warning' ? '#f59e0b' : '#10b981';
        const radius = status === 'critical' ? 9 : status === 'warning' ? 7 : 5;
        const marker = L.circleMarker([data.latitude, data.longitude], {
            color, fillColor: color, fillOpacity: 0.85, radius,
            weight: status === 'critical' ? 2 : 1,
        }).bindPopup(
            `<b>${SENSOR_ICONS[data.type] || '📡'} ${data.sensor_id}</b><br>` +
            `${data.type.replace(/_/g,' ').toUpperCase()}: <b>${data.value.toFixed(2)} ${data.unit}</b><br>` +
            `Status: <b style="color:${color}">${status.toUpperCase()}</b>`
        ).addTo(map);
        sensorMarkers[data.sensor_id] = marker;
    }

    // ── Sidebar panel ────────────────────────────────────────
    sensorReadings[data.sensor_id] = data;
    renderSensorPanel();
}

function renderSensorPanel() {
    let panel = document.getElementById('sensor-panel');
    if (!panel) return;

    const grouped = {};
    Object.values(sensorReadings).forEach(r => {
        if (!grouped[r.type]) grouped[r.type] = [];
        grouped[r.type].push(r);
    });

    panel.innerHTML = Object.entries(grouped).map(([type, sensors]) => {
        const icon = SENSOR_ICONS[type] || '📡';
        const rows = sensors.map(s => {
            const status = sensorStatus(s.type, s.value);
            const color  = status === 'critical' ? '#ef4444' : status === 'warning' ? '#f59e0b' : '#10b981';
            const badge  = status !== 'normal'
                ? `<span style="background:${color};color:#fff;font-size:0.65rem;padding:1px 5px;border-radius:3px;margin-left:4px">${status.toUpperCase()}</span>`
                : '';
            return `<div style="display:flex;justify-content:space-between;align-items:center;
                        padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:0.82rem">
                      <span style="color:#94a3b8">${s.sensor_id}</span>
                      <span style="color:${color};font-weight:600">${s.value.toFixed(2)} ${s.unit}${badge}</span>
                    </div>`;
        }).join('');
        return `<div style="margin-bottom:10px">
                  <div style="font-weight:600;color:#38bdf8;margin-bottom:4px">${icon} ${type.replace(/_/g,' ').toUpperCase()}</div>
                  ${rows}
                </div>`;
    }).join('') || '<div style="color:#94a3b8;font-style:italic;font-size:0.85rem">No sensor data yet…</div>';
}

// Web Speech API for Commander Voice Interface
const pttBtn = document.getElementById('ptt-btn');
const voiceTranscript = document.getElementById('voice-transcript');

if (pttBtn) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
        const recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;
        
        pttBtn.addEventListener('click', () => {
            try {
                recognition.start();
                pttBtn.style.background = 'var(--danger)';
                pttBtn.innerText = '🔴 Listening...';
                voiceTranscript.innerText = "Listening...";
            } catch(e) {}
        });
        
        recognition.onresult = async function(event) {
            const transcript = event.results[0][0].transcript;
            voiceTranscript.innerHTML = `<strong>You:</strong> "${transcript}"<br><em>Processing...</em>`;
            
            try {
                const resp = await fetch('/api/v1/voice-command', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text: transcript })
                });
                const data = await resp.json();
                
                voiceTranscript.innerHTML = `<strong>You:</strong> "${transcript}"<br><strong style="color:var(--success)">Aegis:</strong> "${data.response}"`;
                
                const utterance = new SpeechSynthesisUtterance(data.response);
                window.speechSynthesis.speak(utterance);
                
            } catch (e) {
                voiceTranscript.innerText = "Error processing voice command.";
            }
        };
        
        recognition.onspeechend = function() {
            recognition.stop();
            pttBtn.style.background = 'var(--primary)';
            pttBtn.innerText = '🎤 Push to Talk';
        };
        
        recognition.onerror = function() {
            pttBtn.style.background = 'var(--primary)';
            pttBtn.innerText = '🎤 Push to Talk';
            voiceTranscript.innerText = "Error accessing microphone.";
        };
    } else {
        pttBtn.style.display = 'none';
        voiceTranscript.innerText = "Web Speech API not supported in this browser.";
    }
}
