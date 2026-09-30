import {useEffect,useState} from 'react';
import {currentShop,navigateToShop} from './selected-shop';
import {getProductCostSettings,updateProductCostSettings,type ProductCostSetting} from './data-source';
import {SettingsChangeHistory} from './SettingsChangeHistory';

export function SelectedShopSettings() {
 const shop=currentShop();
 const canWrite=Boolean(shop?.can_write&&shop.allowed_actions.includes('PATCH /api/v1/settings/product-costs'));
 const [items,setItems]=useState<ProductCostSetting[]>([]),[drafts,setDrafts]=useState<Record<number,string>>({}),[reasons,setReasons]=useState<Record<number,string>>({});
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState<number|null>(null),[error,setError]=useState(''),[message,setMessage]=useState('');
 useEffect(()=>{let active=true;getProductCostSettings().then(result=>{if(active){setItems(result.items);setDrafts(Object.fromEntries(result.items.map(item=>[item.nm_id,String(item.cost_per_wb_unit)])));}}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:'Себестоимость магазина недоступна');}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[]);
 async function save(item:ProductCostSetting){
  if(!canWrite||busy!==null)return;
  const cost=Number((drafts[item.nm_id]??'').replace(',','.')),reason=(reasons[item.nm_id]??'').trim();
  if(!Number.isFinite(cost)||cost<0||!(drafts[item.nm_id]??'').trim()||!reason){setError('Укажите неотрицательную себестоимость и основание изменения');return;}
  setBusy(item.nm_id);setError('');setMessage('');
  try{await updateProductCostSettings([{nm_id:item.nm_id,supplier_article:item.supplier_article??'',cost_per_wb_unit:cost,note:item.note??''}],reason);setMessage('Себестоимость сохранена. Версия магазина обновляется.');}
  catch(caught){setError(caught instanceof Error?caught.message:'Себестоимость не сохранена. Повторите действие с теми же значениями.');}
  finally{setBusy(null);}
 }
 return <div className="workspace-view"><section className="panel" aria-label="Настройки выбранного магазина">
  <h2>Настройки · {shop?.name??'выбранный магазин'}</h2><p>Себестоимость относится только к этому WB-магазину. Настройки подключений, общий импорт и резервные копии доступны в основном кабинете.</p>
  <button type="button" className="secondary-button" onClick={()=>navigateToShop(0)}>Открыть настройки основного магазина</button>
 </section><section className="work-section" id="product-costs" aria-labelledby="selected-product-costs"><h2 id="selected-product-costs">Себестоимость товаров</h2>
  <p>Текущая оценка одной продаваемой единицы WB. Она не создаёт движения товаров и не изменяет исторические FIFO-слои. Нулевая ставка остаётся неизвестной себестоимостью.</p>
  {!canWrite&&<p>Только просмотр: изменение себестоимости недоступно для вашей роли.</p>}
  {loading&&<p role="status">Загружаем себестоимость выбранного магазина…</p>}{error&&<p role="alert" className="operation-error">{error}</p>}{message&&<p role="status">{message}</p>}
  {items.map(item=><form key={item.nm_id} className="cost-setting-row" onSubmit={event=>{event.preventDefault();void save(item);}}><div className="cost-setting-product"><strong>{item.product_name||item.supplier_article||'Товар без названия'}</strong><span>WB {item.nm_id} · {item.supplier_article}</span><strong>{Number(item.cost_per_wb_unit).toLocaleString('ru-RU')} ₽</strong></div>
   <label className="operation-field"><span>Себестоимость WB {item.nm_id}, ₽</span><input aria-label={`Себестоимость WB ${item.nm_id}, ₽`} inputMode="decimal" disabled={!canWrite||busy!==null} value={drafts[item.nm_id]??''} onChange={event=>setDrafts(value=>({...value,[item.nm_id]:event.target.value}))}/></label>
   <label className="operation-field"><span>Основание изменения WB {item.nm_id}</span><input aria-label={`Основание изменения WB ${item.nm_id}`} maxLength={500} disabled={!canWrite||busy!==null} value={reasons[item.nm_id]??''} onChange={event=>setReasons(value=>({...value,[item.nm_id]:event.target.value}))}/></label>
   <button className="secondary-button" type="submit" disabled={!canWrite||busy!==null}>{busy===item.nm_id?'Сохраняем…':'Сохранить себестоимость'}</button></form>)}
  {!loading&&!items.length&&!error&&<p>В выбранном магазине ещё нет каталога товаров. Загрузите его в разделе магазинов.</p>}
 </section><SettingsChangeHistory/></div>;
}
