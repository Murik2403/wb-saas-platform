import { useState } from "react";
import { getPriceUploadDetail, type PriceUploadDetail, type PriceUploadHistoryItem } from "./data-source";
import "./PriceUploadDetails.css";

const rub = (value: number) => `${Math.round(value).toLocaleString("ru-RU")} ₽`;
const final = (price: number, discount: number) => Math.round(price * (1 - discount / 100));
const pair = (price: number | null, discount: number | null) =>
  price === null || discount === null ? "—" : `${rub(price)} / ${discount}% / ${rub(final(price, discount))}`;

function result(error: string | null) {
  if (error === null) return <span className="wbx-muted">ожидает</span>;
  return error ? <span className="status-pill danger">{error}</span> : <span className="status-pill good">принято</span>;
}

export function PriceUploadDetails({ item }: { item: PriceUploadHistoryItem }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<"idle" | "loading" | "error" | "ready">("idle");
  const [detail, setDetail] = useState<PriceUploadDetail | null>(null);
  const load = () => {
    setState("loading");
    getPriceUploadDetail(item.wb_upload_id).then((data) => { setDetail(data); setState("ready"); }, () => setState("error"));
  };
  const toggle = () => {
    if (!open && (state === "idle" || state === "error")) load();
    setOpen(!open);
  };
  const panelId = `price-upload-${item.wb_upload_id}`;
  const errors = detail?.error_count ?? 0;

  return (
    <div className="pud">
      <button type="button" className="secondary-button pud-toggle" aria-expanded={open} aria-controls={panelId} onClick={toggle}>
        {open ? "Скрыть детали" : "Детали"}
      </button>
      {open ? (
        <div id={panelId} className="pud-panel">
          {state === "loading" ? <p className="form-hint" role="status">Загружаем детали…</p> : null}
          {state === "error" ? <p className="operation-error" role="alert">Не удалось загрузить детали. <button type="button" className="pud-retry" onClick={load}>Повторить</button></p> : null}
          {state === "ready" && detail && !detail.items.length ? <p className="plan-empty">Детали этой загрузки не сохранились</p> : null}
          {state === "ready" && detail?.items.length ? (
            <>
              <p className="form-hint">{detail.item_count} SKU · {errors} ошибок{detail.actor ? ` · ${detail.actor}` : ""}</p>
              <div className="wbx-table-scroll">
                <table className="wbx-price-table pud-table">
                  <thead><tr><th>Артикул</th><th>Было: цена / скидка / итог</th><th>Стало: цена / скидка / итог</th><th>Результат</th></tr></thead>
                  <tbody>
                    {detail.items.map((row) => (
                      <tr key={row.nm_id}>
                        <td><strong>{row.vendor_code || row.nm_id}</strong>{row.vendor_code ? <small> {row.nm_id}</small> : null}</td>
                        <td>{pair(row.old_price, row.old_discount)}</td>
                        <td>{pair(row.new_price, row.new_discount)}</td>
                        <td>{result(row.result_error)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
