const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '../templates/marketing_calculator.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
const names = ['calcPrice','calcUnits','calcCost','calcCommission','calcLogistics','calcReturns','calcStorage','calcAcceptance','calcAds','calcPackaging','calcLosses','calcTax','calcCapital'];
const basisNames = ['calcCostBasis','calcLogisticsBasis','calcReturnsBasis','calcStorageBasis','calcAcceptanceBasis','calcAdsBasis','calcPackagingBasis','calcLossesBasis','calcTaxBasis'];
const defaults = [1200,1,420,17,90,0,0,0,60,0,0,0,0];
function load(search) {
  const nodes = {};
  names.concat(basisNames,['calcProfit','calcMargin','calcCoverage','calcCapitalResult','calcError','calcDownload']).forEach(id => {
    nodes[id] = { value:'', textContent:'', attrs:{}, hidden:false, disabled:false, className:'', listeners:{},
      setAttribute(k,v){this.attrs[k]=v;}, addEventListener(e,cb){this.listeners[e]=cb;} };
  });
  names.forEach((name,i)=>{nodes[name].value=String(defaults[i]);});
  basisNames.forEach(name=>{nodes[name].value='per_unit';});
  const saved={urls:[],files:[],csv:null};
  const sandbox={document:{getElementById:id=>nodes[id],createElement:()=>({click(){saved.files.push(this.download);}}),body:{appendChild(){},removeChild(){}}},
    URLSearchParams,location:{search:search||'',pathname:'/calculator'},history:{replaceState(s,t,u){saved.urls.push(u);}},
    Blob:function(parts){this.parts=parts;},URL:{createObjectURL:b=>{saved.csv=b.parts.join('');return 'blob:test';},revokeObjectURL(){}}};
  vm.runInNewContext(script,sandbox);
  const run=values=>{names.forEach((name,i)=>{nodes[name].value=String(values[i]);});nodes.calcPrice.listeners.input();};
  return {nodes,saved,run};
}
let page=load();
assert.equal(page.nodes.calcProfit.textContent,'426 ₽');
assert.equal(page.nodes.calcMargin.textContent,'35,5%');
assert.equal(page.nodes.calcCoverage.textContent,'4/9');
page.run([100,1,110,0,0,0,0,0,0,0,0,0,0]);
assert.equal(page.nodes.calcProfit.textContent,'-10 ₽');
page.run([100,1,0,0,0,0,0,0,0,0,0,0,500]);
assert.equal(page.nodes.calcCapitalResult.textContent,'500 ₽');
page.run([100,1,0,0,0,20,0,0,0,0,0,10,0]);
assert.equal(page.nodes.calcProfit.textContent,'70 ₽');
page.nodes.calcDownload.listeners.click();
assert.match(page.saved.csv,/Возвраты и обратная логистика;-20/);
assert.match(page.saved.csv,/Налог;-10/);
page.run([1000,2,100,10,20,30,40,50,60,70,80,90,100]);
assert.match(page.saved.urls.at(-1),/units=2/);
page.run([1000,2,400,0,0,0,0,0,0,0,0,0,0]);
page.nodes.calcCostBasis.value='total';
page.nodes.calcCostBasis.listeners.change();
assert.equal(page.nodes.calcProfit.textContent,'800 ₽');
assert.match(page.saved.urls.at(-1),/cost_basis=total/);
page.run([1000,2,0,0,0,0,0,0,0,0,0,100,0]);
page.nodes.calcTaxBasis.value='total';
page.nodes.calcTaxBasis.listeners.change();
assert.equal(page.nodes.calcProfit.textContent,'950 ₽');
assert.match(page.saved.urls.at(-1),/tax_basis=total/);
page.nodes.calcTaxBasis.value='revenue_pct';
page.nodes.calcTax.value='10';
page.nodes.calcTax.listeners.input();
assert.equal(page.nodes.calcProfit.textContent,'900 ₽');
assert.match(page.saved.urls.at(-1),/tax_basis=revenue_pct/);
page.nodes.calcDownload.listeners.click();
assert.match(page.saved.csv,/Налог;-10;% от выручки;-100/);
page=load('?price=1000&units=2&cost=100&commission=10&logistics=20&returns=30&storage=40&acceptance=50&ads=60&packaging=70&losses=80&tax=90&capital=100');
assert.equal(page.nodes.calcUnits.value,'2');
assert.equal(page.nodes.calcCapitalResult.textContent,'100 ₽');
page=load('?price=<img&units=0');
assert.equal(page.nodes.calcPrice.value,'1200');
assert.equal(page.nodes.calcUnits.value,'1');
console.log('Calculator: nine-article formula, coverage, capital, CSV, permalink and validation passed.');
