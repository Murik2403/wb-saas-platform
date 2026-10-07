export type CsvCell = string | number | null | undefined;

// Excel/LibreOffice evaluate cells that start with these as formulas; the apostrophe keeps them text.
const FORMULA_START = /^[=+\-@\t\r\n]|^\s+[=+\-@]/;

export function safeCsvText(text: string): string {
  return FORMULA_START.test(text) ? `'${text}` : text;
}

function cell(value: CsvCell): string {
  let text = value === null || value === undefined ? "" : typeof value === "number" ? (Number.isFinite(value) ? String(Math.round(value * 100) / 100).replace(".", ",") : "") : value;
  if (typeof value === "string") text = safeCsvText(text);
  return /[";\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Russian-Excel friendly CSV: UTF-8 BOM, ';' separator, decimal comma, CRLF. */
export function buildCsv(headers: string[], rows: CsvCell[][]): string {
  return `﻿${[headers, ...rows].map((line) => line.map(cell).join(";")).join("\r\n")}\r\n`;
}
