/** Computed-browser regression probe using the actual cabinet styles and synthetic markup.
 * No account, API, credentials, or upstream network. Real cabinet acceptance is separate.
 * EDGE_PATH may point to a Chromium browser on another platform.
 */
import {spawn,execFileSync} from 'node:child_process';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const browserPath=process.env.EDGE_PATH??(process.platform==='win32'?'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe':'google-chrome');
const revision=process.argv.find(value=>value.startsWith('--revision='))?.slice(11);
if(revision&&!/^[0-9a-f]{7,40}$/.test(revision))throw new Error('Use an exact Git revision for the baseline probe');
const profile=mkdtempSync(join(tmpdir(),'mh-mobile-css-')),port=12000+Math.floor(Math.random()*1000);
const browser=spawn(browserPath,['--headless=new','--disable-gpu',`--remote-debugging-port=${port}`,`--user-data-dir=${profile}`,'about:blank'],{stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let socket;
try{
 let tabs;
 for(let i=0;i<60&&!tabs;i++){try{tabs=await(await fetch(`http://127.0.0.1:${port}/json`)).json();}catch{await sleep(100);}}
 if(!tabs)throw new Error('Chromium is unavailable; set EDGE_PATH');
 socket=new WebSocket(tabs.find(tab=>tab.type==='page').webSocketDebuggerUrl);
 const pending=new Map();let sequence=0;
 socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.id&&pending.has(message.id)){const done=pending.get(message.id);pending.delete(message.id);message.error?done.reject(new Error(message.error.message)):done.resolve(message.result);}});
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 const command=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
 const evaluate=async expression=>{const value=await command('Runtime.evaluate',{expression,returnByValue:true});if(value.exceptionDetails)throw new Error(value.exceptionDetails.text);return value.result.value;};
 const files=['styles.css','Theme.css','ShopContextBoundary.css','MarketplaceBoundary.css','CashflowPanel.css','TaxReconciliationPanel.css'];
 const styles=files.map(name=>revision?execFileSync('git',['show',`${revision}:mh-redesign-lab/src/${name}`],{encoding:'utf8'}):readFileSync(new URL('../src/'+name,import.meta.url),'utf8')).join('\n');
 await command('Page.enable');const frame=(await command('Page.getFrameTree')).frameTree.frame.id;
 const results=[];
 for(const theme of ['dark','light'])for(const width of [320,360,390,768]){
  await command('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:1,mobile:true});
  const html=`<html data-theme="${theme}"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles}</style></head><body>
  <section class="marketplace-bar"><span>Площадка</span><div role="group"><button>Wildberries</button><button>Ozon</button></div><p>WB · действия в пределах ваших прав</p></section>
  <section class="panel shop-context-bar"><label>Магазин кабинета <select><option>Основной магазин</option></select></label><p class="shop-context-access">Действия доступны в пределах ваших прав.</p><details class="shop-context-help"><summary>Что общее для магазинов</summary><p>Общие ресурсы</p></details></section>
  <div class="app-shell"><main class="workspace"><div class="content-grid compact-flow"><section class="panel">Производство: основная панель</section><aside class="insight-column panel">Дополнительная панель</aside></div>
  <button class="signal done detail-trigger">Выполнено</button><button class="funnel-bar">Рекламные заказы</button><div class="tax-tabs"><button id="tax-tab-calculation" aria-selected="true">Расчёт</button></div><section class="cash-accounts"><input aria-label="Счёт"><select><option>Тип</option></select></section></main></div>
  <nav class="bottom-nav"><button>Сегодня</button><button>Финансы</button><button>Помощник</button><button>Ещё</button></nav>
  <div class="backdrop operation-backdrop"><section class="plan-drawer operation-dialog" role="dialog" aria-modal="true"><h2>Операция</h2><input aria-label="Сумма"><button>Сохранить</button></section></div></body></html>`;
  await command('Page.setDocumentContent',{frameId:frame,html});
  const measured=await evaluate(`(()=>{
   const style=selector=>getComputedStyle(document.querySelector(selector)),rect=selector=>document.querySelector(selector).getBoundingClientRect();
   const channels=color=>color.match(/[\\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});
   const contrast=selector=>{const s=style(selector),l=c=>channels(c).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0),a=l(s.color),b=l(s.backgroundColor);return(Math.max(a,b)+.05)/(Math.min(a,b)+.05);};
   const dialog=rect('.operation-dialog'),backdrop=style('.operation-backdrop'),controls=[...document.querySelectorAll('.cash-accounts input,.cash-accounts select')];
   return{viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,columns:style('.compact-flow').gridTemplateColumns.split(' ').length,mainWidth:rect('.compact-flow>.panel').width,
    dialogLeft:dialog.left,dialogRight:dialog.right,paddingLeft:parseFloat(backdrop.paddingLeft),paddingRight:parseFloat(backdrop.paddingRight),navColumns:style('.bottom-nav').gridTemplateColumns.split(' ').length,navFont:parseFloat(style('.bottom-nav button').fontSize),focusPadding:parseFloat(getComputedStyle(document.documentElement).scrollPaddingBottom),
    touch:controls.map(e=>e.getBoundingClientRect().height),helpTouch:rect('.shop-context-help summary').height,contrast:['.signal.done','.funnel-bar','#tax-tab-calculation'].map(contrast)};
  })()`);
  const failures=[];
  const keyboard=await evaluate(`(()=>{
   document.querySelector('.operation-backdrop').remove();
   const workspace=document.querySelector('.workspace');
   for(let i=0;i<20;i++){const input=document.createElement('input');input.setAttribute('aria-label','Поле '+i);input.style.cssText='display:block;margin:100px 0;width:90%;height:44px';input.className='focus-probe';workspace.append(input);}
   let obscured=0;for(const input of document.querySelectorAll('.focus-probe')){input.focus();const r=input.getBoundingClientRect(),nav=document.querySelector('.bottom-nav').getBoundingClientRect();if(r.bottom>nav.top+.5)obscured++;}
   const before=parseFloat(getComputedStyle(document.querySelector('.marketplace-bar')).paddingTop);
   document.documentElement.style.setProperty('--mobile-status-bar','30px');
   return{obscured,notchPaddingDelta:parseFloat(getComputedStyle(document.querySelector('.marketplace-bar')).paddingTop)-before,shopPadding:parseFloat(getComputedStyle(document.querySelector('.shop-context-bar')).paddingTop)};
  })()`);
  if(measured.columns!==1||measured.mainWidth<width*.6)failures.push('compact main panel collapsed');
  if(measured.scrollWidth>width+1)failures.push('horizontal document overflow');
  if(measured.dialogLeft<measured.paddingLeft-.5||measured.dialogRight>width-measured.paddingRight+.5)failures.push('dialog crosses safe padding');
  if(measured.navColumns!==4||measured.navFont<12||measured.focusPadding<90)failures.push('bottom navigation/focus clearance');
  if(measured.touch.some(height=>height<44)||measured.helpTouch<24)failures.push('small touch target');
  if(measured.contrast.some(value=>value<4.5))failures.push('text contrast below 4.5');
  if(keyboard.obscured)failures.push('focused input hidden below navigation');
  if(width<=640&&(keyboard.notchPaddingDelta<29.5||keyboard.shopPadding>10))failures.push('top safe-area ownership');
  results.push({theme,width,...measured,...keyboard,failures});
 }
 console.log(JSON.stringify(results,null,2));
 if(results.some(result=>result.failures.length))process.exitCode=1;
}finally{
 socket?.close();browser.kill();
 await sleep(300);
 // Only this invocation's verified temp-directory child is removed.
 if(profile.startsWith(join(tmpdir(),'mh-mobile-css-')))try{rmSync(profile,{recursive:true,force:true,maxRetries:3,retryDelay:150});}catch{}
}
