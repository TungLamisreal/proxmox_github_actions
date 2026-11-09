// Source is intentionally plain SystemJS: no build tool or remote scripts at runtime.
System.register(["react", "@grafana/data", "@grafana/runtime", "@grafana/ui"], function (exports) {
  "use strict";
  var React, PanelPlugin, getBackendSrv, useTheme2;
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
        if (active) setLogs({rows: [], error: "Không đọc được log. Kiểm tra dịch vụ journal và datasource meow-journal.", loading: false});
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
        h("div", {style: {marginLeft: "auto", fontSize: 12}}, h("div", null, "CPU pool > 75% · 10 giây"), h("div", {style: {color: colors.text.secondary}}, "Backend & Frontend"))),
      props.data.error ? h("div", {role: "alert", style: {color: colors.error.text}}, "Không đọc được trạng thái alert từ Prometheus") : null,
      h("div", {style: {fontSize: 12, padding: "8px", flexShrink: 0, color: colors.text.secondary}}, "webhook_listener.py · Mới nhất trước · Giờ Việt Nam"),
      logs.error ? h("div", {role: "alert", style: {padding: 12, color: colors.error.text}}, logs.error) : null,
      h("div", {ref: scroll, "data-testid": "meow-log-scroll", style: {flex: 1, minHeight: 0, overflow: "auto"}},
        h("table", {style: {width: "100%", tableLayout: "fixed", borderCollapse: "collapse", fontSize: 12}},
          h("thead", {style: {position: "sticky", top: 0, background: colors.background.primary}},
            h("tr", null, h("th", {style: Object.assign({}, td, {width: 98, textAlign: "left"})}, "Thời gian"), h("th", {style: Object.assign({}, td, {textAlign: "left"})}, "Nội dung log"))),
          h("tbody", null, logs.rows.map(function (row, index) {
            return h("tr", {key: row.time + ":" + index},
              h("td", {style: Object.assign({}, td, {fontVariantNumeric: "tabular-nums", color: colors.text.secondary}), title: new Date(row.time).toLocaleString("vi-VN", {timeZone: "Asia/Ho_Chi_Minh"})}, new Date(row.time).toLocaleTimeString("vi-VN", {timeZone: "Asia/Ho_Chi_Minh", hour12: false})),
              h("td", {style: Object.assign({}, td, {fontFamily: "monospace", whiteSpace: "pre-wrap", overflowWrap: "anywhere"})}, row.message));
          }))),
        !logs.error && !logs.rows.length ? h("div", {style: {padding: 12, color: colors.text.secondary}}, logs.loading ? "Đang đọc log…" : "Không có log trong khoảng thời gian đã chọn.") : null),
      h("div", {style: {fontSize: 11, padding: "6px 8px", color: colors.text.secondary, flexShrink: 0}}, logs.error ? "Nguồn log không khả dụng" : logs.rows.length + " dòng · tối đa 200 dòng mới nhất"));
  }
  return {
    setters: [function (m) { React = m; }, function (m) { PanelPlugin = m.PanelPlugin; }, function (m) { getBackendSrv = m.getBackendSrv; }, function (m) { useTheme2 = m.useTheme2; }],
    execute: function () { exports("plugin", new PanelPlugin(Panel)); }
  };
});
