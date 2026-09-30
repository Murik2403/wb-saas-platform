import { useState, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import type { CustomerQualityData } from "./data-source";
import { cabinetDate, cabinetText, plural } from "./cabinet-copy";
import "./ProductWbSections.css";

type Item = Record<string, unknown>;

export type ShopQualityActions = {
  onRefresh: () => void;
  onAnswerFeedback: (id: string) => void;
  onAnswerQuestion: (id: string) => void;
  onReplyChat: (replySign: string) => void;
  onReturn: (id: string, decision: "approve" | "reject") => void;
};

const SOURCE_RE = /^(rating|unread|feedbacks|questions|quarantine|returns|return_history|chats):/;
const INITIAL = 5;

const text = (value: unknown) => (typeof value === "string" ? value.trim() : "");
// WB nests the product under productDetails; older/other payloads keep it on the item itself.
function productOf(item: Item): string {
  const details = (item.productDetails ?? {}) as Item;
  return text(details.productName) || text(item.productName) || text(details.supplierArticle) || text(item.supplierArticle) || "Товар WB";
}
function bodyOf(item: Item): string {
  const main = text(item.text);
  if (main) return main;
  const parts = [text(item.pros) && `Плюсы: ${text(item.pros)}`, text(item.cons) && `Минусы: ${text(item.cons)}`].filter(Boolean);
  return parts.join(" · ") || "Без текста";
}
const stars = (value: unknown) => {
  const n = Math.round(Number(value));
  return n >= 1 && n <= 5 ? "★".repeat(n) + "☆".repeat(5 - n) : "";
};

function Group({ title, total, children }: { title: string; total: number; children: (limit: number) => ReactNode }) {
  const [all, setAll] = useState(false);
  if (!total) return null;
  return (
    <div className="wbx-group">
      <h3>{title} <span>{total}</span></h3>
      <div className="wbx-items">{children(all ? total : INITIAL)}</div>
      {total > INITIAL ? <button className="ghost-button wbx-more" type="button" onClick={() => setAll((v) => !v)}>{all ? "Свернуть" : `Показать все ${total}`}</button> : null}
    </div>
  );
}

function Row({ product, meta, body, action }: { product: string; meta?: string; body: string; action: ReactNode }) {
  return (
    <article className="wbx-item">
      <div>
        <strong>{product}</strong>
        {meta ? <small>{meta}</small> : null}
        <p>{body}</p>
      </div>
      {action}
    </article>
  );
}

export function ShopQualityPanel({ data, actions }: { data: CustomerQualityData | null; actions: ShopQualityActions }) {
  // An unlabelled transport failure applies to the whole response, never to a genuine zero.
  const unavailable = (source: string) => !data || data.errors.some((error) => {
    const failed = SOURCE_RE.exec(error.trim())?.[1];
    return !failed || failed === source;
  });
  const rating = Number(data?.rating?.valuation ?? 0);
  const ratingOff = unavailable("rating");
  const feedbackCount = data?.rating?.feedbackCount;
  const feedbackKnown = !ratingOff && feedbackCount !== undefined && feedbackCount !== null && String(feedbackCount).trim() !== "" && Number.isFinite(Number(feedbackCount));
  const quarantine = data?.quarantine?.length ?? 0;
  const claims = data?.returns?.claims;
  const returnList = (Array.isArray(claims) ? claims : []) as Item[];
  const returnCount = Number(data?.returns?.total ?? returnList.length);
  const feedbacks = (data?.feedbacks ?? []) as Item[];
  const questions = (data?.questions ?? []) as Item[];
  const chats = (data?.chats ?? []) as Item[];
  // Source errors are already Russian sentences ("rating: у токена нет доступа …"): show them minus the technical prefix.
  const notes = (data?.errors ?? []).map((error) => cabinetText(error.replace(SOURCE_RE, "").trim())).filter(Boolean);
  const empty = !feedbacks.length && !questions.length && !chats.length && !returnList.length;

  return (
    <section className="work-section wbx-panel" aria-label="Качество магазина">
      <div className="section-header">
        <div><span>WB API</span><h2>Качество магазина</h2></div>
        <button className="icon-button" type="button" aria-label="Обновить данные качества магазина" title="Обновить данные качества магазина" onClick={actions.onRefresh}><RefreshCw size={17} /></button>
      </div>

      <div className="wbx-stats">
        <div className="stat"><span>Рейтинг продавца</span><strong>{!ratingOff && rating ? rating.toFixed(2) : "—"}</strong><small>{feedbackKnown ? `${String(feedbackCount)} ${plural(Number(feedbackCount), ["отзыв", "отзыва", "отзывов"])}` : "нет данных об отзывах"}</small></div>
        <div className="stat"><span>Карантин цен</span><strong>{unavailable("quarantine") ? "—" : quarantine}</strong><small>{unavailable("quarantine") ? "нет данных" : "товаров требуют проверки"}</small></div>
        <div className="stat"><span>Возвраты</span><strong>{unavailable("returns") ? "—" : returnCount}</strong><small>{unavailable("returns") ? "нет данных" : "активных обращений WB"}</small></div>
        <div className="stat"><span>Чаты</span><strong>{unavailable("chats") ? "—" : chats.length}</strong><small>{unavailable("chats") ? "нет данных" : "диалогов с покупателями"}</small></div>
      </div>

      {notes.length ? <ul className="wbx-notes">{notes.map((note) => <li key={note}>{note}</li>)}</ul> : null}

      <Group title="Отзывы без ответа" total={feedbacks.length}>
        {(limit) => feedbacks.slice(0, limit).map((item, index) => {
          const rate = stars(item.productValuation);
          return <Row key={String(item.id ?? index)} product={productOf(item)}
            meta={[rate, cabinetDate(text(item.createdDate) || null)].filter(Boolean).join(" · ")}
            body={bodyOf(item)}
            action={<button className="ghost-button" type="button" onClick={() => actions.onAnswerFeedback(String(item.id ?? ""))}>Ответить</button>} />;
        })}
      </Group>

      <Group title="Вопросы покупателей" total={questions.length}>
        {(limit) => questions.slice(0, limit).map((item, index) => (
          <Row key={`q-${String(item.id ?? index)}`} product={productOf(item)}
            meta={cabinetDate(text(item.createdDate) || null)} body={bodyOf(item)}
            action={<button className="ghost-button" type="button" onClick={() => actions.onAnswerQuestion(String(item.id ?? ""))}>Ответить</button>} />
        ))}
      </Group>

      <Group title="Чаты" total={chats.length}>
        {(limit) => chats.slice(0, limit).map((item, index) => {
          const replySign = String(item.replySign ?? item.reply_sign ?? "");
          const last = item.lastMessage as Item | string | undefined;
          return <Row key={`c-${String(item.chatId ?? item.id ?? index)}`} product={text(item.clientName) || productOf(item)}
            body={text(typeof last === "object" ? last?.text : last) || text(item.message) || text(item.title) || "Диалог с покупателем"}
            action={<button className="ghost-button" type="button" disabled={!replySign} title={replySign ? "Ответить покупателю" : "WB не передал подпись чата"} onClick={() => actions.onReplyChat(replySign)}>Ответить</button>} />;
        })}
      </Group>

      <Group title="Возвраты" total={returnList.length}>
        {(limit) => returnList.slice(0, limit).map((item, index) => (
          <Row key={`r-${String(item.id ?? index)}`} product={`WB ${String(item.nm_id ?? item.nmId ?? "")}`.trim()}
            body={text(item.user_comment) || text(item.comment) || "Заявка покупателя"}
            action={<div className="procurement-order-actions">
              <button className="ghost-button" type="button" onClick={() => actions.onReturn(String(item.id ?? ""), "approve")}>Одобрить</button>
              <button className="secondary-button" type="button" onClick={() => actions.onReturn(String(item.id ?? ""), "reject")}>Отклонить</button>
            </div>} />
        ))}
      </Group>

      {empty && !notes.length ? <p className="plan-empty">Новых отзывов, вопросов, чатов и обращений нет.</p> : null}
    </section>
  );
}
