import { Component, Suspense, useState, type ReactNode } from "react";

class PanelErrorBoundary extends Component<{children:ReactNode;label:string},{failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed?<section className="panel" role="alert"><p>Не удалось загрузить {this.props.label}. Другие разделы доступны.</p><p>Перед обновлением страницы сохраните введённые данные.</p><button className="secondary-button" type="button" onClick={()=>window.location.reload()}>Обновить страницу</button></section>:this.props.children;}
}

/** Keep the shell outside the boundary; a missing chunk must not hide navigation. */
export function LazyPanel({children,label="раздел"}:{children:ReactNode;label?:string}){
  return <PanelErrorBoundary label={label}><Suspense fallback={<p role="status" aria-live="polite">Загружаем {label}…</p>}>{children}</Suspense></PanelErrorBoundary>;
}

/** A collapsed native details does not itself prevent mounting its children. */
export function LazyDisclosure({title,children}:{title:string;children:ReactNode}){
  const [visited,setVisited]=useState(false);
  return <details className="panel ozon-cabinet-panel" onToggle={event=>{if(event.currentTarget.open)setVisited(true);}}><summary>{title}</summary>{visited?<LazyPanel label={title}>{children}</LazyPanel>:null}</details>;
}
