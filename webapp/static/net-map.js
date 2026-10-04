/* NetMap — общий движок карты сети (COSMOS Конвейер/Результаты И
 * панель CDPM): силовая «схема» на сеяных островах либо «карта мира»
 * на контурах Natural Earth в равнопромежуточной проекции.
 *
 * Выделен из app.js пакетом трёх работ 2026-08-30 (карта в CDPM):
 * модуль БЕЗ зависимостей от app.js — хозяин отдаёт хуки параметрами.
 *
 * Контракт NetMap.draw(cfg):
 *   svg    — целевой SVGElement (обязателен);
 *   note   — элемент подписи под картой (не обязателен);
 *   data   — { nodes: [{code, base?}], arcs: [{a, b, n?, ndh?, nunc?}],
 *              chains?: [{cid, legs: [{dep, arr, dh?}]}],
 *              geo?: {coords: {code: [lat, lon]}, complete: bool} };
 *   sel    — cid выбранной цепочки (подсветка маршрута) или null;
 *   mode   — "schema" | "world"; «мир» рисуется ТОЛЬКО при
 *            data.geo.complete — включает человек, умолчание всегда
 *            схема (вымышленные коды обезличенных наборов случайно
 *            совпадают с настоящими — см. webapp/geo.py);
 *   seed   — метка раскладки (одна метка = одна раскладка);
 *   redraw — колбэк перерисовки после дозагрузки мировой подложки;
 *   hover  — fn(el, htmlFn) тултипов хозяина; нет хука — без тултипов;
 *   labels — переведённые подписи хозяина: {arc, dhArc, uncArc, base,
 *            selNote(cid)}; нет — подписи опускаются.
 *
 * Мировая подложка грузится лениво один раз на сессию с адреса
 * NetMap.worldUrl (переопределяем: у панели CDPM свой путь статики).
 */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  function _svg(tag, attrs) {
    var n = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      n.setAttribute(k, attrs[k]);
    });
    return n;
  }

  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /** FNV-1a: зерно ГПСЧ из метки решения. */
  function _seed(s) {
    var h = 2166136261;
    s = String(s || "pv");
    for (var i = 0; i < s.length; i++) {
      h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    }
    return h >>> 0;
  }

  /** mulberry32 — сеяный ГПСЧ: одна метка решения = одна раскладка. */
  function _rand(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) | 0;
      var x = Math.imul(a ^ (a >>> 15), 1 | a);
      x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
      return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* Силовая раскладка (Фрухтерман—Рейнгольд, фиксированные итерации):
   * узлы расталкиваются, рёбра стягивают; старт — окружность с сеяным
   * дрожанием, порядок узлов отсортирован — раскладка детерминирована. */
  function _layout(codes, arcs, seedStr) {
    var rnd = _rand(_seed(seedStr));
    var n = codes.length || 1;
    var pos = {};
    codes.forEach(function (c, i) {
      var ang = 2 * Math.PI * i / n;
      pos[c] = { x: Math.cos(ang) + (rnd() - 0.5) * 0.2,
                 y: Math.sin(ang) + (rnd() - 0.5) * 0.2 };
    });
    var k = Math.sqrt(4 / n);
    var iters = 220;
    for (var it = 0; it < iters; it++) {
      var disp = {};
      codes.forEach(function (c) { disp[c] = { x: 0, y: 0 }; });
      for (var i2 = 0; i2 < codes.length; i2++) {
        for (var j = i2 + 1; j < codes.length; j++) {
          var a = pos[codes[i2]], b = pos[codes[j]];
          var dx = a.x - b.x, dy = a.y - b.y;
          var dist = Math.sqrt(dx * dx + dy * dy) || 1e-4;
          var f = (k * k) / dist;
          var ux = dx / dist, uy = dy / dist;
          disp[codes[i2]].x += ux * f;
          disp[codes[i2]].y += uy * f;
          disp[codes[j]].x -= ux * f;
          disp[codes[j]].y -= uy * f;
        }
      }
      arcs.forEach(function (e) {
        var pa = pos[e.a], pb = pos[e.b];
        if (!pa || !pb) { return; }
        var dx = pa.x - pb.x, dy = pa.y - pb.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 1e-4;
        var f = (dist * dist) / k;
        var ux = dx / dist, uy = dy / dist;
        disp[e.a].x -= ux * f;
        disp[e.a].y -= uy * f;
        disp[e.b].x += ux * f;
        disp[e.b].y += uy * f;
      });
      var temp = 0.14 * (1 - it / iters) + 0.005;
      codes.forEach(function (c) {
        var dv = disp[c];
        var len = Math.sqrt(dv.x * dv.x + dv.y * dv.y) || 1e-6;
        var step = Math.min(len, temp);
        pos[c].x += dv.x / len * step;
        pos[c].y += dv.y / len * step;
      });
    }
    return pos;
  }

  /* Мировая подложка: контуры суши Natural Earth 110m (общественное
   * достояние) из статики, загрузка лениво один раз на сессию. */
  var _world = { rings: null, loading: null };
  function _worldRings() {
    if (_world.rings) { return Promise.resolve(_world.rings); }
    if (!_world.loading) {
      _world.loading = fetch(NetMap.worldUrl)
        .then(function (r) {
          if (!r.ok) { throw new Error("world-110m: " + r.status); }
          return r.json();
        })
        .then(function (rings) {
          if (!Array.isArray(rings)) {
            throw new Error("world-110m: not an array");
          }
          _world.rings = rings;
          return rings;
        });
    }
    return _world.loading;
  }

  /* Синтетическая подложка режима «схема»: сеяные «острова» под тем же
   * зерном, что раскладка, — произвольная карта выдуманного мира. */
  function _islandsBg(svg, seedStr, W, H) {
    var rnd = _rand(_seed("isl:" + seedStr));
    var g = _svg("g", { opacity: ".55" });
    for (var i = 0; i < 7; i++) {
      var cx = W * (0.08 + 0.84 * rnd());
      var cy = H * (0.08 + 0.84 * rnd());
      var r0 = 24 + rnd() * Math.min(W, H) * 0.16;
      var pts = [];
      var steps = 14;
      for (var a = 0; a < steps; a++) {
        var ang = 2 * Math.PI * a / steps;
        var rr = r0 * (0.62 + 0.5 * rnd());
        pts.push((cx + Math.cos(ang) * rr).toFixed(1) + "," +
                 (cy + Math.sin(ang) * rr * 0.72).toFixed(1));
      }
      g.appendChild(_svg("polygon", { points: pts.join(" "),
        fill: "var(--rv-duty)", opacity: ".10",
        stroke: "var(--rv-duty)", "stroke-opacity": ".25" }));
    }
    for (var gy = 1; gy < 5; gy++) {
      g.appendChild(_svg("line", { x1: 0, y1: H * gy / 5, x2: W,
        y2: H * gy / 5, stroke: "var(--muted)", opacity: ".12",
        "stroke-dasharray": "2 6" }));
    }
    svg.appendChild(g);
  }

  /* Мировая подложка режима «карта мира»: кольца суши в той же
   * равнопромежуточной проекции, что и точки (prj), с клипом вьюпорта. */
  function _worldBg(svg, rings, prj, W, H) {
    var g = _svg("g", {});
    rings.forEach(function (ring) {
      var pts = [], inside = false;
      for (var i = 0; i < ring.length; i++) {
        var p = prj(ring[i][1], ring[i][0]);
        if (p[0] > -80 && p[0] < W + 80 &&
            p[1] > -80 && p[1] < H + 80) {
          inside = true;
        }
        pts.push(p[0].toFixed(1) + "," + p[1].toFixed(1));
      }
      if (!inside) { return; }
      g.appendChild(_svg("polygon", { points: pts.join(" "),
        fill: "var(--rv-duty)", opacity: ".08",
        stroke: "var(--rv-duty)", "stroke-opacity": ".30",
        "stroke-width": ".7" }));
    });
    svg.appendChild(g);
  }

  function _drawNet(svg, mn, d, sel, nodes, arcs, PX, PY, hover, lb) {
    var hook = typeof hover === "function"
      ? hover : function () {};
    lb = lb || {};
    // дуги-рейсы: толщина — от числа рейсов; непокрытые — красным
    arcs.forEach(function (a) {
      if (PX(a.a) == null || PX(a.b) == null) { return; }
      var bad = (a.nunc || 0) > 0;
      svg.appendChild(_svg("line", { x1: PX(a.a), y1: PY(a.a),
        x2: PX(a.b), y2: PY(a.b),
        stroke: bad ? "var(--rv-bad)" : "var(--rv-duty)",
        "stroke-width": 1 + Math.min(4, Math.log(1 + (a.n || 1))),
        opacity: bad ? ".85" : ".3", "stroke-linecap": "round" }));
      var hit = _svg("line", { x1: PX(a.a), y1: PY(a.a),
        x2: PX(a.b), y2: PY(a.b), stroke: "transparent",
        "stroke-width": "9" });
      hook(hit, function () {
        return "<b>" + _esc(a.a) + " – " + _esc(a.b) + "</b>" +
          "<div class='rv-tt'>" + _esc(lb.arc || "") + " " + (a.n || 0) +
          ((a.ndh || 0)
            ? " · " + _esc(lb.dhArc || "") + " " + a.ndh : "") +
          ((a.nunc || 0)
            ? " · " + _esc(lb.uncArc || "") + " " + a.nunc : "") +
          "</div>";
      });
      svg.appendChild(hit);
    });
    // маршрут выбранной цепочки — поверх дуг, перегоны пунктиром
    var selChain = null;
    if (sel) {
      selChain = (d.chains || []).find(function (c) {
        return c.cid === sel;
      }) || null;
    }
    if (selChain) {
      (selChain.legs || []).forEach(function (l) {
        if (PX(l.dep) == null || PX(l.arr) == null) { return; }
        svg.appendChild(_svg("line", { x1: PX(l.dep), y1: PY(l.dep),
          x2: PX(l.arr), y2: PY(l.arr), stroke: "var(--accent)",
          "stroke-width": "2.6", opacity: ".95",
          "stroke-linecap": "round",
          "stroke-dasharray": l.dh ? "6 4" : "none" }));
      });
    }
    // узлы-города; базы — крупнее и цветом акцента
    nodes.forEach(function (nd) {
      var isBase = !!nd.base;
      var cx = PX(nd.code), cy = PY(nd.code);
      if (cx == null || cy == null) { return; }
      var circ = _svg("circle", { cx: cx, cy: cy, r: isBase ? 7 : 4,
        fill: isBase ? "var(--accent)" : "var(--rv-duty)",
        stroke: "var(--card-bg)", "stroke-width": "1.5" });
      hook(circ, function () {
        return "<b>" + _esc(nd.code) + "</b>" +
          (isBase ? " · " + _esc(lb.base || "") : "");
      });
      svg.appendChild(circ);
      var lab = _svg("text", { x: cx, y: cy - (isBase ? 11 : 8),
        "font-size": isBase ? "10" : "9", "text-anchor": "middle",
        fill: isBase ? "var(--ink)" : "var(--muted)",
        "font-weight": isBase ? "600" : "400" });
      lab.textContent = nd.code;
      svg.appendChild(lab);
    });
    if (selChain && mn && typeof lb.selNote === "function") {
      mn.textContent = lb.selNote(selChain.cid);
    }
  }

  function draw(cfg) {
    var d = cfg.data || {};
    var svg = cfg.svg, mn = cfg.note;
    svg.textContent = "";
    if (mn) { mn.textContent = ""; }
    var nodes = (d.nodes || []).slice();
    var arcs = d.arcs || [];
    var seen = {};
    nodes.forEach(function (nd) { seen[nd.code] = true; });
    // терпимость к неполному ответу: конец дуги без узла достраивается
    arcs.forEach(function (a) {
      [a.a, a.b].forEach(function (code) {
        if (!seen[code]) {
          seen[code] = true;
          nodes.push({ code: code, base: false });
        }
      });
    });
    if (!nodes.length) { return; }
    var codes = nodes.map(function (nd) { return nd.code; }).sort();
    var W = svg.clientWidth || 720, H = 460, pad = 30;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("height", H);
    var geo = d.geo || {};
    var world = cfg.mode === "world" && geo.complete;
    var PX, PY, prj;
    if (world) {
      // равнопромежуточная проекция по рамке сети; поправка долготы
      // косинусом средней широты, чтобы дуги не растягивало у полюсов
      var lats = [], lons = [];
      codes.forEach(function (c) {
        var ll = geo.coords[c];
        if (ll) { lats.push(ll[0]); lons.push(ll[1]); }
      });
      var la0 = Math.min.apply(null, lats);
      var la1 = Math.max.apply(null, lats);
      var lo0 = Math.min.apply(null, lons);
      var lo1 = Math.max.apply(null, lons);
      var padDeg = Math.max(1.5, (la1 - la0) * 0.25,
                            (lo1 - lo0) * 0.12);
      la0 -= padDeg; la1 += padDeg;
      lo0 -= padDeg * 1.4; lo1 += padDeg * 1.4;
      var k = Math.cos((la0 + la1) / 2 * Math.PI / 180);
      var s = Math.min(
        (W - 2 * pad) / Math.max(1e-6, (lo1 - lo0) * k),
        (H - 2 * pad) / Math.max(1e-6, la1 - la0));
      var ox = (W - (lo1 - lo0) * k * s) / 2;
      var oy = (H - (la1 - la0) * s) / 2;
      prj = function (lat, lon) {
        return [ox + (lon - lo0) * k * s, oy + (la1 - lat) * s];
      };
      var P = {};
      codes.forEach(function (c) {
        var ll = geo.coords[c];
        if (ll) { P[c] = prj(ll[0], ll[1]); }
      });
      PX = function (c) { return P[c] ? P[c][0] : null; };
      PY = function (c) { return P[c] ? P[c][1] : null; };
      if (_world.rings) {
        _worldBg(svg, _world.rings, prj, W, H);
      } else {
        _worldRings().then(function () {
          if (cfg.redraw) { cfg.redraw(); }
        });
      }
    } else {
      var pos = _layout(codes, arcs, cfg.seed || "pv");
      var minX = Infinity, maxX = -Infinity,
          minY = Infinity, maxY = -Infinity;
      codes.forEach(function (c) {
        if (pos[c].x < minX) { minX = pos[c].x; }
        if (pos[c].x > maxX) { maxX = pos[c].x; }
        if (pos[c].y < minY) { minY = pos[c].y; }
        if (pos[c].y > maxY) { maxY = pos[c].y; }
      });
      var sx = (W - 2 * pad) / Math.max(1e-6, maxX - minX);
      var sy = (H - 2 * pad) / Math.max(1e-6, maxY - minY);
      PX = function (c) { return pad + (pos[c].x - minX) * sx; };
      PY = function (c) { return pad + (pos[c].y - minY) * sy; };
      _islandsBg(svg, cfg.seed || "pv", W, H);
    }
    _drawNet(svg, mn, d, cfg.sel, nodes, arcs, PX, PY,
             cfg.hover, cfg.labels);
  }

  var NetMap = {
    worldUrl: "geo/world-110m.json",
    draw: draw
  };
  window.NetMap = NetMap;
})();
