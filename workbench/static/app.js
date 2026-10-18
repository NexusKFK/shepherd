"use strict";
const $ = (s) => document.querySelector(s);
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (n) =>
  Number.isFinite(n)
    ? "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : "—";
const dec = (n, k = 2) =>
  Number.isFinite(n)
    ? n.toLocaleString("en-US", { maximumFractionDigits: k })
    : "—";
const badge = (s, cls = "") => `<span class="badge ${cls}">${esc(s)}</span>`;
const statusNames = {
  draft: "草稿",
  ready: "待执行",
  holding: "持仓中",
  closed: "已平仓",
  cancelled: "已取消",
};
const btn = (label, action, id = "", cls = "") =>
  `<button class="button ${cls}" data-action="${action}" data-id="${esc(id)}">${label}</button>`;
const empty = (title, body, action = "") =>
  `<div class="empty"><strong>${esc(title)}</strong>${esc(body)}${action}</div>`;
const notice = (text) => `<div class="notice">${esc(text)}</div>`;
let state,
  route = "trade",
  tab = "today",
  filter = "active",
  month,
  selectedDay = null,
  draftDirty = false;
let toastTimer;
let pendingPlanDraft = null;
let prepDirty = false;
let receiptDirty = false;
function discardReceiptEdits() {
  if (
    receiptDirty &&
    !confirm("回单表格有尚未登记的修改。放弃这些输入并继续？")
  )
    return false;
  receiptDirty = false;
  return true;
}
async function savePrepIfNeeded() {
  const f = $("#prep-form");
  if (!prepDirty || !f) return;
  await api("preparation", {
    day: state.day,
    checks: [...f.querySelectorAll("[name=check]:checked")].map((x) => x.value),
    note: f.elements.note.value,
  });
  prepDirty = false;
}
function toast(msg, error = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (error ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), error ? 9000 : 4500);
}
async function api(path, data) {
  const res = await fetch(
    "/api/" + path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Desk-Request": "1",
          },
          body: JSON.stringify(data),
        },
  );
  let body;
  try {
    body = await res.json();
  } catch {
    throw new Error("服务暂不可用，请重新打开 App");
  }
  if (!res.ok) throw new Error(body.error || "操作失败");
  return body;
}
async function reload() {
  if (!discardReceiptEdits()) return;
  await savePrepIfNeeded();
  state = await api("refresh", {});
  applyTheme(state.appearance?.theme || "dark");
  if (!month) month = state.day.slice(0, 7);
  render();
}
function heading(title, description, action = "") {
  return `<div class="page-heading"><div><div class="eyebrow">${route === "trade" ? "Daily workspace" : route === "risk" ? "Portfolio risk" : "Event calendar"}</div><h1>${title}</h1><p>${esc(description)}</p></div>${action}</div>`;
}
function tradeTabs() {
  return `<div class="tabs" role="tablist" aria-label="交易流程">${[
    ["today", "盘前准备"],
    ["plans", "计划与定仓"],
    ["holdings", "持仓"],
    ["journal", "交易记录"],
  ]
    .map(
      ([id, label]) =>
        `<button class="tab ${tab === id ? "active" : ""}" role="tab" aria-selected="${tab === id}" data-tab="${id}">${label}${id === "plans" ? `<span class="tab-count">${state.plans.filter((p) => ["draft", "ready"].includes(p.status)).length}</span>` : ""}</button>`,
    )
    .join("")}</div>`;
}
function render() {
  document.querySelectorAll("[data-route]").forEach((b) => {
    b.classList.toggle("active", b.dataset.route === route);
    if (b.dataset.route === route) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  $("#breadcrumb").textContent =
    route === "trade"
      ? "交易 / " +
        {
          today: "盘前准备",
          plans: "计划与定仓",
          holdings: "持仓",
          journal: "交易记录",
        }[tab]
      : route === "risk"
        ? "工作空间 / 风险快照"
        : "工作空间 / 日历";
  $("#app").innerHTML =
    route === "trade"
      ? heading(
          tab === "today"
            ? "盘前准备"
            : tab === "plans"
              ? "计划与定仓"
              : tab === "holdings"
                ? "持仓"
                : "交易记录",
          tab === "today"
            ? `${state.day} · 美东交易日视角`
            : tab === "plans"
              ? "每笔计划按统一 1R 定仓，先写清理由与失效条件。"
              : tab === "holdings"
                ? "计划、退出条件与执行记录，跟着持仓走。"
                : "每笔交易的计划、成交和复盘。",
          btn("＋ 新建计划", "new-plan", "", "primary"),
        ) +
        tradeTabs() +
        {
          today: todayPage,
          plans: plansPage,
          holdings: holdingsPage,
          journal: journalPage,
        }[tab]()
      : route === "risk"
        ? riskPage()
        : calendarPage();
  $("#source-line").textContent =
    "持仓来源：" +
    state.snapshot.asof +
    "　 · 　行情截至 " +
    (state.risk.market_day || "未知") +
    "　 · 　金额单位 USD";
}
function stats() {
  const events = state.calendar.events.filter(
    (e) => e.day >= state.day && e.day <= addDays(state.day, 7),
  );
  return `<div class="stats">${[
    [
      "账户净值",
      money(state.snapshot.nlv),
      state.snapshot.nlv === null
        ? "账户净值未齐全"
        : "跨账户汇总 · 以快照为准",
      "",
    ],
    [
      "执行中的计划",
      state.plans.filter((p) => ["ready", "holding"].includes(p.status))
        .length + "",
      "已准备 " +
        state.plans.filter((p) => p.status === "ready").length +
        " · 持仓 " +
        state.plans.filter((p) => p.status === "holding").length,
      "",
    ],
    [
      "股票子组合 VaR · 95%",
      money(state.risk.var95),
      `1 日 · 覆盖 ${state.risk.covered.length} / ${state.positions.length} 条持仓`,
      "accent",
    ],
    ["未来 7 天", events.length + " 项", "宏观 · 财报 · 到期 · 复核", ""],
  ]
    .map(
      ([label, value, note, cls]) =>
        `<div class="stat"><div class="stat-label">${label}</div><div class="stat-value num ${cls}">${value}</div><div class="stat-note">${esc(note)}</div></div>`,
    )
    .join("")}</div>`;
}
function todayPage() {
  const checked = state.preparation.checks || [];
  const tasks = [];
  const unresolved = state.snapshot.positions.filter((p) => p.confirm);
  if (unresolved.length)
    tasks.push({
      title: `${unresolved.length} 条快照持仓需要核对`,
      text: unresolved
        .map((p) => p.sym)
        .filter((v, i, a) => a.indexOf(v) === i)
        .join(" · "),
      action: "show-holdings",
    });
  state.plans
    .filter((p) => p.status === "ready")
    .slice(0, 2)
    .forEach((p) =>
      tasks.push({
        title: p.title,
        text: "计划已完成 · 等待条件满足后登记真实成交",
        action: "detail",
        id: p.id,
      }),
    );
  state.plans
    .filter(
      (p) =>
        p.review_day &&
        p.review_day <= state.day &&
        ["draft", "ready", "holding"].includes(p.status),
    )
    .forEach((p) =>
      tasks.push({
        title: p.title + " · 到期复核",
        text: p.review_day,
        action: "detail",
        id: p.id,
      }),
    );
  return (
    stats() +
    `<div class="two-columns"><section class="panel"><div class="panel-heading"><h2>开盘前，确认三件事</h2>${badge(checked.length + " / 3 已完成", checked.length === 3 ? "green" : "")}</div><div class="panel-body"><form id="prep-form">${[
      ["snapshot", "持仓与风险快照", "核对持仓数量、数据时间与 VaR 覆盖范围"],
      ["events", "今天的重要事件", "宏观公布、持仓财报、到期与复核事项"],
      ["plans", "今天的交易计划", "写清触发条件、风险预算和退出安排"],
    ]
      .map(
        ([id, label, hint]) =>
          `<label class="check-row ${checked.includes(id) ? "done" : ""}"><input type="checkbox" name="check" value="${id}" ${checked.includes(id) ? "checked" : ""}><span>${label}<small>${hint}</small></span></label>`,
      )
      .join(
        "",
      )}<textarea class="prep-note" name="note" aria-label="盘前笔记" placeholder="今天关注什么？什么情况下保持不动？">${esc(state.preparation.note)}</textarea><div class="row-actions"><small id="prep-saved">每天单独保存</small><button type="submit" class="button small">保存盘前准备</button></div></form></div></section><section class="panel"><div class="panel-heading"><h2>今日待处理</h2>${badge(tasks.length + " 项")}</div><div class="panel-body">${
      tasks
        .slice(0, 4)
        .map(
          (t, i) =>
            `<div class="task-row"><span class="task-mark">${i + 1}</span><div><p>${esc(t.title)}</p><small>${esc(t.text)}</small></div>${btn("查看", t.action, t.id, "small subtle")}</div>`,
        )
        .join("") ||
      empty("今天没有待处理计划", "可以整理观察记录，或保留不交易的决定。")
    }${state.calendar.events
      .filter((e) => e.day >= state.day)
      .slice(0, 2)
      .map(eventRow)
      .join(
        "",
      )}</div><div class="panel-footer">依据已有记录整理；状态变化后点击「重新读取快照」。</div></section></div><section class="panel"><div class="panel-heading"><div><h2>盘前观察</h2><small>沿用现有日报事实 · 信号日 ${esc(state.brief.day || "未知")}</small></div>${badge(state.brief.day === state.day ? "当日资料" : "前一资料日", "")}</div>${state.brief.error ? `<div class="panel-body">${notice(state.brief.error)}</div>` : ""}${
      state.brief.items.length
        ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>标的</th><th>已有观察事实</th><th class="right">记录收盘价</th><th></th></tr></thead><tbody>${state.brief.items
            .slice(0, 6)
            .map(
              (r) =>
                `<tr><td><span class="symbol">${esc(r.symbol.split(":").pop())}</span><small class="cell-sub">${esc(r.symbol.split(":")[0])}</small></td><td class="facts">${Object.values(
                  r.facts,
                )
                  .slice(0, 3)
                  .map((f) => `<span>${esc(f)}</span>`)
                  .join(
                    "",
                  )}</td><td class="right mono">${dec(r.close)}</td><td class="right">${btn("写计划", "from-observation", r.symbol, "small subtle")}</td></tr>`,
            )
            .join("")}</tbody></table></div>`
        : empty(
            "盘前资料尚不可用",
            "已有日报生成后重新读取。计划和定仓仍可使用。",
          )
    }</section>`
  );
}
function pct(n, k = 1) {
  return Number.isFinite(n) ? dec(n, k) + "%" : "未知";
}
function riskPolicySummary(inForm = false) {
  const policy = state.risk_policy || {};
  const budget = Number.isFinite(policy.nlv) ? policy.nlv * policy.risk_pct / 100 : null;
  return `<section class="policy-strip ${inForm ? "wide" : ""}"><div><small>统一定仓标准</small><strong>1R = ${money(budget)} <span>· 净值 ${money(policy.nlv)} × ${dec(policy.risk_pct ?? 2)}%</span></strong><p>完整开仓计划须使用 ${pct((policy.min_utilization ?? 0.9) * 100, 0)}–100% 的 1R。观察可不买；实际部分成交与减仓余仓如实保留。</p></div>${btn("设置 1R", "risk-policy", "", "small")}</section>`;
}
function sizingOutput(result) {
  const utilization = Number.isFinite(result.utilization) ? result.utilization * 100 : null;
  return `<div class="sizing-output"><div class="plan-head"><strong>完整开仓计划</strong>${badge(result.eligible ? "合格 · 可标记待执行" : "保持观察 · 暂不可执行", result.eligible ? "green" : "amber")}</div><div class="sizing-numbers"><div><small>系统目标数量</small><b>${dec(result.target_qty ?? result.qty, 0)}</b></div><div><small>本次计划数量</small><b>${dec(result.planned_qty ?? result.qty, 0)}</b></div><div><small>统一 1R</small><b>${money(result.budget)}</b></div><div><small>计划风险 / 利用率</small><b>${money(result.planned_risk)} <small>${pct(utilization)}</small></b></div></div><p>${esc(result.basis)}${Number.isFinite(result.cost_reserve) ? ` · 价格风险 ${money(result.price_risk)} + 费用与滑点预留 ${money(result.cost_reserve)}` : ""}</p>${(result.blockers || []).map((w) => `<p class="warn">${esc(w)}</p>`).join("")}${(result.warnings || []).map((w) => `<p class="muted">${esc(w)}</p>`).join("")}</div>`;
}
function disciplinePanel(p) {
  const d = p.discipline;
  if (!d) return "";
  const r = (v) => Number.isFinite(v) ? dec(v) + "R" : "未知";
  return `<section class="discipline-panel spaced"><div class="plan-head"><strong>仓位执行</strong>${badge(d.label || "待计算", d.state === "over_budget" ? "amber" : "")}</div><div class="discipline-metrics">${[
    ["原始 1R", money(d.baseline_risk)], ["计划风险", r(d.planned_r)],
    ["累计入场风险", r(d.actual_entry_r)], ["当前剩余风险", r(d.current_r)],
    ["入场与计划之差", r(d.execution_deviation_r)], ["已实现收益", r(d.realized_r)],
  ].map(([label, value]) => `<div><small>${label}</small><b class="mono">${value}</b></div>`).join("")}</div><p>原始 1R 在标记待执行时冻结。入场差额为负可能只是部分成交；退出后的余仓无需补满。</p>${(d.warnings || []).map(notice).join("")}</section>`;
}
function planCard(p) {
  return `<article class="panel plan-card"><div class="plan-head"><span class="symbol">${esc(p.symbol)} <small>${esc(p.broker || "账户待填")}</small></span>${badge(statusNames[p.status], p.status === "ready" || p.status === "holding" ? "green" : "")}</div><h3>${esc(p.title)}</h3><p>${esc(p.thesis || "交易理由待补充")}</p><div class="plan-metrics"><div><span>计划入场</span><b class="mono">${dec(p.entry)}</b></div><div><span>${p.kind === "stock" ? "失效价" : "合约乘数"}</span><b class="mono">${dec(p.kind === "stock" ? p.stop : p.mult)}</b></div><div><span>风险预算</span><b class="mono">${money(p.sizing?.budget ?? (p.nlv && p.risk_pct ? (p.nlv * p.risk_pct) / 100 : null))}</b></div></div><div class="row-actions">${btn("打开交易记录", "detail", p.id, "small")}${p.status === "draft" ? btn("编辑与定仓", "edit-plan", p.id, "small primary") : p.status === "ready" ? btn("登记成交", "entry", p.id, "small primary") : p.status === "holding" ? btn("登记退出", "exit", p.id, "small primary") : ""}</div></article>`;
}
function plansPage() {
  let plans = state.plans.filter(
    (p) =>
      filter === "all" ||
      (filter === "active"
        ? ["draft", "ready"].includes(p.status)
        : p.status === filter),
  );
  return `${riskPolicySummary()}<div class="filters">${[
    ["active", "当前计划"],
    ["draft", "草稿"],
    ["ready", "待执行"],
    ["all", "全部"],
  ]
    .map(
      ([id, label]) =>
        `<button class="filter ${filter === id ? "active" : ""}" data-filter="${id}">${label}</button>`,
    )
    .join(
      "",
    )}</div>${plans.length ? `<div class="plan-grid">${plans.map(planCard).join("")}</div>` : empty("从一笔明确的计划开始", "入场依据、失效条件、风险预算，保存在同一张计划里。", btn("＋ 写第一笔计划", "new-plan", "", "primary"))}${state.snapshot.legacy_plans.length ? `<div class="section-line"><h2>原账本里的观察计划</h2><small>作为新计划的参考，不会自动执行</small></div><section class="panel"><table class="data-table"><tbody>${state.snapshot.legacy_plans.map((p) => `<tr><td><span class="symbol">${esc(p.sym)}</span><small class="cell-sub">${esc(p.label)}</small></td><td>${esc(p.basis || "")}</td><td class="right">${btn("转成计划草稿", "from-legacy", p.id, "small")}</td></tr>`).join("")}</tbody></table></section>` : ""}`;
}
function holdingsPage() {
  const managed = state.plans.filter((p) => p.status === "holding");
  const bound = new Set(state.plans.map((p) => p.snapshot_id).filter(Boolean));
  const imported = state.snapshot.positions.filter((p) => !bound.has(p.id));
  return `${strategiesSection()}${state.conflicts.length ? notice(state.conflicts.join("；")) : ""}${state.snapshot.status !== "available" ? notice("持仓快照过期或不可用。请先核对来源时间，当前显示仅供参考。") : ""}<div class="section-line"><h2>已接入交易流程</h2><small>${managed.length} 笔 · 成交记录保存在本机</small></div>${managed.length ? `<div class="plan-grid">${managed.map((p) => `<article class="panel plan-card"><div class="plan-head"><span class="symbol">${esc(p.symbol)} <small>${esc(p.broker)}</small></span>${badge("持仓中", "green")}</div><h3>${esc(p.title)}</h3><p>${esc(p.invalidation || "失效条件待补充")}</p><div class="plan-metrics"><div><span>剩余数量</span><b>${dec(p.open_qty)}</b></div><div><span>持仓均价</span><b>${dec(p.avg_entry)}</b></div><div><span>已实现损益</span><b>${money(p.realized_pnl)}</b></div></div><div class="row-actions">${btn("记录与判据", "detail", p.id, "small")}${btn("登记退出", "exit", p.id, "small primary")}</div></article>`).join("")}</div>` : empty("尚未接入持仓", "新计划登记成交后会出现在这里；已有持仓可在下表核对后接入。")}<div class="section-line"><h2>现有持仓快照</h2><small>${esc(state.snapshot.day || "日期未知")} · ${imported.length} 条</small></div><section class="panel"><div class="table-scroll"><table class="data-table"><thead><tr><th>标的 / 结构</th><th>账户</th><th>数量</th><th>成本 / 标记价</th><th>状态</th><th></th></tr></thead><tbody>${imported.map((p) => `<tr><td><span class="symbol">${esc(p.sym)}</span><small class="cell-sub">${esc(p.label || p.group || "股票")}</small></td><td>${esc(p.broker)}</td><td class="mono nowrap">${p.side === "short" ? "空 " : "多 "}${dec(p.qty)}</td><td class="mono nowrap">${dec(p.open)} / ${dec(p.mark)}</td><td>${badge(p.confirm ? "待核对" : p.broker === "ToS" ? "手填快照" : "网关快照", p.confirm ? "amber" : "")}</td><td class="right">${btn("核对并接入", "adopt", p.id, "small subtle")}</td></tr>`).join("")}</tbody></table></div>${!imported.length ? empty("没有未接入的快照持仓", state.snapshot.errors.join("；")) : ""}</section>`;
}
const econfSideNames = {
  "buy|open": "买开",
  "sell|open": "卖开",
  "buy|close": "买平",
  "sell|close": "卖平",
  buy: "买入",
  sell: "卖出",
};
function econfAction(leg, planSide) {
  if (!leg.effect) return "?";
  if (planSide === "long" && leg.side === "buy" && leg.effect === "open")
    return "entry";
  if (planSide === "long" && leg.side === "sell" && leg.effect === "close")
    return "exit";
  if (planSide === "short" && leg.side === "sell" && leg.effect === "open")
    return "entry";
  if (planSide === "short" && leg.side === "buy" && leg.effect === "close")
    return "exit";
  return "?";
}
function econfFee(f) {
  const p = f.proposal;
  return p.legs.length === 1 && p.commission != null && p.fees != null
    ? Math.round((p.commission + p.fees) * 1e8) / 1e8
    : "";
}
function econfLegRow(f, leg) {
  const key = f.sha + "|" + leg.index;
  const done = ["recorded", "voided"].includes(leg.status);
  const plan = state.plans.find((p) => p.id === leg.plan_id);
  const side = esc(
    econfSideNames[leg.side + "|" + leg.effect] ||
      econfSideNames[leg.side] ||
      "待核对",
  );
  if (done) {
    const execution = leg.execution || leg;
    return `<tr data-recorded-leg><td>${esc(leg.description || leg.raw)}</td><td>${side}</td><td class="mono">${dec(execution.qty)}</td><td class="mono">${dec(execution.price)}</td><td class="mono">${dec(execution.fee)}</td><td>${esc(execution.day || f.proposal.trade_day)}</td><td>${badge(leg.status === "voided" ? "已撤销登记" : plan ? "已并入 " + plan.symbol : "已登记", leg.status === "voided" ? "" : "green")}</td><td>${plan ? btn("查看", "detail", plan.id, "small subtle") : ""}</td></tr>`;
  }
  if (f.status === "dismissed")
    return `<tr><td>${esc(leg.description || leg.raw)}</td><td>${side}</td><td>${dec(leg.qty)}</td><td>${dec(leg.price)}</td><td colspan="4">已忽略 · 恢复处理后可登记</td></tr>`;
  const candidates = state.plans.filter((p) =>
    (leg.candidates || []).includes(p.id),
  );
  const suggested = candidates.length === 1 ? candidates[0] : null;
  return `<tr data-leg-row="${esc(key)}"><td>${esc(
    leg.description || leg.raw || "—",
  )}${
    leg.dupe ? " " + badge("疑似重复", "amber") : ""
  }</td><td>${side}</td><td><input name="qty" type="number" step="any" min="0" value="${
    leg.qty ?? ""
  }" class="econf-input econf-input-64"></td><td><input name="price" type="number" step="any" min="0" value="${
    leg.price ?? ""
  }" class="econf-input econf-input-84"></td><td><input name="fee" type="number" step="any" min="0" placeholder="待核对" value="${econfFee(f)}" class="econf-input econf-input-88"></td><td><input name="day" type="date" value="${esc(
    f.proposal.trade_day || "",
  )}" class="econf-input econf-input-130"></td><td><select name="plan_select">${[
    ["", "选择计划…"],
    ...(leg.effect === "close" ? [] : [["new", "＋ 登记为新计划"]]),
    ...candidates.map((p) => [
      p.id + "::" + p.version,
      `${p.broker} · ${p.symbol} ${p.contract || p.title}（剩 ${dec(p.open_qty)}）`,
    ]),
  ]
    .map(
      ([v, t]) =>
        `<option value="${esc(v)}" ${
          suggested && v === suggested.id + "::" + suggested.version
            ? "selected"
            : ""
        }>${esc(t)}</option>`,
    )
    .join("")}</select> <select name="direction">${[
    ["", "自动判断方向"],
    ["entry", "入场"],
    ["exit", "退出"],
  ]
    .map(([v, t]) => `<option value="${v}">${t}</option>`)
    .join("")}</select></td><td class="right">${btn(
    "登记此腿",
    "econf-record",
    esc(key),
    "small primary",
  )}</td></tr>`;
}
function econfSection() {
  const e = state.econf;
  if (!e) return "";
  const files = e.files || [];
  if (!files.length && !e.errors.length)
    return `<div class="section-line econf-heading"><h2>券商确认单</h2><small>${esc(
      e.inbox,
    )} · 目前没有待处理的确认单</small>${btn("重新扫描", "econf-scan", "", "small subtle")}</div>`;
  return `<div class="section-line econf-heading"><h2>券商确认单（eConf）</h2><small>${esc(
    e.inbox,
  )} · 每一腿都要人工确认后才入账</small>${btn(
    "重新扫描",
    "econf-scan",
    "",
    "small subtle",
  )}</div>${e.errors.map(notice).join("")}${files
    .map((f) => {
      const p = f.proposal || {};
      const statusName = {
        pending: "待确认",
        partial: "部分已登记",
        recorded: "已全部登记",
        corrected: "已处理 · 含更正",
        dismissed: "已忽略",
        error: "解析失败",
      }[f.status];
      return `<section class="panel econf-panel"><div class="plan-head"><span class="symbol">${
        p.conf ? "eConf#" + p.conf : "成交通知（无单号）"
      }</span>${badge(
        statusName,
        f.status === "pending"
          ? "amber"
          : f.status === "recorded"
            ? "green"
            : "",
      )}<small>原回单 · 账户 ${esc(
        p.account || "—",
      )} · ${esc(p.trade_day || "日期未解析")}${
        p.executed_clock ? " " + esc(p.executed_clock) : ""
      } · 佣金 ${p.commission ?? "—"} + 其他费用 ${
        p.fees ?? "—"
      } · ${p.net_amount != null ? "净额 " + dec(p.net_amount) : "成交本金 " + dec(p.principal_amount)}</small>${
        ["pending", "partial"].includes(f.status)
          ? btn("忽略未登记部分", "econf-dismiss", f.sha, "small subtle")
          : f.status === "dismissed"
            ? btn("恢复处理", "econf-restore", f.sha, "small subtle")
            : ""
      }</div>${p.warnings?.length ? `<details class="econf-warnings" ${["pending", "partial"].includes(f.status) ? "open" : ""}><summary>原回单有 ${p.warnings.length} 项需核对</summary>${p.warnings.map(notice).join("")}</details>` : ""}${
        p.legs?.length
          ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>合约 / 标的</th><th>方向</th><th>数量</th><th>价格</th><th>费用</th><th>成交日</th><th>并入计划</th><th></th></tr></thead><tbody>${p.legs
              .map((leg) => econfLegRow(f, leg))
              .join("")}</tbody></table></div>`
          : ""
      }</section>`;
    })
    .join("")}`;
}
function journalPage() {
  const plans = state.plans.filter(
    (p) => p.fills.length || ["closed", "cancelled"].includes(p.status),
  );
  const table = plans.length
    ? `<section class="panel"><div class="table-scroll"><table class="data-table"><thead><tr><th>交易</th><th>状态</th><th>执行记录</th><th>已实现损益</th><th>复盘</th><th></th></tr></thead><tbody>${plans.map((p) => `<tr><td><span class="symbol">${esc(p.symbol)}</span><small class="cell-sub">${esc(p.title)}</small></td><td>${badge(statusNames[p.status])}</td><td>${p.fills.length} 条</td><td class="mono ${p.realized_pnl < 0 ? "negative" : ""}">${money(p.realized_pnl)}</td><td>${p.review_note ? "已记录" : "待补充"}</td><td>${btn("查看与复盘", "detail", p.id, "small")}</td></tr>`).join("")}</tbody></table></div></section>`
    : empty(
        "交易结束后，依据仍然留在这里",
        "登记成交和退出后，会形成完整记录。",
      );
  return econfSection() + table;
}
function lossChart() {
  const losses = state.risk.losses.map((x) => x.loss);
  if (!losses.length)
    return empty("等待完整日线样本", "需要至少 252 个共同的一日收益样本。");
  let lo = Math.min(...losses),
    hi = Math.max(...losses);
  if (hi === lo) hi = lo + 1;
  const bins = 26,
    counts = Array(bins).fill(0);
  losses.forEach(
    (v) =>
      counts[Math.min(bins - 1, Math.floor(((v - lo) / (hi - lo)) * bins))]++,
  );
  const max = Math.max(...counts),
    x = (v) => 42 + ((v - lo) / (hi - lo)) * 650;
  return `<svg class="chart" viewBox="0 0 740 220" role="img" aria-label="历史模拟一日损益分布，正值表示亏损"><line class="grid-line" x1="42" y1="176" x2="696" y2="176"/>${counts.map((n, i) => `<rect class="${lo + ((i + 0.5) * (hi - lo)) / bins >= state.risk.var95 ? "tail" : "bar"}" x="${42 + i * 25}" y="${176 - (n / max) * 130}" width="19" height="${(n / max) * 130}" rx="2"/>`).join("")}<line class="var-line" x1="${x(state.risk.var95)}" y1="30" x2="${x(state.risk.var95)}" y2="179"/><text x="${Math.min(x(state.risk.var95) + 5, 620)}" y="23">VaR 95%</text><text x="42" y="203">${money(lo)}</text><text x="365" y="203" text-anchor="middle">一日损失（负值为盈利）</text><text x="693" y="203" text-anchor="end">${money(hi)}</text></svg>`;
}
function exposureSection() {
  const e = state.exposure;
  if (!e) return notice("Delta 与市场 Beta 数据尚不可用，风险不能按零计算。");
  const s = e.summary || {}, c = e.coverage || {}, b = e.benchmark || {};
  const scope = e.status === "stale" ? "历史参考 · 核对数据时间" : s.scope === "portfolio" ? "全部持仓" : s.scope === "covered_subset" ? "仅已覆盖持仓" : "尚不可计算";
  const metrics = [
    ["净 Delta 美元", money(s.net_dollar_delta), `净方向敞口 / 账户快照净值 ${pct(s.net_nlv_pct)}`],
    ["总 Delta 美元（绝对值）", money(s.gross_dollar_delta), `未相互抵消 / 账户快照净值 ${pct(s.gross_nlv_pct)}`],
    [!b.symbol ? "市场等效 Delta · 基准未知" : b.proxy ? `${b.label || b.symbol || "SPY"} 等效 Delta` : "SPX 等效 Delta", Number.isFinite(b.proxy ? s.benchmark_delta : s.spx_delta) ? dec(b.proxy ? s.benchmark_delta : s.spx_delta) : "未知", b.proxy ? "SPY 代理，不能读作 SPX Delta" : "经过历史 Beta 调整的指数等效单位"],
    ["市场下跌 1% · 一阶损益", money(s.benchmark_down1pct_pnl), `上涨 1%：${money(s.benchmark_1pct_pnl)} · 非最大损失`],
  ];
  return `<section class="exposure-section"><div class="section-line"><h2>方向与市场敏感度</h2>${badge(scope, c.delta_complete && c.beta_complete && e.status !== "stale" ? "green" : "amber")}</div><div class="stats exposure-stats">${metrics.map(([label, value, hint]) => `<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value num">${value}</div><div class="stat-note">${esc(hint)}</div></div>`).join("")}</div><section class="panel"><div class="panel-heading"><h2>科技与行业集中提醒</h2>${badge("仅提示 · 不限制开仓")}</div><div class="panel-body"><p class="risk-explainer">占比按已知持仓的绝对 Delta 美元计算；多空不会互相掩盖集中度。这里帮助识别共同风险，不要求按行业配平。</p><div class="concentration-grid">${(e.concentrations || []).map((x) => `<div class="concentration-item"><span>${esc(x.label)} <small>${x.kind === "theme" ? "主题" : "行业"}</small></span><strong class="mono">${pct(x.gross_share_pct)}</strong><small>绝对敞口 ${money(x.gross_dollar_delta)}</small></div>`).join("") || '<p class="muted">行业或主题信息未知，可在下方持仓补充风险数据。</p>'}</div>${(e.notices || []).map(notice).join("")}</div></section><section class="panel"><div class="panel-heading"><h2>逐仓穿透</h2><small>Delta ${c.delta_covered ?? 0} / ${c.total ?? 0} · Beta ${c.beta_covered ?? 0} / ${c.total ?? 0}</small></div><div class="table-scroll"><table class="data-table exposure-table"><thead><tr><th>持仓</th><th>Delta / Delta 美元</th><th>净值占比 / 绝对权重</th><th>市场 Beta</th><th>数据与缺口</th><th></th></tr></thead><tbody>${(e.rows || []).map((p) => `<tr><td><span class="symbol">${esc(p.symbol)}</span><small class="cell-sub">${esc(p.broker)} · ${esc(p.sector || "行业未知")}${p.theme ? " · " + esc(p.theme) : ""}</small></td><td class="mono">${dec(p.position_delta)}<small class="cell-sub">${money(p.dollar_delta)}</small></td><td class="mono">${pct(p.nlv_pct)}<small class="cell-sub">绝对权重 ${pct(p.gross_share_pct)}</small></td><td class="mono">${dec(p.beta)}<small class="cell-sub">${p.beta_samples ? `${p.beta_samples} 样本 · R² ${dec(p.r_squared)}` : "Beta 未知"}</small><small class="cell-sub">年化波动 ${pct(p.annualized_vol_pct)}</small></td><td><small class="cell-sub">Delta：${esc(p.delta_asof || "未知")} · ${esc(p.delta_source || "来源未知")}</small><small class="cell-sub">价格：${esc(p.price_asof || "未知")} · ${esc(p.price_source || "来源未知")}</small>${p.beta_end ? `<small class="cell-sub">Beta：${esc(p.beta_start)} — ${esc(p.beta_end)}</small>` : ""}${(p.issues || []).map((v) => `<small class="cell-sub warn">${esc(v)}</small>`).join("")}</td><td>${btn("补风险数据", "exposure-input", p.id, "small")}</td></tr>`).join("")}</tbody></table></div><div class="panel-footer">${esc(e.method || "Delta 美元 = 持仓 Delta × 标的价格；Beta 调整衡量相对市场的线性敏感度。")}</div><div class="panel-body"><p class="risk-explainer">基准 ${esc(b.label || b.symbol || "未知")} · ${esc(b.day || e.market_day || "日期未知")} · 价格 ${dec(b.price)}。Beta 反映对市场的历史敏感度，不等于标的全部波动；年化波动与 R² 帮助判断特有风险和解释力。期权 Delta 会随价格、时间与隐波变化，1% 情景不包含 Gamma 与 Vega。</p>${(c.excluded || []).length ? `<details class="spaced"><summary>查看 ${c.excluded.length} 项覆盖缺口</summary><ul class="rules">${c.excluded.map((p) => `<li>${esc(p.symbol)} · ${esc(p.reason)}</li>`).join("")}</ul></details>` : ""}</div></section></section>`;
}
function strategiesSection() {
  const s = state.strategies;
  if (!s) return "";
  const groups = s.groups || [];
  return `<section class="panel strategy-section"><div class="panel-heading"><h2>期权策略与操作批次</h2>${badge(`${groups.length} 个已识别批次`)}</div><div class="panel-body"><p class="risk-explainer">同一操作编号或精确成交时间才归为同批。不同时间的操作分别保留；期权没有明显变化本身不是异常。</p>${groups.map((g) => `<article class="strategy-card"><div class="plan-head"><strong>${esc(g.symbol)} · ${esc(g.strategy?.label || "策略未知")}</strong>${badge(g.strategy?.confidence === "unknown" ? "结构待核对" : "按腿识别", g.strategy?.confidence === "unknown" ? "amber" : "")}</div><p>${esc(g.broker)}${g.account ? " · " + esc(g.account) : ""} · ${g.evidence === "operation_id" ? "操作编号 " + esc(g.operation_id || "未知") : "精确成交时间 " + esc(g.executed_at || "未知")}</p><ul class="strategy-legs">${(g.legs || []).map((l) => `<li>${l.side === "short" ? "卖出" : "买入"} ${dec(l.qty)} × ${esc(l.label || `${l.expiry || "到期未知"} ${l.strike ?? "行权价未知"} ${l.right || "类型未知"}`)}</li>`).join("")}</ul>${[...(g.notes || []), ...(g.strategy?.notes || [])].map((x) => `<p class="warn">${esc(x)}</p>`).join("")}</article>`).join("") || '<p class="muted spaced">尚无能确认同批建仓的期权记录。</p>'}${(s.current_structures || []).length ? `<details class="spaced"><summary>当前持仓形态 · 不代表同批建仓</summary><ul class="rules">${s.current_structures.map((g) => `<li><b>${esc(g.symbol)} · ${esc(g.strategy?.label || "未知结构")}</b> · ${esc(g.broker)}<small>${esc([...(g.notes || []), ...(g.strategy?.notes || [])].join("；"))}</small></li>`).join("")}</ul></details>` : ""}${(s.ungrouped || []).length ? `<details class="spaced"><summary>${s.ungrouped.length} 条期权的操作批次未知</summary><ul class="rules">${s.ungrouped.map((p) => `<li><b>${esc(p.symbol)}</b> · ${esc(p.label)}<small>${esc(p.reason)}</small>${(state.positions || []).some((x) => x.id === (p.position_id || p.id)) ? btn("补充批次信息", "exposure-input", p.position_id || p.id, "small subtle") : ""}</li>`).join("")}</ul></details>` : ""}${(s.notices || []).map(notice).join("")}</div></section>`;
}
function riskPage() {
  const r = state.risk;
  const exp = {};
  r.covered.forEach((p) => (exp[p.symbol] = (exp[p.symbol] || 0) + p.exposure));
  const exposures = Object.entries(exp).sort(
    (a, b) => Math.abs(b[1]) - Math.abs(a[1]),
  );
  const max = Math.max(1, ...exposures.map((x) => Math.abs(x[1])));
  const excluded = {};
  r.excluded.forEach(
    (p) => (excluded[p.reason] = (excluded[p.reason] || 0) + 1),
  );
  return (
    heading(
      "风险快照",
      "先看 Delta、市场敏感度与共同风险，再看历史损失情景。",
      badge(
        r.status === "available"
          ? "完整覆盖"
          : r.status === "stale"
            ? "历史参考"
            : "部分覆盖",
        "amber",
      ),
    ) +
    riskPolicySummary() + exposureSection() +
    `${r.errors.length ? notice(r.errors.join("；")) : ""}<div class="section-line"><h2>历史损失情景</h2><small>VaR 独立展示其覆盖范围</small></div><div class="risk-top"><section class="panel risk-hero"><div class="stat-label">${r.excluded.length ? "股票子组合" : "组合"} · 1 日 VaR 95% ${badge("历史模拟")}</div><div class="big-number num accent">${money(r.var95)}</div><p>${r.var_pct === null ? "总净值未齐全，占比暂不可用" : `相当于总净值的 ${dec(r.var_pct, 3)}%`} · 行情 ${esc(r.market_day || "未知")}</p><p class="spaced">${r.change95 === null ? "较上次：等待同一覆盖范围的另一份快照" : `较上次 ${r.change95 >= 0 ? "+" : ""}${money(r.change95)}`}</p><div class="risk-secondary"><div><span>VaR 99%</span><b class="num">${money(r.var99)}</b></div><div><span>ES 95%</span><b class="num">${money(r.es95)}</b></div><div><span>SPY 参考 VaR 95%</span><b class="num">${r.market_var95_pct !== undefined ? dec(r.market_var95_pct) + "%" : "—"}</b></div></div></section><section class="panel"><div class="panel-heading"><h2>这份快照覆盖了什么</h2>${badge(`${r.covered.length} / ${state.positions.length}`)}</div><div class="coverage-list"><div class="coverage-item"><span>全组合 VaR</span><b class="${r.portfolio_var95 === null ? "warn" : ""}">${r.portfolio_var95 === null ? "暂不可用" : money(r.portfolio_var95)}</b></div><div class="coverage-item"><span>共同交易日样本</span><span>${r.samples} / 252</span></div>${Object.entries(
      excluded,
    )
      .map(
        ([why, count]) =>
          `<div class="coverage-item"><span>${esc(why)}</span><span class="warn">${count} 条</span></div>`,
      )
      .join(
        "",
      )}<div class="coverage-item"><span>持仓快照日期</span><span>${esc(r.snapshot_day || "未知")}</span></div></div><div class="panel-footer">缺失项不会按零风险计入。VaR 不代表最大亏损，也不表示没有系统性风险。</div></section></div><div class="two-columns"><section class="panel"><div class="panel-heading"><h2>历史情景下的一日损失</h2><small>${r.samples} 个情景</small></div><div class="panel-body">${lossChart()}<div class="chart-caption"><span><i class="legend-dot"></i>日损失分布</span><span>尾部情景以琥珀色显示</span></div></div></section><section class="panel"><div class="panel-heading"><h2>已覆盖的股票敞口</h2><small>按标的合并</small></div><div class="panel-body">${
      exposures
        .slice(0, 7)
        .map(
          ([s, v]) =>
            `<div class="exposure"><span>${esc(s)}</span><svg viewBox="0 0 200 7" preserveAspectRatio="none" aria-hidden="true"><rect width="${(Math.abs(v) / max) * 200}" height="7" rx="3"/></svg><span class="mono">${money(v)}</span></div>`,
        )
        .join("") || empty("暂无可计算敞口", "请核对数量与行情覆盖。")
    }</div></section></div><section class="panel"><div class="panel-heading"><h2>计算口径与缺口</h2></div><div class="panel-body"><p class="facts">${esc(r.method)}。使用当前数量与行情包末日价格，对齐相同起止交易日的历史收益；组合损失取经验分位数，保留相关性。${esc(r.price_basis)}。没有把市场参考值替代组合风险。</p>${r.excluded.length ? `<details class="spaced"><summary>查看 ${r.excluded.length} 条未覆盖持仓</summary><ul class="rules">${r.excluded.map((p) => `<li><b>${esc(p.symbol)}</b>　${esc(p.reason)}</li>`).join("")}</ul></details>` : ""}</div></section>`
  );
}
function addDays(day, n) {
  const d = new Date(day + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function eventRow(e) {
  return `<div class="event-row"><div class="event-date"><b>${esc(e.day.slice(8))}</b>${esc(e.day.slice(5, 7))} 月</div><div class="event-text"><p>${esc(e.name)}</p><small>${esc(e.time || "时间未注明")} · ${esc(e.source || e.kind)}</small></div>${badge(e.kind)}${e.created_at ? btn("移除", "delete-event", e.id, "small subtle") : ""}</div>`;
}
function calendarPage() {
  const start = new Date(month + "-01T12:00:00Z"),
    offset = (start.getUTCDay() + 6) % 7;
  const first = addDays(month + "-01", -offset);
  const cells = Array.from({ length: 42 }, (_, i) => addDays(first, i));
  const visible = state.calendar.events.filter((e) =>
    selectedDay
      ? e.day === selectedDay
      : e.day.slice(0, 7) === month && e.day >= state.day,
  );
  return (
    heading(
      "日历",
      "与计划和持仓相关的事件，放在同一个时间轴里。",
      btn("＋ 添加事件", "new-event", "", "primary"),
    ) +
    `${state.calendar.errors.length ? notice(state.calendar.errors.join("；")) : ""}<div class="calendar-layout"><section class="panel"><div class="panel-heading"><h2>${month.replace("-", " 年 ")} 月</h2><div class="calendar-toolbar">${btn("‹", "month-prev", "", "small subtle")}${btn("本月", "month-today", "", "small subtle")}${btn("›", "month-next", "", "small subtle")}</div></div><div class="calendar-grid">${["一", "二", "三", "四", "五", "六", "日"].map((d) => `<div class="weekday">${d}</div>`).join("")}${cells
      .map((day) => {
        const es = state.calendar.events.filter((e) => e.day === day);
        return `<button class="day ${day === state.day ? "today" : ""} ${day.slice(0, 7) !== month ? "outside" : ""} ${selectedDay === day ? "selected" : ""}" data-day="${day}" aria-label="${day}，${es.length} 个事件"><span>${parseInt(day.slice(8))}</span>${es
          .slice(0, 2)
          .map((e) => `<div class="day-event">${esc(e.symbol || e.name)}</div>`)
          .join(
            "",
          )}${es.length > 2 ? `<small>＋${es.length - 2}</small>` : ""}</button>`;
      })
      .join(
        "",
      )}</div></section><section class="panel"><div class="panel-heading"><h2>${selectedDay ? selectedDay.slice(5).replace("-", " 月 ") + " 日" : "本月接下来的事件"}</h2>${selectedDay ? btn("全部", "clear-day", "", "small subtle") : badge(visible.length + " 项")}</div><div class="panel-body">${visible.slice(0, 15).map(eventRow).join("") || empty("暂无已登记事件", "可以添加复核日或已确认的财报日期。")}</div><div class="panel-footer">宏观来源沿用原日历。未确认的财报日期单独标注；市场事件时间按来源显示。</div></section></div>`
  );
}
function openModal(title, body) {
  draftDirty = false;
  $("#dialog-content").innerHTML =
    `<div class="dialog-head"><h2 id="dialog-title">${esc(title)}</h2><button class="close" data-action="close" aria-label="关闭">×</button></div><div class="dialog-body">${body}</div>`;
  if (!$("#dialog").open) $("#dialog").showModal();
}
function closeModal() {
  if (draftDirty && !confirm("还没有保存的修改将丢弃，确定关闭？")) return;
  if ($("#risk-policy-form") && pendingPlanDraft) {
    const draft = pendingPlanDraft;
    pendingPlanDraft = null;
    planForm(draft);
    draftDirty = true;
    return;
  }
  draftDirty = false;
  $("#dialog").close();
}
function field(name, label, value = "", opts = {}) {
  return `<label class="field ${opts.wide ? "wide" : ""}"><span>${label}</span>${opts.options ? `<select name="${name}">${opts.options.map(([v, t]) => `<option value="${v}" ${value === v ? "selected" : ""}>${t}</option>`).join("")}</select>` : opts.area ? `<textarea name="${name}" ${opts.required ? "required" : ""}>${esc(value)}</textarea>` : `<input name="${name}" type="${opts.type || "text"}" value="${esc(value)}" ${opts.type === "number" ? `step="any" min="${opts.min ?? 0}" ${opts.max !== undefined ? `max="${opts.max}"` : ""}` : ""} ${opts.required ? "required" : ""} ${opts.placeholder ? `placeholder="${esc(opts.placeholder)}"` : ""}>`}${opts.hint ? `<small>${opts.hint}</small>` : ""}</label>`;
}
function riskPolicyForm() {
  const p = state.risk_policy || {};
  openModal("统一 1R 标准", `<form id="risk-policy-form" data-version="${p.version || 0}"><p class="risk-explainer">这是所有新计划共用的风险预算。已标记待执行或已成交的计划保留其原始 1R；观察仓也遵循相同标准。</p><div class="form-grid spaced">${field("nlv", "确认后的定仓净值（USD）", p.nlv ?? state.snapshot.nlv ?? "", { type: "number", required: true, hint: "以统一美元口径填写；快照不会自动替你确认。" })}${field("risk_pct", "统一每笔风险（%）", p.risk_pct ?? 2, { type: "number", max: p.max_risk_pct ?? 4, required: true, hint: `默认 2%，最多 ${p.max_risk_pct ?? 4}%；变更仅用于后续计划。` })}</div><p class="risk-explainer spaced">完整计划须达到 0.9R–1R。资金或合约粒度不适合时保持观察，不移动失效位凑数量。股价跳空仍可能使真实亏损超过计划。</p><div id="form-error" class="form-error" role="alert"></div><div class="dialog-actions">${pendingPlanDraft ? btn("返回计划", "return-plan") : btn("取消", "close")}<button type="submit" class="button primary">保存统一标准</button></div></form>`);
}
function exposureInputForm(id) {
  const position = state.positions.find((p) => p.id === id);
  const row = (state.exposure?.rows || []).find((p) => p.id === id);
  if (!position || !row) return toast("持仓已变化，请重新读取", true);
  const input = state.risk_inputs?.[id] || position.exposure_input || {};
  openModal("补充持仓风险数据", `<p class="muted">${esc(row.symbol)} · ${esc(row.broker)} · ${esc(position.label || "")}</p><p class="risk-explainer spaced">这些标注只用于风险计算，不改成交账本。期权填写一份买方合约的 Delta（Call 为正，Put 为负），系统再按实际多空、数量与乘数计算整仓 Delta。</p><form id="exposure-input-form" data-id="${esc(id)}"><div class="form-grid spaced">${position.kind === "option" ? field("delta", "每份买方合约 Delta（-1 至 1）", input.delta ?? "", { type: "number", min: -1, max: 1, hint: "不是整仓 Delta；不清楚可留空，系统显示未知。" }) : ""}${field("underlying_price", "标的现价（USD）", input.underlying_price ?? "", { type: "number", hint: "期权须填标的价格，不能填写权利金。" })}${field("asof", "数据日期", input.asof?.slice(0, 10) || state.day, { type: "date", required: true })}${field("source", "数据来源", input.source || "", { required: true, placeholder: "例如：券商持仓页，人工核对" })}${field("sector", "行业（可选）", input.sector || row.sector || "", { placeholder: "例如：科技、医疗" })}${field("theme", "风险主题（可选）", input.theme || row.theme || "", { placeholder: "例如：科技、半导体" })}${position.kind === "option" ? field("account_id", "账户别名（同批腿保持一致）", input.account_id || "", { hint: "不用真实账号。未确认同一账户时，不能认证同批建仓。" }) + field("operation_id", "已核对的同批操作编号（可选）", input.operation_id || "", { hint: "同一账户、同一次操作的各腿填同一编号，并在来源中写明凭据。不同时间分别编号；未知留空。" }) : ""}</div><div id="form-error" class="form-error" role="alert"></div><div class="dialog-actions">${btn("取消", "close")}<button type="submit" class="button primary">保存风险标注</button></div></form>`);
}
function planForm(seed = {}) {
  openModal(
    seed.id ? "编辑计划与定仓" : "新建交易计划",
    `<form id="plan-form" data-id="${esc(seed.id || "")}" data-version="${seed.version || ""}"><div class="form-grid">${field("symbol", "标的代码", seed.symbol, { required: true, placeholder: "如 SPY" })}${field("title", "计划名称", seed.title, { required: true, placeholder: "用一句话描述这笔交易" })}${field(
      "kind",
      "类型",
      seed.kind || "stock",
      {
        options: [
          ["stock", "股票 / ETF"],
          ["option", "期权 / 定义风险结构"],
        ],
      },
    )}${field("side", "方向", seed.side || "long", {
      options: [
        ["long", "买入 / 做多"],
        ["short", "卖出 / 做空"],
      ],
    })}${field("broker", "账户", seed.broker || "IBKR", {
      options: [
        ["IBKR", "IBKR"],
        ["Schwab", "Schwab"],
        ["ToS", "thinkorswim"],
        ["其他", "其他"],
      ],
    })}${field("review_day", "计划复核日", seed.review_day, { type: "date" })}${field("thesis", "交易理由与触发条件", seed.thesis, { area: true, wide: true })}${field("invalidation", "什么情况下判断失效", seed.invalidation, { area: true })}${field("exit_rule", "退出安排", seed.exit_rule, { area: true })}<div class="form-section">定仓 · 先确定能承担的风险</div>${riskPolicySummary(true)}${field("planned_qty", "完整开仓计划数量（可选）", seed.planned_qty ?? "", { type: "number", hint: "留空采用系统目标。分批成交共用这一笔计划的 1R。" })}${field("estimated_costs", "费用与滑点预留（USD）", seed.estimated_costs ?? 0, { type: "number", hint: "整笔交易预估总额，从 1R 中预留后计算数量。" })}${field("entry", "计划入场价 / 每份权利金", seed.entry, { type: "number" })}${field("stop", "股票失效价", seed.stop, { type: "number", hint: "仅股票定仓使用；期权买方按权利金全损计算。" })}${field("mult", "期权乘数", seed.mult || 100, { type: "number" })}${field("max_loss", "卖方定义风险结构：每组最大亏损（USD）", seed.max_loss, { type: "number", hint: "必须已确认；裸卖期权不适用。" })}${field("contract", "期权合约 / 结构说明", seed.contract, { placeholder: "到期、行权价、C/P 与各腿比例" })}${field("expiry", "期权到期日", seed.expiry, { type: "date" })}</div><div class="row-actions">${btn("计算定仓", "calculate-size", "", "subtle")}</div><div id="sizing-result">${seed.sizing ? sizingOutput(seed.sizing) : ""}</div><div id="form-error" class="form-error" role="alert"></div><div class="dialog-actions"><button type="button" class="button subtle" data-action="close">取消</button><button type="submit" class="button primary">保存计划草稿</button></div></form>`,
  );
}
function detail(id) {
  const p = state.plans.find((x) => x.id === id);
  if (!p) return;
  openModal(
    p.title,
    `<div class="plan-head"><span class="symbol">${esc(p.symbol)} · ${esc(p.broker)}</span>${badge(statusNames[p.status], p.status === "holding" ? "green" : "")}</div><div class="details-grid">${[
      ["交易理由", p.thesis],
      ["失效条件", p.invalidation],
      ["退出安排", p.exit_rule],
      [
        "定仓结果",
        p.sizing
          ? `${p.sizing.planned_qty ?? p.sizing.qty} 单位 · ${p.status === "draft" ? "当前" : "原始"} 1R ${money(p.sizing.budget)} · 利用率 ${pct((p.sizing.utilization ?? (p.sizing.planned_risk / p.sizing.budget)) * 100)} · ${p.sizing.basis}`
          : "尚未定仓 / 原持仓接入",
      ],
    ]
      .map(
        ([label, value]) =>
          `<div class="detail-block"><small>${label}</small><p>${esc(value || "待补充")}</p></div>`,
      )
      .join(
        "",
      )}</div>${disciplinePanel(p)}${p.status === "draft" && p.sizing ? sizingOutput(p.sizing) : ""}${p.imported_rules?.length ? `<details class="spaced"><summary>沿用原持仓判据</summary><ul class="rules">${p.imported_rules.map((r) => `<li>${esc(r.label || r.name || r.kind || "判据")} · ${esc(r.op || r.dir || "")} ${esc(r.level ?? r.value ?? "")}<small>${esc(r.note || "")}</small></li>`).join("")}</ul></details>` : ""}<div class="dialog-actions">${p.status === "draft" ? btn("编辑与定仓", "edit-plan", p.id) : ""}${p.status === "draft" ? btn("标记待执行", "ready", p.id, "primary") : ""}${["draft", "ready"].includes(p.status) ? btn("取消此计划", "cancel-plan", p.id, "subtle danger") : ""}${["ready", "holding"].includes(p.status) ? btn("登记入场成交", "entry", p.id, "primary") : ""}${p.status === "holding" ? btn("登记退出", "exit", p.id) : ""}</div>${(p.execution_warnings || []).map(notice).join("")}<div class="section-line"><h2>执行留痕</h2><small>成交登记不会向券商下单</small></div>${p.fills.map((f) => `<div class="journal-entry"><strong>${f.voided ? "[已撤销登记] " : ""}${esc(f.day)} · ${f.opening_balance ? "期初余额" : f.action === "entry" ? "入场" : "退出"} ${dec(f.qty)} @ ${dec(f.price)}</strong><p>${esc(f.note || "未填说明")} · 费用 ${money(f.fee)}</p></div>`).join("") || '<p class="muted">尚无成交记录。</p>'}${p.fills.filter((f) => !f.voided).length && !p.fills.filter((f) => !f.voided).at(-1).opening_balance ? `<div class="row-actions">${btn("撤销上一条错误登记", "void-fill", p.id, "small subtle")}</div>` : ""}<form id="review-form" data-id="${p.id}" data-version="${p.version}" class="spaced">${field("note", "复盘 / 后续处理记录", p.review_note, { area: true, wide: true })}<div id="form-error" class="form-error" role="alert"></div><div class="row-actions"><small>已实现损益 ${money(p.realized_pnl)}</small><button type="submit" class="button">保存复盘</button></div></form>`,
  );
}
function fillForm(id, action) {
  const p = state.plans.find((x) => x.id === id);
  openModal(
    action === "entry" ? "登记已成交的入场" : "登记已成交的退出",
    `<p class="muted">${esc(p.symbol)} · ${esc(p.title)}</p><div class="notice spaced">这里只记录已发生的成交，不向券商发送订单。${action === "exit" ? "当前剩余 " + dec(p.open_qty) + " 个单位。" : ""}</div><form id="fill-form" data-id="${p.id}" data-version="${p.version}" data-fill-action="${action}" data-request="${crypto.randomUUID()}"><div class="form-grid">${field("qty", "成交数量", action === "exit" ? p.open_qty : "", { type: "number", required: true })}${field("price", "实际成交价格", "", { type: "number", required: true })}${field("fee", "本次费用（USD）", 0, { type: "number" })}${field("day", "成交日期", state.day, { type: "date", required: true })}${field("note", "成交说明", action === "exit" ? p.exit_rule : "", { area: true, wide: true })}</div><div id="form-error" class="form-error" role="alert"></div><div class="dialog-actions"><button type="button" class="button" data-action="close">取消</button><button class="button primary" type="submit">保存真实成交</button></div></form>`,
  );
}
function adoptForm(id) {
  const p = state.snapshot.positions.find((p) => p.id === id);
  openModal(
    "核对并接入持仓",
    `<div class="plan-head"><span class="symbol">${esc(p.sym)} · ${esc(p.broker)}</span>${badge(p.side === "short" ? "空头" : "多头")}</div><p>${esc(p.label || p.group || "股票")}</p><div class="notice spaced">请先在券商核对实际数量与平均成本。接入后，本 App 保存期初余额与后续退出记录；保留原始快照作为来源。</div><form id="adopt-form" data-id="${p.id}" data-fingerprint="${p.fingerprint}"><div class="form-grid">${field("qty", "确认后的实际持仓数量", p.qty, { type: "number", required: true })}${field("open", "确认后的平均成本", p.open, { type: "number", required: true })}${field("day", "余额核对日期", state.day, { type: "date", required: true })}${field("invalidation", "当前失效条件", "", { area: true })}${field("exit_rule", "当前退出安排", "", { area: true, wide: true })}</div><label class="check-row spaced"><input type="checkbox" name="confirmed" required><span>已核对券商持仓；接入的是期初余额</span></label><div id="form-error" class="form-error" role="alert"></div><div class="dialog-actions"><button type="submit" class="button primary">接入交易流程</button></div></form>`,
  );
}
function eventForm() {
  openModal(
    "添加日历事件",
    `<form id="event-form"><div class="form-grid">${field("name", "事件名称", "", { required: true, wide: true })}${field("day", "日期", selectedDay || state.day, { type: "date", required: true })}${field("time", "时间与时区", "", { placeholder: "如 08:30 ET；未知可留空" })}${field("symbol", "关联标的（可选）", "", { placeholder: "如 AAPL" })}</div><div id="form-error" class="form-error" role="alert"></div><div class="dialog-actions"><button class="button primary" type="submit">保存事件</button></div></form>`,
  );
}
function dataOf(form) {
  return Object.fromEntries(new FormData(form).entries());
}
function econfNewPlanForm(key, values) {
  const [sha, idx] = key.split("|");
  const f = (state.econf?.files || []).find((x) => x.sha === sha);
  const leg = f?.proposal?.legs?.[Number(idx)];
  if (!leg) return;
  const opt = leg.option || {};
  const isOption = !!opt.root;
  openModal(
    "确认单登记为新计划",
    `<p class="notice spaced">请核对真实成交和完整合约。登记后形成持仓，判据可以在复盘中补充。</p><form id="econf-new-form" data-sha="${esc(
      sha,
    )}" data-file="${esc(f.file)}" data-leg="${esc(idx)}"><div class="form-grid">${field(
      "symbol",
      "标的代码",
      opt.root || leg.description?.split(" ")[0] || "",
      { required: true },
    )}${field("title", "计划名称", leg.description || "", {
      required: true,
    })}${field("kind", "类型", isOption ? "option" : "stock", {
      options: [
        ["stock", "股票 / ETF"],
        ["option", "期权 / 定义风险结构"],
      ],
    })}${field("side", "方向", leg.side === "sell" ? "short" : "long", {
      options: [
        ["long", "买入 / 做多"],
        ["short", "卖出 / 做空"],
      ],
    })}${field("broker", "账户", "Schwab", {
      options: [
        ["Schwab", "Schwab"],
        ["ToS", "thinkorswim"],
      ],
    })}${field("qty", "成交数量", values.qty, { type: "number", required: true, step: "any" })}${field("price", "成交价格", values.price, { type: "number", required: true, step: "any" })}${field("fee", "已核对费用", values.fee, { type: "number", required: true, step: "any", placeholder: "确认为零时填 0" })}${field("day", "成交日期", values.day, { type: "date", required: true })}${field("mult", "期权乘数", isOption ? 100 : 1, { type: "number" })}${field(
      "expiry",
      "期权到期日",
      opt.expiry || "",
      { type: "date" },
    )}${field("contract", "合约 / 结构说明", leg.description || "")}${field(
      "invalidation",
      "失效条件（可留空）",
      "",
      { area: true },
    )}${field("exit_rule", "退出安排（可留空）", "", {
      area: true,
      wide: true,
    })}</div><label class="check-row spaced"><input type="checkbox" name="confirmed" required><span>已核对成交数量、价格、费用、日期、账户、完整合约与乘数</span></label>${leg.dupe ? `<label class="check-row spaced warn"><input type="checkbox" name="duplicate_confirmed" required><span>已核对：这是另一笔真实成交，并非已有成交的重复通知</span></label>` : ""}<div id="form-error" class="form-error" role="alert"></div><div class="dialog-actions"><button type="button" class="button subtle" data-action="close">取消</button><button type="submit" class="button primary">登记入账</button></div></form>`,
  );
}
async function perform(action, id) {
  if (action === "void-fill") {
    const p = state.plans.find((x) => x.id === id),
      f = p.fills.filter((f) => !f.voided).at(-1);
    openModal(
      "更正成交记录",
      `<p class="notice">撤销的是本机错误登记，不会撤销券商实际成交。原记录与更正原因会保留。</p><form id="void-form" data-id="${p.id}" data-version="${p.version}" data-fill="${f.id}">${field("reason", "更正原因", "", { area: true, required: true })}<div id="form-error" class="form-error"></div><div class="dialog-actions"><button type="submit" class="button danger">确认撤销错误登记</button></div></form>`,
    );
    return;
  }
  if (action === "close") return closeModal();
  if (action === "econf-scan") {
    if (!discardReceiptEdits()) return;
    await api("econf-scan", {});
    await reload();
    toast("已重新扫描确认单文件夹");
    return;
  }
  if (action === "econf-dismiss") {
    if (
      !confirm("忽略尚未登记的成交？原文件和已登记记录保留，可以点击恢复处理。")
    )
      return;
    await api("econf-dismiss", { sha: id });
    await reload();
    toast("确认单已忽略");
    return;
  }
  if (action === "econf-restore") {
    await api("econf-restore", { sha: id });
    await reload();
    toast("已恢复处理");
    return;
  }
  if (action === "econf-record") {
    const row = document.querySelector(`[data-leg-row="${CSS.escape(id)}"]`);
    if (!row) return;
    const [sha, idx] = id.split("|");
    const f = (state.econf?.files || []).find((x) => x.sha === sha);
    const leg = f?.proposal?.legs?.[Number(idx)];
    const val = (name) => row.querySelector(`[name=${name}]`).value.trim();
    const sel = val("plan_select");
    if (!sel)
      return toast("请先选择要并入的计划，或选择「登记为新计划」", true);
    const values = Object.fromEntries(
      ["qty", "price", "fee", "day"].map((k) => [k, val(k)]),
    );
    if (sel === "new") return econfNewPlanForm(id, values);
    if (Object.values(values).some((v) => v === ""))
      return toast("请补齐数量、价格、日期和已核对费用；费用为零时填 0", true);
    const [planId, version] = sel.split("::");
    const plan = state.plans.find((p) => p.id === planId);
    const direction = val("direction") || econfAction(leg, plan?.side);
    if (direction === "?")
      return toast("请核对买卖及开平方向，再选择入场或退出", true);
    if (
      !confirm(
        `将 ${leg.description} 并入 ${plan.broker} · ${plan.contract || plan.title}\n${direction === "entry" ? "入场" : "退出"} ${values.qty}，价格 ${values.price}，费用 ${values.fee}，日期 ${values.day}\n请核对账户、完整合约和乘数。${leg.dupe ? "\n发现相同参数的已登记成交。确认这是另一笔真实成交？" : "\n确认登记？"}`,
      )
    )
      return;
    await api("econf-record", {
      sha,
      file: f.file,
      leg: Number(idx),
      plan_id: planId,
      version: Number(version),
      action: direction,
      ...values,
      confirmed: true,
      instrument_confirmed: true,
      duplicate_confirmed: !!leg.dupe,
    });
    receiptDirty = false;
    await reload();
    toast("确认单成交已登记入账");
    return;
  }
  if (action === "risk-policy") {
    const f = $("#plan-form");
    pendingPlanDraft = f ? { ...dataOf(f), id: f.dataset.id, version: f.dataset.version } : null;
    return riskPolicyForm();
  }
  if (action === "return-plan") {
    const draft = pendingPlanDraft;
    pendingPlanDraft = null;
    planForm(draft || {});
    draftDirty = Boolean(draft);
    return;
  }
  if (action === "exposure-input") return exposureInputForm(id);
  if (action === "new-plan") { pendingPlanDraft = null; return planForm(); }
  if (action === "edit-plan")
    return planForm(state.plans.find((p) => p.id === id));
  if (action === "detail") return detail(id);
  if (action === "entry" || action === "exit") return fillForm(id, action);
  if (action === "adopt") return adoptForm(id);
  if (action === "new-event") return eventForm();
  if (action === "show-holdings") {
    route = "trade";
    tab = "holdings";
    render();
    return;
  }
  if (action === "from-observation") {
    const r = state.brief.items.find((x) => x.symbol === id);
    return planForm({
      symbol: id.split(":").pop(),
      title: id.split(":").pop() + " 观察计划",
      thesis: Object.values(r.facts).join("\n"),
    });
  }
  if (action === "from-legacy") {
    const p = state.snapshot.legacy_plans.find((x) => x.id === id);
    return planForm({
      symbol: p.sym,
      title: p.label || p.sym + " 计划",
      thesis: p.basis,
      entry: p.level,
    });
  }
  if (action === "calculate-size") {
    const result = await api("size", dataOf($("#plan-form")));
    $("#sizing-result").innerHTML = sizingOutput(result);
    $("#form-error").textContent = "";
    return;
  }
  if (action === "ready" || action === "cancel-plan") {
    const p = state.plans.find((p) => p.id === id);
    if (
      action === "cancel-plan" &&
      !confirm("取消这笔尚未成交的计划？记录会保留。")
    )
      return;
    await api("status", {
      id,
      version: p.version,
      status: action === "ready" ? "ready" : "cancelled",
    });
    draftDirty = false;
    await reload();
    detail(id);
    toast(
      action === "ready"
        ? "计划已准备好，可以在成交后登记。"
        : "计划已取消，历史保留。",
    );
    return;
  }
  if (action.startsWith("month-")) {
    if (action === "month-today") month = state.day.slice(0, 7);
    else {
      const d = new Date(month + "-01T12:00:00Z");
      d.setUTCMonth(d.getUTCMonth() + (action === "month-next" ? 1 : -1));
      month = d.toISOString().slice(0, 7);
    }
    selectedDay = null;
    render();
    return;
  }
  if (action === "clear-day") {
    selectedDay = null;
    render();
    return;
  }
  if (action === "delete-event" && confirm("移除这个手工登记的事件？")) {
    await api("event-delete", { id });
    await reload();
    toast("事件已移除");
  }
}
document.addEventListener("click", async (e) => {
  const nav = e.target.closest("[data-route]"),
    tabs = e.target.closest("[data-tab]"),
    f = e.target.closest("[data-filter]"),
    day = e.target.closest("[data-day]"),
    a = e.target.closest("[data-action]");
  if (nav || tabs || f || day) {
    if (!discardReceiptEdits()) return;
    try {
      await savePrepIfNeeded();
    } catch (err) {
      toast(err.message, true);
      return;
    }
  }
  if (nav) {
    route = nav.dataset.route;
    render();
  }
  if (tabs) {
    tab = tabs.dataset.tab;
    render();
  }
  if (f) {
    filter = f.dataset.filter;
    render();
  }
  if (day) {
    selectedDay = day.dataset.day;
    render();
  }
  if (a) {
    e.preventDefault();
    a.disabled = true;
    try {
      await perform(a.dataset.action, a.dataset.id);
    } catch (err) {
      const box = $("#form-error");
      if ($("#dialog").open && box) box.textContent = err.message;
      else toast(err.message, true);
    } finally {
      a.disabled = false;
    }
  }
});
document.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target,
    button = f.querySelector("[type=submit]");
  if (!button) return;
  button.disabled = true;
  const d = dataOf(f);
  try {
    let result;
    if (f.id === "prep-form") {
      await api("preparation", {
        day: state.day,
        checks: [...f.querySelectorAll("[name=check]:checked")].map(
          (x) => x.value,
        ),
        note: d.note,
      });
      prepDirty = false;
      await reload();
      toast("今天的盘前准备已保存");
      return;
    }
    if (f.id === "risk-policy-form") {
      await api("risk-policy", { ...d, version: Number(f.dataset.version) });
      draftDirty = false;
      await reload();
      const draft = pendingPlanDraft;
      pendingPlanDraft = null;
      if (draft) {
        planForm(draft);
        draftDirty = true;
      } else $("#dialog").close();
      toast("统一 1R 已保存；已冻结计划保留原始基准");
      return;
    }
    if (f.id === "exposure-input-form") {
      await api("exposure-input", { ...d, position_id: f.dataset.id });
    }
    if (f.id === "plan-form") {
      if (f.dataset.id) {
        d.id = f.dataset.id;
        d.version = Number(f.dataset.version);
      }
      result = await api("plans", d);
    }
    if (f.id === "fill-form") {
      result = await api("fills", {
        ...d,
        id: f.dataset.id,
        version: Number(f.dataset.version),
        action: f.dataset.fillAction,
        request_id: f.dataset.request,
      });
    }
    if (f.id === "void-form") {
      result = await api("void-fill", {
        id: f.dataset.id,
        version: Number(f.dataset.version),
        fill_id: f.dataset.fill,
        reason: d.reason,
      });
    }
    if (f.id === "review-form") {
      result = await api("review", {
        id: f.dataset.id,
        version: Number(f.dataset.version),
        note: d.note,
      });
    }
    if (f.id === "adopt-form") {
      result = await api("adopt", {
        ...d,
        id: f.dataset.id,
        fingerprint: f.dataset.fingerprint,
        confirmed: f.querySelector("[name=confirmed]").checked,
      });
    }
    if (f.id === "econf-new-form") {
      await api("econf-record", {
        sha: f.dataset.sha,
        file: f.dataset.file,
        leg: Number(f.dataset.leg),
        confirmed: f.querySelector("[name=confirmed]").checked,
        new_plan: true,
        qty: d.qty,
        price: d.price,
        fee: d.fee,
        day: d.day,
        instrument_confirmed: true,
        duplicate_confirmed: !!f.querySelector(
          "[name=duplicate_confirmed]:checked",
        ),
        plan: {
          ...d,
          mult: d.mult,
          expiry: d.expiry || "",
        },
      });
      draftDirty = false;
      receiptDirty = false;
      $("#dialog").close();
      await reload();
      toast("确认单已登记为新计划并入账");
      return;
    }
    if (f.id === "event-form") await api("events", d);
    draftDirty = false;
    $("#dialog").close();
    await reload();
    if (result?.id && f.id !== "adopt-form") detail(result.id);
    toast("已保存到本机");
  } catch (err) {
    const box = $("#form-error");
    if (box) box.textContent = err.message;
    else toast(err.message, true);
  } finally {
    button.disabled = false;
  }
});
document.addEventListener("input", (e) => {
  if (e.target.closest("#plan-form")) {
    const preview = $("#sizing-result");
    if (preview?.innerHTML) preview.innerHTML = '<p class="muted spaced">输入已变更，请重新计算定仓。</p>';
  }
  if (e.target.closest("#prep-form")) prepDirty = true;
  if (e.target.closest("[data-leg-row]")) receiptDirty = true;
});
document.addEventListener("change", (e) => {
  if (e.target.closest("[data-leg-row]")) receiptDirty = true;
});
$("#dialog").addEventListener("input", () => (draftDirty = true));
$("#dialog").addEventListener("cancel", (e) => {
  if (draftDirty) {
    e.preventDefault();
    closeModal();
  }
});
$("#reload").addEventListener("click", async () => {
  const b = $("#reload");
  b.disabled = true;
  try {
    await reload();
    toast("已重新读取现有快照与交易记录");
  } catch (e) {
    toast(e.message, true);
  } finally {
    b.disabled = false;
  }
});
$("#backup").addEventListener("click", async () => {
  try {
    const b = await api("backup", {});
    if (window.webkit?.messageHandlers?.desktop)
      window.webkit.messageHandlers.desktop.postMessage({
        action: "revealBackup",
        path: b.path,
      });
    toast("备份已保存：" + b.path);
  } catch (e) {
    toast(e.message, true);
  }
});
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("#theme").innerHTML =
    (theme === "dark" ? "切换浅色外观" : "切换深色外观") + " <span>◐</span>";
  if (window.webkit?.messageHandlers?.desktop)
    window.webkit.messageHandlers.desktop.postMessage({
      action: "appearance",
      theme,
    });
}
$("#theme").addEventListener("click", async () => {
  const button = $("#theme");
  const next =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  button.disabled = true;
  try {
    await api("appearance", { theme: next });
    applyTheme(next);
  } catch (e) {
    toast(e.message, true);
  } finally {
    button.disabled = false;
  }
});
applyTheme("dark");
let seenReceipts = null;
async function checkInbox() {
  if (document.hidden || !state) return;
  try {
    const inbox = await api("econf");
    const key = JSON.stringify(inbox.files.map((f) => [f.sha, f.status]));
    if (seenReceipts !== null && key !== seenReceipts && inbox.pending) {
      toast(`成交收件箱有 ${inbox.pending} 份待处理回单，可在交易记录中查看`);
    }
    seenReceipts = key;
    // Only update the model. Do not replace forms while the user is editing.
    state.econf = inbox;
  } catch {} // Manual scan still reports a connection error and supports retry.
}
setInterval(checkInbox, 60000);
function tick() {
  $("#clock").textContent =
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date()) + " ET";
}
tick();
setInterval(tick, 30000);
reload().catch((e) => {
  $("#app").innerHTML = empty("暂时无法打开交易资料", e.message);
});
