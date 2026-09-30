import { useState } from "react";
import { PackageCheck, Search } from "lucide-react";
import type { ProductCard } from "./data-source";
import { plural } from "./cabinet-copy";
import "./ProductWbSections.css";

const STEP = 12;

export function ProductCardsPanel({ cards, errors, onEdit }: { cards: ProductCard[]; errors: string[]; onEdit: (card: ProductCard) => void }) {
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(STEP);
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const found = cards.filter((card) => !needle || `${card.title} ${card.vendorCode} ${card.nmId} ${card.brand}`.toLocaleLowerCase("ru-RU").includes(needle));
  const shown = found.slice(0, limit);

  return (
    <section className="work-section wbx-panel" aria-label="Карточки и медиа">
      <div className="section-header">
        <div><span>Контент WB</span><h2>Карточки и медиа</h2></div>
        <PackageCheck size={18} aria-hidden="true" />
      </div>

      {cards.length ? (
        <>
          <div className="wbx-toolbar">
            <label className="search-box wbx-search">
              <Search size={16} aria-hidden="true" />
              <input type="search" value={query} placeholder="Название, артикул или бренд"
                onChange={(event) => { setQuery(event.target.value); setLimit(STEP); }} />
            </label>
            <span className="wbx-count">Показано {shown.length} из {found.length}{found.length !== cards.length ? ` (всего ${cards.length})` : ""}</span>
          </div>
          <p className="form-hint">Нажмите на карточку, чтобы изменить название, описание и медиа. Изменения отправляются в WB только после подтверждения.</p>
          {shown.length ? (
            <div className="product-card-grid">
              {shown.map((card) => (
                <button className="product-card-preview detail-trigger" type="button" key={card.nmId} title={card.title} onClick={() => onEdit(card)}>
                  <div className="product-card-image">{card.photos[0] ? <img src={card.photos[0]} alt="" loading="lazy" /> : <PackageCheck size={24} />}</div>
                  <div>
                    <strong>{card.title}</strong>
                    <span>{card.vendorCode || `WB ${card.nmId}`}</span>
                    <small>{[card.brand, card.subject].filter(Boolean).join(" · ") || "Карточка WB"} · {card.sizes} {plural(card.sizes, ["размер", "размера", "размеров"])}</small>
                  </div>
                </button>
              ))}
            </div>
          ) : <p className="plan-empty">По этому запросу карточки не найдены</p>}
          {found.length > shown.length ? (
            <button className="secondary-button wbx-more" type="button" onClick={() => setLimit((value) => value + STEP)}>
              Показать ещё {Math.min(STEP, found.length - shown.length)}
            </button>
          ) : null}
        </>
      ) : <p className="plan-empty">{errors.length ? "После исправления доступа обновите раздел." : "Карточек WB пока нет."}</p>}
    </section>
  );
}
