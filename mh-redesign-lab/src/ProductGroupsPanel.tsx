import { useId, useState } from "react";
import { archiveProductGroup, createProductGroup, updateProductGroup, type ProductEconomicsRow, type ProductGroup } from "./data-source";
import { ALL_PRODUCTS } from "./useProductGroups";
import "./ProductGroups.css";

/** "Все товары / <группы>" select above the products table. */
export function ProductGroupFilter({ groups, value, onChange }: { groups: ProductGroup[]; value: string; onChange: (id: string) => void }) {
  const id = useId();
  if (!groups.length) return null;
  return <span className="pg-filter"><label htmlFor={id}>
Группа</label>
    <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value={ALL_PRODUCTS}>Все товары</option>
      {groups.map((group) => <option key={group.id} value={String(group.id)}>{group.name} ({group.count})</option>)}
    </select>
    {value !== ALL_PRODUCTS && <small>плитки выше — по всему магазину</small>}
  </span>;
}

function GroupEditor({ products, group, onSave, onCancel }: {
  products: ProductEconomicsRow[]; group?: ProductGroup;
  onSave: (name: string, nmIds: number[]) => Promise<void>; onCancel: () => void;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [picked, setPicked] = useState<Set<number>>(() => new Set(group?.nmIds ?? []));
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const q = search.trim().toLocaleLowerCase("ru-RU");
  const shown = products.filter((p) => !q || `${p.article} ${p.product} ${p.nmId}`.toLocaleLowerCase("ru-RU").includes(q));
  const toggle = (nm: number) => setPicked((prev) => { const next = new Set(prev); if (!next.delete(nm)) next.add(nm); return next; });
  const submit = async () => {
    setBusy(true); setError("");
    try { await onSave(name.trim(), [...picked]); } catch (e) { setError(e instanceof Error ? e.message : "Не удалось сохранить"); setBusy(false); }
  };
  return <form className="pg-editor" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <label className="pg-field"><span>Название группы</span><input value={name} maxLength={60} onChange={(event) => setName(event.target.value)} required /></label>
    <label className="pg-field"><span>Найти товар</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Артикул или товар" /></label>
    <ul className="pg-picker" aria-label="Товары для группы">
      {shown.map((p) => {
        const nm = Number(p.nmId);
        return <li key={p.nmId}><label><input type="checkbox" checked={picked.has(nm)} onChange={() => toggle(nm)} /><span className="pg-article">{p.article}</span><span className="pg-product">{p.product}</span></label></li>;
      })}
      {!shown.length && <li className="pg-empty">Ничего не найдено</li>}
    </ul>
    <p className="pg-count">Выбрано: {picked.size}</p>
    {error && <p className="pg-error" role="alert">{error}</p>}
    <div className="pg-actions">
      <button className="primary-button" type="submit" disabled={busy || !name.trim()}>{group ? "Сохранить" : "Создать группу"}</button>
      <button className="ghost-button" type="button" onClick={onCancel} disabled={busy}>Отмена</button>
    </div>
  </form>;
}

/** Collapsed manager for product groups: create from a SKU picker, edit, archive. */
export function ProductGroupsPanel({ products, groups, error, onChanged }: { products: ProductEconomicsRow[]; groups: ProductGroup[]; error: string | null; onChanged: () => Promise<void> }) {
  const [editing, setEditing] = useState<ProductGroup | "new" | null>(null);
  const [message, setMessage] = useState("");
  const done = async () => { setEditing(null); await onChanged(); };
  const archive = async (group: ProductGroup) => {
    setMessage("");
    try { await archiveProductGroup(group.id); await onChanged(); } catch (e) { setMessage(e instanceof Error ? e.message : "Не удалось архивировать"); }
  };
  return <details className="pg-panel">
    <summary>Группы товаров{groups.length ? ` · ${groups.length}` : ""}</summary>
    <div className="pg-body">
      {error && <p className="pg-error" role="alert">{error}</p>}
      {message && <p className="pg-error" role="alert">{message}</p>}
      {!groups.length && editing !== "new" && <p className="pg-empty">Групп пока нет. Соберите товары в группу и фильтруйте по ней таблицу.</p>}
      <ul className="pg-list">
        {groups.map((group) => <li key={group.id}>
          {editing && editing !== "new" && editing.id === group.id
            ? <GroupEditor products={products} group={group} onCancel={() => setEditing(null)} onSave={async (name, ids) => { await updateProductGroup(group.id, name, ids); await done(); }} />
            : <div className="pg-row"><strong>{group.name}</strong><span className="pg-count">{group.count} шт.</span>
              <button className="ghost-button" type="button" onClick={() => setEditing(group)} aria-label={`Изменить группу ${group.name}`}>Изменить</button>
              <button className="ghost-button" type="button" onClick={() => void archive(group)} aria-label={`Архивировать группу ${group.name}`}>В архив</button></div>}
        </li>)}
      </ul>
      {editing === "new"
        ? <GroupEditor products={products} onCancel={() => setEditing(null)} onSave={async (name, ids) => { await createProductGroup(name, ids); await done(); }} />
        : <button className="ghost-button" type="button" onClick={() => setEditing("new")}>Новая группа</button>}
    </div>
  </details>;
}
