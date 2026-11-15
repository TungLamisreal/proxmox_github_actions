// Source is intentionally plain SystemJS: no build tool or remote scripts at runtime.
System.register(["react", "@grafana/data", "@grafana/runtime", "@grafana/ui"], function (exports) {
  "use strict";
  var React, PanelPlugin, getBackendSrv, useTheme2;
  // Presentation-only translation of known historical webhook journal messages.
  // Raw service logs are preserved; unrecognized messages are shown verbatim.
  var logTranslations = [
    [/\[\*\] Nhận Webhook từ Alertmanager:/g, "[INFO] Received Alertmanager webhook:"],
    [/\[\*\] Từ chối SCALE-OUT/g, "[INFO] SCALE-OUT skipped"],
    [/\[\*\] Từ chối SCALE-IN/g, "[INFO] SCALE-IN skipped"],
    [/Đang Cooldown/g, "Cooldown active"],
    [/\[\!\] QUÁ TẢI CPU \(BATCH\): Gửi lệnh Scale-Out/g, "[WARN] HIGH CPU (BATCH): Dispatching scale-out"],
    [/\[\*\] CHẶN SCALE-IN: Hệ thống vừa Scale-Out/g, "[INFO] SCALE-IN blocked: scale-out occurred"],
    [/s trước \(/g, "s ago ("],
    [/\)\. Bảo vệ luồng Scale-Out không bị đè!/g, "). Waiting for stabilization."],
    [/\[-\] TẢI THẤP: Gửi lệnh Scale-In/g, "[INFO] LOW CPU: Dispatching scale-in"],
    [/\[OK\] Đã gửi Trigger tới/g, "[OK] Dispatched workflow"],
    [/ với inputs=/g, " with inputs="],
    [/\[ERROR\] Lỗi khi gọi GitHub API:/g, "[ERROR] GitHub API call failed:"],
    [/\[\!\] Lỗi phân tích payload:/g, "[ERROR] Failed to parse webhook payload:"],
    [/\[\!\] Lỗi: Chưa cấu hình biến môi trường GITHUB_PAT_TOKEN\./g, "[ERROR] GITHUB_PAT_TOKEN environment variable not configured."],
    [/Khởi Động/g, "Started"],
    [/log kiểm tra/g, "test log"]
  ];
  function englishLogMessage(message) {
    return logTranslations.reduce(function (value, rule) { return value.replace(rule[0], rule[1]); }, String(message));
  }

  function Panel(props) {
    var h = React.createElement, theme = useTheme2(), colors = theme.colors;
    var state = React.useState({rows: [], error: "", loading: true});
    var logs = state[0], setLogs = state[1];
    var scroll = React.useRef(null);
    var from = props.timeRange.from.valueOf(), to = props.timeRange.to.valueOf();
    var request = props.data.request && props.data.request.requestId;
    React.useEffect(function () {
      var active = true;
      setLogs(function (old) { return Object.assign({}, old, {loading: true}); });
      var query = new URLSearchParams({query: '{service="meowcloud-webhook"}', start: String(from * 1000000), end: String(to * 1000000), limit: "200", direction: "backward"});
      getBackendSrv().get("/api/datasources/proxy/uid/meow-journal/loki/api/v1/query_range?" + query).then(function (reply) {
        if (!active) return;
        if (reply.status !== "success") throw new Error("Log query failed");
        var rows = [];
        (reply.data.result || []).forEach(function (stream) {
          stream.values.forEach(function (v) { rows.push({time: Number(v[0]) / 1000000, message: v[1]}); });
        });
        rows.sort(function (a, b) { return b.time - a.time; });
        setLogs({rows: rows, error: "", loading: false});
        if (scroll.current) scroll.current.scrollTop = 0;
      }).catch(function () {
        if (active) setLogs({rows: [], error: "Unable to load logs. Check the journal service and meow-journal datasource.", loading: false});
      });
      return function () { active = false; };
    }, [from, to, request]);
    function count(ref) {
      if (props.data.state === "Error") return "—";
      var frame = props.data.series.find(function (s) { return s.refId === ref; });
      var field = frame && frame.fields.find(function (f) { return f.type === "number"; });
      if (!field || !field.values.length) return "—";
      var value = field.values[field.values.length - 1];
      return value != null && Number.isFinite(Number(value)) ? String(value) : "—";
    }
    function stat(label, ref, color) {
      var value = count(ref);
      return h("div", {style: {minWidth: 90}},
        h("div", {style: {fontSize: 11, color: colors.text.secondary}}, label),
        h("div", {style: {fontSize: 30, lineHeight: "36px", color: value === "0" ? colors.success.text : color}}, value));
    }
    var td = {padding: "8px 10px", verticalAlign: "top", borderBottom: "1px solid " + colors.border.weak};
    return h("div", {"data-testid": "meow-alertlog", style: {height: props.height, width: props.width, display: "flex", flexDirection: "column", overflow: "hidden", color: colors.text.primary}},
      h("div", {style: {display: "flex", flexWrap: "wrap", gap: 24, padding: "0 8px 8px", flexShrink: 0, borderBottom: "1px solid " + colors.border.weak}},
        stat("PENDING", "Pending", colors.warning.text), stat("FIRING", "Firing", colors.error.text),
        h("div", {style: {marginLeft: "auto", fontSize: 12}}, h("div", null, "CPU pool > 75% · 10 seconds"), h("div", {style: {color: colors.text.secondary}}, "Backend & Frontend"))),
      props.data.error ? h("div", {role: "alert", style: {color: colors.error.text}}, "Unable to load alert status from Prometheus.") : null,
      h("div", {style: {fontSize: 12, padding: "8px", flexShrink: 0, color: colors.text.secondary}}, "webhook_listener.py · Newest first · Vietnam time (UTC+7)"),
      logs.error ? h("div", {role: "alert", style: {padding: 12, color: colors.error.text}}, logs.error) : null,
      h("div", {ref: scroll, "data-testid": "meow-log-scroll", style: {flex: 1, minHeight: 0, overflow: "auto"}},
        h("table", {style: {width: "100%", tableLayout: "fixed", borderCollapse: "collapse", fontSize: 12}},
          h("thead", {style: {position: "sticky", top: 0, background: colors.background.primary}},
            h("tr", null, h("th", {style: Object.assign({}, td, {width: 98, textAlign: "left"})}, "Time"), h("th", {style: Object.assign({}, td, {textAlign: "left"})}, "Log message"))),
          h("tbody", null, logs.rows.map(function (row, index) {
            return h("tr", {key: row.time + ":" + index},
              h("td", {style: Object.assign({}, td, {fontVariantNumeric: "tabular-nums", color: colors.text.secondary}), title: new Date(row.time).toLocaleString("en-GB", {timeZone: "Asia/Ho_Chi_Minh"})}, new Date(row.time).toLocaleTimeString("en-GB", {timeZone: "Asia/Ho_Chi_Minh", hour12: false})),
              h("td", {style: Object.assign({}, td, {fontFamily: "monospace", whiteSpace: "pre-wrap", overflowWrap: "anywhere"})}, englishLogMessage(row.message)));
          }))),
        !logs.error && !logs.rows.length ? h("div", {style: {padding: 12, color: colors.text.secondary}}, logs.loading ? "Loading logs..." : "No logs in the selected time range.") : null),
      h("div", {style: {fontSize: 11, padding: "6px 8px", color: colors.text.secondary, flexShrink: 0}}, logs.error ? "Log source unavailable" : logs.rows.length + " entries · up to 200 most recent"));
  }
  return {
    setters: [function (m) { React = m; }, function (m) { PanelPlugin = m.PanelPlugin; }, function (m) { getBackendSrv = m.getBackendSrv; }, function (m) { useTheme2 = m.useTheme2; }],
    execute: function () { exports("plugin", new PanelPlugin(Panel)); }
  };
});
