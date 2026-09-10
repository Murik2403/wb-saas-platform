export type MetricOption = { code: string; label: string };
export type ScheduleTypeOption = { code: "daily" | "weekly" | "monthly"; label: string };

export type ReportSchedule = {
  id: number;
  name: string;
  metrics: string[];
  metric_names: string;
  schedule_type: "daily" | "weekly" | "monthly";
  schedule_label: string;
  email_enabled: boolean;
  telegram_enabled: boolean;
  delivery_note: string;
  last_run_at: string | null;
  last_run_label: string;
};

export type ReportRun = {
  id: number;
  definition_id: number;
  report_name: string;
  started_at: string;
  started_label: string;
  file_name: string;
};

export type ReportsResponse = {
  copy: {
    title: string;
    subtitle: string;
    telegram_header: string;
    telegram_hint: string;
    telegram_linked: string;
    telegram_caption: string;
    schedules_hint: string;
    schedules_empty_title: string;
    schedules_empty_note: string;
    files_header: string;
    files_hint: string;
    files_empty_title: string;
    files_empty_note: string;
    email_unavailable: string;
    telegram_unavailable: string;
  };
  telegram_configured: boolean;
  telegram_linked: boolean;
  email_configured: boolean;
  metric_options: MetricOption[];
  schedule_type_options: ScheduleTypeOption[];
  weekdays: string[];
  schedules: ReportSchedule[];
  runs: ReportRun[];
};

export type NewSchedulePayload = {
  name: string;
  metrics: string[];
  schedule_type: "daily" | "weekly" | "monthly";
  schedule_time: string; // "HH:MM"
  schedule_weekday: number | null;
  schedule_day: number | null;
  email_enabled: boolean;
  telegram_enabled: boolean;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8020";

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `API ${res.status}`);
  }
  return res.json();
}

export function fetchReports(): Promise<ReportsResponse> {
  return fetch(`${API_URL}/api/reports`, { cache: "no-store" }).then(unwrap<ReportsResponse>);
}

export function createReport(payload: NewSchedulePayload): Promise<{ id: number; message: string }> {
  return fetch(`${API_URL}/api/reports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(unwrap<{ id: number; message: string }>);
}

export function deleteReport(id: number): Promise<{ message: string }> {
  return fetch(`${API_URL}/api/reports/${id}`, { method: "DELETE" }).then(unwrap<{ message: string }>);
}

export function generateReportNow(id: number): Promise<{ message: string }> {
  return fetch(`${API_URL}/api/reports/${id}/generate`, { method: "POST" }).then(unwrap<{ message: string }>);
}

export function requestTelegramLinkCode(): Promise<{ code: string; ttl_minutes: number }> {
  return fetch(`${API_URL}/api/reports/telegram/link-code`, { method: "POST" }).then(unwrap<{ code: string; ttl_minutes: number }>);
}

export function reportDownloadUrl(runId: number): string {
  return `${API_URL}/api/reports/runs/${runId}/download`;
}
