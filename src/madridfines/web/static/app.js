const MONTHS = [
  "",
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const GRADE_LABELS = {
  LEVE: "Minor",
  GRAVE: "Serious",
  "MUY GRAVE": "Very serious",
};

const state = {
  months: [],
  map: null,
  layer: null,
  timer: null,
};

const yearSelect = document.querySelector("#year");
const monthSelect = document.querySelector("#month");
const statusLine = document.querySelector("#status");
const emptyPanel = document.querySelector("#empty");
const emptyText = document.querySelector("#empty-text");
const dashboard = document.querySelector("#dashboard");
const downloadButton = document.querySelector("#download");
const refreshButton = document.querySelector("#refresh");

yearSelect.addEventListener("change", () => {
  fillMonths();
  showSelection();
});
monthSelect.addEventListener("change", showSelection);
refreshButton.addEventListener("click", () => syncSelection(true));
downloadButton.addEventListener("click", () => syncSelection(false));
document.querySelector("#search").addEventListener("input", (event) => {
  window.clearTimeout(state.timer);
  state.timer = window.setTimeout(() => loadPlaces(event.target.value), 250);
});

boot();

async function boot() {
  await loadCatalog();
}

async function loadCatalog() {
  const response = await fetch("/api/catalog");
  const payload = await response.json();
  state.months = payload.months || [];
  if (payload.error) {
    statusLine.textContent = payload.error;
  }
  fillYears();
  const local = state.months.filter((item) => item.local);
  const preferred = local.length ? local[local.length - 1] : payload.latest;
  if (preferred) {
    yearSelect.value = String(preferred.year);
    fillMonths();
    monthSelect.value = String(preferred.month);
  }
  await showSelection();
}

function fillYears() {
  const years = [...new Set(state.months.map((item) => item.year))].sort((a, b) => b - a);
  yearSelect.replaceChildren();
  years.forEach((year) => yearSelect.append(option(year, year)));
  if (!years.length) {
    yearSelect.append(option("", "—"));
  }
}

function fillMonths() {
  const year = Number(yearSelect.value);
  const months = state.months
    .filter((item) => item.year === year)
    .map((item) => item.month)
    .sort((a, b) => b - a);
  const current = monthSelect.value;
  monthSelect.replaceChildren();
  months.forEach((month) => monthSelect.append(option(month, MONTHS[month])));
  if (months.includes(Number(current))) {
    monthSelect.value = current;
  }
}

function option(value, label) {
  const node = document.createElement("option");
  node.value = String(value);
  node.textContent = String(label);
  return node;
}

function selectedPeriod() {
  const year = Number(yearSelect.value);
  const month = Number(monthSelect.value);
  if (!year || !month) {
    return null;
  }
  return { year, month };
}

async function showSelection() {
  const period = selectedPeriod();
  if (!period) {
    showEmpty("There is no month on this computer. The City Council publishes the detail about three months late.");
    downloadButton.dataset.scope = "latest";
    downloadButton.textContent = "Download the latest month";
    return;
  }
  const response = await fetch(`/api/summary?year=${period.year}&month=${period.month}`);
  if (!response.ok) {
    showEmpty(`${labelOf(period)} is not downloaded.`);
    downloadButton.dataset.scope = "month";
    downloadButton.textContent = "Download this month";
    return;
  }
  const summary = await response.json();
  emptyPanel.hidden = true;
  dashboard.hidden = false;
  document.querySelector("#count").textContent = formatNumber(summary.num_multas);
  document.querySelector("#collected").textContent = formatEuro(summary.recaudado);
  const discounted = summary.con_descuento || 0;
  document.querySelector("#collected-note").textContent =
    `${formatNumber(discounted)} ${discounted === 1 ? "fine" : "fines"} eligible for the discount, counted at 50%. The file does not say whether they were paid. The rest are left out of this figure.`;
  document.querySelector("#period-label").textContent = labelOf(period);
  renderGrades(summary.calificacion || []);
  renderMoney(summary);
  const hours = await fetchJson(`/api/by-hour?year=${period.year}&month=${period.month}`);
  drawRoad(hours);
  const reporters = await fetchJson(`/api/by-denunciante?year=${period.year}&month=${period.month}`);
  drawReporters(reporters);
  await loadPlaces(document.querySelector("#search").value);
  await loadMap(period);
}

function showEmpty(text) {
  dashboard.hidden = true;
  emptyPanel.hidden = false;
  emptyText.textContent = text;
}

function labelOf(period) {
  return `${MONTHS[period.month]} ${period.year}`;
}

function fold(value) {
  return String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase()
    .trim();
}

function gradeLabel(code) {
  return GRADE_LABELS[fold(code)] || code;
}

function gradeClass(code) {
  const value = fold(code);
  if (value.includes("MUY")) {
    return "muy";
  }
  if (value === "LEVE" || value === "GRAVE") {
    return value.toLowerCase();
  }
  return "other";
}

function reporterLabel(name) {
  const key = fold(name);
  if (key === "SER") {
    return "Regulated parking (SER)";
  }
  if (key.includes("POLICIA MUNICIPAL")) {
    return "Municipal Police";
  }
  if (key.includes("RADAR")) {
    return "Mobility radar";
  }
  if (key.includes("MOVILIDAD")) {
    return "Mobility officers";
  }
  if (key.includes("CAPTACION") || key.includes("IMAGEN") || key.includes("CAMARA")) {
    return "Camera enforcement";
  }
  if (key.includes("SACE")) {
    return "Parking control (SACE)";
  }
  if (key === "NO REPORTER" || key.includes("SIN DENUNCIANTE")) {
    return "No reporter listed";
  }
  return name;
}

function drawReporters(rows) {
  const host = document.querySelector("#reporters");
  host.replaceChildren();
  const max = Math.max(...rows.map((item) => item.num_multas), 1);
  rows.forEach((item) => {
    const row = document.createElement("div");
    row.className = "reporter";
    const width = Math.max((item.num_multas / max) * 100, item.num_multas ? 1.5 : 0);
    row.innerHTML = `
      <span class="reporter-name">${escapeHtml(reporterLabel(item.denunciante))}</span>
      <span class="reporter-track"><span class="reporter-bar" style="width:${width}%"></span></span>
      <span class="reporter-count">${formatNumber(item.num_multas)}</span>`;
    host.append(row);
  });
}

function renderGrades(grades) {
  const host = document.querySelector("#grades");
  host.replaceChildren();
  grades.forEach((grade) => {
    const row = document.createElement("p");
    row.className = `grade ${gradeClass(grade.calificacion)}`;
    row.innerHTML = `<span>${escapeHtml(gradeLabel(grade.calificacion))}</span><span>${formatNumber(grade.num_multas)}</span>`;
    host.append(row);
  });
}

function renderMoney(summary) {
  const host = document.querySelector("#money");
  host.replaceChildren();
  host.append(moneyRow("Full amount", summary.importe_max));
  host.append(moneyRow("With early payment", summary.importe_min));
}

function moneyRow(name, amount) {
  const row = document.createElement("p");
  row.className = "money-row";
  row.innerHTML = `<span>${name}</span><span>${formatEuro(amount)}</span>`;
  return row;
}

function drawRoad(hours) {
  const lane = document.querySelector("#lane");
  const ticks = document.querySelector("#ticks");
  const max = Math.max(...hours.map((item) => item.num_multas), 1);
  lane.replaceChildren();
  hours.forEach((item) => {
    const bar = document.createElement("span");
    bar.className = "traffic";
    const ratio = item.num_multas / max;
    bar.style.height = `${Math.max(ratio * 100, item.num_multas ? 4 : 0)}%`;
    bar.title = `${String(item.hora).padStart(2, "0")}:00 · ${formatNumber(item.num_multas)}`;
    lane.append(bar);
  });
  ticks.replaceChildren();
  [0, 6, 12, 18, 23].forEach((hour) => {
    const mark = document.createElement("span");
    mark.textContent = String(hour);
    ticks.append(mark);
  });
}

async function loadPlaces(query) {
  const period = selectedPeriod();
  if (!period || dashboard.hidden) {
    return;
  }
  const rows = await fetchJson(
    `/api/places?year=${period.year}&month=${period.month}&q=${encodeURIComponent(query || "")}`,
  );
  const body = document.querySelector("#places");
  body.replaceChildren();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(row.lugar)}</td><td>${formatNumber(row.num_multas)}</td><td>${formatEuro(row.importe)}</td>`;
    body.append(tr);
  });
}

async function loadMap(period) {
  const points = await fetchJson(`/api/map?year=${period.year}&month=${period.month}`);
  if (typeof L === "undefined") {
    return;
  }
  if (!state.map) {
    state.map = L.map("map", { zoomControl: true }).setView([40.4168, -3.7038], 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
      maxZoom: 19,
    }).addTo(state.map);
    state.layer = L.layerGroup().addTo(state.map);
  }
  state.layer.clearLayers();
  if (!points.length) {
    state.map.setView([40.4168, -3.7038], 12);
    return;
  }
  const bounds = [];
  points.forEach((point) => {
    const radius = Math.max(4, Math.min(18, Math.sqrt(point.num_multas)));
    const marker = L.circleMarker([point.lat, point.lon], {
      radius,
      color: "#e8a317",
      weight: 1,
      fillColor: "#e8a317",
      fillOpacity: 0.75,
    });
    marker.bindPopup(
      `${escapeHtml(point.lugar)}<br>${formatNumber(point.num_multas)} fines<br>${formatEuro(point.importe)}`,
    );
    marker.addTo(state.layer);
    bounds.push([point.lat, point.lon]);
  });
  state.map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
  window.setTimeout(() => state.map.invalidateSize(), 0);
}

async function syncSelection(refresh) {
  const period = selectedPeriod();
  const body = { refresh };
  const downloadingLatest = !period || downloadButton.dataset.scope === "latest";
  if (period && !downloadingLatest) {
    body.year = period.year;
    body.month = period.month;
  }
  if (refresh && period) {
    body.year = period.year;
    body.month = period.month;
  }
  setBusy(true);
  statusLine.textContent = "Preparing the download…";
  const response = await fetch("/api/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    statusLine.textContent = payload.detail || "Could not start the download";
    setBusy(false);
    return;
  }
  await poll(payload.year, payload.month);
}

async function poll(year, month) {
  const report = await fetchJson("/api/status");
  if (report.running) {
    statusLine.textContent = report.message || `Downloading ${MONTHS[month]} ${year}…`;
    window.setTimeout(() => poll(year, month), 1000);
    return;
  }
  setBusy(false);
  if (report.error) {
    statusLine.textContent = report.error;
    return;
  }
  statusLine.textContent = `${MONTHS[month]} ${year} is on this computer.`;
  await loadCatalog();
  yearSelect.value = String(year);
  fillMonths();
  monthSelect.value = String(month);
  await showSelection();
}

function setBusy(busy) {
  refreshButton.disabled = busy;
  downloadButton.disabled = busy;
  yearSelect.disabled = busy;
  monthSelect.disabled = busy;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    return [];
  }
  return response.json();
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-GB").format(value || 0);
}

function formatEuro(value) {
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(value || 0);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
