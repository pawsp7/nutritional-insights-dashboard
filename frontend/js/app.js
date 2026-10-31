// Nutritional Insights dashboard: talks to the Azure Function API and draws the charts.
(() => {
    "use strict";

    const query = new URLSearchParams(location.search);
    const API = (query.get("api") || window.APP_CONFIG?.apiBaseUrl || "/api").replace(/\/+$/, "");

    const MACROS = ["protein", "carbs", "fat"];
    const MACRO_COLORS = { protein: "#2563eb", carbs: "#d97706", fat: "#db2777" };
    const DIET_COLORS = { dash: "#0891b2", keto: "#7c3aed", mediterranean: "#059669", paleo: "#ea580c", vegan: "#65a30d" };
    const PALETTE = ["#2563eb", "#d97706", "#db2777", "#059669", "#7c3aed", "#0891b2", "#ea580c", "#65a30d", "#9ca3af"];

    const state = {
        diet: "all", search: "", sort: "protein", order: "desc", page: 1, pageSize: 10, k: 3,
        recipesOpen: false, clustersOpen: false, scatter: [], log: [],
    };
    const charts = {};
    const latest = {}; // newest request number per endpoint, so slow stale replies are ignored
    const $ = (id) => document.getElementById(id);

    const cap = (s) => String(s).replace(/\b\w/g, (c) => c.toUpperCase());
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const fmt = (n, digits = 1) => (n === null || n === undefined ? "–" : Number(n).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }));
    const dietColor = (diet, i = 0) => DIET_COLORS[diet] || PALETTE[i % PALETTE.length];
    // log axes: label only 0.1, 1, 10, 100… so the ticks stay readable
    const logAxis = (text) => ({
        type: "logarithmic",
        title: { display: true, text },
        ticks: { maxRotation: 0, callback: (v) => (Number.isInteger(Math.log10(v)) ? Number(v).toLocaleString() : "") },
    });

    // ---------- API ----------

    async function api(endpoint, params = {}) {
        const qs = new URLSearchParams(
            Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined && v !== "all")
        ).toString();
        const url = `${API}/${endpoint}${qs ? `?${qs}` : ""}`;
        const started = performance.now();
        let res;
        try {
            res = await fetch(url, { headers: { Accept: "application/json" } });
        } catch (err) {
            logRequest(endpoint, qs, "network error", null, performance.now() - started, null);
            setStatus("error", "Cannot reach the Azure Function");
            throw new Error(`Could not reach ${url}. Check that the Function App is running and that its CORS settings allow ${location.origin}.`);
        }
        let body;
        try {
            body = await res.json();
        } catch {
            body = { error: `Unexpected non-JSON response (HTTP ${res.status}) from ${url}` };
        }
        const roundTrip = performance.now() - started;
        logRequest(endpoint, qs, res.status, body.meta?.executionTimeMs, roundTrip, body.meta?.cache);
        if (body.meta) showMeta(body.meta, roundTrip);
        if (!res.ok) {
            if (res.status >= 500) setStatus("error", "Azure Function returned an error");
            throw new Error(body.error || `HTTP ${res.status}`);
        }
        setStatus("ok", `Connected · ${new URL(url, location.href).host}`);
        return body;
    }

    function setStatus(kind, text) {
        $("statusDot").className = `status-dot status-${kind}`;
        $("statusText").textContent = text;
    }

    function showAlert(message) {
        $("alert").textContent = message;
        $("alert").classList.remove("hidden");
    }

    function hideAlert() {
        $("alert").classList.add("hidden");
    }

    function showMeta(meta, roundTrip) {
        $("metaExec").textContent = `${fmt(meta.executionTimeMs)} ms${meta.coldStart ? " (cold start)" : ""}`;
        $("metaRoundTrip").textContent = `${fmt(roundTrip, 0)} ms`;
        if (meta.cache) {
            $("metaCache").textContent = meta.cache === "hit"
                ? "Warm cache hit"
                : `Read from Blob (${fmt(meta.dataset?.blobLoadMs, 0)} ms)`;
        }
        if (meta.dataset) {
            const d = meta.dataset;
            $("metaBlob").textContent = `${d.storageAccount}/${d.container}/${d.blob}`;
            $("metaBlob").title = `${d.cleanRows.toLocaleString()} rows · ${(d.sizeBytes / 1024).toFixed(0)} KB · ETag ${d.etag}`;
            $("metaBlobModified").textContent = d.blobLastModified ? new Date(d.blobLastModified).toLocaleString() : "–";
        }
        const inst = meta.instance || {};
        $("metaInstance").textContent = `${inst.app} · ${inst.region}`;
        $("metaInstance").title = `Python ${inst.python} · generated ${meta.generatedAt}`;
    }

    function logRequest(endpoint, qs, status, fnMs, roundTrip, cache) {
        state.log.unshift({ time: new Date(), request: `GET /${endpoint}${qs ? `?${decodeURIComponent(qs)}` : ""}`, status, fnMs, roundTrip, cache });
        state.log.length = Math.min(state.log.length, 10);
        $("logBody").innerHTML = state.log.map((r) => {
            const ok = typeof r.status === "number" && r.status < 400;
            return `<tr class="border-t">
                <td class="px-4 py-2 whitespace-nowrap">${r.time.toLocaleTimeString()}</td>
                <td class="px-4 py-2"><code>${esc(r.request)}</code></td>
                <td class="px-4 py-2 font-semibold ${ok ? "text-green-700" : "text-red-700"}">${esc(r.status)}</td>
                <td class="px-4 py-2 text-right">${fmt(r.fnMs)}</td>
                <td class="px-4 py-2 text-right">${fmt(r.roundTrip, 0)}</td>
                <td class="px-4 py-2">${esc(r.cache ?? "–")}</td>
            </tr>`;
        }).join("");
    }

    function busy(id, on) {
        $(id).disabled = on;
    }

    // ---------- charts ----------

    function drawChart(key, canvasId, config) {
        if (charts[key]) charts[key].destroy();
        charts[key] = new Chart($(canvasId), config);
    }

    function renderSummary(s) {
        $("sumRecipes").textContent = s.recipes.toLocaleString();
        $("sumRecipesSub").textContent = `${s.dietTypes} diet type${s.dietTypes === 1 ? "" : "s"} · ${s.cuisines} cuisines`;
        if (state.diet === "all") {
            $("sumTopLabel").textContent = "Highest-protein diet";
            $("sumTopDiet").textContent = cap(s.highestProteinDiet.diet);
            $("sumTopDietSub").textContent = `${fmt(s.highestProteinDiet.avgProtein)} g protein per recipe on average`;
        } else {
            $("sumTopLabel").textContent = "Highest-protein recipe";
            $("sumTopDiet").textContent = `${fmt(s.highestProteinRecipe.protein, 0)} g`;
            $("sumTopDietSub").textContent = s.highestProteinRecipe.name;
        }
        $("sumMacros").textContent = `${fmt(s.avgProtein, 0)} / ${fmt(s.avgCarbs, 0)} / ${fmt(s.avgFat, 0)}`;
        $("sumRatio").textContent = fmt(s.medianProteinToCarbs, 2);
        $("sumRatioSub").textContent = `median carbs : fat is ${fmt(s.medianCarbsToFat, 2)}`;
    }

    function renderBar(avg) {
        drawChart("bar", "barChart", {
            type: "bar",
            data: {
                labels: avg.labels.map(cap),
                datasets: MACROS.map((m) => ({ label: cap(m), data: avg.series[m], backgroundColor: MACRO_COLORS[m], borderRadius: 3 })),
            },
            options: {
                maintainAspectRatio: false,
                scales: { y: { beginAtZero: true, title: { display: true, text: "Average grams per recipe" } } },
                plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y)} g` } } },
            },
        });
    }

    function renderScatter() {
        const log = $("scatterLog").checked;
        const byDiet = {};
        for (const p of state.scatter) {
            if (log && (p.protein <= 0 || p.carbs <= 0)) continue; // log axes cannot show zero
            (byDiet[p.diet] ||= []).push({ x: p.protein, y: p.carbs, name: p.name });
        }
        const axis = (text) => (log ? logAxis(`${text}, log scale`) : { beginAtZero: true, title: { display: true, text } });
        drawChart("scatter", "scatterPlot", {
            type: "scatter",
            data: {
                datasets: Object.entries(byDiet).map(([diet, data], i) => ({
                    label: cap(diet), data, backgroundColor: `${dietColor(diet, i)}99`, borderColor: dietColor(diet, i), pointRadius: 3,
                })),
            },
            options: {
                maintainAspectRatio: false,
                scales: { x: axis("Protein (g)"), y: axis("Carbs (g)") },
                plugins: { tooltip: { callbacks: { label: (c) => `${c.raw.name}: ${fmt(c.raw.x)} g protein, ${fmt(c.raw.y)} g carbs` } } },
            },
        });
    }

    function heatColor(v) {
        // -1 → red, 0 → light gray, +1 → blue
        const mix = (a, b, t) => Math.round(a + (b - a) * t);
        const [from, to] = v >= 0 ? [[243, 244, 246], [29, 78, 216]] : [[243, 244, 246], [220, 38, 38]];
        const t = Math.min(1, Math.abs(v));
        return `rgb(${mix(from[0], to[0], t)}, ${mix(from[1], to[1], t)}, ${mix(from[2], to[2], t)})`;
    }

    function renderHeatmap(corr) {
        const box = $("heatmap");
        box.style.gridTemplateColumns = `auto repeat(${corr.labels.length}, 1fr)`;
        let html = `<div></div>${corr.labels.map((l) => `<div class="hm-label">${esc(l)}</div>`).join("")}`;
        corr.matrix.forEach((row, i) => {
            html += `<div class="hm-label pr-2">${esc(corr.labels[i])}</div>`;
            row.forEach((v, j) => {
                const text = v === null ? "–" : v.toFixed(2);
                const color = v === null ? "#e5e7eb" : heatColor(v);
                const ink = v !== null && Math.abs(v) > 0.55 ? "#fff" : "#1f2937";
                html += `<div class="hm-cell" style="background:${color};color:${ink}" title="${esc(cap(corr.labels[i]))} vs ${esc(corr.labels[j])}: r = ${text}">${text}</div>`;
            });
        });
        html += `<div class="hm-legend"><span>−1</span><span class="hm-scale"></span><span>+1</span></div>`;
        box.innerHTML = html;
    }

    function renderPie(dist) {
        const colors = dist.labels.map((l, i) => (l === "other" ? "#9ca3af" : dist.by === "diet" ? dietColor(l, i) : PALETTE[i % PALETTE.length]));
        const total = dist.values.reduce((a, b) => a + b, 0);
        $("pieCaption").textContent = dist.by === "diet"
            ? "Recipe distribution by diet type."
            : `Recipe distribution by cuisine within the ${cap(state.diet)} diet.`;
        drawChart("pie", "pieChart", {
            type: "pie",
            data: { labels: dist.labels.map(cap), datasets: [{ data: dist.values, backgroundColor: colors, borderColor: "#fff", borderWidth: 2 }] },
            options: {
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "right" },
                    tooltip: { callbacks: { label: (c) => `${c.label}: ${c.parsed.toLocaleString()} recipes (${((c.parsed / total) * 100).toFixed(1)}%)` } },
                },
            },
        });
    }

    function fillDietOptions(diets) {
        const select = $("dietSelect");
        if (select.options.length > 1) return;
        for (const d of diets) select.add(new Option(cap(d), d));
        select.value = state.diet;
    }

    // ---------- loaders ----------

    async function loadInsights(refresh = false) {
        const ticket = (latest.insights = (latest.insights || 0) + 1);
        busy("insightsBtn", true);
        try {
            const { data } = await api("insights", { diet: state.diet, refresh: refresh ? "true" : "" });
            if (ticket !== latest.insights) return;
            hideAlert();
            fillDietOptions(data.dietTypes);
            renderSummary(data.summary);
            renderBar(data.averageMacros);
            state.scatter = data.scatter;
            renderScatter();
            renderHeatmap(data.correlations);
            renderPie(data.distribution);
            $("chartScope").textContent = state.diet === "all"
                ? `All ${data.summary.recipes.toLocaleString()} recipes`
                : `${data.summary.recipes.toLocaleString()} ${cap(state.diet)} recipes`;
        } catch (err) {
            showAlert(err.message);
        } finally {
            busy("insightsBtn", false);
        }
    }

    async function loadRecipes() {
        const ticket = (latest.recipes = (latest.recipes || 0) + 1);
        state.recipesOpen = true;
        $("recipesPanel").classList.remove("hidden");
        busy("recipesBtn", true);
        try {
            const { data } = await api("recipes", {
                diet: state.diet, search: state.search, sort: state.sort, order: state.order, page: state.page, pageSize: state.pageSize,
            });
            if (ticket !== latest.recipes) return;
            hideAlert();
            state.page = data.page;
            $("recipesBody").innerHTML = data.items.length
                ? data.items.map((r) => `<tr class="border-t hover:bg-gray-50">
                    <td class="px-4 py-2">${esc(r.name)}</td>
                    <td class="px-4 py-2"><span class="diet-pill" style="background:${dietColor(r.diet)}">${esc(r.diet)}</span></td>
                    <td class="px-4 py-2 capitalize">${esc(r.cuisine)}</td>
                    <td class="px-4 py-2 text-right">${fmt(r.protein)}</td>
                    <td class="px-4 py-2 text-right">${fmt(r.carbs)}</td>
                    <td class="px-4 py-2 text-right">${fmt(r.fat)}</td>
                    <td class="px-4 py-2 text-right">${fmt(r.proteinToCarbs, 2)}</td>
                </tr>`).join("")
                : `<tr><td colspan="7" class="px-4 py-6 text-center text-gray-500">No recipes match these filters.</td></tr>`;
            const scope = state.diet === "all" ? "" : ` in ${cap(state.diet)}`;
            $("recipesCaption").textContent = `${data.total.toLocaleString()} recipes${scope}${state.search ? ` matching “${state.search}”` : ""}`;
            renderPagination(data);
        } catch (err) {
            showAlert(err.message);
        } finally {
            busy("recipesBtn", false);
        }
    }

    function renderPagination({ page, totalPages, total, pageSize }) {
        const box = $("pagination");
        const wanted = new Set([1, totalPages, page - 1, page, page + 1].filter((n) => n >= 1 && n <= totalPages));
        const pages = [...wanted].sort((a, b) => a - b);
        const btn = (label, target, extra = "") =>
            `<button class="px-3 py-1 bg-gray-300 rounded hover:bg-gray-400" data-page="${target}" ${extra}>${label}</button>`;
        let html = btn("Previous", page - 1, page <= 1 ? "disabled" : "");
        pages.forEach((n, i) => {
            if (i > 0 && n - pages[i - 1] > 1) html += `<span class="px-1 text-gray-500">…</span>`;
            html += btn(n, n, n === page ? 'aria-current="page"' : "");
        });
        html += btn("Next", page + 1, page >= totalPages ? "disabled" : "");
        box.innerHTML = html;
        $("pageInfo").textContent = `Page ${page} of ${totalPages.toLocaleString()} · ${total.toLocaleString()} recipes · ${pageSize} per page`;
    }

    async function loadClusters() {
        const ticket = (latest.clusters = (latest.clusters || 0) + 1);
        state.clustersOpen = true;
        $("clustersPanel").classList.remove("hidden");
        busy("clustersBtn", true);
        try {
            const { data } = await api("clusters", { diet: state.diet, k: state.k });
            if (ticket !== latest.clusters) return;
            hideAlert();
            const total = data.clusters.reduce((a, c) => a + c.size, 0);
            const name = (c) => `C${c.id + 1} · ${c.label}`;
            drawChart("clusters", "clusterChart", {
                type: "scatter",
                data: {
                    datasets: data.clusters.map((c) => ({
                        label: name(c),
                        data: data.points.filter((p) => p.cluster === c.id && p.protein > 0 && p.fat > 0).map((p) => ({ x: p.protein, y: p.fat, name: p.name })),
                        backgroundColor: `${PALETTE[c.id]}99`, borderColor: PALETTE[c.id], pointRadius: 3,
                    })),
                },
                options: {
                    maintainAspectRatio: false,
                    scales: { x: logAxis("Protein (g), log scale"), y: logAxis("Fat (g), log scale") },
                    plugins: { tooltip: { callbacks: { label: (c) => `${c.raw.name}: ${fmt(c.raw.x)} g protein, ${fmt(c.raw.y)} g fat` } } },
                },
            });
            $("clustersBody").innerHTML = data.clusters.map((c) => `<tr class="border-t">
                <td class="px-3 py-2 whitespace-nowrap"><span class="diet-pill" style="background:${PALETTE[c.id]}">C${c.id + 1}</span> ${esc(c.label)}</td>
                <td class="px-3 py-2 text-right">${c.size.toLocaleString()} <span class="text-gray-500">(${((c.size / total) * 100).toFixed(0)}%)</span></td>
                <td class="px-3 py-2 text-right whitespace-nowrap">${fmt(c.centroid.protein, 0)} / ${fmt(c.centroid.carbs, 0)} / ${fmt(c.centroid.fat, 0)}</td>
                <td class="px-3 py-2"><span class="diet-pill" style="background:${dietColor(c.topDiet)}">${esc(c.topDiet)}</span></td>
            </tr>`).join("");
            const scope = state.diet === "all" ? "" : `${cap(state.diet)} `;
            $("clustersCaption").textContent = `${data.k} clusters over ${total.toLocaleString()} ${scope}recipes · features: ${data.features.join(", ")}`;
        } catch (err) {
            showAlert(err.message);
        } finally {
            busy("clustersBtn", false);
        }
    }

    function reloadOpenPanels() {
        if (state.recipesOpen) loadRecipes();
        if (state.clustersOpen) loadClusters();
    }

    // ---------- wiring ----------

    function init() {
        $("apiBase").textContent = API;
        if (!window.Chart) {
            showAlert("Chart.js could not be loaded from the CDN, so charts cannot be drawn.");
            return;
        }
        Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
        Chart.defaults.color = "#4b5563";

        $("insightsBtn").addEventListener("click", () => loadInsights(true));
        $("recipesBtn").addEventListener("click", () => { state.page = 1; loadRecipes(); });
        $("clustersBtn").addEventListener("click", loadClusters);
        $("scatterLog").addEventListener("change", renderScatter);

        $("dietSelect").addEventListener("change", (e) => {
            state.diet = e.target.value;
            state.page = 1;
            loadInsights();
            reloadOpenPanels();
        });

        let timer;
        $("searchInput").addEventListener("input", (e) => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                state.search = e.target.value.trim();
                state.page = 1;
                loadRecipes();
            }, 350);
        });

        $("sortSelect").addEventListener("change", (e) => {
            [state.sort, state.order] = e.target.value.split(":");
            state.page = 1;
            loadRecipes();
        });

        $("kSelect").addEventListener("change", (e) => {
            state.k = Number(e.target.value);
            if (state.clustersOpen) loadClusters();
        });

        $("resetBtn").addEventListener("click", () => {
            Object.assign(state, { diet: "all", search: "", sort: "protein", order: "desc", page: 1 });
            $("dietSelect").value = "all";
            $("searchInput").value = "";
            $("sortSelect").value = "protein:desc";
            loadInsights();
            reloadOpenPanels();
        });

        $("pagination").addEventListener("click", (e) => {
            const target = e.target.closest("button[data-page]");
            if (!target || target.disabled) return;
            state.page = Number(target.dataset.page);
            loadRecipes();
        });

        loadInsights();
    }

    init();
})();
