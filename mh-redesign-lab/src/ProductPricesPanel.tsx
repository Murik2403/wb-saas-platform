import { useState } from "react";
import type { PriceUploadHistoryItem, ProductPriceRow } from "./data-source";
import { PriceUploadDetails } from "./PriceUploadDetails";
import "./ProductWbSections.css";

export type PriceDraft = { price: number; discount: number };

const STEP = 50;
const rub = (value: number) => `${Math.round(value).toLocaleString("ru-RU")} ₽`;
const afterDiscount = (draft: PriceDraft) => Math.round(draft.price * (1 - draft.discount / 100));
const historyTone = (status: string) => (status.toLowerCase().includes("ошиб") ? "danger" : status.toLowerCase().includes("заверш") ? "good" : "info");

export function ProductPricesPanel({
  prices, query, drafts, onDraft, onSubmit, busy, message, uploadStatus, results, history, hasErrors,
}: {
  prices: ProductPriceRow[];
  query: string;
  drafts: Record<number, PriceDraft>;
  onDraft: (nmId: number, draft: PriceDraft) => void;
  onSubmit: () => void;
  busy: boolean;
  message: string;
  uploadStatus: string;
  results: Array<Record<string, unknown>>;
  history: PriceUploadHistoryItem[];
  hasErrors: boolean;
}) {
  const [limit, setLimit] = useState(STEP);
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const found = prices.filter((item) => !needle || `${item.vendorCode} ${item.nmId}`.toLocaleLowerCase("ru-RU").includes(needle));
  const shown = found.slice(0, limit);
  const changed = Object.keys(drafts).length;

  return (
    <section className="work-section wbx-panel" aria-label="Цены и скидки">
      <div className="section-header">
        <div><span>WB API</span><h2>Цены и скидки</h2></div>
      </div>

      {message ? <p className="operation-error" role="status">{message}</p> : null}
      {uploadStatus ? <p className="form-hint" role="status">Статус загрузки WB: {uploadStatus}</p> : null}
      {results.filter((row) => row.errorText).map((row, index) => <p className="operation-error" key={index}>WB {String(row.nmID)}: {String(row.errorText)}</p>)}

      {prices.length ? (
        <>
          <div className="wbx-toolbar">
            <span className="wbx-count">Показано {shown.length} из {found.length}{found.length !== prices.length ? ` (всего ${prices.length})` : ""}</span>
            <span className="form-hint">Правьте цену и скидку прямо в таблице, затем отправьте изменения одной кнопкой.</span>
          </div>
          <div className="wbx-table-scroll">
            <table className="wbx-price-table">
              <thead><tr><th>Артикул</th><th>WB ID</th><th>Статус WB</th><th>Цена, ₽</th><th>Скидка, %</th><th>После скидки</th></tr></thead>
              <tbody>
                {shown.map((item) => {
                  const draft = drafts[item.nmId] ?? { price: item.price, discount: item.discount };
                  const edited = item.nmId in drafts;
                  return (
                    <tr key={item.nmId} className={edited ? "edited" : ""}>
                      <td><strong>{item.vendorCode}</strong></td>
                      <td>{item.nmId}</td>
                      <td>
                        {item.errorText ? <span className="status-pill danger">{item.errorText}</span>
                          : item.status !== undefined ? <span className={`status-pill ${item.status === 0 ? "good" : "info"}`}>{item.status === 0 ? "принято WB" : `статус WB ${item.status}`}</span>
                          : <span className="wbx-muted">—</span>}
                      </td>
                      <td><input type="number" min="0" aria-label={`Цена ${item.vendorCode}`} value={draft.price}
                        onChange={(event) => onDraft(item.nmId, { ...draft, price: Number(event.target.value) })} /></td>
                      <td><input type="number" min="0" max="99" aria-label={`Скидка ${item.vendorCode}`} value={draft.discount}
                        onChange={(event) => onDraft(item.nmId, { ...draft, discount: Number(event.target.value) })} /></td>
                      <td className="wbx-final">{rub(afterDiscount(draft))}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {found.length > shown.length ? (
            <button className="secondary-button wbx-more" type="button" onClick={() => setLimit((value) => value + STEP)}>
              Показать ещё {Math.min(STEP, found.length - shown.length)}
            </button>
          ) : null}
          {!found.length ? <p className="plan-empty">По этому запросу цены не найдены</p> : null}

          <div className="wbx-submit-bar">
            <span>{changed ? `Изменено: ${changed} SKU. В WB уйдёт только изменённое.` : "Изменений нет"}</span>
            <button className="primary-button" type="button" disabled={busy || !changed} onClick={onSubmit}>Отправить изменения в WB</button>
          </div>
        </>
      ) : <p className="plan-empty">{hasErrors ? "После исправления доступа обновите раздел." : "Цены WB пока не загружены или токен не имеет категории «Цены и скидки»."}</p>}

      {history.length ? (
        <div className="wbx-group">
          <h3>История загрузок <span>{history.length}</span></h3>
          <div className="wbx-items">
            {history.slice(0, 10).map((item) => (
              <article className="wbx-item" key={`${item.wb_upload_id}-${item.created_at}`}>
                <div><strong>WB загрузка №{item.wb_upload_id}</strong><small>{item.goods_count} SKU · {item.created_at}</small></div>
                <span className={`status-pill ${historyTone(item.status)}`}>{item.status}</span>
                <PriceUploadDetails item={item} />
              </article>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
