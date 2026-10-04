let autoRefreshIntervalId = null;
const formatter = JSJoda.DateTimeFormatter.ofPattern("MM/dd/YYYY HH:mm").withLocale(JSJodaLocale.Locale.ENGLISH);

const zoomMin = 1000 * 60 * 60 * 8 // 2 hours in milliseconds
const zoomMax = 2 * 7 * 1000 * 60 * 60 * 24 // 2 weeks in milliseconds

const byTimelineOptions = {
    timeAxis: {scale: "hour", step: 8},
    orientation: {axis: "top"},
    stack: false,
    xss: {disabled: true}, // Items are XSS safe through JQuery
    zoomMin: zoomMin,
    zoomMax: zoomMax,
};

const byCrewPanel = document.getElementById("byCrewPanel");
let byCrewGroupData = new vis.DataSet();
let byCrewItemData = new vis.DataSet();
let byCrewTimeline = new vis.Timeline(byCrewPanel, byCrewItemData, byCrewGroupData, byTimelineOptions);

const byFlightPanel = document.getElementById("byFlightPanel");
let byFlightGroupData = new vis.DataSet();
let byFlightItemData = new vis.DataSet();
let byFlightTimeline = new vis.Timeline(byFlightPanel, byFlightItemData, byFlightGroupData, byTimelineOptions);

// Pick a font size that grows with available item width and shrinks only when
// the text cannot fit. Wide flight strips get a comfortable ~13 px, very tight
// ones drop down to 7 px.
function fitTimelineItemText(root) {
    if (!root) return;
    const MAX_FS = 13;
    const MIN_FS = 7;
    const items = root.querySelectorAll(".vis-item .vis-item-content");
    items.forEach((content) => {
        if (!content.textContent.trim()) return;
        const host = content.closest(".vis-item");
        const itemW = host ? host.clientWidth : content.clientWidth;
        let fs = Math.max(MIN_FS, Math.min(MAX_FS, Math.floor(itemW / 7)));
        content.style.fontSize = fs + "px";
        let safety = 14;
        while (content.scrollWidth > content.clientWidth + 0.5 &&
               fs > MIN_FS && safety-- > 0) {
            fs -= 0.5;
            content.style.fontSize = fs + "px";
        }
    });
}

let _fitTimelineScheduled = false;
function scheduleFit() {
    if (_fitTimelineScheduled) return;
    _fitTimelineScheduled = true;
    requestAnimationFrame(() => {
        _fitTimelineScheduled = false;
        fitTimelineItemText(byCrewPanel);
        fitTimelineItemText(byFlightPanel);
    });
}

byCrewTimeline.on("changed", scheduleFit);
byFlightTimeline.on("changed", scheduleFit);
window.addEventListener("resize", scheduleFit);

// Replace the contents of a vis DataSet in-place: keeps unchanged items
// stable on the timeline and avoids the full-clear "flash" on every solver
// tick. Items carry stable ids (employee.id / flight.flightNumber /
// assignment.id), so update() + targeted remove() is safe.
function diffSyncDataSet(dataset, newItems) {
    const existing = new Set(dataset.getIds());
    const nextIds = new Set();
    newItems.forEach((item) => {
        nextIds.add(item.id);
        dataset.update(item);
    });
    const toRemove = [];
    existing.forEach((id) => {
        if (!nextIds.has(id)) toRemove.push(id);
    });
    if (toRemove.length) dataset.remove(toRemove);
}

let scheduleId = null;
let loadedSchedule = null;
let viewType = "R";
let elapsedIntervalId = null;
let solveStartedAt = null;
let lastShownSummaryJob = null;
let manuallyStopped = false;

$(document).ready(function () {
    $("#solveButton").click(function () {
        solve();
    });
    $("#stopSolvingButton").click(function () {
        stopSolving();
    });
    $("#analyzeButton").click(function () {
        analyze();
    });
    $("#byCrewTab").click(function () {
        viewType = "R";
        refreshSchedule();
    });
    $("#byFlightTab").click(function () {
        viewType = "F";
        refreshSchedule();
    });
    // HACK to allow vis-timeline to work within Bootstrap tabs
    $("#byCrewTab").on('shown.bs.tab', function (event) {
        byCrewTimeline.redraw();
    })
    $("#byFlightTab").on('shown.bs.tab', function (event) {
        byFlightTimeline.redraw();
    })

    $("#addEmployeeButton").click(() => openEmployeeModal(null));
    $("#addFlightButton").click(() => openFlightModal(null));
    $("#empSaveButton").click(saveEmployee);
    $("#flSaveButton").click(saveFlight);
    $("#settingsButton").click(openSettingsModal);
    $("#stSaveButton").click(saveSettings);
    $("#resetButton").click(resetToDemo);

    // Redraw vis-timeline when the schedule tab becomes visible again
    $("#mainScheduleTab").on("shown.bs.tab", function () {
        byCrewTimeline.redraw();
        byFlightTimeline.redraw();
    });

    setupAjax();
    refreshSchedule();
});

function setupAjax() {
    $.ajaxSetup({
        headers: {
            'Content-Type': 'application/json', 'Accept': 'application/json,text/plain', // plain text is required by solve() returning UUID of the solver job
        }
    });

    // Extend jQuery to support $.put() and $.delete()
    jQuery.each(["put", "delete"], function (i, method) {
        jQuery[method] = function (url, data, callback, type) {
            if (jQuery.isFunction(data)) {
                type = type || callback;
                callback = data;
                data = undefined;
            }
            return jQuery.ajax({
                url: url, type: method, dataType: type, data: data, success: callback
            });
        };
    });
}

function refreshSchedule() {
    let path = "/schedules/" + scheduleId;
    if (scheduleId === null) {
        path = "/demo-data";
    }

    $.getJSON(path, function (schedule) {
        loadedSchedule = schedule;
        $('#exportData').attr('href', 'data:text/plain;charset=utf-8,' + JSON.stringify(loadedSchedule));
        renderSchedule(schedule);
    })
        .fail(function (xhr, ajaxOptions, thrownError) {
            showError("Getting the schedule has failed.", xhr);
            refreshSolvingButtons(false);
        });
}

function renderSchedule(schedule) {
    refreshSolvingButtons(schedule.solverStatus != null && schedule.solverStatus !== "NOT_SOLVING");
    $("#score").text(formatScore(schedule.score));

    if (viewType === "R") {
        renderScheduleByCrew(schedule);
    }
    if (viewType === "F") {
        renderScheduleByFlight(schedule);
    }

    renderEmployeeTable();
    renderFlightTable();
    updateTabBadges();

    if (schedule.summary && scheduleId && scheduleId !== lastShownSummaryJob) {
        lastShownSummaryJob = scheduleId;
        showFinishSummary(schedule.summary);
    }
}

function showFinishSummary(s) {
    const labels = {
        manual: {title: "Остановлено вручную", klass: "alert-secondary"},
        timeout: {title: "Истёк таймаут", klass: "alert-warning"},
        threshold: {title: "Достигнут порог мягкой оценки", klass: "alert-success"},
        optimum: {title: "Найден локальный оптимум (улучшений не обнаружено)", klass: "alert-success"},
    };
    const info = labels[s.finishReason] || {title: s.finishReason, klass: "alert-info"};

    const panel = $("#notificationPanel");
    panel.children().remove();

    const scoreText = (s.hard != null && s.soft != null)
        ? `Жесткие: ${s.hard} / Мягкие: ${s.soft}`
        : "—";
    const timeText = s.timeLimitSeconds
        ? `${s.elapsedSeconds} с из ${s.timeLimitSeconds} с`
        : `${s.elapsedSeconds} с`;
    const thresholdText = s.softThreshold != null ? `${s.softThreshold}` : "не задан";
    const engineLabels = {
        ortools: "OR-Tools CP-SAT",
        timefold_python: "Timefold Python 1.24",
        timefold_kotlin: "Timefold Kotlin 2.0",
        timefold_kotlin_comprehensive: "Timefold Kotlin 2.0 (комплексный)",
        solverforge: "SolverForge-Legacy 1.24",
        timefold: "Timefold Python 1.24",
    };
    const engineLabel = engineLabels[s.engine] || s.engine || "Timefold";

    const html = `
        <div class="alert ${info.klass} alert-dismissible fade show pn-summary-alert" role="alert">
            <strong>${info.title}</strong>
            <div style="margin-top:6px; font-size:0.88rem;">
                <div>Движок: <b>${engineLabel}</b>${s.engineStatus ? ` <span class="text-muted">(${s.engineStatus})</span>` : ""}</div>
                <div>Оценка: <b>${scoreText}</b></div>
                <div>Время: <b>${timeText}</b></div>
                <div>Порог мягкой оценки: <b>${thresholdText}</b></div>
                <div>Назначено: <b>${s.assignedCount}</b> из <b>${s.totalAssignments}</b> (задействовано сотрудников: <b>${s.employeesInvolved}</b>)</div>
            </div>
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Закрыть"></button>
        </div>
    `;
    panel.append(html);
}

function updateTabBadges() {
    $("#crewCount").text((loadedSchedule.employees || []).length);
    $("#flightCount").text((loadedSchedule.flights || []).length);
}

let _crewWindowApplied = false;
function renderScheduleByCrew(schedule) {
    const unassignedCrew = $("#unassignedCrew");
    unassignedCrew.children().remove();
    let unassignedCrewCount = 0;

    const newGroups = [];
    const newItems = [];

    const sortedEmployees = schedule.employees.slice()
        .sort((e1, e2) => (e1.name || "").localeCompare(e2.name || ""));

    sortedEmployees.forEach((employee) => {
        const isPilot = (employee.skills || []).some(isPilotRank);
        const crewIcon = isPilot
            ? '<span class="fas fa-solid fa-plane-departure" title="Пилот"></span>'
            : '<span class="fas fa-solid fa-user-tie" title="Бортпроводник"></span>';
        const content = `<div class="d-flex flex-column"><div><h5 class="card-title mb-1">${employee.name} <span class="text-muted">(${employee.homeAirport})</span> ${crewIcon}</h5></div>`;
        newGroups.push({id: employee.id, content});

        (employee.unavailableDays || []).forEach((date, idx) => {
            const day = JSJoda.LocalDate.parse(date);
            newItems.push({
                id: `${employee.id}-unav-${idx}`,
                group: employee.id,
                content: "",
                start: day.atStartOfDay().toString(),
                end: day.atStartOfDay().withHour(23).withMinute(59).toString(),
                style: "background-color: gray; min-height: 50px",
            });
        });
    });

    const flightMap = new Map();
    schedule.flights.forEach((f) => flightMap.set(f.flightNumber, f));

    schedule.flightAssignments.forEach((assignment) => {
        const flight = flightMap.get(assignment.flight);
        if (!flight) return;
        if (assignment.employee == null) {
            unassignedCrewCount++;
            const departureDateTime = JSJoda.LocalDateTime.parse(flight.departureUTCDateTime);
            const arrivalDateTime = JSJoda.LocalDateTime.parse(flight.arrivalUTCDateTime);
            const unassignedElement = $(`<div class="pn-card"/>`)
                .append($(`<h5/>`).text(`${flight.departureAirport}→${flight.arrivalAirport}`))
                .append($(`<p/>`).text(`Длительность: ${departureDateTime.until(arrivalDateTime, JSJoda.ChronoUnit.HOURS)} ч`))
                .append($(`<p/>`).text(`Вылет: ${formatter.format(departureDateTime)}`))
                .append($(`<p/>`).text(`Прилёт: ${formatter.format(arrivalDateTime)}`));
            unassignedCrew.append(unassignedElement);
            // No timeline item for unassigned rows (they have no group).
        } else {
            const content = `<div><div class="d-flex justify-content-center"><h5 class="card-title mb-1">${flight.departureAirport}→${flight.arrivalAirport}</h5></div></div>`;
            newItems.push({
                id: assignment.id,
                group: assignment.employee,
                content,
                start: flight.departureUTCDateTime,
                end: flight.arrivalUTCDateTime,
                style: "min-height: 50px",
            });
        }
    });

    diffSyncDataSet(byCrewGroupData, newGroups);
    diffSyncDataSet(byCrewItemData, newItems);

    if (unassignedCrewCount === 0) {
        unassignedCrew.append($(`<p class="pn-empty"/>`).text(`Все рейсы обеспечены экипажем.`));
    }
    if (!_crewWindowApplied) {
        byCrewTimeline.setWindow(JSJoda.LocalDateTime.now().minusMinutes(1).toString(),
            JSJoda.LocalDateTime.now().plusDays(4).withHour(23).withMinute(59).toString());
        _crewWindowApplied = true;
    }
}

let _flightWindowApplied = false;
function renderScheduleByFlight(schedule) {
    const unassignedCrew = $("#unassignedCrew");
    unassignedCrew.children().remove();

    const newGroups = [];
    const newItems = [];

    const sortedFlights = schedule.flights.slice().sort((e1, e2) =>
        JSJoda.LocalDateTime.parse(e1.departureUTCDateTime)
            .compareTo(JSJoda.LocalDateTime.parse(e2.departureUTCDateTime)));

    sortedFlights.forEach((flight) => {
        const gContent = `<div class="d-flex flex-column"><div><h5 class="card-title mb-1">${flight.departureAirport}→${flight.arrivalAirport}</h5></div></div>`;
        newGroups.push({id: flight.flightNumber, content: gContent});
    });

    const employeeMap = new Map();
    schedule.employees.forEach((e) => employeeMap.set(e.id, e));

    schedule.flights.forEach((flight) => {
        const assignments = schedule.flightAssignments.filter((f) => f.flight === flight.flightNumber);
        let countUnassigned = 0;
        const missingSkills = [];
        const pilots = [];
        const attendants = [];
        assignments.forEach((a) => {
            if (a.employee == null) {
                countUnassigned++;
                missingSkills.push(a.requiredSkill);
            } else {
                const emp = employeeMap.get(a.employee);
                if (!emp) return;
                if (a.requiredSkill === "Pilot") pilots.push(emp.name);
                else attendants.push(emp.name);
            }
        });

        if (pilots.length > 0 && attendants.length > 0) {
            const lines = [];
            lines.push(`<div style="font-weight:600">Пилоты</div>`);
            pilots.sort().forEach((p) => lines.push(`<div>${p}</div>`));
            lines.push(`<div style="font-weight:600;margin-top:2px">Бортпроводники</div>`);
            attendants.sort().forEach((a) => lines.push(`<div>${a}</div>`));
            newItems.push({
                id: flight.flightNumber,
                group: flight.flightNumber,
                content: `<div class="d-flex flex-column">${lines.join("")}</div>`,
                start: flight.departureUTCDateTime,
                end: flight.arrivalUTCDateTime,
            });
        }

        if (countUnassigned > 0) {
            const label = missingSkills.sort().map((s) => s === "Pilot" ? "Пилот" : "Бортпроводник").join(", ");
            const card = $(`<div class="pn-card"/>`)
                .append($(`<h5/>`).text(`${flight.departureAirport}→${flight.arrivalAirport}`))
                .append($(`<p/>`).text(`Требуется: ${label}`));
            unassignedCrew.append(card);
        }
    });

    diffSyncDataSet(byFlightGroupData, newGroups);
    diffSyncDataSet(byFlightItemData, newItems);

    if (!_flightWindowApplied) {
        byFlightTimeline.setWindow(JSJoda.LocalDateTime.now().minusMinutes(1).toString(),
            JSJoda.LocalDateTime.now().plusDays(4).withHour(23).withMinute(59).toString());
        _flightWindowApplied = true;
    }
}

function solve() {
    lastShownSummaryJob = null;
    manuallyStopped = false;
    $("#notificationPanel").children().remove();
    $.post("/schedules", JSON.stringify(loadedSchedule), function (data) {
        scheduleId = data;
        refreshSolvingButtons(true);
    }).fail(function (xhr, ajaxOptions, thrownError) {
        showError("Start solving failed.", xhr);
        refreshSolvingButtons(false);
    }, "text");
}

async function analyze() {
    new bootstrap.Modal("#scoreAnalysisModal").show();
    const scoreAnalysisModalContent = $("#scoreAnalysisModalContent");
    scoreAnalysisModalContent.children().remove();
    if (loadedSchedule.score == null) {
        scoreAnalysisModalContent.text("Сначала запустите оптимизацию — анализ появится, когда солвер найдёт решение.");
        return;
    }
    $('#scoreAnalysisScoreLabel').text(`(${formatScore(loadedSchedule.score)})`);
    scoreAnalysisModalContent.text("Загружаю анализ…");

    let scoreAnalysis;
    try {
        const response = await fetch("/schedules/analyze", {
            method: "PUT",
            headers: {"Content-Type": "application/json", "Accept": "application/json"},
            body: JSON.stringify(loadedSchedule),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        scoreAnalysis = await response.json();
    } catch (err) {
        scoreAnalysisModalContent.text("");
        showError("Анализ не выполнен.", {statusText: String(err)});
        return;
    }

    let constraints = scoreAnalysis.constraints || [];
    constraints.sort((a, b) => {
        let aComponents = getScoreComponents(a.score), bComponents = getScoreComponents(b.score);
        if (aComponents.hard < 0 && bComponents.hard > 0) return -1;
        if (aComponents.hard > 0 && bComponents.soft < 0) return 1;
        if (Math.abs(aComponents.hard) > Math.abs(bComponents.hard)) {
            return -1;
        } else {
            if (aComponents.medium < 0 && bComponents.medium > 0) return -1;
            if (aComponents.medium > 0 && bComponents.medium < 0) return 1;
            if (Math.abs(aComponents.medium) > Math.abs(bComponents.medium)) {
                return -1;
            } else {
                if (aComponents.soft < 0 && bComponents.soft > 0) return -1;
                if (aComponents.soft > 0 && bComponents.soft < 0) return 1;
                return Math.abs(bComponents.soft) - Math.abs(aComponents.soft);
            }
        }
    });
    constraints.forEach((e) => {
        let components = getScoreComponents(e.weight);
        e.type = components.hard != 0 ? 'hard' : (components.medium != 0 ? 'medium' : 'soft');
        e.weight = components[e.type];
        let scores = getScoreComponents(e.score);
        e.implicitScore = scores.hard != 0 ? scores.hard : (scores.medium != 0 ? scores.medium : scores.soft);
    });

    scoreAnalysisModalContent.children().remove();
    scoreAnalysisModalContent.text("");

    const analysisTable = $(`<table class="table"/>`).css({textAlign: 'center'});
    const analysisTHead = $(`<thead/>`).append($(`<tr/>`)
        .append($(`<th></th>`))
        .append($(`<th>Ограничение</th>`).css({textAlign: 'left'}))
        .append($(`<th>Тип</th>`))
        .append($(`<th>Совпадений</th>`))
        .append($(`<th>Вес</th>`))
        .append($(`<th>Вклад</th>`))
        .append($(`<th></th>`)));
    analysisTable.append(analysisTHead);
    const analysisTBody = $(`<tbody/>`);
    constraints.forEach((c) => {
        let icon = c.type == "hard" && c.implicitScore < 0
            ? '<span class="fas fa-exclamation-triangle" style="color: red"></span>'
            : (c.matches.length == 0 ? '<span class="fas fa-check-circle" style="color: green"></span>' : '');
        let row = $(`<tr/>`);
        row.append($(`<td/>`).html(icon))
            .append($(`<td/>`).text(c.name).css({textAlign: 'left'}))
            .append($(`<td/>`).text(c.type))
            .append($(`<td/>`).html(`<b>${c.matches.length}</b>`))
            .append($(`<td/>`).text(c.weight))
            .append($(`<td/>`).text(c.implicitScore))
            .append($(`<td/>`));
        analysisTBody.append(row);
    });
    analysisTable.append(analysisTBody);
    scoreAnalysisModalContent.append(analysisTable);
}

function getScoreComponents(score) {
    let components = {hard: 0, medium: 0, soft: 0};

    $.each([...score.matchAll(/(-?[0-9]+)(hard|medium|soft)/g)], (i, parts) => {
        components[parts[2]] = parseInt(parts[1], 10);
    });

    return components;
}

function refreshSolvingButtons(solving) {
    // Once user clicked Stop we don't allow the polling loop to flip back into
    // "solving" mode just because the backend hasn't yet set final_at.
    if (manuallyStopped) solving = false;
    if (solving) {
        $("#solveButton").hide();
        $("#stopSolvingButton").show();
        if (autoRefreshIntervalId == null) {
            autoRefreshIntervalId = setInterval(refreshSchedule, 2000);
        }
        startElapsedTimer();
    } else {
        const wasSolving = autoRefreshIntervalId != null;
        $("#solveButton").show();
        $("#stopSolvingButton").hide();
        if (autoRefreshIntervalId != null) {
            clearInterval(autoRefreshIntervalId);
            autoRefreshIntervalId = null;
        }
        stopElapsedTimer();
        // The solver may flip status to NOT_SOLVING a moment before the final
        // best-solution consumer writes the `final_at` marker on the server.
        // Re-fetch a couple of times so we catch the summary banner reliably.
        if (wasSolving && scheduleId && !loadedSchedule?.summary) {
            setTimeout(refreshSchedule, 1200);
            setTimeout(refreshSchedule, 3000);
        }
    }
}

function startElapsedTimer() {
    if (elapsedIntervalId != null) return;
    solveStartedAt = Date.now();
    $("#elapsedWrap").show();
    tickElapsed();
    elapsedIntervalId = setInterval(tickElapsed, 1000);
}

function stopElapsedTimer() {
    if (elapsedIntervalId == null) return;
    clearInterval(elapsedIntervalId);
    elapsedIntervalId = null;
    tickElapsed(); // final snapshot
}

function tickElapsed() {
    if (solveStartedAt == null) return;
    const seconds = Math.max(0, Math.floor((Date.now() - solveStartedAt) / 1000));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = (n) => String(n).padStart(2, "0");
    const text = h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
    $("#elapsed").text(text);
}

function stopSolving() {
    manuallyStopped = true;
    $.delete("/schedules/" + scheduleId, function () {
        refreshSolvingButtons(false);
        refreshSchedule();
    }).fail(function (xhr, ajaxOptions, thrownError) {
        showError("Stop solving failed.", xhr);
    });
}

function showError(message, xhr) {
    const panel = $("#notificationPanel");
    panel.children().remove();
    const detail = xhr && xhr.responseJSON && xhr.responseJSON.detail
        ? xhr.responseJSON.detail
        : (xhr && xhr.statusText ? xhr.statusText : "");
    panel.append(
        $(`<div class="alert alert-danger alert-dismissible fade show" role="alert"/>`)
            .append(`<strong>${message}</strong> ${detail}`)
            .append(`<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Закрыть"></button>`)
    );
}

// =========================================================== CRUD: employees

function openEmployeeModal(employee) {
    resetEmployeeForm();
    populateAirportSelect($("#empHomeAirport"));
    if (employee) {
        $("#employeeModalLabel").text("Редактирование сотрудника");
        $("#empId").val(employee.id);
        $("#empName").val(employee.name);
        $("#empHomeAirport").val(employee.homeAirport);
        const rank = (employee.skills || [])[0];
        if (rank) $(`#empSkills input[value="${rank}"]`).prop("checked", true);
        (employee.typeRatings || []).forEach(t =>
            $(`#empTypeRatings input[value="${t}"]`).prop("checked", true));
        (employee.languages || []).forEach(l =>
            $(`#empLanguages input[value="${l}"]`).prop("checked", true));
        $("#empUnavailable").val((employee.unavailableDays || []).join(", "));
        $("#empPreferredOff").val((employee.preferredDaysOff || []).join(", "));
        $("#empSeniority").val(employee.seniority != null ? employee.seniority : 500);
        $("#empIsReserve").prop("checked", !!employee.isReserve);
    } else {
        $("#employeeModalLabel").text("Новый сотрудник");
        $(`#empLanguages input[value="en"]`).prop("checked", true);
        $(`#empTypeRatings input[value="A320"]`).prop("checked", true);
        $("#empSeniority").val(500);
    }
    new bootstrap.Modal("#employeeModal").show();
}

function resetEmployeeForm() {
    $("#empId").val("");
    $("#empName").val("");
    $("#empSkills input, #empLanguages input, #empTypeRatings input").prop("checked", false);
    $("#empUnavailable, #empPreferredOff").val("");
    $("#empSeniority").val(500);
    $("#empIsReserve").prop("checked", false);
    $("#empError").text("");
}

function _parseDateList(raw) {
    const text = (raw || "").trim();
    if (!text) return [];
    return text.split(",").map(s => s.trim()).filter(Boolean);
}

function saveEmployee() {
    const origId = $("#empId").val();
    const id = origId || generateEmployeeId();
    const name = $("#empName").val().trim();
    const homeAirportCode = $("#empHomeAirport").val();
    const rank = $('#empSkills input[name="empRank"]:checked').val() || null;
    const typeRatings = $("#empTypeRatings input:checked").map((_, el) => el.value).get();
    const languages = $("#empLanguages input:checked").map((_, el) => el.value).get();
    const unavailableDays = _parseDateList($("#empUnavailable").val());
    const preferredDaysOff = _parseDateList($("#empPreferredOff").val());
    const seniority = parseInt($("#empSeniority").val(), 10);
    const isReserve = $("#empIsReserve").prop("checked");

    if (!name) return $("#empError").text("Укажите имя сотрудника.");
    if (!homeAirportCode) return $("#empError").text("Выберите домашнюю базу.");
    if (!rank) return $("#empError").text("Выберите должность.");
    if (isPilotRank(rank) && typeRatings.length === 0) {
        return $("#empError").text("Пилот должен иметь хотя бы один type rating.");
    }
    for (const d of [...unavailableDays, ...preferredDaysOff]) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) {
            return $("#empError").text("Неверная дата: " + d);
        }
    }
    if (!Number.isFinite(seniority) || seniority < 1 || seniority > 1000) {
        return $("#empError").text("Стаж — целое число от 1 до 1000.");
    }

    const employee = {
        id, name,
        homeAirport: homeAirportCode,
        skills: [rank],
        typeRatings,
        languages,
        unavailableDays,
        preferredDaysOff,
        undesiredAirports: [],
        seniority,
        isReserve,
    };
    const employees = loadedSchedule.employees || [];
    const idx = employees.findIndex(e => e.id === id);
    if (idx >= 0) employees[idx] = employee;
    else employees.push(employee);
    loadedSchedule.employees = employees;

    bootstrap.Modal.getInstance(document.getElementById("employeeModal")).hide();
    renderSchedule(loadedSchedule);
}

function deleteEmployee(id) {
    if (!confirm("Удалить сотрудника?")) return;
    loadedSchedule.employees = (loadedSchedule.employees || []).filter(e => e.id !== id);
    (loadedSchedule.flightAssignments || []).forEach(fa => {
        if (fa.employee === id) fa.employee = null;
    });
    renderSchedule(loadedSchedule);
}

function generateEmployeeId() {
    const ids = (loadedSchedule.employees || [])
        .map(e => parseInt(e.id, 10))
        .filter(n => !isNaN(n));
    const max = ids.length > 0 ? Math.max(...ids) : -1;
    return String(max + 1);
}

const RANK_LABELS_RU = {
    "Captain": "Командир",
    "First officer": "Второй пилот",
    "Senior cabin attendant": "Старший БП",
    "Cabin attendant": "Бортпроводник",
    "Pilot": "Пилот",               // legacy
    "Flight attendant": "Бортпроводник",
};
function rankLabel(rank) {
    return RANK_LABELS_RU[rank] || rank;
}
function isPilotRank(rank) {
    return rank === "Captain" || rank === "First officer" || rank === "Pilot";
}

function renderEmployeeTable() {
    const tbody = $("#employeeTable tbody");
    tbody.empty();
    const employees = (loadedSchedule.employees || []).slice()
        .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    if (employees.length === 0) {
        tbody.append(`<tr><td colspan="8" class="pn-empty" style="text-align:center;padding:14px;">Экипаж не добавлен</td></tr>`);
        return;
    }
    employees.forEach(emp => {
        const rankChips = (emp.skills || []).map(s =>
            `<span class="pn-chip">${escapeHtml(rankLabel(s))}</span>`).join("");
        const typeChips = (emp.typeRatings || []).map(t =>
            `<span class="pn-chip pn-chip-neutral">${escapeHtml(t)}</span>`).join("");
        const langs = (emp.languages || [])
            .map(l => `<span class="pn-chip pn-chip-neutral">${escapeHtml(l)}</span>`).join("");
        const seniority = (emp.seniority != null) ? emp.seniority : "—";
        const reserveBadge = emp.isReserve
            ? `<span class="pn-chip" style="background:#fef3c7;color:#b45309;">stand-by</span> `
            : "";
        const unavailable = (emp.unavailableDays || []).join(", ");
        const preferred = (emp.preferredDaysOff || []).join(", ");
        const offInfo = [
            unavailable ? `<div><span class="text-muted" style="font-size:11px">отпуск:</span> ${escapeHtml(unavailable)}</div>` : "",
            preferred ? `<div><span class="text-muted" style="font-size:11px">wish:</span> ${escapeHtml(preferred)}</div>` : "",
        ].filter(Boolean).join("") || "—";

        const row = $(`<tr/>`);
        row.append(`<td>${reserveBadge}${escapeHtml(emp.name || "")}</td>`);
        row.append(`<td>${escapeHtml(emp.homeAirport || "—")}</td>`);
        row.append(`<td>${rankChips || "—"}</td>`);
        row.append(`<td>${typeChips || "—"}</td>`);
        row.append(`<td>${langs || "—"}</td>`);
        row.append(`<td style="font-variant-numeric: tabular-nums;">${seniority}</td>`);
        row.append(`<td>${offInfo}</td>`);
        const actions = $(`<td/>`).append($(`<div class="pn-row-actions"/>`)
            .append(`<button class="pn-icon-btn" title="Редактировать"><span class="fas fa-pen"></span></button>`)
            .append(`<button class="pn-icon-btn pn-danger-hover" title="Удалить"><span class="fas fa-trash"></span></button>`));
        actions.find(".pn-icon-btn").eq(0).click(() => openEmployeeModal(emp));
        actions.find(".pn-icon-btn").eq(1).click(() => deleteEmployee(emp.id));
        row.append(actions);
        tbody.append(row);
    });
}

// =========================================================== CRUD: flights

function openFlightModal(flight) {
    resetFlightForm();
    populateAirportSelect($("#flDepAirport"));
    populateAirportSelect($("#flArrAirport"));
    if (flight) {
        $("#flightModalLabel").text("Редактирование рейса");
        $("#flId").val(flight.flightNumber);
        $("#flNumber").val(flight.flightNumber);
        $("#flDepAirport").val(flight.departureAirport);
        $("#flArrAirport").val(flight.arrivalAirport);
        $("#flAircraftType").val(flight.aircraftType || "A320");
        $("#flDepDatetime").val(isoToLocalInput(flight.departureUTCDateTime));
        $("#flArrDatetime").val(isoToLocalInput(flight.arrivalUTCDateTime));
        $("#flPilots, #flAttendants").prop("disabled", true);
    } else {
        $("#flightModalLabel").text("Новый рейс");
        $("#flPilots").val(2);
        $("#flAttendants").val(3);
        $("#flAircraftType").val("A320");
        $("#flPilots, #flAttendants").prop("disabled", false);
    }
    new bootstrap.Modal("#flightModal").show();
}

function resetFlightForm() {
    $("#flId").val("");
    $("#flNumber, #flDepAirport, #flArrAirport, #flDepDatetime, #flArrDatetime").val("");
    $("#flPilots").val(2);
    $("#flAttendants").val(3);
    $("#flError").text("");
}

function saveFlight() {
    const origId = $("#flId").val();
    const flightNumber = $("#flNumber").val().trim();
    const depCode = $("#flDepAirport").val();
    const arrCode = $("#flArrAirport").val();
    const depISO = localInputToIso($("#flDepDatetime").val());
    const arrISO = localInputToIso($("#flArrDatetime").val());
    const pilots = Math.max(1, parseInt($("#flPilots").val(), 10) || 2);
    const attendants = Math.max(0, parseInt($("#flAttendants").val(), 10) || 0);

    if (!flightNumber) return $("#flError").text("Укажите номер рейса.");
    if (!depCode || !arrCode) return $("#flError").text("Выберите аэропорты.");
    if (depCode === arrCode) return $("#flError").text("Аэропорты должны отличаться.");
    if (!depISO || !arrISO) return $("#flError").text("Укажите время вылета и прибытия.");
    if (new Date(arrISO) <= new Date(depISO))
        return $("#flError").text("Прибытие должно быть позже вылета.");

    const flights = loadedSchedule.flights || [];
    const assignments = loadedSchedule.flightAssignments || [];

    const collision = flights.some(f => f.flightNumber === flightNumber && f.flightNumber !== origId);
    if (collision) return $("#flError").text("Рейс с таким номером уже существует.");

    const updated = {
        flightNumber,
        departureAirport: depCode,
        departureUTCDateTime: depISO,
        arrivalAirport: arrCode,
        arrivalUTCDateTime: arrISO,
        aircraftType: $("#flAircraftType").val() || "A320",
    };

    if (origId) {
        const idx = flights.findIndex(f => f.flightNumber === origId);
        if (idx >= 0) flights[idx] = updated;
        if (origId !== flightNumber) {
            assignments.forEach(fa => { if (fa.flight === origId) fa.flight = flightNumber; });
        }
    } else {
        flights.push(updated);
        generateAssignmentsForFlight(flightNumber, pilots, attendants)
            .forEach(a => assignments.push(a));
    }
    loadedSchedule.flights = flights;
    loadedSchedule.flightAssignments = assignments;

    bootstrap.Modal.getInstance(document.getElementById("flightModal")).hide();
    renderSchedule(loadedSchedule);
}

function deleteFlight(flightNumber) {
    if (!confirm("Удалить рейс и все связанные назначения?")) return;
    loadedSchedule.flights = (loadedSchedule.flights || []).filter(f => f.flightNumber !== flightNumber);
    loadedSchedule.flightAssignments = (loadedSchedule.flightAssignments || [])
        .filter(fa => fa.flight !== flightNumber);
    renderSchedule(loadedSchedule);
}

function generateAssignmentsForFlight(flightNumber, pilots, attendants) {
    const existing = new Set((loadedSchedule.flightAssignments || []).map(a => a.id));
    let counter = Date.now();
    const mkId = () => {
        let id;
        do { id = "fa_" + (counter++); } while (existing.has(id));
        existing.add(id);
        return id;
    };
    const result = [];
    for (let i = 1; i <= pilots; i++) {
        result.push({
            id: mkId(),
            flight: flightNumber,
            indexInFlight: i,
            requiredSkill: "Pilot",
            employee: null,
        });
    }
    for (let i = 1; i <= attendants; i++) {
        result.push({
            id: mkId(),
            flight: flightNumber,
            indexInFlight: pilots + i,
            requiredSkill: "Flight attendant",
            employee: null,
        });
    }
    return result;
}

function renderFlightTable() {
    const tbody = $("#flightTable tbody");
    tbody.empty();
    const flights = (loadedSchedule.flights || []).slice()
        .sort((a, b) => new Date(a.departureUTCDateTime) - new Date(b.departureUTCDateTime));
    if (flights.length === 0) {
        tbody.append(`<tr><td colspan="8" class="pn-empty" style="text-align:center;padding:14px;">Рейсы не добавлены</td></tr>`);
        return;
    }
    flights.forEach(f => {
        const dep = new Date(f.departureUTCDateTime);
        const arr = new Date(f.arrivalUTCDateTime);
        const mins = Math.max(0, Math.round((arr - dep) / 60000));
        const h = Math.floor(mins / 60);
        const m = mins % 60;
        const dur = `${h} ч${m ? " " + m + " мин" : ""}`;
        const longHaul = mins >= 8 * 60;
        const acType = f.aircraftType || "—";
        const acChip = `<span class="pn-chip ${longHaul ? "" : "pn-chip-neutral"}">${escapeHtml(acType)}</span>${longHaul ? ' <span class="text-muted" style="font-size:10px">long-haul</span>' : ""}`;
        const row = $(`<tr/>`);
        row.append(`<td><strong>${escapeHtml(f.flightNumber)}</strong></td>`);
        row.append(`<td>${escapeHtml(f.departureAirport || "—")}</td>`);
        row.append(`<td>${escapeHtml(f.arrivalAirport || "—")}</td>`);
        row.append(`<td>${acChip}</td>`);
        row.append(`<td>${escapeHtml(formatIsoForTable(f.departureUTCDateTime))}</td>`);
        row.append(`<td>${escapeHtml(formatIsoForTable(f.arrivalUTCDateTime))}</td>`);
        row.append(`<td>${dur}</td>`);
        const actions = $(`<td/>`).append($(`<div class="pn-row-actions"/>`)
            .append(`<button class="pn-icon-btn" title="Редактировать"><span class="fas fa-pen"></span></button>`)
            .append(`<button class="pn-icon-btn pn-danger-hover" title="Удалить"><span class="fas fa-trash"></span></button>`));
        actions.find(".pn-icon-btn").eq(0).click(() => openFlightModal(f));
        actions.find(".pn-icon-btn").eq(1).click(() => deleteFlight(f.flightNumber));
        row.append(actions);
        tbody.append(row);
    });
}

// =========================================================== CRUD: utils

function populateAirportSelect($select) {
    $select.empty();
    const airports = loadedSchedule.airports || [];
    $select.append(`<option value="">—</option>`);
    airports.forEach(a => {
        $select.append(`<option value="${escapeHtml(a.code)}">${escapeHtml(a.code)} · ${escapeHtml(a.name)}</option>`);
    });
}

function isoToLocalInput(iso) {
    if (!iso) return "";
    return iso.substring(0, 16);
}

function localInputToIso(value) {
    if (!value) return null;
    return value.length === 16 ? value + ":00" : value;
}

function formatIsoForTable(iso) {
    if (!iso) return "—";
    return iso.replace("T", " ").substring(0, 16);
}

// =========================================================== reset

async function resetToDemo() {
    if (!confirm("Сбросить все изменения и загрузить демо-данные заново?")) {
        return;
    }
    // Stop the solver if it's still running
    if (scheduleId && loadedSchedule && loadedSchedule.solverStatus &&
        loadedSchedule.solverStatus !== "NOT_SOLVING") {
        try { await fetch(`/schedules/${scheduleId}`, {method: "DELETE"}); } catch (e) {}
    }
    // Clear all client-side state
    scheduleId = null;
    loadedSchedule = null;
    lastShownSummaryJob = null;
    manuallyStopped = false;
    solveStartedAt = null;
    $("#notificationPanel").children().remove();
    $("#elapsedWrap").hide();
    $("#elapsed").text("00:00");
    $("#score").text("—");
    stopElapsedTimer();
    refreshSolvingButtons(false);
    byCrewItemData.clear();
    byCrewGroupData.clear();
    byFlightItemData.clear();
    byFlightGroupData.clear();
    $("#unassignedCrew").children().remove();
    // Go back to the Schedule tab so the user sees the new baseline
    new bootstrap.Tab(document.getElementById("mainScheduleTab")).show();
    // Re-fetch fresh demo data
    refreshSchedule();
}

// =========================================================== settings

async function openSettingsModal() {
    $("#stError").text("");
    try {
        const r = await fetch("/settings");
        if (r.ok) {
            const s = await r.json();
            $("#stTimeLimit").val(s.time_limit_s ?? 120);
            $("#stSoftThreshold").val(s.soft_threshold == null ? "" : Math.abs(s.soft_threshold));
            // Backward-compat: server may have stored legacy "timefold" — map to new key.
            const eng = s.engine === "timefold" ? "timefold_python" : (s.engine || "timefold_python");
            $("#stEngine").val(eng);
        }
    } catch (err) {
        // fall through with defaults
    }
    new bootstrap.Modal("#settingsModal").show();
}

async function saveSettings() {
    const timeLimit = parseInt($("#stTimeLimit").val(), 10);
    const rawThreshold = $("#stSoftThreshold").val().trim();
    const absSoft = rawThreshold === "" ? null : parseInt(rawThreshold, 10);
    const engine = $("#stEngine").val() || "timefold_python";

    if (!Number.isFinite(timeLimit) || timeLimit < 3 || timeLimit > 3600) {
        return $("#stError").text("Максимальное время должно быть от 3 до 3600 секунд.");
    }
    if (absSoft !== null && (!Number.isFinite(absSoft) || absSoft < 0)) {
        return $("#stError").text("Порог — положительное целое число (например, 50000).");
    }
    const soft = absSoft == null ? null : -Math.abs(absSoft);

    try {
        const r = await fetch("/settings", {
            method: "POST",
            headers: {"Content-Type": "application/json", "Accept": "application/json"},
            body: JSON.stringify({time_limit_s: timeLimit, soft_threshold: soft, engine}),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        bootstrap.Modal.getInstance(document.getElementById("settingsModal")).hide();
    } catch (err) {
        $("#stError").text("Не удалось сохранить: " + err);
    }
}

function formatScore(raw) {
    if (raw == null) return "—";
    return String(raw)
        .replace(/(-?\d+)hard/, "Жесткие: $1")
        .replace(/(-?\d+)medium/, "Средние: $1")
        .replace(/(-?\d+)soft/, "Мягкие: $1");
}

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// =========================================================== benchmark

let benchPollTimer = null;
const BENCH_ENGINE_LABELS = {
    timefold_python: "Timefold Py 1.24",
    ortools: "OR-Tools 9.15",
    timefold_kotlin: "Timefold Kotlin 2.0",
    timefold_kotlin_comprehensive: "TF-Kot Compr.",
    solverforge: "SolverForge 1.24",
};

function benchEngineLabel(e) {
    return BENCH_ENGINE_LABELS[e] || e || "—";
}

function benchSelectedEngines() {
    return $(".benchEngine:checked").map(function () { return this.value; }).get();
}

function benchSelectedTimeouts() {
    return $(".benchTimeout:checked").map(function () { return parseInt(this.value, 10); }).get();
}

// Approximate average elapsed/budget ratio per engine (from BENCHMARK_REPORT.md
// statistics). Used to convert raw budget time into a realistic wall-clock
// estimate for a planned run.
const BENCH_OVERHEAD = {
    timefold_python: 1.10,
    ortools:         2.00,
    timefold_kotlin: 1.10,
    // Comprehensive runs 4 islands in parallel for the same wall-clock budget,
    // so wall-clock overhead is the same as plain Kotlin (CPU usage is 4x).
    timefold_kotlin_comprehensive: 1.15,
    solverforge:     1.30,
};

function fmtSeconds(s) {
    s = Math.round(s);
    if (s < 60) return `${s} с`;
    const m = Math.round(s / 60);
    if (m < 90) return `${m} мин`;
    const h = (s / 3600).toFixed(1);
    return `${h} ч`;
}

function benchUpdateEstimate() {
    const engines = benchSelectedEngines();
    const timeouts = benchSelectedTimeouts();
    const datasets = 6;
    if (engines.length === 0 || timeouts.length === 0) {
        $("#benchEstimate").text("выберите движки и таймауты");
        return;
    }
    const totalCells = engines.length * timeouts.length * datasets;
    const sumT = timeouts.reduce((a, b) => a + b, 0);
    // Engine-aware estimate: each engine multiplies its share of budget by its overhead.
    let realisticSec = 0;
    for (const e of engines) {
        const k = BENCH_OVERHEAD[e] || 1.2;
        realisticSec += sumT * datasets * k;
    }
    const minSec = sumT * datasets * engines.length;
    $("#benchEstimate").html(
        `<div><b>${totalCells}</b> прогонов</div>` +
        `<div>≈ <b>${fmtSeconds(realisticSec)}</b> (мин ${fmtSeconds(minSec)})</div>`
    );
}

async function benchStart() {
    const engines = benchSelectedEngines();
    const timeouts = benchSelectedTimeouts();
    if (engines.length === 0 || timeouts.length === 0) {
        alert("Выбери хотя бы один движок и один таймаут.");
        return;
    }
    const append = $("#benchAppend").is(":checked");
    if (!append && !confirm(
        `Запустить бенчмарк? Текущий CSV (если есть) будет перезаписан.\n\n` +
        `Движки: ${engines.length} • Таймауты: ${timeouts.length} с • Датасетов: 6 • Итого ${engines.length * timeouts.length * 6} прогонов.`)) {
        return;
    }
    const r = await fetch("/benchmark/start", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({timeouts, engines, append}),
    });
    const j = await r.json();
    if (!j.ok) {
        const healthLine = j.health
            ? Object.entries(j.health).map(([k, v]) => `${k}: ${v}`).join("; ")
            : "";
        alert("Не удалось запустить бенчмарк:\n" + (j.error || "?") +
              (healthLine ? "\n\n" + healthLine : ""));
    }
    benchRefresh();
}

async function benchResume() {
    const r = await fetch("/benchmark/resume", {method: "POST"});
    const j = await r.json();
    if (!j.ok) alert("Не удалось продолжить бенчмарк: " + (j.error || "?"));
    benchRefresh();
}

async function benchStop() {
    if (!confirm("Остановить бенчмарк? Текущий прогон закончится, остальные не запустятся.")) return;
    const r = await fetch("/benchmark/stop", {method: "DELETE"});
    const j = await r.json();
    if (!j.ok) alert("Не удалось остановить: " + (j.error || "?"));
    benchRefresh();
}

async function benchRefresh() {
    let s;
    try {
        const r = await fetch("/benchmark/status");
        s = await r.json();
    } catch (err) {
        return;
    }

    const total = s.total || 0;
    const done = (s.results || []).length;
    $("#benchProgress").text(total > 0 ? `${done} / ${total}` : "—");

    const cur = s.current;
    $("#benchCurrent").text(cur
        ? `[${(s.cursor || 0) + 1}/${total}] ${cur.timeout_s}s · ${cur.dataset_label} · ${benchEngineLabel(cur.engine)}`
        : (s.running ? "запуск…" : "—"));

    const healthEntries = Object.entries(s.health || {});
    if (healthEntries.length) {
        $("#benchHealth").html(healthEntries.map(([k, v]) =>
            `<span class="text-muted">${escapeHtml(k)}:</span> <b style="color:${v === "ok" ? "#27ae60" : "#c0392b"}">${escapeHtml(v)}</b>`
        ).join(" · "));
    } else {
        $("#benchHealth").text("—");
    }

    const $body = $("#benchTable tbody").empty();
    (s.results || []).slice().reverse().forEach((row) => {
        const reasonClass = row.finish_reason === "error" ? "text-danger" : "";
        $body.append(`
            <tr>
                <td>${row.run_index + 1}</td>
                <td>${row.timeout_s}</td>
                <td>${escapeHtml(row.dataset_label)}</td>
                <td>${row.count_flights}</td>
                <td>${row.teams_per_airport}</td>
                <td>${benchEngineLabel(row.engine)}</td>
                <td>${row.hard ?? "—"}</td>
                <td>${row.soft ?? "—"}</td>
                <td>${row.elapsed_s ?? "—"}</td>
                <td class="${reasonClass}">${escapeHtml(row.finish_reason)}${row.error ? " · " + escapeHtml(row.error) : ""}</td>
            </tr>
        `);
    });

    if (s.running) {
        $("#benchStartButton").prop("disabled", true);
        $("#benchResumeButton").prop("disabled", true);
        $("#benchStopButton").show();
    } else {
        $("#benchStartButton").prop("disabled", false);
        $("#benchResumeButton").prop("disabled", false);
        $("#benchStopButton").hide();
    }
}

function benchStartPolling() {
    if (benchPollTimer) return;
    benchRefresh();
    benchPollTimer = setInterval(benchRefresh, 2000);
}

function benchStopPolling() {
    if (benchPollTimer) {
        clearInterval(benchPollTimer);
        benchPollTimer = null;
    }
}

$(document).on("shown.bs.tab", "#mainBenchTab", function () {
    benchUpdateEstimate();
    benchStartPolling();
});
$(document).on("hidden.bs.tab", "#mainBenchTab", benchStopPolling);
$(document).on("change", ".benchEngine, .benchTimeout", benchUpdateEstimate);

$(document).ready(function () {
    $("#benchStartButton").on("click", benchStart);
    $("#benchResumeButton").on("click", benchResume);
    $("#benchStopButton").on("click", benchStop);
});
