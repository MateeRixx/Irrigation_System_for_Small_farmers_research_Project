(function () {
  "use strict";

  var DEFAULT = { lat: 26.9157, lon: 70.9083 };

  var map = L.map("map", { zoomControl: true, doubleClickZoom: false })
    .setView([DEFAULT.lat, DEFAULT.lon], 14);

  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 22,
      maxNativeZoom: 18,
      attribution: "Esri, Maxar, Earthstar Geographics",
    }
  ).addTo(map);

  L.control.scale({ metric: true, imperial: false }).addTo(map);

  // ---------------------------------------------------------------------
  // Elements
  // ---------------------------------------------------------------------
  var btnRect = document.getElementById("btn-draw");
  var btnPoly = document.getElementById("btn-poly");
  var btnClear = document.getElementById("btn-clear");
  var btnRun = document.getElementById("btn-run");
  var hint = document.getElementById("map-hint");
  var hud = document.getElementById("map-hud");
  var selStatus = document.getElementById("sel-status");
  var selShape = document.getElementById("sel-shape");
  var searchInput = document.getElementById("place-search");
  var farmSelect = document.getElementById("farm_id");
  var kInput = document.getElementById("force_k");

  var STYLE = { color: "#7CE496", weight: 2, fill: false, dashArray: "6 5" };

  // shape state ------------------------------------------------------------
  var mode = "idle";           // idle | rect | poly
  var shapeLayer = null;       // L.rectangle or L.polygon (finished shape)
  var rectBounds = null;       // L.latLngBounds when shape is a rect
  var polyLatLngs = [];        // vertex list when shape is a polygon
  var handles = [];            // draggable vertex/corner markers
  var centerHandle = null;     // rect move handle
  var drawStart = null;        // rect drag anchor
  var tempLine = null;         // in-progress polygon outline
  var drawingRect = false;

  // ---------------------------------------------------------------------
  // Tabs (map / upload)
  // ---------------------------------------------------------------------
  var tabMap = document.getElementById("tab-map");
  var tabUpload = document.getElementById("tab-upload");
  var panelMap = document.getElementById("panel-map");
  var panelUpload = document.getElementById("panel-upload");

  function selectTab(which) {
    var isMap = which === "map";
    tabMap.classList.toggle("is-active", isMap);
    tabUpload.classList.toggle("is-active", !isMap);
    tabMap.setAttribute("aria-selected", String(isMap));
    tabUpload.setAttribute("aria-selected", String(!isMap));
    panelMap.classList.toggle("is-hidden", !isMap);
    panelUpload.classList.toggle("is-hidden", isMap);
    if (isMap) setTimeout(function () { map.invalidateSize(); }, 60);
  }
  tabMap.addEventListener("click", function () { selectTab("map"); });
  tabUpload.addEventListener("click", function () { selectTab("upload"); });

  // ---------------------------------------------------------------------
  // Geometry helpers
  // ---------------------------------------------------------------------
  function rectAreaHa(b) {
    var midLat = (b.getNorth() + b.getSouth()) / 2;
    var wM = (b.getEast() - b.getWest()) * 111320 * Math.cos((midLat * Math.PI) / 180);
    var hM = (b.getNorth() - b.getSouth()) * 110540;
    return (wM * hM) / 10000;
  }

  function polyAreaHa(lls) {
    if (lls.length < 3) return 0;
    var lat0 = 0;
    lls.forEach(function (l) { lat0 += l.lat; });
    lat0 = (lat0 / lls.length) * Math.PI / 180;
    var kx = 111320 * Math.cos(lat0), ky = 110540;
    var sum = 0;
    for (var i = 0; i < lls.length; i++) {
      var a = lls[i], b = lls[(i + 1) % lls.length];
      sum += (a.lng * kx) * (b.lat * ky) - (b.lng * kx) * (a.lat * ky);
    }
    return Math.abs(sum / 2) / 10000;
  }

  function fmtCoord(v) { return v.toFixed(5) + "°"; }

  function fmtArea(ha) {
    return ha < 1 ? (ha * 100).toFixed(0) + " ares" : ha.toFixed(2) + " ha";
  }

  function getBounds() {
    if (rectBounds) return rectBounds;
    if (polyLatLngs.length >= 2) return L.latLngBounds(polyLatLngs);
    return null;
  }

  // ---------------------------------------------------------------------
  // HUD + run button
  // ---------------------------------------------------------------------
  function updateHud() {
    var b = getBounds();
    var hasShape = (rectBounds && shapeLayer) || (polyLatLngs.length >= 3 && shapeLayer);
    if (!hasShape || !b) {
      hud.hidden = true;
      selStatus.textContent = mode === "idle" ? "None yet" : "Drawing…";
      if (selShape) selShape.textContent = "—";
      btnRun.disabled = true;
      return;
    }
    hud.hidden = false;
    selStatus.textContent = "Shape ready";
    btnRun.disabled = false;
    document.getElementById("hud-nw").textContent =
      fmtCoord(b.getWest()) + ", " + fmtCoord(b.getNorth());
    document.getElementById("hud-se").textContent =
      fmtCoord(b.getEast()) + ", " + fmtCoord(b.getSouth());
    var c = b.getCenter();
    document.getElementById("hud-center").textContent =
      fmtCoord(c.lat) + ", " + fmtCoord(c.lng);
    var ha = rectBounds ? rectAreaHa(rectBounds) : polyAreaHa(polyLatLngs);
    document.getElementById("hud-area").textContent = fmtArea(ha);
    if (selShape) {
      selShape.textContent = rectBounds
        ? "Rectangle · " + fmtArea(ha)
        : "Polygon · " + polyLatLngs.length + " vertices · " + fmtArea(ha);
    }
  }

  // ---------------------------------------------------------------------
  // Handles (draggable markers for editing finished shapes)
  // ---------------------------------------------------------------------
  function clearHandles() {
    handles.forEach(function (h) { map.removeLayer(h); });
    handles = [];
    if (centerHandle) { map.removeLayer(centerHandle); centerHandle = null; }
  }

  function makeHandle(ll, onDrag) {
    var m = L.marker(ll, {
      draggable: true,
      keyboard: false,
      icon: L.divIcon({ className: "map-handle", iconSize: [12, 12] }),
    }).addTo(map);
    m.on("drag", onDrag);
    m.on("dragend", updateHud);
    return m;
  }

  function buildRectHandles() {
    clearHandles();
    if (!rectBounds) return;
    var corners = [
      L.latLng(rectBounds.getNorth(), rectBounds.getWest()),
      L.latLng(rectBounds.getNorth(), rectBounds.getEast()),
      L.latLng(rectBounds.getSouth(), rectBounds.getEast()),
      L.latLng(rectBounds.getSouth(), rectBounds.getWest()),
    ];
    corners.forEach(function (corner, i) {
      var anchorIdx = (i + 2) % 4; // opposite corner
      handles.push(makeHandle(corner, function (e) {
        var anchor = handles[anchorIdx] ? handles[anchorIdx].getLatLng() : null;
        if (!anchor) return;
        rectBounds = L.latLngBounds(anchor, e.latlng);
        shapeLayer.setBounds(rectBounds);
        // reposition the other three corners
        var pts = [
          L.latLng(rectBounds.getNorth(), rectBounds.getWest()),
          L.latLng(rectBounds.getNorth(), rectBounds.getEast()),
          L.latLng(rectBounds.getSouth(), rectBounds.getEast()),
          L.latLng(rectBounds.getSouth(), rectBounds.getWest()),
        ];
        handles.forEach(function (h, j) {
          if (j !== i) h.setLatLng(pts[j]);
        });
        if (centerHandle) centerHandle.setLatLng(rectBounds.getCenter());
      }));
    });

    centerHandle = makeHandle(rectBounds.getCenter(), function (e) {
      if (!rectBounds) return;
      var c = rectBounds.getCenter();
      var dLat = e.latlng.lat - c.lat, dLng = e.latlng.lng - c.lng;
      rectBounds = L.latLngBounds(
        L.latLng(rectBounds.getSouth() + dLat, rectBounds.getWest() + dLng),
        L.latLng(rectBounds.getNorth() + dLat, rectBounds.getEast() + dLng)
      );
      shapeLayer.setBounds(rectBounds);
      var pts = [
        L.latLng(rectBounds.getNorth(), rectBounds.getWest()),
        L.latLng(rectBounds.getNorth(), rectBounds.getEast()),
        L.latLng(rectBounds.getSouth(), rectBounds.getEast()),
        L.latLng(rectBounds.getSouth(), rectBounds.getWest()),
      ];
      handles.forEach(function (h, j) { h.setLatLng(pts[j]); });
      centerHandle.setLatLng(rectBounds.getCenter());
    });
    centerHandle.getElement().classList.add("map-handle-center");
  }

  function buildPolyHandles() {
    clearHandles();
    polyLatLngs.forEach(function (ll, i) {
      handles.push(makeHandle(ll, function (e) {
        polyLatLngs[i] = e.latlng;
        shapeLayer.setLatLngs(polyLatLngs);
        if (tempLine) tempLine.setLatLngs(polyLatLngs);
      }));
    });
  }

  // ---------------------------------------------------------------------
  // Drawing
  // ---------------------------------------------------------------------
  function setMode(next) {
    mode = next;
    btnRect.classList.toggle("is-on", next === "rect");
    btnPoly.classList.toggle("is-on", next === "poly");
    btnRect.querySelector("span").textContent = next === "rect" ? "Drawing…" : "Rectangle";
    btnPoly.querySelector("span").textContent = next === "poly" ? "Drawing…" : "Polygon";
    map.getContainer().style.cursor = next === "idle" ? "" : "crosshair";
    if (next === "rect") {
      hint.innerHTML = "Drag over your plot to outline it. Release, then drag the corner or centre handles to adjust.";
    } else if (next === "poly") {
      hint.innerHTML = "Click to add vertices around an irregular field. Double-click to finish.";
      map.doubleClickZoom.disable();
    } else {
      map.doubleClickZoom.enable();
    }
  }

  function removeShape() {
    if (shapeLayer) { map.removeLayer(shapeLayer); shapeLayer = null; }
    if (tempLine) { map.removeLayer(tempLine); tempLine = null; }
    clearHandles();
    rectBounds = null;
    polyLatLngs = [];
    drawStart = null;
    drawingRect = false;
  }

  btnRect.addEventListener("click", function () {
    removeShape();
    setMode(mode === "rect" ? "idle" : "rect");
    updateHud();
    if (mode === "idle") hint.innerHTML = defaultHint();
  });

  btnPoly.addEventListener("click", function () {
    removeShape();
    setMode(mode === "poly" ? "idle" : "poly");
    updateHud();
    if (mode === "idle") hint.innerHTML = defaultHint();
  });

  btnClear.addEventListener("click", function () {
    removeShape();
    setMode("idle");
    hint.innerHTML = defaultHint();
    updateHud();
  });

  function defaultHint() {
    return 'Press <strong>Rectangle</strong> or <strong>Polygon</strong> to outline your plot.';
  }

  // rectangle drag -------------------------------------------------------
  map.on("mousedown", function (e) {
    if (mode !== "rect") return;
    drawingRect = true;
    drawStart = e.latlng;
    removeShape();
    rectBounds = L.latLngBounds(drawStart, drawStart);
    shapeLayer = L.rectangle(rectBounds, STYLE).addTo(map);
    map.dragging.disable();
  });

  map.on("mousemove", function (e) {
    if (!drawingRect || !drawStart) return;
    rectBounds.extend(e.latlng);
    shapeLayer.setBounds(rectBounds);
    updateHud();
  });

  function finishRect() {
    if (!drawingRect) return;
    drawingRect = false;
    map.dragging.enable();
    if (rectBounds && rectAreaHa(rectBounds) > 0.0001) {
      setMode("idle");
      buildRectHandles();
      hint.innerHTML = "Shape ready — drag handles to adjust, then run the analysis.";
      updateHud();
    }
  }
  map.on("mouseup", finishRect);
  map.on("mouseout", function () { if (drawingRect) finishRect(); });

  // polygon click --------------------------------------------------------
  map.on("click", function (e) {
    if (mode !== "poly" || drawingRect) return;
    polyLatLngs.push(e.latlng);
    if (!shapeLayer) {
      shapeLayer = L.polygon(polyLatLngs, STYLE).addTo(map);
    } else {
      shapeLayer.setLatLngs(polyLatLngs);
    }
    if (!tempLine) {
      tempLine = L.polyline(polyLatLngs, {
        color: "#7CE496", weight: 1, dashArray: "3 6", fill: false,
      }).addTo(map);
    } else {
      tempLine.setLatLngs(polyLatLngs);
    }
    updateHud();
    hint.innerHTML = polyLatLngs.length < 3
      ? "Keep clicking to add vertices (" + polyLatLngs.length + " so far). Double-click to finish."
      : "Double-click to finish the polygon (" + polyLatLngs.length + " vertices).";
  });

  map.on("dblclick", function () {
    if (mode !== "poly") return;
    if (polyLatLngs.length >= 3) {
      if (tempLine) { map.removeLayer(tempLine); tempLine = null; }
      setMode("idle");
      buildPolyHandles();
      hint.innerHTML = "Polygon ready — drag vertices to adjust, then run the analysis.";
      updateHud();
    } else {
      hint.innerHTML = "A polygon needs at least 3 vertices.";
    }
  });

  // ---------------------------------------------------------------------
  // Place search (Nominatim)
  // ---------------------------------------------------------------------
  var marker = null;
  function searchPlace() {
    var q = searchInput.value.trim();
    if (!q) return;
    hint.textContent = "Searching…";
    fetch("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        if (!rows.length) {
          hint.textContent = "No place found for “" + q + "”. Try a village, city or district name.";
          return;
        }
        var r0 = rows[0];
        var lat = parseFloat(r0.lat), lon = parseFloat(r0.lon);
        map.flyTo([lat, lon], 15, { duration: 1.1 });
        if (marker) map.removeLayer(marker);
        marker = L.marker([lat, lon]).addTo(map);
        removeShape();
        setMode("rect");
        updateHud();
        hint.textContent = "At " + r0.display_name.split(",").slice(0, 2).join(",") +
          ". Drag a rectangle over your plot.";
      })
      .catch(function () { hint.textContent = "Place search is unreachable right now."; });
  }
  document.getElementById("place-go").addEventListener("click", searchPlace);
  searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); searchPlace(); }
  });

  // ---------------------------------------------------------------------
  // Farm selector flies to location
  // ---------------------------------------------------------------------
  farmSelect.addEventListener("change", function () {
    var id = parseInt(farmSelect.value, 10);
    var farm = (window.ECOVRI.farms || []).find(function (f) { return f.id === id; });
    if (farm) map.flyTo([farm.lat, farm.lon], 14, { duration: 1.0 });
  });

  // ---------------------------------------------------------------------
  // Run analysis (with staged overlay)
  // ---------------------------------------------------------------------
  var overlay = document.getElementById("run-overlay");
  var overlaySteps = overlay.querySelectorAll(".run-step");
  var overlayError = document.getElementById("run-error");
  var overlayTitle = document.getElementById("run-title");
  var btnCancel = document.getElementById("run-cancel");
  var stageTimers = [];
  var activeController = null;

  function showOverlay(title, labels) {
    overlayTitle.textContent = title;
    overlay.classList.remove("is-hidden");
    overlayError.classList.add("is-hidden");
    overlaySteps.forEach(function (li, i) {
      li.classList.remove("is-active", "is-done");
      li.querySelector(".run-step-label").textContent = labels[i];
    });
    setStage(0);
    stageTimers.push(setTimeout(function () { setStage(1); }, 3500));
    stageTimers.push(setTimeout(function () { setStage(2); }, 9000));
  }

  function setStage(idx) {
    overlaySteps.forEach(function (li, i) {
      li.classList.toggle("is-active", i === idx);
      li.classList.toggle("is-done", i < idx);
    });
  }

  function hideOverlay() {
    stageTimers.forEach(clearTimeout);
    stageTimers = [];
    overlay.classList.add("is-hidden");
  }

  function failOverlay(msg) {
    stageTimers.forEach(clearTimeout);
    stageTimers = [];
    overlaySteps.forEach(function (li) { li.classList.remove("is-active"); });
    overlayError.textContent = msg;
    overlayError.classList.remove("is-hidden");
    btnCancel.textContent = "Close";
  }

  btnCancel.addEventListener("click", function () {
    if (activeController) activeController.abort();
    hideOverlay();
    btnCancel.textContent = "Cancel";
    resetRunButton();
  });

  function resetRunButton() {
    if (btnRun) {
      btnRun.disabled = !getBounds() || !(shapeLayer && ((rectBounds) || polyLatLngs.length >= 3));
      btnRun.querySelector("span").textContent = "Run analysis";
    }
    var upSubmit = document.querySelector("#upload-form button[type=submit]");
    if (upSubmit) {
      upSubmit.disabled = false;
      upSubmit.querySelector("span").textContent = "Run analysis";
    }
  }

  var MAP_STAGES = [
    "Fetching satellite imagery",
    "Clustering pixels (K-Means)",
    "Scoring zones & pulling weather",
  ];
  var UPLOAD_STAGES = [
    "Uploading field image",
    "Clustering pixels (K-Means)",
    "Scoring zones & pulling weather",
  ];

  btnRun.addEventListener("click", function () {
    var b = getBounds();
    if (!b || !shapeLayer) return;
    btnRun.disabled = true;
    btnRun.querySelector("span").textContent = "Working…";
    showOverlay("Running analysis", MAP_STAGES);
    btnCancel.textContent = "Cancel";
    activeController = new AbortController();

    var payload = {
      bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()],
      farm_id: parseInt(farmSelect.value, 10) || 0,
      force_k: parseInt(kInput.value, 10) || null,
    };

    fetch(window.ECOVRI.endpoints.run, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: activeController.signal,
    })
      .then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, body: j }; });
      })
      .then(function (res) {
        if (res.ok && res.body.redirect) {
          setStage(3);
          window.location.href = res.body.redirect;
          return;
        }
        throw new Error(res.body.error || "Analysis failed.");
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        failOverlay(err.message || "Analysis failed. Try a smaller selection.");
        resetRunButton();
      });
  });

  // ---------------------------------------------------------------------
  // Upload form runs through the same overlay
  // ---------------------------------------------------------------------
  var uploadForm = document.getElementById("upload-form");
  uploadForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var fileInput = document.getElementById("image");
    if (!fileInput.files.length) return;

    var fd = new FormData(uploadForm);
    var submitBtn = uploadForm.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    submitBtn.querySelector("span").textContent = "Working…";
    showOverlay("Running analysis", UPLOAD_STAGES);
    btnCancel.textContent = "Cancel";
    activeController = new AbortController();

    fetch(uploadForm.action, {
      method: "POST",
      body: fd,
      signal: activeController.signal,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (r.redirected) { window.location.href = r.url; return null; }
        return r.text().then(function (t) {
          throw new Error(r.ok ? "Unexpected server response." :
            (t.slice(0, 160) || "Upload failed."));
        });
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        failOverlay(err.message);
        resetRunButton();
      });
  });

  // ---------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------
  if (farmSelect.value && farmSelect.value !== "0") farmSelect.dispatchEvent(new Event("change"));
  hint.innerHTML = defaultHint();
  updateHud();
})();
