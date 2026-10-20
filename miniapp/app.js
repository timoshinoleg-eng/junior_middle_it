/* Career Copilot Mini App.
 *
 * The page is opened from Telegram, so the identity of the user comes from the
 * signed `tgWebAppData` string: either from the query string (when the page
 * is opened through a link) or from the Telegram WebApp SDK.
 */
(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var sdk = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  var initData = params.get("tgWebAppData") || (sdk ? sdk.initData : "") || "";
  var jobFromLink = params.get("job") || "";

  var statusBox = document.getElementById("status");
  var jobInput = document.getElementById("job-hash");
  var fitResult = document.getElementById("fit-result");
  var cvResult = document.getElementById("cv-result");
  var searchesResult = document.getElementById("searches-result");

  function setStatus(text, kind) {
    if (!text) {
      statusBox.hidden = true;
      statusBox.textContent = "";
      statusBox.className = "status";
      return;
    }
    statusBox.hidden = false;
    statusBox.textContent = text;
    statusBox.className = "status " + (kind === "ok" ? "is-ok" : "is-error");
  }

  function setBusy(button, busy) {
    button.disabled = !!busy;
    button.dataset.label = button.dataset.label || button.textContent;
    button.textContent = busy ? "Работаю…" : button.dataset.label;
  }

  function api(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ init_data: initData }, body || {}))
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return { ok: false, error: "invalid_response", message: "Сервис ответил некорректно." };
        })
        .then(function (payload) {
          if (!response.ok || payload.ok === false) {
            var error = new Error(payload.message || "Не удалось выполнить запрос.");
            error.code = payload.error || "error";
            throw error;
          }
          return payload;
        });
    });
  }

  function chips(items, missing) {
    if (!items || !items.length) {
      return "";
    }
    return (
      '<div class="chips">' +
      items
        .map(function (item) {
          var cls = missing ? "chip is-missing" : "chip";
          return '<span class="' + cls + '">' + escapeHtml(item) + "</span>";
        })
        .join("") +
      "</div>"
    );
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderFit(payload) {
    var fit = payload.fit || {};
    var audit = payload.grade_audit || {};
    var job = payload.job || {};
    var hr = payload.hr || {};
    var html = "<h2>" + escapeHtml(job.title || "Вакансия") + "</h2>";
    html += '<div class="score">' + escapeHtml(fit.score) + "%</div>";
    html += "<p>Совпадение стека: " + escapeHtml(fit.stack_match_score) + "%</p>";
    if (audit.declared_grade) {
      html +=
        "<p>Грейд: " +
        escapeHtml(audit.declared_grade) +
        (audit.real_grade && audit.real_grade !== audit.declared_grade
          ? " → " + escapeHtml(audit.real_grade)
          : "") +
        "</p>";
    }
    if (fit.matched_skills && fit.matched_skills.length) {
      html += "<p><b>Совпало</b></p>" + chips(fit.matched_skills, false);
    }
    if (fit.missing_skills && fit.missing_skills.length) {
      html += "<p><b>Не хватает</b></p>" + chips(fit.missing_skills, true);
    }
    if (hr.url) {
      html +=
        '<p><a href="' +
        escapeHtml(hr.url) +
        '" target="_blank" rel="noopener">Написать HR: ' +
        escapeHtml(hr.handle) +
        "</a></p>";
    }
    if (job.url) {
      html +=
        '<p><a href="' +
        escapeHtml(job.url) +
        '" target="_blank" rel="noopener">Открыть вакансию</a></p>';
    }
    fitResult.innerHTML = html;
    fitResult.hidden = false;
  }

  function renderCv(payload) {
    var ats = payload.ats || {};
    var html = "<h2>CV готов</h2>";
    html += "<p>" + escapeHtml(payload.message || "CV отправлен в чат бота.") + "</p>";
    if (ats.summary) {
      html += "<p>" + escapeHtml(ats.summary) + "</p>";
    }
    if (ats.problems && ats.problems.length) {
      html += "<p><b>Что поправить:</b></p><ul>";
      ats.problems.forEach(function (problem) {
        html += "<li>" + escapeHtml(problem) + "</li>";
      });
      html += "</ul>";
    }
    cvResult.innerHTML = html;
    cvResult.hidden = false;
  }

  function renderSearches(payload) {
    var searches = payload.searches || [];
    if (!searches.length) {
      searchesResult.innerHTML = "<h2>Пока пусто</h2><p>Сохранённых поисков нет.</p>";
      searchesResult.hidden = false;
      return;
    }
    var html = "<h2>Мои поиски</h2><ul>";
    searches.forEach(function (item) {
      var name = item.name || item.keywords || "Поиск";
      html += "<li>" + escapeHtml(name);
      if (item.keywords && item.keywords !== name) {
        html += " — " + escapeHtml(item.keywords);
      }
      html += "</li>";
    });
    html += "</ul>";
    searchesResult.innerHTML = html;
    searchesResult.hidden = false;
  }

  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".tab").forEach(function (item) {
        item.classList.remove("is-active");
      });
      document.querySelectorAll(".panel").forEach(function (panel) {
        panel.classList.remove("is-active");
      });
      tab.classList.add("is-active");
      var panel = document.getElementById("tab-" + tab.dataset.tab);
      if (panel) {
        panel.classList.add("is-active");
      }
    });
  });

  document.getElementById("fit-button").addEventListener("click", function () {
    var button = this;
    var jobHash = (jobInput.value || "").trim();
    if (!jobHash) {
      setStatus("Укажи хэш вакансии.", "error");
      return;
    }
    setBusy(button, true);
    setStatus("");
    api("/api/copilot", { action: "fit", job_hash: jobHash })
      .then(function (payload) {
        renderFit(payload);
        setStatus("Разбор готов.", "ok");
      })
      .catch(function (error) {
        setStatus(error.message, "error");
      })
      .finally(function () {
        setBusy(button, false);
      });
  });

  document.getElementById("cv-button").addEventListener("click", function () {
    var button = this;
    var jobHash = (jobInput.value || "").trim();
    if (!jobHash) {
      setStatus("Укажи хэш вакансии.", "error");
      return;
    }
    var input = document.getElementById("cv-file");
    var file = input.files && input.files[0];
    if (!file) {
      setStatus("Выбери файл резюме (PDF или DOCX).", "error");
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      var base64 = String(reader.result || "").split(",").pop();
      setBusy(button, true);
      setStatus("");
      api("/api/copilot/pdf", {
        job_hash: jobHash,
        resume: {
          filename: file.name,
          mime_type: file.type || "",
          content_base64: base64
        }
      })
        .then(function (payload) {
          renderCv(payload);
          setStatus(payload.cached ? "Отправил сохранённый CV." : "CV отправлен в чат.", "ok");
        })
        .catch(function (error) {
          setStatus(error.message, "error");
        })
        .finally(function () {
          setBusy(button, false);
        });
    };
    reader.onerror = function () {
      setStatus("Не удалось прочитать файл.", "error");
    };
    reader.readAsDataURL(file);
  });

  document.getElementById("searches-button").addEventListener("click", function () {
    var button = this;
    setBusy(button, true);
    setStatus("");
    api("/api/copilot", { action: "searches" })
      .then(function (payload) {
        renderSearches(payload);
      })
      .catch(function (error) {
        setStatus(error.message, "error");
      })
      .finally(function () {
        setBusy(button, false);
      });
  });

  if (jobFromLink) {
    jobInput.value = jobFromLink;
  }
  if (sdk) {
    if (sdk.ready) {
      sdk.ready();
    }
    if (sdk.expand) {
      sdk.expand();
    }
  }
  if (!initData) {
    setStatus("Открой приложение из Telegram, чтобы данные были подписаны.", "error");
  }
})();
