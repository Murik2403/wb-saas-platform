export function ProductFeedbackLink({ gatewayUrl = "https://marketshelper.ru" }: { gatewayUrl?: string }) {
  return <section className="work-section" aria-labelledby="product-feedback-title">
    <h3 id="product-feedback-title">Поделиться опытом работы</h3>
    <p>Добровольно сообщите причину отказа или фактическое время ручной сверки за выбранный период. Форма доступна владельцу кабинета. Ответ не изменит учёт и тариф.</p>
    <a className="secondary-button" href={`${gatewayUrl}/feedback`}>Открыть защищённую форму обратной связи</a>
  </section>;
}
