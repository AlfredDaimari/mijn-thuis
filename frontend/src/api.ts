export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};
export type Row = {
  id: number;
  provider_listing_id?: number;
  subject?: string;
  sender?: string;
  accessed_at: string;
  provider_url?: string;
  provider_title?: string;
  location?: string;
  monthly_rent_cents?: number;
  room_count?: number;
  area_m2?: number;
  status: string;
  processing_error?: string;
  processed_at?: string;
  attempt_count: number;
  evidence_count?: number;
  link_count?: number;
  link_errors?: number;
};
export type SourceLink = {
  id: number;
  title?: string;
  stekkies_url: string;
  provider_url?: string;
  status: string;
  processing_error?: string;
};
export type Screenshot = {
  screenshot_id: number;
  stage: string;
  nginx_path: string;
  captured_at: string;
  attempt_count: number;
};
export type Failure = {
  source_id: number;
  source_url?: string;
  label?: string;
  processed_at: string;
  error_class: string;
  processing_error: string;
  attempt_count: number;
};
export type FailureSummary = {
  summary: {
    by_class: { source_table: string; error_class: string; count: number }[];
    by_url: {
      source_table: string;
      source_url: string;
      count: number;
      by_class: { error_class: string; count: number }[];
    }[];
  };
};
export type Stats = {
  sources: Record<string, Record<string, number>>;
  failures: Record<string, number>;
};

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json", ...options?.headers },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`Server returned an invalid response (${response.status})`);
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  return payload as T;
}

export const label = (text: string) => text.replaceAll("_", " ");
export const dateTime = (value?: string) =>
  value
    ? new Date(
        value.includes("T") ? value : value.replace(" ", "T") + "Z",
      ).toLocaleString()
    : "—";
export const rent = (cents?: number) =>
  cents
    ? new Intl.NumberFormat("nl-NL", {
        style: "currency",
        currency: "EUR",
      }).format(cents / 100)
    : "—";
