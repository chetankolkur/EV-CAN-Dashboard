const dashboardView = document.getElementById("dashboardView");
const liveView = document.getElementById("liveView");
const idsView = document.getElementById("idsView");
const pageTitle = document.getElementById("pageTitle");

const connectionValue = document.getElementById("connectionValue");
const connectionDetail = document.getElementById("connectionDetail");
const connectionText = document.getElementById("connectionText");
const statusDot = document.getElementById("statusDot");
const metricStatusDot = document.getElementById("metricStatusDot");
const frameCount = document.getElementById("frameCount");
const uniqueIdCount = document.getElementById("uniqueIdCount");
const decodedCount = document.getElementById("decodedCount");
const dbcSummary = document.getElementById("dbcSummary");
const dbcPill = document.getElementById("dbcPill");
const dbcText = document.getElementById("dbcText");
const bannerTitle = document.getElementById("bannerTitle");
const bannerMessage = document.getElementById("bannerMessage");
const connectionBanner = document.getElementById("connectionBanner");

const dashboardTable = document.getElementById("dashboardTable");
const bmsSignalGrid = document.getElementById("bmsSignalGrid");
const bmsSignalCount = document.getElementById("bmsSignalCount");
const liveTable = document.getElementById("liveTable");
const idsTable = document.getElementById("idsTable");
const searchInput = document.getElementById("searchInput");
const decodeFilter = document.getElementById("decodeFilter");
const idSearchInput = document.getElementById("idSearchInput");
const visibleCount = document.getElementById("visibleCount");
const idVisibleCount = document.getElementById("idVisibleCount");

let allFrames = [];
let allUniqueIds = [];
let clearedBefore = 0;
let isUpdating = false;
let lastIdsSignature = "";
let lastErrorState = false;

// Per-signal history collected from real decoded CAN frames in this browser session.
const signalHistory = new Map();
let signalTimestampOffset = null;
const MAX_SIGNAL_HISTORY_MS = 60 * 60 * 1000;
const MAX_SIGNAL_HISTORY_POINTS = 3600;
const GRAPH_REFRESH_INTERVAL_MS = 1000;
let lastGraphRefreshAt = 0;

const UI_REFRESH_INTERVAL_MS = 250;

// Navigation
document.querySelectorAll(".nav-button").forEach(button => {
    button.addEventListener("click", () => {
        const selectedView = button.dataset.view;

        dashboardView.hidden = selectedView !== "dashboard";
        liveView.hidden = selectedView !== "live";
        idsView.hidden = selectedView !== "ids";

        const titles = {
            dashboard: "Live CAN Monitor",
            live: "Live CAN monitor",
            ids: "Observed CAN IDs"
        };

        pageTitle.textContent = titles[selectedView] || "CAN overview";

        document.querySelectorAll(".nav-button").forEach(item => {
            item.classList.toggle("active", item === button);
        });

        if (selectedView === "ids") renderUniqueIds();
    });
});

const openLiveButton = document.getElementById("openLiveButton");

if (openLiveButton) {
    openLiveButton.addEventListener("click", () => {
        const liveNavButton = document.querySelector('[data-view="live"]');
        if (liveNavButton) liveNavButton.click();
    });
}

const refreshNowButton = document.getElementById("refreshNowButton");

if (refreshNowButton) {
    refreshNowButton.addEventListener("click", updateDashboard);
}

document.getElementById("clearButton").addEventListener("click", () => {
    clearedBefore = Date.now() / 1000;
    searchInput.value = "";
    renderFrames();
});

searchInput.addEventListener("input", renderFrames);
decodeFilter.addEventListener("change", renderFrames);
idSearchInput.addEventListener("input", renderUniqueIds);

function formatTime(timestamp) {
    if (!timestamp) return "--";
    return new Date(Number(timestamp) * 1000).toLocaleTimeString();
}

function displayValue(value) {
    if (value === null || value === undefined) {
        return "—";
    }

    if (typeof value === "string") {
        const trimmed = value.trim();

        if (
            trimmed.startsWith("{") ||
            trimmed.startsWith("[")
        ) {
            try {
                const parsed = JSON.parse(trimmed);

                if (parsed && typeof parsed === "object") {
                    if (parsed.name !== undefined) {
                        return String(parsed.name);
                    }

                    if (parsed.value !== undefined) {
                        return displayValue(parsed.value);
                    }
                }
            } catch (error) {
                // Keep the original text if it is not valid JSON.
            }
        }

        return value;
    }

    if (typeof value === "number") {
        return Number.isInteger(value)
            ? String(value)
            : String(Number(value.toFixed(4)));
    }

    if (typeof value === "object") {
        if (value.name !== undefined) {
            return String(value.name);
        }

        if (value.value !== undefined) {
            return displayValue(value.value);
        }

        return JSON.stringify(value);
    }

    return String(value);
}

function decodedSummary(frame) {
    if (!frame.decoded || frame.decoded.length === 0) {
        if (frame.decode_status === "dbc_unavailable") {
            return "DBC unavailable";
        }

        if (frame.decode_status === "format_mismatch") {
            return "CAN format mismatch";
        }

        if (
            frame.decode_status &&
            frame.decode_status.startsWith("error:")
        ) {
            return "Decode error";
        }

        return "No DBC match";
    }

    return frame.decoded.map(signal => {
        const unit = signal.unit ? ` ${signal.unit}` : "";

        return `${displayValue(signal.value)}${unit}`;
    }).join(" · ");
}

function setCellText(cell, value) {
    const text =
        value === null || value === undefined
            ? "—"
            : String(value);

    if (cell.textContent !== text) {
        cell.textContent = text;
    }
}

function frameKey(frame) {
    return [
        frame.timestamp ?? "",
        frame.can_id ?? "",
        frame.data ?? "",
        frame.dlc ?? "",
        frame.decode_status ?? ""
    ].join("|");
}

function frameRowSignature(frame, includeFormat) {
    return JSON.stringify({
        timestamp: formatTime(frame.timestamp),
        can_id: frame.can_id,
        message_name: frame.message_name || "—",
        dlc: frame.dlc,
        data: frame.data || "—",
        decoded: decodedSummary(frame),
        extended: Boolean(frame.extended),
        decode_status: frame.decode_status || "unmatched",
        includeFormat
    });
}

function makeFrameRow(frame, includeFormat = false) {
    const row = document.createElement("tr");

    row.dataset.frameKey = frameKey(frame);
    row.dataset.signature = "";

    const cellCount = includeFormat ? 7 : 6;

    for (let i = 0; i < cellCount; i++) {
        row.appendChild(document.createElement("td"));
    }

    updateFrameRow(row, frame, includeFormat);

    return row;
}

function updateFrameRow(row, frame, includeFormat = false) {
    const signature = frameRowSignature(frame, includeFormat);

    if (row.dataset.signature === signature) return;

    const cells = row.cells;

    setCellText(cells[0], formatTime(frame.timestamp));
    cells[0].className = "timestamp-cell";

    setCellText(cells[1], frame.can_id);
    cells[1].className = "can-id-cell";

    setCellText(cells[2], frame.message_name || "—");
    cells[2].className = "message-cell";

    setCellText(cells[3], frame.dlc);

    setCellText(cells[4], frame.data || "—");
    cells[4].className = "raw-cell";

    setCellText(cells[5], decodedSummary(frame));
    cells[5].className = "decoded-cell";

    if (includeFormat && cells[6]) {
        setCellText(
            cells[6],
            frame.extended
                ? "Extended · 29-bit"
                : "Standard · 11-bit"
        );

        cells[6].className = "format-cell";
    }

    const status = frame.decode_status || "unmatched";

    row.classList.toggle("row-decoded", status === "decoded");
    row.classList.toggle("row-raw", status !== "decoded");

    row.dataset.frameKey = frameKey(frame);
    row.dataset.signature = signature;
}

function showEmpty(table, columns, message) {
    const existing = table.firstElementChild;

    if (
        table.children.length === 1 &&
        existing?.dataset.emptyMessage === message
    ) {
        return;
    }

    const row = document.createElement("tr");
    const cell = document.createElement("td");

    cell.colSpan = columns;
    cell.className = "empty";
    cell.textContent = message;

    row.dataset.emptyMessage = message;
    row.appendChild(cell);

    table.replaceChildren(row);
}

// Reuse table rows instead of rebuilding the entire table.

// Reuse table rows instead of rebuilding the entire table.

function reconcileFrameTable(
    table,
    frames,
    includeFormat,
    emptyMessage,
    columns
) {
    if (frames.length === 0) {
        showEmpty(table, columns, emptyMessage);
        return;
    }

    const existingRows = new Map();

    for (const row of table.querySelectorAll("tr[data-frame-key]")) {
        existingRows.set(row.dataset.frameKey, row);
    }

    // Remove placeholder rows before rendering real frames.
    for (const row of Array.from(table.querySelectorAll("tr"))) {
        if (!row.dataset.frameKey) {
            row.remove();
        }
    }

    const wantedKeys = new Set();
    let previousRow = null;

    for (const frame of frames) {
        const key = frameKey(frame);
        wantedKeys.add(key);

        let row = existingRows.get(key);

        if (!row) {
            row = makeFrameRow(frame, includeFormat);
        } else {
            updateFrameRow(row, frame, includeFormat);
        }

        if (previousRow === null) {
            if (table.firstElementChild !== row) {
                table.insertBefore(row, table.firstElementChild);
            }
        } else if (previousRow.nextElementSibling !== row) {
            table.insertBefore(row, previousRow.nextElementSibling);
        }

        previousRow = row;
    }

    for (const [key, row] of existingRows) {
        if (!wantedKeys.has(key)) {
            row.remove();
        }
    }
}


function renderFrames() {
    const recentFrames = allFrames.filter(
        frame => Number(frame.timestamp) > clearedBefore
    );

    const query = searchInput.value.trim().toLowerCase();
    const selectedStatus = decodeFilter.value;

    const filteredFrames = recentFrames.filter(frame => {
        const searchable = [
            frame.can_id,
            frame.data,
            frame.message_name,
            decodedSummary(frame),
            frame.decode_status
        ].join(" ").toLowerCase();

        const queryMatches = searchable.includes(query);

        const statusMatches =
            selectedStatus === "all" ||
            frame.decode_status === selectedStatus ||
            (
                selectedStatus === "unmatched" &&
                [
                    "unmatched",
                    "no_signals",
                    "format_mismatch"
                ].includes(frame.decode_status)
            );

        return queryMatches && statusMatches;
    });

    reconcileFrameTable(
        dashboardTable,
        recentFrames.slice(0, 12),
        false,
        "No frames received yet. Connect the PCAN adapter to see live data.",
        6
    );

    reconcileFrameTable(
        liveTable,
        filteredFrames,
        true,
        query || selectedStatus !== "all"
            ? "No frames match these filters."
            : "Waiting for CAN frames…",
        7
    );

    const countText =
        `${filteredFrames.length} frame` +
        `${filteredFrames.length === 1 ? "" : "s"}`;

    if (visibleCount.textContent !== countText) {
        visibleCount.textContent = countText;
    }
}

function signalPriority(name) {
    const n = String(name)
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "");

    if (n.includes("soc") || n.includes("stateofcharge")) return 100;
    if (n.includes("packvoltage") || n.includes("batteryvoltage")) return 95;
    if (n.includes("packcurrent") || n.includes("batterycurrent")) return 90;
    if (n.includes("mincellvoltage") || n.includes("maxcellvoltage")) return 85;
    if (n.includes("packtemperature") || n.includes("batterytemperature")) return 80;
    if (n.includes("soh") || n.includes("stateofhealth")) return 75;
    if (n.includes("capacity")) return 70;
    if (n.includes("cell") && n.includes("volt")) return 65;
    if (n.includes("temperature") || n.includes("temp")) return 55;
    if (n.includes("current")) return 50;
    if (n.includes("voltage") || n.includes("volt")) return 45;

    return 10;
}

function numericSignalValue(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;

    if (typeof value === "string") {
        const trimmed = value.trim();
        if (!trimmed) return null;

        // DBC enumeration values may arrive as JSON strings, e.g. {"name":"Off","value":0}.
        if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
            try {
                const parsed = JSON.parse(trimmed);
                if (parsed && typeof parsed === "object" && parsed.value !== undefined) {
                    return numericSignalValue(parsed.value);
                }
            } catch (error) {
                // Fall through to numeric parsing.
            }
        }

        const number = Number(trimmed);
        return Number.isFinite(number) ? number : null;
    }

    if (value && typeof value === "object" && value.value !== undefined) {
        return numericSignalValue(value.value);
    }

    return null;
}

function recordSignalHistory(frames) {
    const now = Date.now();

    for (const frame of frames) {
        if (!Array.isArray(frame.decoded)) continue;

        const rawTimestamp = Number(frame.timestamp);
        if (!Number.isFinite(rawTimestamp) || rawTimestamp <= 0) continue;

        let timestamp;

if (rawTimestamp < 1e12) {
    if (signalTimestampOffset === null) {
        signalTimestampOffset =
            Date.now() - rawTimestamp * 1000;
    }

    timestamp =
        rawTimestamp * 1000 + signalTimestampOffset;
} else {
    timestamp = rawTimestamp;
}
            

        const frameIdentity = [
            frame.timestamp,
            frame.can_id || "",
            frame.data || "",
            frame.dlc ?? ""
        ].join("|");

        for (const signal of frame.decoded) {
            if (!signal || !signal.name) continue;

            const value = numericSignalValue(signal.value);
            if (value === null) continue;

            if (!signalHistory.has(signal.name)) {
                signalHistory.set(signal.name, {
                    samples: [],
                    seen: new Set()
                });
            }

            const history = signalHistory.get(signal.name);
            const sampleKey =
                `${frameIdentity}|${signal.name}`;

            if (history.seen.has(sampleKey)) continue;

            history.seen.add(sampleKey);

            history.samples.push({
                timestamp,
                value,
                unit: signal.unit || "",
                key: sampleKey
            });
        }
    }

    const cutoff = now - MAX_SIGNAL_HISTORY_MS;

    for (const [name, history] of signalHistory) {
        history.samples = history.samples.filter(sample => {
            if (sample.timestamp >= cutoff) return true;

            history.seen.delete(sample.key);
            return false;
        });

        if (history.samples.length > MAX_SIGNAL_HISTORY_POINTS) {
            const removed = history.samples.splice(
                0,
                history.samples.length - MAX_SIGNAL_HISTORY_POINTS
            );

            removed.forEach(sample => history.seen.delete(sample.key));
        }

        if (history.samples.length === 0) {
            signalHistory.delete(name);
        }
    }
}

function ensureSignalGraphStyles() {
    if (document.getElementById("signalGraphStyles")) return;

    const style = document.createElement("style");
    style.id = "signalGraphStyles";
    style.textContent = `
        .bms-signal-card { cursor: pointer; }
        .bms-signal-card:focus-visible { outline: 2px solid #3975f6; outline-offset: 3px; }
        .bms-signal-card.is-graph-open { grid-column: 1 / -1; cursor: default; }
        .signal-graph-panel { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border-color, #e2e8f0); cursor: default; }
        .signal-graph-panel[hidden] { display: none !important; }
        .signal-graph-heading { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 12px; }
        .signal-graph-title { font-weight: 700; font-size: 14px; }
        .signal-graph-subtitle { color: var(--muted, #64748b); font-size: 12px; margin-top: 3px; }
        .signal-graph-controls { display: flex; flex-wrap: wrap; gap: 5px; }
        .signal-graph-range { border: 1px solid #d8e0ea; background: transparent; color: inherit; border-radius: 7px; padding: 5px 9px; font: inherit; font-size: 12px; cursor: pointer; }
        .signal-graph-range.active { background: #eaf1ff; color: #2458c6; border-color: #9dbbff; }
        .signal-graph-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
        .signal-graph-stat { padding: 9px 10px; background: rgba(148,163,184,.10); border-radius: 8px; min-width: 0; }
        .signal-graph-stat-label { font-size: 11px; color: var(--muted, #64748b); margin-bottom: 4px; }
        .signal-graph-stat-value { font-weight: 700; font-size: 14px; overflow-wrap: anywhere; }
        .signal-graph-svg-wrap { width: 100%; min-height: 180px; }
        .signal-graph-svg { display: block; width: 100%; height: 190px; overflow: visible; }
        .signal-graph-empty { padding: 28px 12px; text-align: center; color: var(--muted, #64748b); font-size: 13px; }
        @media (max-width: 520px) { .signal-graph-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    `;
    document.head.appendChild(style);
}

function formatGraphNumber(value) {
    if (!Number.isFinite(value)) return "—";
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)));
}

function formatGraphTime(timestamp, rangeMs) {
    const date = new Date(timestamp);
    if (rangeMs <= 15 * 60 * 1000) {
        return date.toLocaleTimeString([], { minute: "2-digit", second: "2-digit" });
    }
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderSignalGraph(card, force = false) {
    const panel = card.querySelector(".signal-graph-panel");
    if (!panel || panel.hidden) return;

    const now = Date.now();
    const lastDraw = Number(panel.dataset.lastDraw || 0);
    if (!force && now - lastDraw < GRAPH_REFRESH_INTERVAL_MS) return;

    const signalName = card.dataset.signalName;
    const history = signalHistory.get(signalName);
    const rangeMs = Number(panel.dataset.rangeMs || 5 * 60 * 1000);
    const cutoff = now - rangeMs;
    const samples = history ? history.samples.filter(sample => sample.timestamp >= cutoff).sort((a, b) => a.timestamp - b.timestamp) : [];
    const unit = (samples.length && samples[samples.length - 1].unit) || card.dataset.signalUnit || "";

    panel.dataset.lastDraw = String(now);
    const title = panel.querySelector(".signal-graph-title");
    const subtitle = panel.querySelector(".signal-graph-subtitle");
    const chartWrap = panel.querySelector(".signal-graph-svg-wrap");
    const stats = panel.querySelector(".signal-graph-stats");

    title.textContent = `${signalName.replaceAll("_", " ")} — Live History`;
    subtitle.textContent = `${unit ? `Value (${unit})` : "Value"} versus time · ${samples.length} samples`;

    const statValues = samples.map(sample => sample.value);
    const current = statValues.length ? statValues[statValues.length - 1] : null;
    const min = statValues.length ? Math.min(...statValues) : null;

        const max = statValues.length ? Math.max(...statValues) : null;
    const avg = statValues.length ? statValues.reduce((sum, value) => sum + value, 0) / statValues.length : null;
    const statItems = [
        ["Current", current], ["Minimum", min], ["Maximum", max], ["Average", avg]
    ];
    stats.replaceChildren();
    for (const [label, value] of statItems) {
        const box = document.createElement("div");
        box.className = "signal-graph-stat";
        const labelNode = document.createElement("div");
        labelNode.className = "signal-graph-stat-label";
        labelNode.textContent = label;
        const valueNode = document.createElement("div");
        valueNode.className = "signal-graph-stat-value";
        valueNode.textContent = value === null ? "—" : `${formatGraphNumber(value)}${unit ? ` ${unit}` : ""}`;
        box.append(labelNode, valueNode);
        stats.appendChild(box);
    }

    chartWrap.replaceChildren();
    if (samples.length < 2) {
        const empty = document.createElement("div");
        empty.className = "signal-graph-empty";
        empty.textContent = samples.length === 1
            ? "Collecting more real CAN samples… The line will appear as new frames arrive."
            : "No numeric history for this signal in the selected time range yet.";
        chartWrap.appendChild(empty);
        return;
    }

    const width = 760;
    const height = 190;
    const left = 48;
    const right = 12;
    const top = 12;
    const bottom = 30;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    let low = min;
    let high = max;
    if (low === high) {
        const pad = Math.abs(low || 1) * 0.05;
        low -= pad;
        high += pad;
    } else {
        const pad = (high - low) * 0.08;
        low -= pad;
        high += pad;
    }

    const x = timestamp => left + ((timestamp - cutoff) / rangeMs) * plotWidth;
    const y = value => top + ((high - value) / (high - low)) * plotHeight;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${signalName} line chart`);
    svg.classList.add("signal-graph-svg");

    for (let i = 0; i <= 4; i++) {
        const fraction = i / 4;
        const gy = top + fraction * plotHeight;
        const value = high - fraction * (high - low);
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", left); line.setAttribute("x2", width - right);
        line.setAttribute("y1", gy); line.setAttribute("y2", gy);
        line.setAttribute("stroke", "#94a3b8"); line.setAttribute("stroke-opacity", "0.25");
        svg.appendChild(line);
        const label = document.createElementNS(ns, "text");
        label.setAttribute("x", left - 8); label.setAttribute("y", gy + 4);
        label.setAttribute("text-anchor", "end"); label.setAttribute("font-size", "10");
        label.setAttribute("fill", "currentColor"); label.setAttribute("opacity", "0.7");
        label.textContent = formatGraphNumber(value);
        svg.appendChild(label);
    }

    const path = document.createElementNS(ns, "path");
    const pathData = samples.map((sample, index) => `${index === 0 ? "M" : "L"}${x(sample.timestamp).toFixed(2)},${y(sample.value).toFixed(2)}`).join(" ");
    path.setAttribute("d", pathData);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#16a36a");
    path.setAttribute("stroke-width", "2.5");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-linecap", "round");
    svg.appendChild(path);

    const firstLabel = document.createElementNS(ns, "text");
    firstLabel.setAttribute("x", left); firstLabel.setAttribute("y", height - 7);
    firstLabel.setAttribute("font-size", "10"); firstLabel.setAttribute("fill", "currentColor");
    firstLabel.setAttribute("opacity", "0.7"); firstLabel.textContent = formatGraphTime(cutoff, rangeMs);
    svg.appendChild(firstLabel);

    const lastLabel = document.createElementNS(ns, "text");
    lastLabel.setAttribute("x", width - right); lastLabel.setAttribute("y", height - 7);
    lastLabel.setAttribute("text-anchor", "end"); lastLabel.setAttribute("font-size", "10");
    lastLabel.setAttribute("fill", "currentColor"); lastLabel.setAttribute("opacity", "0.7");
    lastLabel.textContent = "Now";
    svg.appendChild(lastLabel);
    chartWrap.appendChild(svg);
}

function renderOpenSignalGraphs(force = false) {
    const now = Date.now();
    if (!force && now - lastGraphRefreshAt < GRAPH_REFRESH_INTERVAL_MS) return;
    lastGraphRefreshAt = now;
    bmsSignalGrid?.querySelectorAll(".bms-signal-card.is-graph-open").forEach(card => renderSignalGraph(card, force));
}

function toggleSignalGraph(card) {
    const wasOpen = card.classList.contains("is-graph-open");
    bmsSignalGrid.querySelectorAll(".bms-signal-card.is-graph-open").forEach(openCard => {
        if (openCard !== card) {
            openCard.classList.remove("is-graph-open");
            openCard.setAttribute("aria-expanded", "false");
            const openPanel = openCard.querySelector(".signal-graph-panel");
            if (openPanel) openPanel.hidden = true;
        }
    });

    const panel = card.querySelector(".signal-graph-panel");
    if (!panel) return;
    panel.hidden = wasOpen;
    card.classList.toggle("is-graph-open", !wasOpen);
    card.setAttribute("aria-expanded", String(!wasOpen));
    if (!wasOpen) renderSignalGraph(card, true);
}

function createSignalGraphPanel(signal) {
    const panel = document.createElement("div");
    panel.className = "signal-graph-panel";
    panel.hidden = true;
    panel.dataset.rangeMs = String(5 * 60 * 1000);

    const heading = document.createElement("div");
    heading.className = "signal-graph-heading";
    const titleWrap = document.createElement("div");
    const title = document.createElement("div");
    title.className = "signal-graph-title";
    title.textContent = `${signal.name.replaceAll("_", " ")} — Live History`;
    const subtitle = document.createElement("div");
    subtitle.className = "signal-graph-subtitle";
    subtitle.textContent = "Value versus time";
    titleWrap.append(title, subtitle);

    const controls = document.createElement("div");
    controls.className = "signal-graph-controls";
    const ranges = [
        ["1 Min", 60 * 1000],
        ["5 Min", 5 * 60 * 1000],
        ["15 Min", 15 * 60 * 1000],
        ["1 Hour", 60 * 60 * 1000]
    ];
    for (const [label, milliseconds] of ranges) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `signal-graph-range${milliseconds === 5 * 60 * 1000 ? " active" : ""}`;
        button.textContent = label;
        button.addEventListener("click", event => {
            event.stopPropagation();
            panel.dataset.rangeMs = String(milliseconds);
            controls.querySelectorAll("button").forEach(item => item.classList.toggle("active", item === button));
            renderSignalGraph(cardForPanel(panel), true);
        });
        controls.appendChild(button);
    }
    heading.append(titleWrap, controls);

    const stats = document.createElement("div");
    stats.className = "signal-graph-stats";
    const chartWrap = document.createElement("div");
    chartWrap.className = "signal-graph-svg-wrap";
    panel.append(heading, stats, chartWrap);
    return panel;
}

function cardForPanel(panel) {
    return panel.closest(".bms-signal-card");
}

function createSignalCard(signal) {
    ensureSignalGraphStyles();

    const card = document.createElement("article");
    card.className = "bms-signal-card";
    card.dataset.signalName = signal.name;
    card.dataset.signalUnit = signal.unit || "";
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-expanded", "false");
    card.setAttribute("aria-label", `Show graph for ${signal.name.replaceAll("_", " ")}`);

    const label = document.createElement("div");
    label.className = "bms-signal-label";

    const value = document.createElement("div");
    value.className = "bms-signal-value";

    const source = document.createElement("div");
    source.className = "bms-signal-source";

    const hint = document.createElement("div");
    hint.className = "signal-graph-subtitle";
    hint.textContent = "Click to view graph ↗";

    card.append(label, value, source, hint);
    card.appendChild(createSignalGraphPanel(signal));

    card.addEventListener("click", event => {
        if (event.target.closest(".signal-graph-panel")) return;
        toggleSignalGraph(card);
    });
    card.addEventListener("keydown", event => {
        if (event.target !== card) return;
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggleSignalGraph(card);
        }
    });

    updateSignalCard(card, signal);
    return card;
}

function updateSignalCard(card, signal) {
    card.dataset.signalUnit = signal.unit || "";
    const label = signal.name.replaceAll("_", " ");
    const unit = signal.unit ? ` ${signal.unit}` : "";
    const value = `${displayValue(signal.value)}${unit}`;

    const source =
        `${signal.messageName} · ${signal.canId}`;

    const labelElement = card.querySelector(".bms-signal-label");
    const valueElement = card.querySelector(".bms-signal-value");
    const sourceElement = card.querySelector(".bms-signal-source");

    if (labelElement.textContent !== label) {
        labelElement.textContent = label;
    }

    if (valueElement.textContent !== value) {
        valueElement.textContent = value;
    }

    if (sourceElement.textContent !== source) {
        sourceElement.textContent = source;
    }

    card.dataset.signalSignature = JSON.stringify({
        label,
        value,
        source
    });
}

// Update BMS cards without rebuilding the entire grid.
function renderBmsSignals() {
    if (!bmsSignalGrid || !bmsSignalCount) return;

    const latest = new Map();

    for (const frame of allFrames) {
        if (
            frame.decode_status !== "decoded" ||
            !Array.isArray(frame.decoded)
        ) {
            continue;
        }

        for (const signal of frame.decoded) {
            if (
                !signal ||
                !signal.name ||
                latest.has(signal.name)
            ) {
                continue;
            }

            latest.set(signal.name, {
                name: signal.name,
                value: signal.value,
                unit: signal.unit || "",
                canId: frame.can_id || "",
                messageName: frame.message_name || "",
                timestamp: frame.timestamp || 0,
                priority: signalPriority(signal.name)
            });
        }
    }

    const signals = [...latest.values()].sort(
        (a, b) =>
            b.priority - a.priority ||
            a.name.localeCompare(b.name)
    );

    const countText =
        `${signals.length} signal` +
        `${signals.length === 1 ? "" : "s"}`;
    if (bmsSignalCount.textContent !== countText) {
        bmsSignalCount.textContent = countText;
    }

    if (signals.length === 0) {
        const existing =
            bmsSignalGrid.querySelector(".signal-empty");

        if (existing) return;

        bmsSignalGrid.replaceChildren();

        const empty = document.createElement("div");
        empty.className = "signal-empty";

        empty.textContent =
            "No decoded BMS signals received yet. " +
            "Check that CAN traffic is active and the IDs match the DBC.";

        bmsSignalGrid.appendChild(empty);
        return;
    }

    const empty =
        bmsSignalGrid.querySelector(".signal-empty");

    if (empty) empty.remove();

    const existingCards = new Map();

    bmsSignalGrid
        .querySelectorAll(".bms-signal-card")
        .forEach(card => {
            existingCards.set(card.dataset.signalName, card);
        });

    const wantedNames = new Set();

    signals.forEach((signal, index) => {
        wantedNames.add(signal.name);

        let card = existingCards.get(signal.name);

        if (!card) {
            card = createSignalCard(signal);
        } else {
            updateSignalCard(card, signal);
        }

        const currentAtIndex =
            bmsSignalGrid.children[index];

        if (currentAtIndex !== card) {
            bmsSignalGrid.insertBefore(
                card,
                currentAtIndex || null
            );
        }
    });

    for (const [name, card] of existingCards) {
        if (!wantedNames.has(name)) {
            card.remove();
        }
    }

    renderOpenSignalGraphs();
}

function renderUniqueIds() {
    const query = idSearchInput.value.trim().toLowerCase();

    const filteredIds = allUniqueIds.filter(
        canId => canId.toLowerCase().includes(query)
    );

    const countText =
        `${filteredIds.length} ID` +
        `${filteredIds.length === 1 ? "" : "s"}`;

    if (idVisibleCount.textContent !== countText) {
        idVisibleCount.textContent = countText;
    }

    const signature = JSON.stringify({
        ids: filteredIds,
        frames: allFrames.map(frame => [
            frame.can_id,
            frame.message_name,
            frame.decode_status
        ]),
        query
    });

    if (signature === lastIdsSignature) return;

    lastIdsSignature = signature;

    if (filteredIds.length === 0) {
        showEmpty(
            idsTable,
            5,
            query
                ? "No CAN IDs match your search."
                : "No CAN IDs received yet."
        );

        return;
    }

    const existingRows = new Map();

    idsTable
        .querySelectorAll("tr[data-can-id]")
        .forEach(row => {
            existingRows.set(row.dataset.canId, row);
        });

    for (
        const row of Array.from(
            idsTable.querySelectorAll("tr")
        )
    ) {
        if (!row.dataset.canId) {
            row.remove();
        }
    }

    const wantedIds = new Set();

    filteredIds.forEach((canId, index) => {
        wantedIds.add(canId);

        const numericId = Number.parseInt(canId, 16);

        const frameFormat =
            numericId <= 0x7FF
                ? "Standard · 11-bit"
                : "Extended · 29-bit";

        const matchingFrame =
            allFrames.find(frame => frame.can_id === canId);

        const hasMatch =
            matchingFrame &&
            ["decoded", "no_signals"].includes(
                matchingFrame.decode_status
            );

        let row = existingRows.get(canId);

        if (!row) {
            row = document.createElement("tr");
            row.dataset.canId = canId;

            for (let i = 0; i < 5; i++) {
                row.appendChild(
                    document.createElement("td")
                );
            }
        }

        setCellText(row.cells[0], index + 1);
        setCellText(row.cells[1], canId);
        row.cells[1].className = "can-id-cell";

        setCellText(row.cells[2], frameFormat);

        let badge =
            row.cells[3].querySelector(".decode-badge");

        if (!badge) {
            row.cells[3].replaceChildren();

            badge = document.createElement("span");
            badge.className = "decode-badge";

            row.cells[3].appendChild(badge);
        }

        badge.className =
            `decode-badge ${hasMatch ? "badge-good" : "badge-muted"}`;

        badge.textContent = hasMatch
            ? "Matched"
            : "Unmatched / not seen in current frames";

        setCellText(
            row.cells[4],
            matchingFrame
                ? matchingFrame.message_name
                : "Not available in recent frames"
        );

        row.cells[4].className = "message-cell";

        const currentAtIndex = idsTable.children[index];

        if (currentAtIndex !== row) {
            idsTable.insertBefore(
                row,
                currentAtIndex || null
            );
        }
    });

    for (const [canId, row] of existingRows) {
        if (!wantedIds.has(canId)) {
            row.remove();
        }
    }
}

async function updateDashboard() {
    if (isUpdating) return;

    isUpdating = true;

    try {
        const response = await fetch(
            "/api/can-data",
            { cache: "no-store" }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const result = await response.json();

        const connected = Boolean(
            result.status && result.status.connected
        );

        const connectionState =
            connected ? "Connected" : "Disconnected";

        if (connectionValue.textContent !== connectionState) {
            connectionValue.textContent = connectionState;
        }

        connectionDetail.textContent = connected
            ? "Receiving CAN frames"
            : "PCAN adapter not available";

        connectionText.textContent = connected
            ? "PCAN connected"
            : "PCAN disconnected";

        statusDot.className =
            `status-dot ${connected ? "connected" : "disconnected"}`;

        metricStatusDot.className =
            `status-dot ${connected ? "connected" : "disconnected"}`;

        const dbc = result.dbc || {};

        dbcPill.className =
            `dbc-pill ${dbc.loaded ? "dbc-ready" : "dbc-error"}`;

        dbcText.textContent = dbc.loaded
            ? `DBC loaded · ${dbc.message_count} messages`
            : "DBC not loaded";

        dbcSummary.textContent = dbc.loaded
            ? `${dbc.message_count} messages · ${dbc.signal_count} signals`
            : "Check local DBC path";

        bannerTitle.textContent = connected
            ? "CAN adapter connected"
            : "PCAN adapter is disconnected";

        bannerMessage.textContent =
            `${(result.status && result.status.message) || "No connection details"} ` +
            (
                dbc.loaded
                    ? `The local DBC is ready (${dbc.message_count} message definitions).`
                    : (dbc.message || "DBC status unavailable")
            );

        connectionBanner.className =
            `connection-banner ${
                connected
                    ? "banner-connected"
                    : "banner-warning"
            }`;

        allFrames = result.frames || [];
        allUniqueIds = result.unique_can_ids || [];
        recordSignalHistory(allFrames);

        frameCount.textContent =
            result.total_frames ?? allFrames.length;

        uniqueIdCount.textContent =
            result.total_unique_can_ids ?? allUniqueIds.length;

        decodedCount.textContent =
            result.decoded_frames ??
            allFrames.filter(
                frame => frame.decode_status === "decoded"
            ).length;

        renderFrames();
        renderUniqueIds();
        renderBmsSignals();

        lastErrorState = false;
    } catch (error) {
        connectionValue.textContent = "Backend offline";
        connectionDetail.textContent = "Start FastAPI to reconnect";
        connectionText.textContent = "Backend unavailable";

        statusDot.className = "status-dot disconnected";
        metricStatusDot.className = "status-dot disconnected";

        bannerTitle.textContent = "Cannot reach the backend";

        bannerMessage.textContent =
            "Check that Uvicorn is running and refresh this page.";

        connectionBanner.className =
            "connection-banner banner-warning";

        if (!lastErrorState) {
            console.error("Could not fetch CAN data:", error);
            lastErrorState = true;
        }
    } finally {
        isUpdating = false;
    }
}

// UI polling updates the dashboard; it does not change the CAN bus bitrate.
updateDashboard();

setInterval(
    updateDashboard,
    UI_REFRESH_INTERVAL_MS
);


/* ==========================================
   CAN Recording Controls
   ========================================== */

const recordingIndicator = document.getElementById("recordingIndicator");
const recordingStatus = document.getElementById("recordingStatus");
const recordingCount = document.getElementById("recordingCount");
const startRecordingBtn = document.getElementById("startRecordingBtn");
const stopRecordingBtn = document.getElementById("stopRecordingBtn");
const exportRecordingBtn = document.getElementById("exportRecordingBtn");

async function refreshRecordingStatus() {
    try {
        const response = await fetch("/api/recording/status");
        if (!response.ok) {
            throw new Error("Could not get recording status");
        }

        const status = await response.json();

        recordingStatus.textContent = status.recording
            ? "Recording in progress"
            : "Recording stopped";

        recordingCount.textContent =
            `${status.total_recorded} frames recorded`;

        recordingIndicator.classList.toggle("active", status.recording);
        startRecordingBtn.disabled = status.recording;
        stopRecordingBtn.disabled = !status.recording;
    } catch (error) {
        console.error("Recording status error:", error);
    }
}

startRecordingBtn.addEventListener("click", async () => {
    try {
        const response = await fetch("/api/recording/start", {
            method: "POST"
        });
        const result = await response.json();

        if (!response.ok || !result.success) {
            throw new Error(result.detail || result.message || "Could not start recording");
        }

        await refreshRecordingStatus();
    } catch (error) {
        alert(`Could not start recording: ${error.message}`);
    }
});

stopRecordingBtn.addEventListener("click", async () => {
    try {
        const response = await fetch("/api/recording/stop", {
            method: "POST"
        });
        const result = await response.json();

        if (!response.ok || !result.success) {
            throw new Error(result.detail || result.message || "Could not stop recording");
        }

        await refreshRecordingStatus();
    } catch (error) {
        alert(`Could not stop recording: ${error.message}`);
    }
});

exportRecordingBtn.addEventListener("click", async () => {
    try {
        const statusResponse = await fetch("/api/recording/status");
        const status = await statusResponse.json();

        if (status.recording) {
            alert("Please stop recording before exporting your CSV.");
            return;
        }

        if (status.total_recorded === 0) {
            alert("No recorded CAN frames available. Start recording first.");
            return;
        }

        const response = await fetch("/api/recording/export");

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || "CSV export failed");
        }

        const blob = await response.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");

        link.href = downloadUrl;
        link.download = "can_recording.csv";
        document.body.appendChild(link);
        link.click();
        link.remove();

        URL.revokeObjectURL(downloadUrl);
    } catch (error) {
        alert(`Could not export CSV: ${error.message}`);
    }
});

// Initialize the recording controls and refresh the frame count.
refreshRecordingStatus();
setInterval(refreshRecordingStatus, 1000);
