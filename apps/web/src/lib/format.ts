const PKT_DATE_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Karachi",
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const PKT_DATETIME_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Karachi",
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/**
 * Formats an ISO timestamp deterministically in Asia/Karachi (PKT, UTC+05:00)
 * so SSR and client hydration always produce identical output.
 */
export function formatPKT(value: string | Date | null | undefined, includeTime = true): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return includeTime
    ? `${PKT_DATETIME_FORMATTER.format(date)} PKT`
    : PKT_DATE_FORMATTER.format(date);
}
