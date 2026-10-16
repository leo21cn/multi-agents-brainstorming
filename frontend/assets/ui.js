// 火花会 Sparkwave · 共享 UI 原语（toast、模态、确认）
(function (global) {
  "use strict";
  const SW = (global.SW = global.SW || {});

  function ensureToast() {
    let t = document.getElementById("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
    return t;
  }
  SW.toast = function (msg, kind) {
    const t = ensureToast();
    const el = document.createElement("div");
    el.className = "toast-item" + (kind ? " " + kind : "");
    el.textContent = msg;
    t.appendChild(el);
    t.classList.add("show");
    setTimeout(() => {
      el.remove();
      if (!t.children.length) t.classList.remove("show");
    }, 2600);
  };

  // 打开/关闭模态：openModal(id) / 绑定 [data-close] 与遮罩点击
  SW.openModal = function (id) { const m = document.getElementById(id); if (m) m.classList.add("open"); };
  SW.closeModal = function (id) { const m = document.getElementById(id); if (m) m.classList.remove("open"); };
  SW.wireModal = function (id) {
    const m = document.getElementById(id);
    if (!m || m._wired) return;
    m._wired = true;
    m.addEventListener("click", (e) => { if (e.target === m) m.classList.remove("open"); });
    m.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => m.classList.remove("open")));
  };

  // 报告一次接口结果：成功提示 ok 文案，失败弹错误
  SW.report = function (res, okMsg) {
    if (res.ok) { if (okMsg) SW.toast(okMsg, "ok"); return true; }
    SW.toast(res.error || ("请求失败 HTTP " + res.status), "err");
    return false;
  };
})(window);
