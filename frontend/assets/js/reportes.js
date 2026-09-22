// ---------------------------------------------------------------------------
// REPORTES / MÉTRICAS: usuarios, seguridad, pedidos y soporte/actividad.
// (Antes era una sección de gestionuser.html; ver reportes.html.)
// ---------------------------------------------------------------------------
const METRICS_URL = `${window.APP_CONFIG.API_BASE}/users/metrics/`;
const ORDERS_METRICS_URL = `${window.APP_CONFIG.API_BASE}/orders/metrics/`;
const SUPPORT_METRICS_URL = `${window.APP_CONFIG.API_BASE}/support-messages/metrics/`;
const AUDIT_METRICS_URL = `${window.APP_CONFIG.API_BASE}/audit/metrics/`;

function renderMetricsBars(containerId, items, { labelKey, countKey, labelFormatter }) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!items || items.length === 0) {
    container.innerHTML = `<p class="roles-loading">Sin datos para mostrar.</p>`;
    return;
  }
  const max = Math.max(...items.map((item) => item[countKey]), 1);
  container.innerHTML = items
    .map((item) => {
      const label = escapeHtml(labelFormatter ? labelFormatter(item[labelKey]) : item[labelKey]);
      const pct = Math.round((item[countKey] / max) * 100);
      return `
        <div class="metrics-bar-row">
          <span class="metrics-bar-label" title="${label}">${label}</span>
          <span class="metrics-bar-track"><span class="metrics-bar-fill" style="width:${pct}%"></span></span>
          <span class="metrics-bar-count">${item[countKey]}</span>
        </div>
      `;
    })
    .join("");
}

function renderAuthMethodStats(authMethod) {
  const container = document.getElementById("metricsAuthMethod");
  if (!container) return;
  const local = authMethod?.local ?? 0;
  const google = authMethod?.google ?? 0;
  container.innerHTML = `
    <div class="metrics-stat">
      <span class="metrics-stat-value">${local}</span>
      <span class="metrics-stat-label">Local (email/password)</span>
    </div>
    <div class="metrics-stat">
      <span class="metrics-stat-value">${google}</span>
      <span class="metrics-stat-label">Google</span>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Helpers genéricos de reportes: el cálculo viene hecho del backend, acá
// solo se pinta (tablas simples y filas de números grandes, reusando
// metrics-bars / metrics-stat-row / stat-value — sin librerías de gráficos).
// ---------------------------------------------------------------------------
function setMetricsLoading(ids) {
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = `<p class="roles-loading">Cargando métricas...</p>`;
  });
}

function renderMetricsEmpty(containerId, text = "Sin datos todavía.") {
  const container = document.getElementById(containerId);
  if (container) container.innerHTML = `<p class="roles-loading">${escapeHtml(text)}</p>`;
}

// "YYYY-MM" -> "ene 2026". Los meses sin datos ya vienen resueltos por el
// backend (0 en vez de omitir el mes en las series que lo necesitan).
function formatMonthLabel(month) {
  const [year, monthNum] = String(month || "").split("-");
  const date = new Date(Number(year), Number(monthNum) - 1, 1);
  if (Number.isNaN(date.getTime())) return month;
  return date.toLocaleDateString("es-AR", { month: "short", year: "numeric" });
}

// Nunca un "—" sin explicación: null/undefined siempre cae en el mismo texto.
function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "Sin datos todavía";
  return `${Number(value).toFixed(1)}%`;
}

function formatHours(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "Sin datos todavía";
  return `${Number(value).toFixed(1)} h`;
}

function renderMetricsTable(containerId, columns, rows, emptyText = "Sin datos todavía.") {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!rows || rows.length === 0) {
    container.innerHTML = `<p class="roles-loading">${emptyText}</p>`;
    return;
  }
  const head = columns.map((col) => `<th>${escapeHtml(col.label)}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        `<tr>${columns
          .map((col) => `<td>${escapeHtml(col.render ? col.render(row) : row[col.key] ?? "-")}</td>`)
          .join("")}</tr>`
    )
    .join("");
  container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderStatRow(containerId, stats, emptyText = "Sin datos todavía.") {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!stats || stats.length === 0) {
    container.innerHTML = `<p class="roles-loading">${emptyText}</p>`;
    return;
  }
  container.innerHTML = stats
    .map(
      (stat) => `
        <div class="metrics-stat">
          <span class="metrics-stat-value">${escapeHtml(stat.value)}</span>
          <span class="metrics-stat-label">${escapeHtml(stat.label)}</span>
        </div>
      `
    )
    .join("");
}

// --- Bloque "Usuarios" (incluye el contrato original: role_distribution,
// signups_by_month, auth_method) + "Seguridad" -----------------------------

const REPORTS_USER_IDS = [
  "metricsRoleDistribution", "metricsSignupsByMonth", "metricsAuthMethod",
  "metricsActiveVsInactive", "metricsActiveUsers", "metricsRetention",
  "metricsLockoutsByMonth", "metricsTopFailedAttempts", "metricsAccountAge",
  "metricsEmailVerification", "metricsPendingPasswordChange", "metricsAuthVerificationCross",
];

function renderUsersMetrics(data) {
  renderMetricsBars("metricsRoleDistribution", data.role_distribution, {
    labelKey: "label", countKey: "count",
  });
  renderMetricsBars("metricsSignupsByMonth", data.signups_by_month, {
    labelKey: "month", countKey: "count", labelFormatter: formatMonthLabel,
  });
  renderAuthMethodStats(data.auth_method);

  renderMetricsTable(
    "metricsActiveVsInactive",
    [
      { label: "Mes", render: (r) => formatMonthLabel(r.month) },
      { label: "Altas", key: "signups" },
      { label: "Bajas", key: "deactivations" },
      { label: "Neto", render: (r) => (r.net > 0 ? `+${r.net}` : r.net) },
    ],
    data.active_vs_inactive_by_month
  );

  const activeUsers = data.active_users || {};
  renderStatRow("metricsActiveUsers", [
    { value: activeUsers.last_7_days ?? 0, label: "Activos (7 días)" },
    { value: activeUsers.last_30_days ?? 0, label: "Activos (30 días)" },
    { value: activeUsers.last_90_days ?? 0, label: "Activos (90 días)" },
    { value: activeUsers.never_logged_in ?? 0, label: "Nunca inició sesión" },
  ]);

  renderMetricsTable(
    "metricsRetention",
    [
      { label: "Mes de registro", render: (r) => formatMonthLabel(r.month) },
      { label: "Cohorte", key: "cohort_size" },
      { label: "Volvieron", key: "returned" },
      { label: "% Retención", render: (r) => formatPercent(r.retention_rate) },
    ],
    data.retention
  );

  renderMetricsBars("metricsLockoutsByMonth", data.lockouts_by_month, {
    labelKey: "month", countKey: "count", labelFormatter: formatMonthLabel,
  });

  renderMetricsTable(
    "metricsTopFailedAttempts",
    [
      { label: "Email", key: "email" },
      { label: "Intentos", key: "failed_attempts" },
      { label: "Bloqueada ahora", render: (r) => (r.is_locked ? "Sí" : "No") },
    ],
    data.top_failed_attempts
  );

  const accountAge = data.account_age || {};
  const ageStats = [
    {
      value: accountAge.average_days != null ? `${Math.round(accountAge.average_days)} días` : "Sin datos todavía",
      label: "Antigüedad promedio",
    },
    ...(accountAge.by_role || []).map((row) => ({
      value: `${Math.round(row.average_days)} días`,
      label: translateRole(row.role.charAt(0).toUpperCase() + row.role.slice(1)),
    })),
  ];
  renderStatRow("metricsAccountAge", ageStats);
}

function renderSecurityMetrics(data) {
  const verification = data.email_verification || {};
  renderStatRow("metricsEmailVerification", [
    { value: verification.verified ?? 0, label: "Verificados" },
    { value: verification.unverified ?? 0, label: "Sin verificar" },
    { value: verification.expired_token ?? 0, label: "Con token vencido" },
  ]);

  renderStatRow("metricsPendingPasswordChange", [
    { value: data.pending_password_change ?? 0, label: "Cambio de contraseña pendiente" },
  ]);

  renderMetricsTable(
    "metricsAuthVerificationCross",
    [
      { label: "Método", render: (r) => (r.auth_method === "google" ? "Google" : "Local") },
      { label: "Verificados", key: "verified" },
      { label: "Sin verificar", key: "unverified" },
    ],
    data.auth_method_email_verification
  );
}

// --- Bloque "Pedidos" (apps.orders, orders.view_all) -----------------------

const REPORTS_ORDERS_IDS = [
  "metricsOrdersByMonth", "metricsOrdersByStatus", "metricsCancellationRate",
  "metricsAvgTimeBetweenStatuses", "metricsTopUsersByOrders", "metricsUsersWithOrders",
  "metricsOrdersByCity", "metricsOrdersByState",
];

function renderOrdersMetrics(data) {
  renderMetricsBars("metricsOrdersByMonth", data.orders_by_month, {
    labelKey: "month", countKey: "count", labelFormatter: formatMonthLabel,
  });

  const statusData = data.orders_by_status || {};
  renderMetricsBars("metricsOrdersByStatus", statusData.funnel, { labelKey: "label", countKey: "count" });

  const cancellation = data.cancellation_rate || {};
  const cancelRows = [
    { label: "Total de pedidos", value: cancellation.total_orders ?? 0 },
    { label: "Cancelados", value: cancellation.cancelled_count ?? 0 },
    { label: "Tasa de cancelación", value: formatPercent(cancellation.rate) },
    ...(cancellation.prior_status_breakdown || []).map((row) => ({
      label: `Cancelado desde "${row.label}"`,
      value: row.count,
    })),
  ];
  renderMetricsTable(
    "metricsCancellationRate",
    [{ label: "Métrica", key: "label" }, { label: "Valor", key: "value" }],
    cancelRows
  );

  renderMetricsTable(
    "metricsAvgTimeBetweenStatuses",
    [
      { label: "Transición", key: "transition" },
      { label: "Horas promedio", render: (r) => formatHours(r.average_hours) },
      { label: "Muestras", key: "sample_size" },
    ],
    data.avg_time_between_statuses
  );

  renderMetricsTable(
    "metricsTopUsersByOrders",
    [
      { label: "Email", key: "email" },
      { label: "Pedidos", key: "count" },
      { label: "Último pedido", render: (r) => formatDateTime(r.last_order_at) },
    ],
    data.top_users_by_orders
  );

  const usersWithOrders = data.users_with_orders || {};
  renderStatRow("metricsUsersWithOrders", [
    { value: usersWithOrders.count ?? 0, label: "Usuarios con pedidos" },
    {
      value: formatPercent(usersWithOrders.percentage),
      label: `del total (${usersWithOrders.total_users ?? 0} usuarios)`,
    },
  ]);

  const location = data.orders_by_location || {};
  renderMetricsBars("metricsOrdersByCity", location.by_city, { labelKey: "city", countKey: "count" });
  renderMetricsBars("metricsOrdersByState", location.by_state, { labelKey: "state", countKey: "count" });
}

// --- Bloque "Soporte y actividad" (apps.accounts soporte + apps.audit) ----

const REPORTS_SUPPORT_ACTIVITY_IDS = [
  "metricsSupportByMonth", "metricsSupportByStatus", "metricsAvgResponseTime",
  "metricsAdminActivityByActor", "metricsAdminActivityByAction", "metricsLoginEvents",
];

function renderSupportActivityMetrics(supportData, auditData) {
  renderMetricsBars("metricsSupportByMonth", supportData.support_by_month, {
    labelKey: "month", countKey: "count", labelFormatter: formatMonthLabel,
  });
  renderMetricsBars("metricsSupportByStatus", supportData.support_by_status, {
    labelKey: "label", countKey: "count",
  });

  const avgResponse = supportData.avg_response_time || {};
  renderStatRow("metricsAvgResponseTime", [
    {
      value: formatHours(avgResponse.average_hours),
      label: `Tiempo promedio de respuesta (${avgResponse.sample_size ?? 0} muestras)`,
    },
  ]);

  const adminActivity = auditData.admin_activity || {};
  renderMetricsTable(
    "metricsAdminActivityByActor",
    [{ label: "Administrador", key: "actor_email" }, { label: "Acciones", key: "count" }],
    adminActivity.by_actor
  );
  renderMetricsTable(
    "metricsAdminActivityByAction",
    [{ label: "Acción", render: (r) => actionLabel(r.action) }, { label: "Cantidad", key: "count" }],
    adminActivity.by_action
  );

  renderMetricsTable(
    "metricsLoginEvents",
    [
      { label: "Mes", render: (r) => formatMonthLabel(r.month) },
      { label: "Exitosos", key: "success" },
      { label: "Fallidos", key: "failed" },
    ],
    auditData.login_events
  );
}

function getReportsMonths() {
  return document.getElementById("reportsMonthsSelect")?.value || "6";
}

async function fetchMetricsJsonOrEmpty(url) {
  const response = await apiFetch(url);
  if (!response.ok) return {};
  return response.json().catch(() => ({}));
}

async function loadOrdersMetrics(months) {
  const grid = document.getElementById("ordersMetricsGrid");
  const empty = document.getElementById("ordersMetricsEmpty");
  if (!canUseUserPermission("orders.view_all")) {
    if (grid) grid.style.display = "none";
    if (empty) {
      empty.style.display = "";
      empty.textContent = "No tenés permisos para ver las métricas de pedidos.";
    }
    return;
  }
  setMetricsLoading(REPORTS_ORDERS_IDS);
  try {
    const response = await apiFetch(`${ORDERS_METRICS_URL}?months=${months}`);
    const data = await response.json().catch(() => ({}));
    if (response.status === 403) throw new Error("No tenés permisos para ver las métricas de pedidos.");
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar las métricas de pedidos."));
    if (grid) grid.style.display = "";
    if (empty) empty.style.display = "none";
    renderOrdersMetrics(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar métricas de pedidos:", err);
    if (grid) grid.style.display = "none";
    if (empty) {
      empty.style.display = "";
      empty.textContent = err.message || "No se pudieron cargar las métricas de pedidos.";
    }
  }
}

async function loadSupportActivityMetrics(months) {
  const title = document.getElementById("supportActivityTitle");
  const grid = document.getElementById("supportActivityGrid");
  const canSupport = canUseUserPermission("support.view_all");
  const canAudit = canUseUserPermission("audit.view");

  // Bloque entero opcional: si ninguna de las dos partes está disponible
  // (permiso o backend no implementado), ni se muestra el subtítulo.
  if (!canSupport && !canAudit) {
    if (title) title.style.display = "none";
    if (grid) grid.style.display = "none";
    return;
  }
  if (title) title.style.display = "";
  if (grid) grid.style.display = "";
  setMetricsLoading(REPORTS_SUPPORT_ACTIVITY_IDS);

  await loadAuditActionsCatalog();

  try {
    const [supportData, auditData] = await Promise.all([
      canSupport ? fetchMetricsJsonOrEmpty(`${SUPPORT_METRICS_URL}?months=${months}`) : Promise.resolve({}),
      canAudit ? fetchMetricsJsonOrEmpty(`${AUDIT_METRICS_URL}?months=${months}`) : Promise.resolve({}),
    ]);
    renderSupportActivityMetrics(supportData, auditData);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar métricas de soporte/actividad:", err);
    REPORTS_SUPPORT_ACTIVITY_IDS.forEach((id) =>
      renderMetricsEmpty(id, "No se pudieron cargar las métricas.")
    );
  }
}

async function loadMetrics() {
  const months = getReportsMonths();
  const titleEl = document.getElementById("metricsSignupsByMonthTitle");
  if (titleEl) titleEl.textContent = `Altas por mes (últimos ${months} meses)`;

  setMetricsLoading(REPORTS_USER_IDS);
  try {
    const response = await apiFetch(`${METRICS_URL}?months=${months}`);
    const data = await response.json().catch(() => ({}));
    if (response.status === 403) throw new Error("No tenés permisos para ver las métricas.");
    if (!response.ok) throw new Error(data.detail || "No se pudieron cargar las métricas.");
    renderUsersMetrics(data);
    renderSecurityMetrics(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar métricas:", err);
    REPORTS_USER_IDS.forEach((id) =>
      renderMetricsEmpty(id, err.message || "No se pudieron cargar las métricas.")
    );
  }

  await loadOrdersMetrics(months);
  await loadSupportActivityMetrics(months);
}

document.getElementById("reportsRefreshBtn")?.addEventListener("click", () => loadMetrics());
document.getElementById("reportsMonthsSelect")?.addEventListener("change", () => loadMetrics());

// ---------------------------------------------------------------------------
// Esta pantalla requiere el mismo permiso que su bloque principal (Usuarios):
// users.view. Igual que antes -- Reportes era accesible desde gestionuser.html,
// que ya exigía ese permiso para todo el mundo.
// ---------------------------------------------------------------------------
function bootstrap() {
  if (!canViewUsers()) {
    window.location.replace("dashboard.html");
    return;
  }
  loadMetrics();
}

bootstrap();
