// 火花会 Sparkwave · 前端接口封装（同源，无构建）
// 所有 REST 调用经此模块，统一处理 JSON、错误信封与状态码。
(function (global) {
  "use strict";

  let adminToken = "";
  let memberId = "", memberToken = "";

  function authHeaders(base) {
    const h = base || {};
    if (adminToken) h["X-Admin-Token"] = adminToken;
    if (memberId) { h["X-Member-Id"] = memberId; h["X-Member-Token"] = memberToken; }
    return h;
  }

  async function request(method, path, body) {
    const opt = { method, headers: authHeaders({}) };
    if (body !== undefined && body !== null) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    let res, text;
    try {
      res = await fetch(path, opt);
      text = await res.text();
    } catch (e) {
      return { ok: false, status: 0, body: null, error: "网络不可达：" + e.message };
    }
    let parsed = text;
    try { parsed = text ? JSON.parse(text) : null; } catch (_) { /* 保留原文 */ }
    const ok = res.status >= 200 && res.status < 300;
    const detail = ok ? null : (parsed && parsed.detail) || ("HTTP " + res.status);
    return { ok, status: res.status, body: parsed, error: detail };
  }

  async function upload(path, formData) {
    let res, text;
    try {
      res = await fetch(path, { method: "POST", headers: authHeaders({}), body: formData });
      text = await res.text();
    } catch (e) {
      return { ok: false, status: 0, body: null, error: "网络不可达：" + e.message };
    }
    let parsed = text;
    try { parsed = text ? JSON.parse(text) : null; } catch (_) {}
    const ok = res.status >= 200 && res.status < 300;
    return { ok, status: res.status, body: parsed, error: ok ? null : (parsed && parsed.detail) || ("HTTP " + res.status) };
  }

  // 原始字节上传（语音转写：PCM 二进制），同样携带身份头；signal 供调用方超时中止
  async function postBytes(path, bytes, signal) {
    let res, text;
    try {
      res = await fetch(path, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/octet-stream" }),
        body: bytes,
        signal,
      });
      text = await res.text();
    } catch (e) {
      return { ok: false, status: 0, body: null, error: "网络不可达：" + e.message, aborted: e && e.name === "AbortError" };
    }
    let parsed = text;
    try { parsed = text ? JSON.parse(text) : null; } catch (_) {}
    const ok = res.status >= 200 && res.status < 300;
    return { ok, status: res.status, body: parsed, error: ok ? null : (parsed && parsed.detail) || ("HTTP " + res.status) };
  }

  global.api = {
    get: (p) => request("GET", p),
    post: (p, b) => request("POST", p, b === undefined ? {} : b),
    patch: (p, b) => request("PATCH", p, b),
    del: (p) => request("DELETE", p),
    upload,
    postBytes,
    setAdminToken: (t) => { adminToken = t || ""; },
    getAdminToken: () => adminToken,
    setMember: (id, token) => { memberId = id || ""; memberToken = token || ""; },
    getMemberId: () => memberId,
    hasMember: () => !!memberId,
    clearMember: () => { memberId = ""; memberToken = ""; },
    memberQs: () => memberId
      ? "?member_id=" + encodeURIComponent(memberId) + "&token=" + encodeURIComponent(memberToken)
      : "",
  };

  // ---- 通用小工具 ----
  global.SW = global.SW || {};
  global.SW.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  };
  global.SW.initial = function (name) {
    const s = String(name || "?").trim();
    return s ? Array.from(s)[0] : "?";
  };
  global.SW.fmtTime = function (iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const p = (n) => String(n).padStart(2, "0");
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  };
  global.SW.qs = function (name) {
    return new URLSearchParams(location.search).get(name);
  };

  // ---- 房间成员身份（标签页级）----
  // 存 sessionStorage 而非 localStorage：单机演示要在同一浏览器开多个标签扮演多个人，
  // 存到浏览器级会让第二个成员继承第一个的身份。只存标识与令牌；显示名与是否主持人一律
  // 从成员名单实时取，故主持人变更不会让界面停留在过期角色上。
  global.SW.member = {
    key: function (roomId) { return "sw.member." + roomId; },
    load: function (roomId) {
      try { return JSON.parse(sessionStorage.getItem(this.key(roomId)) || "null"); }
      catch (e) { return null; }
    },
    save: function (roomId, id, token) {
      try { sessionStorage.setItem(this.key(roomId), JSON.stringify({ member_id: id, token: token })); }
      catch (e) { /* 隐私模式下不可写：本次会话内仍可用，刷新后重新进房 */ }
    },
    drop: function (roomId) { try { sessionStorage.removeItem(this.key(roomId)); } catch (e) {} },
    enter: function (roomId, id, token) {
      this.save(roomId, id, token);
      global.api.setMember(id, token);
    },
  };
})(window);
