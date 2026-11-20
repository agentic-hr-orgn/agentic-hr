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

const PKT_TIME_SECONDS_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Karachi",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
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

/**
 * Formats a Date or ISO timestamp as HH:MM:SS PKT in Asia/Karachi timezone.
 */
export function formatPKTTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return `${PKT_TIME_SECONDS_FORMATTER.format(date)} PKT`;
}

const PKT_ISO_DATE_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Karachi",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/**
 * Returns the calendar date string (`YYYY-MM-DD`) in `Asia/Karachi` (PKT, UTC+05:00).
 */
export function getTodayPKTDateString(now: Date = new Date()): string {
  return PKT_ISO_DATE_FORMATTER.format(now);
}

/**
 * Computes whole calendar days between `referenceDatePkt` (defaults to today in Asia/Karachi)
 * and `expiryDateIso` (`YYYY-MM-DD`). Negative values mean already expired.
 */
export function getPKTDaysUntil(
  expiryDateIso: string | null | undefined,
  referenceDatePkt?: string
): number | null {
  if (!expiryDateIso) return null;
  const datePart = expiryDateIso.slice(0, 10);
  const expMatch = datePart.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!expMatch) return null;

  const refStr = referenceDatePkt ?? getTodayPKTDateString();
  const refMatch = refStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!refMatch) return null;

  const expUtc = Date.UTC(Number(expMatch[1]), Number(expMatch[2]) - 1, Number(expMatch[3]));
  const refUtc = Date.UTC(Number(refMatch[1]), Number(refMatch[2]) - 1, Number(refMatch[3]));
  return Math.round((expUtc - refUtc) / 86_400_000);
}
