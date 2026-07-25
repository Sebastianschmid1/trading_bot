/* Galerie-Interaktion: Drag&Drop-Upload mit Fortschritt, Lightbox, Lösch-Bestätigung.
   Vanilla JS, keine externen Bibliotheken. */
(function () {
    "use strict";

    var BASE = document.body.getAttribute("data-base") || "";

    // ------------------------------------------------------------ Upload --
    var form = document.getElementById("upload-form");
    var input = document.getElementById("file-input");
    var progress = document.getElementById("upload-progress");
    var fill = document.getElementById("progress-fill");
    var label = document.getElementById("progress-label");
    var results = document.getElementById("upload-results");
    var refreshBtn = document.getElementById("refresh-gallery");
    var shell = document.getElementById("upload-shell");
    var fab = document.getElementById("upload-fab");
    var closeBtn = document.getElementById("upload-close");

    function showResults(items, fallbackError) {
        results.innerHTML = "";
        var failed = false;
        (items || []).forEach(function (item) {
            var li = document.createElement("li");
            if (item.ok) {
                li.textContent = "✓ " + item.original_name + " hochgeladen";
            } else {
                failed = true;
                li.className = "is-error";
                li.textContent = "✗ " + item.original_name + " — " + (item.error || "abgelehnt");
            }
            results.appendChild(li);
        });
        if (fallbackError) {
            failed = true;
            var li = document.createElement("li");
            li.className = "is-error";
            li.textContent = fallbackError;
            results.appendChild(li);
        }
        results.hidden = results.children.length === 0;
        return failed;
    }

    function upload(fileList) {
        var files = Array.prototype.slice.call(fileList || []);
        if (!files.length) {
            return;
        }
        var data = new FormData();
        files.forEach(function (file) {
            data.append("files", file, file.name);
        });

        results.hidden = true;
        refreshBtn.hidden = true;
        progress.hidden = false;
        fill.style.width = "0%";
        label.textContent = "Lade " + files.length + (files.length === 1 ? " Foto" : " Fotos") + " hoch …";

        var xhr = new XMLHttpRequest();
        xhr.open("POST", BASE + "/upload", true);
        xhr.upload.addEventListener("progress", function (event) {
            if (event.lengthComputable) {
                var percent = Math.round((event.loaded / event.total) * 100);
                fill.style.width = percent + "%";
                label.textContent = "Lade hoch … " + percent + "%";
            }
        });
        xhr.addEventListener("load", function () {
            fill.style.width = "100%";
            progress.hidden = true;
            var payload = {};
            try {
                payload = JSON.parse(xhr.responseText || "{}");
            } catch (err) {
                payload = {};
            }
            if (xhr.status === 401) {
                window.location.href = payload.login || BASE + "/login";
                return;
            }
            var failed = showResults(payload.results, payload.error);
            if (!failed) {
                label.textContent = "Fertig!";
                window.setTimeout(function () {
                    window.location.reload();
                }, 700);
            } else {
                refreshBtn.hidden = false;
            }
        });
        xhr.addEventListener("error", function () {
            progress.hidden = true;
            showResults([], "Upload fehlgeschlagen — bitte Verbindung prüfen und nochmal versuchen.");
            refreshBtn.hidden = false;
        });
        xhr.send(data);
    }

    // Nur-Ansehen-Zugang: die Upload-Karte wird gar nicht gerendert — dann alles überspringen.
    if (form && input && progress && fill && label && results && refreshBtn && shell && fab) {
        // Ausklapp-Steuerung. Im Markup ist die Karte sichtbar (damit sie ohne JS
        // benutzbar bleibt); hier wird sie eingeklappt und der „+"-Button aktiviert.
        var setOpen = function (open) {
            shell.classList.toggle("is-collapsed", !open);
            fab.classList.toggle("is-open", open);
            fab.setAttribute("aria-expanded", open ? "true" : "false");
            fab.textContent = open ? "Schließen" : "+ Fotos hinzufügen";
            if (open) {
                var smooth = !window.matchMedia
                    || !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
                shell.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "center" });
            }
        };

        setOpen(false);
        fab.hidden = false;
        if (closeBtn) {
            closeBtn.hidden = false;
            closeBtn.addEventListener("click", function () {
                setOpen(false);
                fab.focus({ preventScroll: true });
            });
        }
        fab.addEventListener("click", function () {
            setOpen(shell.classList.contains("is-collapsed"));
        });

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            upload(input.files);
        });
        input.addEventListener("change", function () {
            upload(input.files);
            input.value = "";
        });

        ["dragenter", "dragover"].forEach(function (name) {
            form.addEventListener(name, function (event) {
                event.preventDefault();
                form.classList.add("is-dragover");
            });
        });
        ["dragleave", "dragend", "drop"].forEach(function (name) {
            form.addEventListener(name, function (event) {
                event.preventDefault();
                form.classList.remove("is-dragover");
            });
        });
        form.addEventListener("drop", function (event) {
            if (event.dataTransfer && event.dataTransfer.files) {
                upload(event.dataTransfer.files);
            }
        });
        // Ein versehentlicher Drop neben die Zone soll die Datei nicht im Tab öffnen.
        window.addEventListener("dragover", function (event) { event.preventDefault(); });
        window.addEventListener("drop", function (event) { event.preventDefault(); });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener("click", function () {
            window.location.reload();
        });
    }

    // ------------------------------------------------- Lösch-Bestätigung --
    Array.prototype.forEach.call(document.querySelectorAll("[data-confirm]"), function (node) {
        node.addEventListener("submit", function (event) {
            if (!window.confirm(node.getAttribute("data-confirm"))) {
                event.preventDefault();
            }
        });
    });

    // ---------------------------------------------------------- Lightbox --
    var lightbox = document.getElementById("lightbox");
    if (!lightbox) {
        return;
    }
    var lightboxImg = document.getElementById("lightbox-img");
    var lightboxVideo = document.getElementById("lightbox-video");
    var caption = document.getElementById("lightbox-caption");
    // Nur inline darstellbare Kacheln (Bilder + abspielbare Videos) landen in der Lightbox.
    var tiles = Array.prototype.filter.call(
        document.querySelectorAll(".tile"),
        function (tile) { return tile.getAttribute("data-renderable") === "true"; }
    );
    var current = -1;

    function stopVideo() {
        if (!lightboxVideo) {
            return;
        }
        lightboxVideo.pause();
        lightboxVideo.removeAttribute("src");
        lightboxVideo.load();
        lightboxVideo.hidden = true;
    }

    function show(index) {
        if (!tiles.length) {
            return;
        }
        current = (index + tiles.length) % tiles.length;
        var tile = tiles[current];
        var src = tile.getAttribute("data-src");
        var who = tile.getAttribute("data-uploader");
        caption.textContent = who + " · " + tile.getAttribute("data-date");
        if (tile.getAttribute("data-kind") === "video" && lightboxVideo) {
            lightboxImg.hidden = true;
            lightboxImg.src = "";
            lightboxVideo.hidden = false;
            lightboxVideo.setAttribute("src", src);
            lightboxVideo.setAttribute("type", tile.getAttribute("data-content-type") || "");
            lightboxVideo.load();
            var playing = lightboxVideo.play();
            if (playing && typeof playing.catch === "function") {
                playing.catch(function () { /* Autoplay evtl. blockiert — Controls bleiben */ });
            }
        } else {
            stopVideo();
            lightboxImg.hidden = false;
            lightboxImg.src = src;
            lightboxImg.alt = "Hochzeitsfoto von " + who;
        }
        lightbox.hidden = false;
        document.body.style.overflow = "hidden";
    }

    function close() {
        lightbox.hidden = true;
        lightboxImg.src = "";
        stopVideo();
        document.body.style.overflow = "";
    }

    tiles.forEach(function (tile, index) {
        var opener = tile.querySelector(".tile__open");
        if (opener) {
            opener.addEventListener("click", function () { show(index); });
        }
    });

    document.getElementById("lightbox-close").addEventListener("click", close);
    document.getElementById("lightbox-prev").addEventListener("click", function () { show(current - 1); });
    document.getElementById("lightbox-next").addEventListener("click", function () { show(current + 1); });
    lightbox.addEventListener("click", function (event) {
        if (event.target === lightbox || event.target.classList.contains("lightbox__figure")) {
            close();
        }
    });
    document.addEventListener("keydown", function (event) {
        if (lightbox.hidden) {
            return;
        }
        if (event.key === "Escape") {
            close();
        } else if (event.key === "ArrowLeft") {
            show(current - 1);
        } else if (event.key === "ArrowRight") {
            show(current + 1);
        }
    });

    // Browser ohne HEIC-Support: Kachel auf die Download-Karte umstellen.
    Array.prototype.forEach.call(document.querySelectorAll(".tile__img"), function (img) {
        img.addEventListener("error", function () {
            var tile = img.closest(".tile");
            if (!tile) {
                return;
            }
            tile.setAttribute("data-renderable", "false");
            tile.classList.add("tile--nopreview");
            var opener = tile.querySelector(".tile__open");
            if (opener) {
                var fallback = document.createElement("div");
                fallback.className = "tile__fallback";
                fallback.innerHTML =
                    '<span class="tile__fallback-icon" aria-hidden="true">📷</span>' +
                    '<span class="tile__fallback-text">Nur per Download ansehbar</span>';
                opener.parentNode.replaceChild(fallback, opener);
            }
            var index = tiles.indexOf(tile);
            if (index >= 0) {
                tiles.splice(index, 1);
            }
        });
    });
})();
